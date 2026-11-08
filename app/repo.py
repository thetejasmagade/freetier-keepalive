import json

from psycopg.types.json import Jsonb

from . import crypto
from .config import settings
from .db import connect


# ---- services -------------------------------------------------------------

def list_services():
    with connect() as c:
        return c.execute("SELECT * FROM services ORDER BY name").fetchall()


def get_service(service_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM services WHERE id = %s", (service_id,)).fetchone()


def secrets_of(service) -> dict:
    return json.loads(crypto.decrypt(service["secret"])) if service["secret"] else {}


def full_config(service) -> dict:
    return {**service["config"], **secrets_of(service)}


def create_service(name: str, type_: str, config: dict, secrets: dict) -> int:
    with connect() as c:
        row = c.execute(
            "INSERT INTO services (name, type, config, secret) VALUES (%s, %s, %s, %s) RETURNING id",
            (name, type_, Jsonb(config), crypto.encrypt(json.dumps(secrets))),
        ).fetchone()
        return row["id"]


def update_service(service_id: int, name: str, config: dict, new_secrets: dict) -> None:
    """Blank values in new_secrets keep the stored value."""
    service = get_service(service_id)
    merged = secrets_of(service)
    merged.update({k: v for k, v in new_secrets.items() if v})
    with connect() as c:
        c.execute(
            "UPDATE services SET name = %s, config = %s, secret = %s, updated_at = now() WHERE id = %s",
            (name, Jsonb(config), crypto.encrypt(json.dumps(merged)), service_id),
        )


def delete_service(service_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM services WHERE id = %s", (service_id,))


def toggle_service(service_id: int) -> None:
    with connect() as c:
        c.execute("UPDATE services SET enabled = NOT enabled WHERE id = %s", (service_id,))


# ---- results --------------------------------------------------------------

def add_result(service_id: int, ok: bool, latency_ms, error) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO results (service_id, ok, latency_ms, error) VALUES (%s, %s, %s, %s)",
            (service_id, ok, latency_ms, error),
        )
        c.execute(
            """DELETE FROM results WHERE service_id = %s AND id NOT IN (
                   SELECT id FROM results WHERE service_id = %s ORDER BY id DESC LIMIT %s)""",
            (service_id, service_id, settings.history),
        )


def latest_results() -> dict:
    """{service_id: [newest, ..., oldest]} (at most HISTORY_PER_SERVICE each)."""
    with connect() as c:
        rows = c.execute("SELECT * FROM results ORDER BY service_id, id DESC").fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["service_id"], []).append(r)
    return out


# ---- run log (last N executions) ------------------------------------------

def add_run(trigger: str, output: dict, duration_ms: int) -> None:
    with connect() as c:
        c.execute(
            """INSERT INTO runs (trigger, status, reason, checked, failed, duration_ms, output)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (trigger, output["status"], output.get("reason"), output.get("checked", 0),
             len(output.get("failed", [])), duration_ms, Jsonb(output)),
        )
        c.execute(
            "DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT %s)",
            (settings.history,),
        )


def recent_runs():
    with connect() as c:
        return c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT %s", (settings.history,)).fetchall()


# ---- meta / run lock ------------------------------------------------------

def set_last_run_now() -> None:
    with connect() as c:
        c.execute(
            """INSERT INTO meta (key, value) VALUES ('last_run_at', now()::text)
               ON CONFLICT (key) DO UPDATE SET value = excluded.value"""
        )


def last_run_at():
    with connect() as c:
        row = c.execute("SELECT value::timestamptz AS t FROM meta WHERE key = 'last_run_at'").fetchone()
        return row["t"] if row else None


def minutes_since_last_run():
    with connect() as c:
        row = c.execute(
            "SELECT extract(epoch FROM now() - value::timestamptz) / 60 AS m FROM meta WHERE key = 'last_run_at'"
        ).fetchone()
        return float(row["m"]) if row else None


def acquire_run_lock() -> bool:
    """Pooler-safe lock (no session advisory locks). A lock older than 3 minutes is stale."""
    with connect() as c:
        row = c.execute(
            """INSERT INTO meta (key, value) VALUES ('run_lock', now()::text)
               ON CONFLICT (key) DO UPDATE SET value = now()::text
               WHERE meta.value::timestamptz < now() - interval '3 minutes'
               RETURNING key"""
        ).fetchone()
        return row is not None


def release_run_lock() -> None:
    with connect() as c:
        c.execute("UPDATE meta SET value = '1970-01-01 00:00:00+00' WHERE key = 'run_lock'")
