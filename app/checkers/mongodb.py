from pymongo import MongoClient

from .base import Checker, Field, register


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

    def check(self, cfg: dict, timeout: int) -> None:
        ms = timeout * 1000
        client = MongoClient(
            cfg["uri"], serverSelectionTimeoutMS=ms, connectTimeoutMS=ms, socketTimeoutMS=ms
        )
        try:
            client.admin.command("ping")
            if cfg.get("database"):
                client[cfg["database"]]["keepalive"].find_one()
        finally:
            client.close()
