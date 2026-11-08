# Auto-Cron: Keep-Alive Service for Free-Tier Databases

## 1. Goal

Free-tier databases (Supabase, MongoDB Atlas, Aiven) pause when idle. One small Flask service
connects to each of them every hour with a cheap real query and shows the results on its home page.
Services are added from the UI, with no code change or redeploy.

## 2. Decisions

| Topic | Decision |
|---|---|
| Hosting | **Render** free web service |
| Trigger | **Cloudflare Worker Cron Trigger** (free) calls `GET /cron/run` hourly with a bearer token |
| App storage | **Supabase Postgres** (`CONTROL_DB_URL`, Session pooler string) |
| Targets | Supabase Postgres, Aiven Postgres, MongoDB Atlas; HTTP URLs later |
| Alerts | Only a banner on the Flask home page |
| History | Last 5 results per service |
| Plug and play | Add services in the UI; add a new *type* with one plugin file |

## 3. Why this combination

- **Render free sleeps** after 15 minutes idle and takes up to about 1 minute to wake.
  **Cloudflare Workers** can wait that long (no 30-second limit like cron-job.org), so the hourly
  call wakes Render and gets the result. The Worker waits up to 3 minutes and retries once.
- No keep-awake pings are needed. Render runs about 15–20 minutes per hour, roughly 200 of the
  750 free hours a month, which leaves room for other free Render services.
- **Render's disk is wiped** on spin-down, restart and redeploy, so all state lives in Supabase.
- Supabase's direct connection is IPv6-only, so the app uses the **Session pooler** (IPv4). The code
  avoids session advisory locks and prepared statements, so it is pooler-safe.
- Duplicate or retried calls are harmless: a run is skipped if the last one was under 30 minutes ago,
  and a lock row in the database prevents overlapping runs.

## 4. Data model (Supabase Postgres, created on first request)

- `services(id, name, type, config jsonb, secret bytea, enabled, ...)`: secrets are Fernet-encrypted
  using a key derived from `SECRET_KEY`.
- `results(id, service_id, ok, latency_ms, error, checked_at)`: trimmed to the latest 5 per service.
- `meta(key, value)`: `last_run_at` and `run_lock`.

## 5. Plug and play

- **New service:** Dashboard, "Add service", choose a type, fill the generated form, optionally "Test
  connection", Save. The next run includes it.
- **New type:** one file in `app/checkers/` using `@register` with a list of `Field`s and a `check()`
  that raises on failure. It is auto-discovered and its form is generated from the fields.
- Built-in types: `postgres`, `mongodb`, `http`.

## 6. Routes

| Path | Auth | Purpose |
|---|---|---|
| `GET /` | login | Dashboard: failing banner, summary, services with last-5 dots |
| `GET/POST /login`, `POST /logout` | password | Single admin (`ADMIN_PASSWORD`) |
| `GET/POST /services/new`, `/services/<id>/edit` | login | Dynamic forms, Test connection |
| `POST /services/<id>/delete \| toggle \| run` | login | Manage one service |
| `POST /run` | login | Run all now |
| `GET /cron/run` | `RUN_TOKEN` (Bearer or `?token=`) | Called by the Cloudflare Worker; returns a JSON summary |
| `GET /health` | none | Render health check |

## 7. Layout

```
app/  __init__ config db crypto repo runner auth routes
      checkers/ base postgres mongodb http_ping   (auto-discovered)
      templates/  static/
cloudflare/  wrangler.toml  src/index.js           (hourly trigger Worker)
render.yaml  wsgi.py  requirements.txt  .env.example  README.md
```

## 8. Status

- [x] Plan
- [x] Flask app, Cloudflare Worker and Render config written
- [x] venv created and requirements installed
- [ ] First local run against Supabase
- [ ] Deploy to Render
- [ ] Deploy the Cloudflare Worker
- [ ] Not built: login rate limiting, automated tests

## 9. Risks to verify

- Cloudflare's wall-time limit for scheduled Workers must cover waiting up to 3 minutes per attempt.
- Render free tier terms can change. Check the Render dashboard's instance-hour usage after a week.
