import requests

from .base import Checker, Field, register


def _parse_headers(text: str) -> dict:
    headers = {}
    for line in (text or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


@register
class HttpChecker(Checker):
    type = "http"
    label = "HTTP URL (web apps, REST APIs)"
    fields = [
        Field("url", "URL", required=True, placeholder="https://example.com/health"),
        Field("method", "Method", kind="select", choices=["GET", "HEAD", "POST"], default="GET"),
        Field("expect_status", "Expected status", default="200"),
        Field("headers", "Headers (one 'Key: Value' per line)", secret=True, kind="textarea",
              help="Stored encrypted, e.g. apikey: ..."),
    ]

    def check(self, cfg: dict, timeout: int):
        expected = int(cfg.get("expect_status") or 200)
        resp = requests.request(
            cfg.get("method") or "GET",
            cfg["url"],
            headers=_parse_headers(cfg.get("headers", "")),
            timeout=timeout,
        )
        if resp.status_code != expected:
            raise RuntimeError(f"expected HTTP {expected}, got {resp.status_code}")
        body = resp.text.strip()
        out = f"HTTP {resp.status_code} {resp.reason}"
        if body:
            out += "\n" + body[:500] + (" ..." if len(body) > 500 else "")
        return out
