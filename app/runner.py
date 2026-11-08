import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait

from . import checkers, repo
from .config import settings

log = logging.getLogger(__name__)


def _clean_error(exc: Exception, cfg: dict, checker) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    for f in checker.fields:
        val = cfg.get(f.name)
        if f.secret and val and len(val) >= 4:
            msg = msg.replace(val, "***")
    msg = re.sub(r"(://[^:/\s@]+:)[^@\s]+@", r"\1***@", msg)
    return msg[:500]


def check_config(type_: str, cfg: dict, retries: int | None = None) -> dict:
    """Run one check (with retries). Never raises. Returns {ok, latency_ms, error}."""
    cls = checkers.get(type_)
    if cls is None:
        return {"ok": False, "latency_ms": None, "error": f"Unknown service type '{type_}'"}
    checker = cls()
    attempts = (settings.check_retries if retries is None else retries) + 1
    error = None
    for attempt in range(attempts):
        start = time.monotonic()
        try:
            checker.check(cfg, settings.check_timeout)
            return {"ok": True, "latency_ms": int((time.monotonic() - start) * 1000), "error": None}
        except Exception as exc:  # noqa: BLE001 - any failure is a failed check
            error = _clean_error(exc, cfg, cls)
            if attempt < attempts - 1:
                time.sleep(1)
    return {"ok": False, "latency_ms": None, "error": error}


def _check_service(service: dict) -> dict:
    try:
        cfg = repo.full_config(service)
    except Exception:  # noqa: BLE001 - e.g. SECRET_KEY changed
        return {"ok": False, "latency_ms": None, "error": "Cannot decrypt stored secrets (SECRET_KEY changed?)"}
    return check_config(service["type"], cfg)


def _run(services: list) -> list:
    if not services:
        return []
    ex = ThreadPoolExecutor(max_workers=min(8, len(services)))
    futures = {ex.submit(_check_service, s): s for s in services}
    done, _ = wait(futures, timeout=settings.run_budget)
    out = []
    for fut, svc in futures.items():
        if fut in done:
            res = fut.result()
        else:
            res = {"ok": False, "latency_ms": None, "error": "Timed out (run time budget exceeded)"}
        repo.add_result(svc["id"], res["ok"], res["latency_ms"], res["error"])
        out.append({"name": svc["name"], **res})
    ex.shutdown(wait=False, cancel_futures=True)
    return out


def run_all(respect_gap: bool = False, trigger: str = "manual") -> dict:
    """Check every enabled service now, record the execution, and return the summary."""
    start = time.monotonic()
    out = _run_all(respect_gap)
    try:
        repo.add_run(trigger, out, int((time.monotonic() - start) * 1000))
    except Exception:  # noqa: BLE001 - logging the run must not break the run itself
        log.exception("Could not record run")
    return out


def _run_all(respect_gap: bool) -> dict:
    if respect_gap:
        since = repo.minutes_since_last_run()
        if since is not None and since < settings.min_run_gap_minutes:
            return {"status": "skipped", "reason": f"last run was {since:.0f} min ago"}
    if not repo.acquire_run_lock():
        return {"status": "skipped", "reason": "another run is in progress"}
    try:
        services = [s for s in repo.list_services() if s["enabled"]]
        results = _run(services)
        repo.set_last_run_now()
    finally:
        repo.release_run_lock()
    failed = [r["name"] for r in results if not r["ok"]]
    return {"status": "done", "checked": len(results), "failed": failed, "results": results}


def run_one(service_id: int) -> dict | None:
    service = repo.get_service(service_id)
    if service is None:
        return None
    res = _check_service(service)
    repo.add_result(service["id"], res["ok"], res["latency_ms"], res["error"])
    return {"name": service["name"], **res}
