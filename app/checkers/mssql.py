from .base import Checker, Field, format_rows, register


@register
class SqlServerChecker(Checker):
    type = "mssql"
    label = "Microsoft SQL Server (Azure SQL, ...)"
    fields = [
        Field("host", "Server host", required=True, placeholder="myserver.database.windows.net"),
        Field("port", "Port", default="1433"),
        Field("database", "Database", required=True),
        Field("user", "User", required=True),
        Field("password", "Password", secret=True, required=True),
        Field("query", "Query", default="SELECT 1", help="A cheap read-only query."),
    ]

    def check(self, cfg: dict, timeout: int):
        import pymssql

        conn = pymssql.connect(
            server=cfg["host"],
            port=str(cfg.get("port") or "1433"),
            user=cfg["user"],
            password=cfg["password"],
            database=cfg["database"],
            login_timeout=timeout,
            timeout=timeout,
        )
        try:
            cur = conn.cursor()
            cur.execute(cfg.get("query") or "SELECT 1")
            if cur.description is None:
                return f"OK, {cur.rowcount} row(s) affected"
            return format_rows([c[0] or f"col{i + 1}" for i, c in enumerate(cur.description)],
                               cur.fetchall())
        finally:
            conn.close()
