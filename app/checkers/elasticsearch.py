import requests

from .base import Checker, Field, register, to_text


@register
class SearchClusterChecker(Checker):
    type = "elasticsearch"
    label = "Elasticsearch / OpenSearch (Elastic Cloud, Aiven, Bonsai, ...)"
    fields = [
        Field("url", "Cluster URL", required=True, placeholder="https://my-cluster.example.com:9200"),
        Field("username", "Username (optional)"),
        Field("password", "Password (optional)", secret=True),
        Field("api_key", "API key (optional)", secret=True,
              help="Elasticsearch API key (sent as 'Authorization: ApiKey ...'). "
                   "Use either this or username/password."),
    ]

    def check(self, cfg: dict, timeout: int):
        headers = {}
        auth = None
        if cfg.get("api_key"):
            headers["Authorization"] = f"ApiKey {cfg['api_key']}"
        elif cfg.get("username"):
            auth = (cfg["username"], cfg.get("password") or "")
        resp = requests.get(
            cfg["url"].rstrip("/") + "/_cluster/health", headers=headers, auth=auth, timeout=timeout
        )
        if resp.status_code != 200:
            raise RuntimeError(f"cluster health returned HTTP {resp.status_code}")
        health = resp.json()
        if health.get("status") == "red":
            raise RuntimeError("cluster status is red")
        keys = ("cluster_name", "status", "number_of_nodes", "active_shards", "unassigned_shards")
        return to_text({k: health[k] for k in keys if k in health})
