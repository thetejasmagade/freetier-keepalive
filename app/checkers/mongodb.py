from pymongo import MongoClient

from .base import Checker, Field, register, to_text


@register
class MongoChecker(Checker):
    type = "mongodb"
    label = "MongoDB (Atlas)"
    fields = [
        Field("uri", "Connection URI", secret=True, required=True,
              placeholder="mongodb+srv://user:pass@cluster.mongodb.net/"),
        Field("database", "Database (optional)",
              help="If set, also reads one document from its 'keepalive' collection."),
    ]

    def check(self, cfg: dict, timeout: int):
        ms = timeout * 1000
        client = MongoClient(
            cfg["uri"], serverSelectionTimeoutMS=ms, connectTimeoutMS=ms, socketTimeoutMS=ms
        )
        try:
            lines = ["ping -> " + to_text(client.admin.command("ping"))]
            if cfg.get("database"):
                doc = client[cfg["database"]]["keepalive"].find_one()
                lines.append(f"{cfg['database']}.keepalive.find_one() -> "
                             + ("null (no documents)" if doc is None else to_text(doc)[:300]))
            return "\n".join(lines)
        finally:
            client.close()
