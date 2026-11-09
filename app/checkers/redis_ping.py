from .base import Checker, Field, register, to_text


@register
class RedisChecker(Checker):
    type = "redis"
    label = "Redis / Valkey (Upstash, Redis Cloud, Aiven, KeyDB, Dragonfly, ...)"
    fields = [
        Field("url", "Connection URL", secret=True, required=True,
              placeholder="rediss://default:password@host:6379/0",
              help="Use rediss:// for TLS (most hosted providers require it)."),
        Field("key", "Key to read (optional)",
              help="If set, also runs GET on this key (a missing key is fine)."),
    ]

    def check(self, cfg: dict, timeout: int):
        import redis

        client = redis.Redis.from_url(
            cfg["url"], socket_timeout=timeout, socket_connect_timeout=timeout
        )
        try:
            if not client.ping():
                raise RuntimeError("PING did not return PONG")
            lines = ["PING -> PONG"]
            if cfg.get("key"):
                value = client.get(cfg["key"])
                lines.append(f"GET {cfg['key']} -> " + ("(nil)" if value is None else to_text(value)[:300]))
            return "\n".join(lines)
        finally:
            client.close()
