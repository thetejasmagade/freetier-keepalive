from .base import Checker, Field, format_rows, register


@register
class Neo4jChecker(Checker):
    type = "neo4j"
    label = "Neo4j (AuraDB, ...)"
    fields = [
        Field("uri", "Connection URI", required=True, placeholder="neo4j+s://xxxxxxxx.databases.neo4j.io"),
        Field("user", "User", default="neo4j", required=True),
        Field("password", "Password", secret=True, required=True),
        Field("database", "Database (optional)", help="Leave blank for the default database."),
        Field("query", "Cypher query", default="RETURN 1",
              help="Use a small write query here if your provider only counts writes as activity."),
    ]

    def check(self, cfg: dict, timeout: int):
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            cfg["uri"],
            auth=(cfg["user"], cfg["password"]),
            connection_timeout=timeout,
            connection_acquisition_timeout=timeout,
        )
        try:
            with driver.session(database=cfg.get("database") or None) as session:
                result = session.run(cfg.get("query") or "RETURN 1")
                rows = result.data()
                counters = result.consume().counters
                text = format_rows(None, rows)
                if counters.contains_updates:
                    text += "\nupdates: " + ", ".join(
                        f"{k}={v}" for k, v in vars(counters).items()
                        if isinstance(v, int) and not isinstance(v, bool) and v
                    )
                return text
        finally:
            driver.close()
