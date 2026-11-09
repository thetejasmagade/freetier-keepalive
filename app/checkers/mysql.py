import ssl
from urllib.parse import unquote, urlparse

from .base import Checker, Field, format_rows, register


def _ssl_context(mode: str):
    if mode == "disable":
        return None
    ctx = ssl.create_default_context()
    if mode == "require":  # encrypt, but don't verify the server certificate
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


@register
class MySQLChecker(Checker):
    type = "mysql"
    label = "MySQL / MariaDB (Aiven, TiDB Cloud, ...)"
    fields = [
        Field("url", "Connection URL", secret=True, required=True,
              placeholder="mysql://user:pass@host:3306/dbname"),
        Field("tls", "TLS", kind="select", choices=["verify", "require", "disable"], default="verify",
              help="verify = encrypted and certificate checked; require = encrypted only "
                   "(for providers with their own CA, e.g. Aiven); disable = no TLS."),
        Field("query", "Query", default="SELECT 1", help="A cheap read-only query."),
    ]

    def check(self, cfg: dict, timeout: int):
        import pymysql

        u = urlparse(cfg["url"])
        if not u.hostname:
            raise ValueError("Connection URL must look like mysql://user:pass@host:3306/dbname")
        conn = pymysql.connect(
            host=u.hostname,
            port=u.port or 3306,
            user=unquote(u.username or ""),
            password=unquote(u.password or ""),
            database=unquote(u.path.lstrip("/")) or None,
            ssl=_ssl_context(cfg.get("tls") or "verify"),
            connect_timeout=timeout,
            read_timeout=timeout,
            write_timeout=timeout,
        )
        try:
            with conn.cursor() as cur:
                affected = cur.execute(cfg.get("query") or "SELECT 1")
                if cur.description is None:
                    return f"OK, {affected} row(s) affected"
                return format_rows([c[0] for c in cur.description], cur.fetchall())
        finally:
            conn.close()
