import psycopg

from .base import Checker, Field, format_rows, register


@register
class PostgresChecker(Checker):
    type = "postgres"
    label = "PostgreSQL / compatible (Supabase, Aiven, Neon, CockroachDB, ...)"
    fields = [
        Field(
            "dsn", "Connection string", secret=True, required=True,
            placeholder="postgresql://user:pass@host:5432/db?sslmode=require",
        ),
        Field("query", "Query", default="SELECT 1", help="A cheap read-only query."),
    ]

    def check(self, cfg: dict, timeout: int):
        with psycopg.connect(cfg["dsn"], connect_timeout=timeout, prepare_threshold=None) as conn:
            cur = conn.execute(cfg.get("query") or "SELECT 1")
            if cur.description is None:  # statement without a result set
                return cur.statusmessage
            return format_rows([c.name for c in cur.description], cur.fetchall())
