import psycopg
from psycopg.rows import dict_row

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    id          serial PRIMARY KEY,
    name        text UNIQUE NOT NULL,
    type        text NOT NULL,
    config      jsonb NOT NULL DEFAULT '{}'::jsonb,
    secret      bytea,
    enabled     boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS results (
    id          bigserial PRIMARY KEY,
    service_id  int NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    ok          boolean NOT NULL,
    latency_ms  int,
    error       text,
    checked_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS results_service_idx ON results (service_id, id DESC);
CREATE TABLE IF NOT EXISTS meta (
    key    text PRIMARY KEY,
    value  text
);
"""

_ready = False


def connect() -> psycopg.Connection:
    """Use as `with connect() as c:` - commits on success, rolls back on error, then closes.

    prepare_threshold=None keeps this safe behind Supabase's pooler (Supavisor/pgbouncer).
    """
    return psycopg.connect(
        settings.control_db_url,
        row_factory=dict_row,
        connect_timeout=10,
        prepare_threshold=None,
    )


def ensure_schema() -> None:
    global _ready
    if _ready:
        return
    with connect() as conn:
        conn.execute(SCHEMA)
    _ready = True
