# Auto-Cron

A small Flask service that connects to your free-tier databases (Supabase, Aiven, MongoDB Atlas, ...)
every hour so the providers don't pause them. Services are added from the web UI, with no code change
or redeploy.

See [plans/PLAN.md](plans/PLAN.md) for the design.

## How it works

```
Cloudflare Worker cron (hourly)
      │  GET /cron/run   (Authorization: Bearer RUN_TOKEN)
      ▼
Flask app on Render free  ──wakes if asleep (up to ~1 min)
      │  runs every enabled check in parallel
      ▼
Supabase Postgres  ◄── stores services, encrypted secrets, last 5 results
```

- The Worker waits up to 3 minutes for a reply and retries once, so Render's slow wake-up is fine.
- Render sleeps about 15 minutes after each run, so it uses roughly 200 of the 750 free hours a month.
- The dashboard (`/`) shows an alert banner for failing services plus the last 5 results per service.
- Each run also counts as activity on the Supabase project that holds the app's data.

## Setup

### 1. Supabase (app storage)
- In Supabase, open **Connect** and copy the **Session pooler** connection string (port 5432).
  Render can't reach Supabase's direct connection, which is IPv6-only.
- Tables are created automatically on the first request.

### 2. Render (Flask app)
- Push this repo to GitHub, then in Render choose **New > Blueprint** and pick the repo
  ([render.yaml](render.yaml) is included). You can also create a Python web service by hand with:
  - Build: `pip install -r requirements.txt`
  - Start: `gunicorn wsgi:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`
- Environment variables (see [.env.example](.env.example)):
  `CONTROL_DB_URL`, `SECRET_KEY` (the blueprint generates it), `ADMIN_PASSWORD`, `RUN_TOKEN`.
- Open `https://<app>.onrender.com`, log in with `ADMIN_PASSWORD`, and click **Add service**.

### 3. Cloudflare Worker (hourly trigger)
In [cloudflare/](cloudflare/):
1. Set `APP_URL` in [cloudflare/wrangler.toml](cloudflare/wrangler.toml) to your Render URL.
2. `npx wrangler login`
3. `npx wrangler secret put RUN_TOKEN`, using the same value as on Render
4. `npx wrangler deploy`

Check runs under **Workers > auto-cron-trigger > Logs** in the Cloudflare dashboard. To test the
schedule locally, run `npx wrangler dev --test-scheduled` and open `/__scheduled`.

### Local run
```
.\venv\Scripts\Activate.ps1
# set CONTROL_DB_URL, SECRET_KEY, ADMIN_PASSWORD, RUN_TOKEN and COOKIE_SECURE=false, then:
flask --app wsgi run
```

## Adding a new service type (plug and play)

Drop one file into [app/checkers/](app/checkers/). It is discovered automatically and appears in the
"Add service" list with its own form:

```python
from .base import Checker, Field, register

@register
class RedisChecker(Checker):
    type = "redis"
    label = "Redis / Valkey"
    fields = [Field("url", "Connection URL", secret=True, required=True)]

    def check(self, cfg, timeout):
        import redis
        redis.from_url(cfg["url"], socket_timeout=timeout).ping()   # raise on failure
```

Fields marked `secret=True` are stored encrypted and never shown again.
Existing types: `postgres`, `mongodb`, `http`.

## Notes

- Changing `SECRET_KEY` makes stored connection strings unreadable. Re-enter them if you rotate it.
- `/cron/run` skips if the last run was under `MIN_RUN_GAP_MINUTES` ago, so retries and duplicate calls are harmless.
- The first dashboard load after Render has been asleep can take up to a minute.
- Not included: login rate limiting and automated tests.
