import psycopg

from .base import Checker, Field, register


@register
class PostgresChecker(Checker):
    type = "postgres"
    label = "PostgreSQL (Supabase, Aiven, Neon, ...)"
    fields = [
        Field(
            "dsn", "Connection string", secret=True, required=True,
            placeholder="postgresql://user:pass@host:5432/db?sslmode=require",
        ),
        Field("query", "Query", default="SELECT 1", help="A cheap read-only query."),
    ]

    def check(self, cfg: dict, timeout: int) -> None:
        with psycopg.connect(cfg["dsn"], connect_timeout=timeout, prepare_threshold=None) as conn:
            conn.execute(cfg.get("query") or "SELECT 1").fetchall()
