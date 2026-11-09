from .base import Checker, Field, format_rows, register


@register
class OracleChecker(Checker):
    type = "oracle"
    label = "Oracle Database (Autonomous Database, ...)"
    fields = [
        Field("dsn", "Connect string", required=True, kind="textarea",
              placeholder="(description=(address=(protocol=tcps)(port=1522)(host=...))...)",
              help="Easy Connect (host:port/service) or a full TLS connect string. "
                   "For Autonomous Database, copy a TLS (walletless) connection string."),
        Field("user", "User", required=True),
        Field("password", "Password", secret=True, required=True),
        Field("query", "Query", default="SELECT 1 FROM DUAL", help="A cheap read-only query."),
    ]

    def check(self, cfg: dict, timeout: int):
        import oracledb  # thin mode: no Oracle Client libraries needed

        conn = oracledb.connect(
            user=cfg["user"],
            password=cfg["password"],
            dsn=cfg["dsn"],
            tcp_connect_timeout=timeout,
        )
        try:
            conn.call_timeout = timeout * 1000
            with conn.cursor() as cur:
                cur.execute(cfg.get("query") or "SELECT 1 FROM DUAL")
                if cur.description is None:
                    return f"OK, {cur.rowcount} row(s) affected"
                return format_rows([c[0] for c in cur.description], cur.fetchall())
        finally:
            conn.close()
