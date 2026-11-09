from datetime import datetime, timedelta, timezone

import psycopg
from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from . import checkers, db, repo, runner
from .auth import login_required, password_ok, token_ok
from .config import settings

bp = Blueprint("main", __name__)


@bp.before_request
def _bootstrap():
    if request.endpoint != "main.health":
        db.ensure_schema()


@bp.app_template_filter("ts")
def _ts(value):
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "never"


@bp.app_template_filter("tss")
def _tss(value):
    return value.strftime("%Y-%m-%d %H:%M:%S UTC") if value else "-"


@bp.app_context_processor
def _utc_now():
    return {"utc_now": datetime.now(timezone.utc)}


# ---- public ---------------------------------------------------------------

@bp.get("/health")
def health():
    return "ok"


@bp.get("/cron/run")
def cron_run():
    """Called daily by the Cloudflare Worker cron. Runs all checks and replies when done."""
    if not token_ok():
        abort(401)
    return jsonify(runner.run_all(respect_gap=True, trigger="cron"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if password_ok(request.form.get("password", "")):
            session.clear()
            session["admin"] = True
            return redirect(url_for("main.index"))
        flash("Wrong password.", "error")
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))


# ---- dashboard ------------------------------------------------------------

@bp.get("/")
@login_required
def index():
    services = repo.list_services()
    results = repo.latest_results()
    rows = []
    for s in services:
        rs = results.get(s["id"], [])  # newest first
        if not s["enabled"]:
            status = "disabled"
        elif not rs:
            status = "pending"
        else:
            status = "ok" if rs[0]["ok"] else "fail"
        cls = checkers.get(s["type"])
        rows.append({"s": s, "status": status, "history": list(reversed(rs)),
                     "type_label": cls.label if cls else s["type"], "last": rs[0] if rs else None})
    failing = [r for r in rows if r["status"] == "fail"]
    last_run = repo.last_run_at()
    next_due = last_run + timedelta(minutes=settings.run_interval_minutes) if last_run else None
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("ok", "fail", "pending", "disabled")}
    return render_template("dashboard.html", rows=rows, failing=failing,
                           last_run=last_run, next_due=next_due, counts=counts,
                           runs=repo.recent_runs())


@bp.post("/run")
@login_required
def run_all_now():
    out = runner.run_all(trigger="manual")
    if out["status"] == "skipped":
        flash("Run skipped: " + out["reason"], "error")
    else:
        flash(f"Checked {out['checked']} service(s); {len(out['failed'])} failing.", "info")
    return redirect(url_for("main.index"))


# ---- service CRUD ---------------------------------------------------------

def _read_form(cls):
    cfg, secrets = {}, {}
    for f in cls.fields:
        value = request.form.get(f.name, "").strip()
        (secrets if f.secret else cfg)[f.name] = value
    return cfg, secrets


def _missing(cls, cfg, secrets, stored_secrets):
    missing = []
    for f in cls.fields:
        if not f.required:
            continue
        if f.secret:
            if not secrets.get(f.name) and not stored_secrets.get(f.name):
                missing.append(f.label)
        elif not cfg.get(f.name):
            missing.append(f.label)
    return missing


@bp.route("/services/new", methods=["GET", "POST"])
@login_required
def service_new():
    type_ = request.values.get("type")
    cls = checkers.get(type_) if type_ else None
    if cls is None:
        return render_template("service_type.html", types=checkers.all_types())

    cfg, secrets = _read_form(cls) if request.method == "POST" else ({}, {})
    name = request.form.get("name", "").strip()
    test = None  # "Test connection" result, shown below the form
    if request.method == "POST":
        problems = (["Name"] if not name else []) + _missing(cls, cfg, secrets, {})
        if problems:
            flash("Required: " + ", ".join(problems), "error")
        elif request.form.get("action") == "test":
            test = runner.check_config(cls.type, {**cfg, **secrets}, retries=0)
        else:
            try:
                repo.create_service(name, cls.type, cfg, secrets)
            except psycopg.errors.UniqueViolation:
                flash("A service with that name already exists.", "error")
            else:
                flash(f"Added '{name}'. It will be included in the next run.", "info")
                return redirect(url_for("main.index"))
    return render_template("service_form.html", cls=cls, name=name, cfg=cfg, editing=False,
                           stored=set(), test=test)


@bp.route("/services/<int:service_id>/edit", methods=["GET", "POST"])
@login_required
def service_edit(service_id: int):
    service = repo.get_service(service_id)
    cls = checkers.get(service["type"]) if service else None
    if cls is None:
        abort(404)
    stored = repo.secrets_of(service)
    cfg, name = service["config"], service["name"]
    test = None  # "Test connection" result, shown below the form
    if request.method == "POST":
        cfg, secrets = _read_form(cls)
        name = request.form.get("name", "").strip()
        problems = (["Name"] if not name else []) + _missing(cls, cfg, secrets, stored)
        if problems:
            flash("Required: " + ", ".join(problems), "error")
        elif request.form.get("action") == "test":
            merged = {**stored, **{k: v for k, v in secrets.items() if v}}
            test = runner.check_config(cls.type, {**cfg, **merged}, retries=0)
        else:
            try:
                repo.update_service(service_id, name, cfg, secrets)
            except psycopg.errors.UniqueViolation:
                flash("A service with that name already exists.", "error")
            else:
                flash("Saved.", "info")
                return redirect(url_for("main.index"))
    return render_template("service_form.html", cls=cls, name=name, cfg=cfg, editing=True,
                           stored={k for k, v in stored.items() if v}, test=test)


@bp.post("/services/<int:service_id>/delete")
@login_required
def service_delete(service_id: int):
    repo.delete_service(service_id)
    flash("Deleted.", "info")
    return redirect(url_for("main.index"))


@bp.post("/services/<int:service_id>/toggle")
@login_required
def service_toggle(service_id: int):
    repo.toggle_service(service_id)
    return redirect(url_for("main.index"))


@bp.post("/services/<int:service_id>/run")
@login_required
def service_run(service_id: int):
    res = runner.run_one(service_id)
    if res is None:
        abort(404)
    flash(f"{res['name']}: " + ("OK" if res["ok"] else "failed - " + res["error"]),
          "info" if res["ok"] else "error")
    return redirect(url_for("main.index"))
