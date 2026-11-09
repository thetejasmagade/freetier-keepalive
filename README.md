# freetier-keepalive

Free-tier databases/services get paused when nobody uses them: Supabase pauses projects after about a week
without activity, and Aiven, MongoDB Atlas, Neon and others do something similar. freetier-keepalive is a
small Flask app that connects to each of your services **on a schedule you choose** (daily by
default; hourly, every few hours, weekly or any other cron expression also work) and runs a cheap
query or request, so every provider sees regular activity and keeps the service running.

You add, edit and remove services from a web dashboard. No code change or redeploy is needed, and
connection strings are stored encrypted.

The app is a standard Python WSGI app, so you can **host it anywhere** that runs Python: Render,
Railway, Fly.io, Koyeb, Google Cloud Run, Azure App Service, a VPS, a home server, or a Docker
container. The reference setup below uses only free tiers:

| Piece | Reference setup | Alternatives | Job |
|---|---|---|---|
| Trigger | Cloudflare Workers (cron) | Any scheduler that can send an HTTP request: system cron, GitHub Actions, cron-job.org, ... | Calls the app on your schedule |
| App | Render (free web service) | Any Python host (see [Part 3](#part-3-deploy-the-flask-app-any-server)) | Dashboard, plus runs the checks |
| App storage | Supabase Postgres | Any PostgreSQL database | Services, encrypted secrets, recent results |

---

## Contents

1. [How it works](#how-it-works)
2. [Set up with an AI assistant](#set-up-with-an-ai-assistant): a copy-paste prompt
3. [Deployment guide](#deployment-guide)
   - [Before you start](#before-you-start)
   - [Part 1: Supabase database](#part-1-supabase-database-the-apps-storage) (with SQL)
   - [Part 2: Generate the secrets](#part-2-generate-the-secrets)
   - [Part 3: Deploy the Flask app (any server)](#part-3-deploy-the-flask-app-any-server): Render, Linux VPS, Docker, other platforms
   - [Part 4: Cloudflare Worker](#part-4-cloudflare-worker-the-scheduled-trigger): CLI, dashboard, or another scheduler
   - [Choosing your schedule](#choosing-your-schedule)
   - [Part 5: Verify end to end](#part-5-verify-end-to-end)
   - [Updating and maintenance](#updating-and-maintenance)
4. [Using the dashboard](#using-the-dashboard)
5. [Service types and their fields](#service-types-and-their-fields)
6. [Configuration reference](#configuration-reference)
7. [Running locally](#running-locally)
8. [Adding a new service type](#adding-a-new-service-type)
9. [Project layout](#project-layout)
10. [Security notes](#security-notes)
11. [Troubleshooting](#troubleshooting)

---

## How it works

### The big picture

```
┌──────────────────────────────┐
│ Cloudflare Worker            │  your cron schedule (default "0 0 * * *")
│ cloudflare/src/index.js      │
└──────────────┬───────────────┘
               │ GET /cron/run
               │ Authorization: Bearer <RUN_TOKEN>
               ▼
┌──────────────────────────────┐
│ Flask app (any host)         │  on free hosts it may sleep and wake on the request
│ app/routes.py → runner.py    │
└──────┬───────────────┬───────┘
       │ reads/writes  │ checks every enabled service in parallel
       ▼               ▼
┌──────────────┐  ┌───────────────────────────────────────────┐
│ Supabase     │  │ Your services:                            │
│ Postgres     │  │  relational: Postgres, MySQL/MariaDB,     │
│ (app storage)│  │              SQL Server, Oracle           │
└──────────────┘  │  cache:      Redis / Valkey               │
                  │  NoSQL:      MongoDB, Neo4j,              │
                  │              Elasticsearch / OpenSearch   │
                  │  anything else with an HTTP URL           │
                  └───────────────────────────────────────────┘
```

### Step by step: what happens on each scheduled run

1. **The Worker fires.** At each time in your cron schedule (00:00 UTC by default), Cloudflare runs the `scheduled` handler in
   [cloudflare/src/index.js](cloudflare/src/index.js). It sends `GET <APP_URL>/cron/run` with
   `Authorization: Bearer <RUN_TOKEN>`.
2. **The app wakes up if needed.** Many free hosts (Render, Koyeb, ...) put idle apps to sleep, so
   the first request can take up to a minute. On an always-on host it answers right away. The
   Worker waits up to **3 minutes** for a reply. If the request fails or
   returns an error, it waits 30 seconds and **retries once**.
3. **The app checks the token.** `/cron/run` compares the token with `RUN_TOKEN` using a
   constant-time comparison ([app/auth.py](app/auth.py)). A wrong token gets `401`.
4. **Duplicate protection.** Before doing any work, [app/runner.py](app/runner.py) checks:
   - **Run gap:** if the last run finished less than `MIN_RUN_GAP_MINUTES` (default 30) ago,
     the call is skipped. This is why the Worker's retry can't run the checks twice.
   - **Run lock:** a row in the `meta` table acts as a lock so two runs never overlap. A lock
     older than 3 minutes counts as stale and can be taken over, so a crashed run can't block
     later ones. (This lock also works through connection poolers such as Supabase's, where
     Postgres session advisory locks are unreliable.)
5. **Checks run in parallel.** Every *enabled* service is loaded, its encrypted secrets are
   decrypted, and its checker runs on a thread pool of up to 8 workers:
   - each attempt has a timeout of `CHECK_TIMEOUT_SECONDS` (default 15),
   - a failed attempt is retried up to `CHECK_RETRIES` times (default 2), 1 second apart,
   - the whole run has a budget of `RUN_BUDGET_SECONDS` (default 90). A check still running
     after that is recorded as *"Timed out (run time budget exceeded)"*.
6. **Results are saved.** Each service gets one result row (ok, latency in ms, error text).
   Only the newest `HISTORY_PER_SERVICE` (default 5) results per service are kept. Passwords
   are masked as `***` in error messages before they are stored.
7. **The run is logged.** The app records the run (trigger, status, number checked, number
   failed, duration and the full JSON summary) in the `runs` table, keeping the latest 5, and
   updates `last_run_at`.
8. **The app replies** with a JSON summary, which the Worker writes to its logs:
   ```json
   {
     "status": "done",
     "checked": 3,
     "failed": ["aiven-pg"],
     "results": [
       {"name": "supabase-main", "ok": true,  "latency_ms": 412, "error": null},
       {"name": "atlas",         "ok": true,  "latency_ms": 980, "error": null},
       {"name": "aiven-pg",      "ok": false, "latency_ms": null, "error": "OperationalError: ..."}
     ]
   }
   ```
   A skipped call returns `{"status": "skipped", "reason": "last run was 2 min ago"}`.
9. **The app goes idle again.** On sleeping free hosts it goes back to sleep (Render: after about
   15 minutes). With the default daily schedule on Render this uses only about 8 of the 750 free
   instance-hours per month.

As a side effect, each run also counts as activity on the database that stores the app's own
data. If that's a free Supabase project, it stays active as well.

### What "a check" actually does

| Type | What it does | Reply shown on **Test connection** |
|---|---|---|
| `postgres`, `mysql`, `mssql`, `oracle` | Opens a connection and runs your query (default `SELECT 1`, or `SELECT 1 FROM DUAL` for Oracle). | The rows returned (up to 5, e.g. `1 row: {"one": 1}`), or `N row(s) affected` |
| `redis` | Connects and sends `PING`, plus an optional `GET` on a key you choose. | `PING -> PONG`, and `GET key -> value` or `(nil)` |
| `mongodb` | Connects and sends `ping`. If you set a database, it also reads one document from its `keepalive` collection. | `ping -> {"ok": 1.0}`, and the document or `null (no documents)` |
| `neo4j` | Connects and runs your Cypher query (default `RETURN 1`). | The rows returned, plus update counts for write queries |
| `elasticsearch` | Calls `GET /_cluster/health` and fails if the cluster status is `red`. | Cluster name, status, node and shard counts |
| `http` | Sends a GET/HEAD/POST to your URL with optional headers and expects a given status code (default 200). | Status line and the first 500 characters of the body |

Any exception counts as a failure. The error text appears on the dashboard and in the test
result. Replies are only shown when you test a connection. Scheduled runs record only
ok/failed, latency and error, never the reply.

### Data stored in the app's database

The tables are created automatically on the first request ([app/db.py](app/db.py)):

| Table | Contents |
|---|---|
| `services` | name, type, non-secret settings (`config`, JSON), encrypted secrets (`secret`), enabled flag |
| `results` | per-check result: ok, latency_ms, error, checked_at (last 5 per service) |
| `runs` | per-run log: trigger (`cron`/`manual`), status (`done`/`skipped`), counts, duration, JSON output (last 5) |
| `meta` | `last_run_at` and `run_lock` |

---

## Set up with an AI assistant

Instead of following the [Deployment guide](#deployment-guide) by hand, you can let an AI
assistant walk you through it, or do it for you.

- **Coding agents that can run commands** (Claude Code, Cursor, Windsurf, Copilot agent mode,
  ...): open this repository in the agent first. It runs the commands, edits the config files,
  and stops whenever you need to click something in a browser.
- **Chat assistants** (Claude, ChatGPT, Gemini, ...): they give you one step at a time to carry
  out yourself. Attach this README to the chat, or paste the repository URL if the assistant
  can browse.

Copy everything in the box below, fill in the three lines at the top, and paste it as your
first message. The assistant asks for anything else it needs.

> The prompt tells the assistant **never to ask you for secret values in the chat** (database
> password, tokens, admin password). You type those directly into the terminal, `.env` file or
> provider dashboards. If an assistant asks you to paste one anyway, don't.

````text
You are helping me deploy "freetier-keepalive", an open-source Flask app that keeps free-tier
databases and services from being paused for inactivity. It connects to each service I register
on a schedule and runs a cheap query or request. Set it up for me completely, from nothing to a
verified working deployment.

MY DETAILS (edit these before sending)
- Repository: <URL of my fork/copy of freetier-keepalive, or "already open in this workspace">
- My computer's OS: <Windows / macOS / Linux>
- Preferred app host: <Render / my own Linux server / Docker / Railway / Fly.io / not sure, recommend one>

THE ARCHITECTURE (do not redesign it)
1. App storage: a PostgreSQL database, Supabase free plan by default. The app keeps services,
   encrypted secrets and recent results there (tables: services, results, runs, meta).
2. The app: Python 3.10+ Flask, started with
   gunicorn wsgi:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT
   Health check: GET /health returns "ok". Dashboard at "/" (password login).
3. The scheduler: a Cloudflare Worker with a Cron Trigger (in the cloudflare/ folder) calls
   GET <APP_URL>/cron/run with the header "Authorization: Bearer <RUN_TOKEN>" on a schedule. It waits up to
   3 minutes for a sleeping host and retries once.
Required app environment variables: CONTROL_DB_URL, SECRET_KEY, ADMIN_PASSWORD, RUN_TOKEN.
Optional: RUN_INTERVAL_MINUTES (default 1440), MIN_RUN_GAP_MINUTES (default 30),
CHECK_TIMEOUT_SECONDS, CHECK_RETRIES, RUN_BUDGET_SECONDS, HISTORY_PER_SERVICE, COOKIE_SECURE.

SOURCE OF TRUTH
README.md in the repository has the full deployment guide: exact SQL, host-specific steps,
wrangler commands and troubleshooting. Read it before you start, and follow it rather than
improvising. If you can't open files or URLs, tell me which README section you need and I'll
paste it. Also read cloudflare/wrangler.toml, render.yaml and .env.example, and use the names
actually in those files (for example the Worker "name" in wrangler.toml) even if the README's
examples differ.

HOW TO WORK WITH ME
- First, find out whether you can run commands on my machine. If you can, run them yourself. If
  you can't, give me one step at a time with the exact command or click path, and wait for me to
  report the result before moving on.
- Before starting, ask me, in one short message, everything you need to decide the plan:
  1. Which app host (recommend Render's free plan if I'm unsure, and explain the trade-off:
     it sleeps when idle, and the Worker handles that).
  2. Do I already have Supabase / Cloudflare / GitHub / host accounts?
  3. How often should the keep-alive run? (default: daily at 00:00 UTC)
  4. Which services do I want to keep alive, by type only (e.g. "Supabase Postgres, Upstash
     Redis, MongoDB Atlas")? Supported types: postgres (also CockroachDB), mysql/mariadb, mssql,
     oracle, redis/valkey, mongodb, neo4j, elasticsearch/opensearch, http.
  Then show me a short numbered plan and wait for my OK.
- Whenever a step needs a browser (creating accounts or projects, OAuth logins such as
  "npx wrangler login", copying a connection string, dashboard clicks), say exactly what to
  click, then wait.
- Report progress after each part, e.g. "Part 1 done: database ready, RLS enabled".

SECRETS: STRICT RULES
- Never ask me to paste a password, token, connection string or key into this chat, and never
  print, echo or log one. If I paste one by accident, tell me to rotate it.
- Generate SECRET_KEY and RUN_TOKEN locally with:
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  Write them straight into a local .env file (copied from .env.example), or tell me to.
  .env is git-ignored; make sure it stays that way.
- I choose ADMIN_PASSWORD myself and type it in where it's needed.
- RUN_TOKEN must be the same value on the app host and in the Worker. Set the Worker's copy with
  the interactive "npx wrangler secret put RUN_TOKEN" (on Windows PowerShell never pipe the value
  in, because that adds a line break to the token).
- Never commit .env, .dev.vars or any file containing a secret. If you create cloudflare/.dev.vars
  for a local Worker test, add ".dev.vars" to .gitignore first.
- Never change SECRET_KEY once services have been added; that makes stored secrets unreadable.

ASK BEFORE ACTING
Get my explicit OK before anything that is public, costs money, or is hard to undo:
git commit or push, creating paid resources, deploying (host or "npx wrangler deploy"), deleting
anything, editing system files on a server, or opening firewall ports. Never pick a paid plan
without asking.

THE STEPS (details for each are in README.md, Deployment guide Parts 1-5)
Part 0: Preflight. Check git, Python 3.10+ and Node.js 20+ (node -v, npx -v); help me install
  anything missing. Make sure the code is in my GitHub account if the host deploys from GitHub.
Part 1: Database (Supabase).
  - Guide me to create a project: save the DB password, pick a region near the app host.
  - Have me copy the SESSION POOLER connection string (port 5432; the direct connection is
    IPv6-only and fails on most hosts). Replace [YOUR-PASSWORD], URL-encode special
    characters in the password, and append ?sslmode=require. That's CONTROL_DB_URL.
  - Run the schema SQL from README Part 1.4 in the Supabase SQL Editor. Then ALWAYS run the
    Part 1.5 RLS + REVOKE block; without it, Supabase's public Data API can read the tables.
    Confirm with the Part 1.6 check query (four tables, rls_on = true).
Part 2: Secrets. Generate SECRET_KEY and RUN_TOKEN as above.
Part 3: Deploy the app on the chosen host using README Part 3 (Render Blueprint via render.yaml,
  Linux with systemd + nginx + certbot, Docker, or another platform). Set the four required
  variables in the host's own settings or secret store, not in code. If the schedule isn't
  daily, also set RUN_INTERVAL_MINUTES to match it, and keep MIN_RUN_GAP_MINUTES below the
  interval. Confirm https://<app URL>/health returns "ok". Note the final HTTPS app URL.
Part 4: Cloudflare Worker (README Part 4A).
  cd cloudflare; set APP_URL in wrangler.toml to the app URL (no /cron/run at the end); set
  crons to my schedule (UTC); npx wrangler login; npx wrangler whoami;
  npx wrangler deploy (output must show "schedule: <cron>");
  npx wrangler secret put RUN_TOKEN; npx wrangler deployments list; npx wrangler secret list.
  If I don't want Cloudflare, use README Part 4C instead (system cron, GitHub Actions or
  cron-job.org).
Part 5: Verify. Go through every item of README Part 5:
  - /health returns ok
  - login works
  - curl with the Bearer token to /cron/run returns {"status": "done"...} or "skipped"
  - the same request without the token returns 401
  - a test run of the Worker: "npx wrangler dev --test-scheduled" plus the __scheduled URL, or
    the first real cron run, shows "attempt 1: HTTP 200" in the Worker logs
  Show me which checks passed and which didn't.
Part 6: Add my services. Walk me through the dashboard: Add service, pick the type, fill the
  fields, Test connection (green panel with the service's reply), Save. Then Run all now. I
  type the secret fields in myself. Use README "Service types and their fields" for what each
  field means and the provider-specific tips (e.g. MySQL TLS "require" for Aiven).

IF SOMETHING FAILS
Check README "Troubleshooting" first. Show me the exact error with any secrets masked, explain
the likely cause in one or two sentences, fix it, and re-run the failed check. Don't skip a
failed check or call the setup done while anything is failing.

WHEN FINISHED
Give me a short summary:
- the app URL
- where each secret lives (names only, never values)
- the schedule, in UTC and in my local time
- the Worker name
- how to see logs (host logs, "npx wrangler tail", the dashboard's "Last executions")
- what to do for updates (README "Updating and maintenance")
Remind me to keep SECRET_KEY and RUN_TOKEN in a password manager.
````

The setup has steps only you can do: creating accounts, signing in through the browser, and
choosing passwords. The assistant pauses at each one and tells you exactly what to do.

---

## Deployment guide

This guide takes you from nothing to a working setup, and it assumes you've never used
Supabase, Cloudflare Workers or a Python host before. Follow the parts **in order**. Each part
produces something the next one needs:

```
Part 1  Supabase database     ──►  CONTROL_DB_URL
Part 2  Secrets               ──►  SECRET_KEY, ADMIN_PASSWORD, RUN_TOKEN
Part 3  Flask app (any host)  ──►  your app URL, e.g. https://freetier-keepalive.example.com
Part 4  Cloudflare Worker     ──►  calls <app URL>/cron/run on your schedule
Part 5  Verify end to end
```

Plan for about 30–45 minutes the first time.

### Before you start

**Accounts** (all have free plans):

| Account | Needed for | Sign up |
|---|---|---|
| GitHub | Hosting your copy of the code (most app hosts deploy from GitHub) | https://github.com/signup |
| Supabase | The app's database (Part 1) | https://supabase.com/dashboard |
| Cloudflare | The scheduled trigger (Part 4). **No domain needed.** | https://dash.cloudflare.com/sign-up |
| An app host | Running the Flask app (Part 3): Render, Railway, a VPS, ... | depends on your choice |

**Tools on your computer:**

| Tool | Check it's installed | Needed for |
|---|---|---|
| Git | `git --version` | Pushing the code to GitHub |
| Python 3.10+ | `python --version` | Generating secrets, running locally |
| Node.js 20+ (LTS) | `node -v` and `npx -v` | The Cloudflare `wrangler` CLI (Part 4). Get it from https://nodejs.org |

**Get the code into your GitHub account.** Fork this repository on GitHub, or push your local
copy to a new repository:

```bash
git remote add origin https://github.com/<you>/freetier-keepalive.git
git push -u origin main
```

---

### Part 1: Supabase database (the app's storage)

The app keeps its service list, encrypted secrets and recent results in a PostgreSQL database.
Supabase's free plan is used here, but any PostgreSQL 13+ database works (Neon, Aiven, RDS,
your own server). With another database, skip to [1.4](#14-create-the-tables) and run the SQL
there.

#### 1.1 Create the project

1. Open https://supabase.com/dashboard and click **New project**.
2. Choose an **organization** (one is created for you on sign-up).
3. Fill in:
   - **Project name:** e.g. `freetier-keepalive`
   - **Database password:** click **Generate a password** and **save it somewhere safe**. You
     need it in the next step, and Supabase won't show it again. (You can reset it later under
     **Project Settings > Database**.)
   - **Region:** pick the one closest to where you'll host the app (Part 3).
4. Click **Create new project** and wait 1–2 minutes until the project is ready.

#### 1.2 Copy the connection string

1. In your project, click **Connect** at the top of the page.
2. Under **Connection String**, choose the type **URI**. There are three options:

   | Option | Port | Use it when |
   |---|---|---|
   | **Session pooler** | 5432 | **Recommended.** Works from every host (IPv4). |
   | Direct connection | 5432 | Only if your host supports IPv6. Supabase's direct connection is IPv6-only, and many hosts (Render, most PaaS) don't have IPv6. |
   | Transaction pooler | 6543 | Also works (the app is pooler-safe), but there's no benefit here. |

3. Copy the **Session pooler** string. It looks like this:
   ```
   postgresql://postgres.abcdefghijklmnop:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
   ```

#### 1.3 Build `CONTROL_DB_URL`

1. Replace `[YOUR-PASSWORD]` (including the brackets) with the database password from 1.1.
2. If the password contains special characters, **URL-encode** them:
   `@` → `%40`, `:` → `%3A`, `/` → `%2F`, `#` → `%23`, `?` → `%3F`, `%` → `%25`.
   Python can do it for you: `python -c "import urllib.parse; print(urllib.parse.quote('p@ss:word', safe=''))"`.
3. Add `?sslmode=require` to the end so the connection is always encrypted.

The result is your **`CONTROL_DB_URL`**:

```
postgresql://postgres.abcdefghijklmnop:My%40Password@aws-0-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=require
```

#### 1.4 Create the tables

The app creates its tables automatically on its first request, so this step is optional. Running
it yourself is still recommended: you can see exactly what's created, and you can lock the
tables down (1.5) before the app goes live.

In Supabase, open **SQL Editor** (left sidebar) → **New query**, paste the following, and click
**Run**. Running it more than once is safe.

```sql
-- freetier-keepalive schema (same as app/db.py)

-- Services you add from the dashboard
CREATE TABLE IF NOT EXISTS services (
    id          serial PRIMARY KEY,
    name        text UNIQUE NOT NULL,
    type        text NOT NULL,                      -- postgres, mysql, redis, http, ...
    config      jsonb NOT NULL DEFAULT '{}'::jsonb, -- non-secret settings (query, URL, ...)
    secret      bytea,                              -- Fernet-encrypted secrets (passwords, URIs)
    enabled     boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- One row per check; the app keeps only the newest HISTORY_PER_SERVICE per service
CREATE TABLE IF NOT EXISTS results (
    id          bigserial PRIMARY KEY,
    service_id  int NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    ok          boolean NOT NULL,
    latency_ms  int,
    error       text,
    checked_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS results_service_idx ON results (service_id, id DESC);

-- One row per execution (cron or manual); the app keeps only the newest few
CREATE TABLE IF NOT EXISTS runs (
    id           bigserial PRIMARY KEY,
    trigger      text NOT NULL,              -- cron | manual
    status       text NOT NULL,              -- done | skipped
    reason       text,
    checked      int NOT NULL DEFAULT 0,
    failed       int NOT NULL DEFAULT 0,
    duration_ms  int,
    output       jsonb NOT NULL,             -- the JSON summary the run returned
    started_at   timestamptz NOT NULL DEFAULT now()
);

-- Key/value state: last_run_at and run_lock
CREATE TABLE IF NOT EXISTS meta (
    key    text PRIMARY KEY,
    value  text
);
```

#### 1.5 Lock the tables down (important on Supabase)

Supabase publishes every table in the `public` schema through its REST API (the Data API).
Without row-level security, anyone who has your project's public `anon` key could read or change
these tables. The app doesn't use that API, so switch it off for these tables. Run this in the
SQL Editor:

```sql
-- Turn on row-level security with no policies: the Data API (anon/authenticated) sees nothing.
-- The app connects as the table owner (postgres), which RLS doesn't restrict.
ALTER TABLE services ENABLE ROW LEVEL SECURITY;
ALTER TABLE results  ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE meta     ENABLE ROW LEVEL SECURITY;

-- Belt and braces: remove the API roles' privileges entirely.
REVOKE ALL ON services, results, runs, meta FROM anon, authenticated;
REVOKE ALL ON SEQUENCE services_id_seq, results_id_seq, runs_id_seq FROM anon, authenticated;
```

If the app created the tables before you got here, run this block anyway.

#### 1.6 Check it

```sql
SELECT table_name,
       (SELECT relrowsecurity FROM pg_class WHERE oid = ('public.' || table_name)::regclass) AS rls_on
FROM information_schema.tables
WHERE table_schema = 'public' AND table_name IN ('services', 'results', 'runs', 'meta')
ORDER BY table_name;
```

You should see four rows, each with `rls_on = true`. The **Table Editor** shows the same four
tables, each marked RLS enabled.

#### 1.7 (Optional) Use a dedicated database user

By default the app connects as `postgres`, which can do anything in the project. To limit it to
its own schema, run the following **instead of 1.4 and 1.5**. The app then creates its tables
inside that schema on first start, and the Data API never sees them, because Supabase only
exposes `public`.

```sql
CREATE ROLE freetier_keepalive WITH LOGIN PASSWORD 'choose-a-long-random-password';
CREATE SCHEMA freetier_keepalive;
GRANT USAGE, CREATE ON SCHEMA freetier_keepalive TO freetier_keepalive;
ALTER ROLE freetier_keepalive SET search_path = freetier_keepalive;
```

Then use this user in `CONTROL_DB_URL`. On the pooler the user name is `freetier_keepalive.<project-ref>`:

```
postgresql://freetier_keepalive.abcdefghijklmnop:<password>@aws-0-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=require
```

After the app's first start, check that the tables were created:
`SELECT table_name FROM information_schema.tables WHERE table_schema = 'freetier_keepalive';`

#### 1.8 Handy queries for later

Run these in the SQL Editor whenever you need them. Don't add services with SQL: their secrets
must be encrypted with your `SECRET_KEY`, so always add them through the dashboard.

```sql
-- Every service with its latest result
SELECT s.id, s.name, s.type, s.enabled, r.ok, r.latency_ms, r.error, r.checked_at
FROM services s
LEFT JOIN LATERAL (
    SELECT * FROM results WHERE service_id = s.id ORDER BY id DESC LIMIT 1
) r ON true
ORDER BY s.name;

-- Recent executions
SELECT id, started_at, trigger, status, reason, checked, failed, duration_ms
FROM runs ORDER BY id DESC;

-- When did the last run happen? Is a run holding the lock?
SELECT * FROM meta;

-- Release a stuck run lock (it also expires on its own after 3 minutes)
UPDATE meta SET value = '1970-01-01 00:00:00+00' WHERE key = 'run_lock';

-- Let the next /cron/run go ahead even if the last run was recent (resets the run gap)
DELETE FROM meta WHERE key = 'last_run_at';

-- Pause or resume a service
UPDATE services SET enabled = false WHERE name = 'my-service';
UPDATE services SET enabled = true  WHERE name = 'my-service';

-- Clear all history (services are kept)
TRUNCATE results, runs;
```

> Supabase pauses free projects after about a week without activity. This project stays active
> because the app writes to it on every run. If it does get paused (for example, because the
> scheduler was broken for a week), restore it from the Supabase dashboard. The app works again
> once it's back.

---

### Part 2: Generate the secrets

You need three random values. Run this once and keep the output somewhere safe, such as a
password manager:

```bash
python -c "import secrets; [print(n + '=' + secrets.token_urlsafe(32)) for n in ('SECRET_KEY', 'RUN_TOKEN')]"
```

| Variable | What it's for | Where it goes |
|---|---|---|
| `SECRET_KEY` | Encrypts the connection strings you store and signs login sessions. **Never change it after you've added services**, or the stored secrets become unreadable. | App host only |
| `RUN_TOKEN` | Password that `/cron/run` requires. | App host **and** Cloudflare Worker (same value) |
| `ADMIN_PASSWORD` | Your dashboard login. Choose a strong one. | App host only |

---

### Part 3: Deploy the Flask app (any server)

The app is a standard Python WSGI app, so it runs anywhere Python 3.10+ runs. Whatever host you
choose, it needs the same few things:

| Requirement | Value |
|---|---|
| Install command | `pip install -r requirements.txt` |
| Start command | `gunicorn wsgi:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT` |
| Health check (optional) | `GET /health` returns `ok` (doesn't touch the database) |
| Required environment variables | `CONTROL_DB_URL`, `SECRET_KEY`, `ADMIN_PASSWORD`, `RUN_TOKEN` (the app refuses to start without them) |
| Optional environment variables | See [Configuration reference](#configuration-reference) |
| HTTPS | Required. Login cookies are `Secure` unless `COOKIE_SECURE=false`. |
| Outbound network | Must reach your Supabase project and every service you want to keep alive |

Keep `--workers 1` (one process with 4 threads). That's plenty, and free hosts have little
memory. Keep `--timeout 120` above `RUN_BUDGET_SECONDS` (default 90).

Pick **one** of the options below.

#### Option A: Render (free, easiest)

Render's free plan sleeps after 15 minutes without traffic and wakes on the next request (that
takes up to a minute). The Cloudflare Worker is built to wait for that.

1. Sign in at https://dashboard.render.com with your GitHub account, and allow Render to access
   your `freetier-keepalive` repository.
2. Click **New +** → **Blueprint**, select the repository, and click **Connect**.
   Render reads [render.yaml](render.yaml), which defines a free Python web service with the
   commands above, the `/health` check, and an automatically generated `SECRET_KEY`.
3. Render asks for the variables marked `sync: false`. Fill in:
   - `CONTROL_DB_URL`: from Part 1
   - `ADMIN_PASSWORD` and `RUN_TOKEN`: from Part 2
4. Click **Apply**. The first build takes a few minutes. Follow it under **Logs**.
5. When the status is **Live**, copy the URL at the top of the service page, e.g.
   `https://freetier-keepalive-xxxx.onrender.com`. This is your **app URL**.

To set up the service by hand instead: **New +** → **Web Service** → pick the repository →
Runtime **Python 3**, with the install and start commands from the table above, instance type
**Free**, and all four environment variables (generate `SECRET_KEY` yourself in Part 2).

#### Option B: Your own Linux server (VPS, home server)

These steps are for Ubuntu/Debian. You need a domain (or subdomain) pointing at the server's
IP for HTTPS, e.g. `freetier-keepalive.example.com`.

**1. Install packages and create a user for the app:**

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git nginx
sudo useradd --system --create-home --home-dir /opt/freetier-keepalive --shell /usr/sbin/nologin freetier-keepalive
```

**2. Get the code and install the dependencies:**

```bash
sudo -u freetier-keepalive git clone https://github.com/<you>/freetier-keepalive.git /opt/freetier-keepalive/app
sudo -u freetier-keepalive python3 -m venv /opt/freetier-keepalive/venv
sudo -u freetier-keepalive /opt/freetier-keepalive/venv/bin/pip install -r /opt/freetier-keepalive/app/requirements.txt
```

**3. Put the configuration in a file only root can read:**

```bash
sudo tee /etc/freetier-keepalive.env > /dev/null <<'EOF'
CONTROL_DB_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
SECRET_KEY=<from Part 2>
ADMIN_PASSWORD=<from Part 2>
RUN_TOKEN=<from Part 2>
EOF
sudo chmod 600 /etc/freetier-keepalive.env
```

Write the values without quotes.

**4. Run the app as a systemd service.** Create `/etc/systemd/system/freetier-keepalive.service`:

```ini
[Unit]
Description=freetier-keepalive
After=network-online.target
Wants=network-online.target

[Service]
User=freetier-keepalive
WorkingDirectory=/opt/freetier-keepalive/app
EnvironmentFile=/etc/freetier-keepalive.env
ExecStart=/opt/freetier-keepalive/venv/bin/gunicorn wsgi:app --workers 1 --threads 4 --timeout 120 --bind 127.0.0.1:8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now freetier-keepalive
sudo systemctl status freetier-keepalive          # should say "active (running)"
curl http://127.0.0.1:8000/health        # should print: ok
```

Logs: `sudo journalctl -u freetier-keepalive -f`

**5. Put nginx in front of it.** Create `/etc/nginx/sites-available/freetier-keepalive`:

```nginx
server {
    listen 80;
    server_name freetier-keepalive.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 180s;   # a full run can take up to ~90 s
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/freetier-keepalive /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**6. Turn on HTTPS** with a free Let's Encrypt certificate:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d freetier-keepalive.example.com
```

**7. Open the firewall** (if you use `ufw`): `sudo ufw allow 'Nginx Full'`

Your **app URL** is `https://freetier-keepalive.example.com`. To update later:

```bash
sudo -u freetier-keepalive git -C /opt/freetier-keepalive/app pull
sudo -u freetier-keepalive /opt/freetier-keepalive/venv/bin/pip install -r /opt/freetier-keepalive/app/requirements.txt
sudo systemctl restart freetier-keepalive
```

> No domain, or the server is behind a home router? A Cloudflare Tunnel (`cloudflared`) can give
> the app a public HTTPS URL without opening any ports.

#### Option C: Docker (any server or container platform)

The repo doesn't include a Dockerfile, so create one in the project root:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
CMD gunicorn wsgi:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT
```

Also create a `.dockerignore` so local files stay out of the image:

```
venv/
.venv/
.env
.git/
cloudflare/
__pycache__/
```

Build and run it. `.env` is your filled-in copy of [.env.example](.env.example), with no quotes
around the values:

```bash
docker build -t freetier-keepalive .
docker run -d --name freetier-keepalive --restart unless-stopped --env-file .env -p 8000:8000 freetier-keepalive
curl http://localhost:8000/health        # ok
```

Put it behind HTTPS (nginx/Caddy as in Option B, or your platform's load balancer). The same
image works on Google Cloud Run, Azure Container Apps, AWS App Runner, Fly.io and Koyeb. Those
platforms set `$PORT` and HTTPS for you.

#### Option D: Other Python platforms

All of these need the same install command, start command and environment variables:

| Platform | How |
|---|---|
| Railway | **New Project → Deploy from GitHub repo**, set the start command and variables under **Settings / Variables**, then **Generate Domain**. |
| Fly.io | `fly launch` (detects Python, or use the Dockerfile from Option C), then `fly secrets set CONTROL_DB_URL=... SECRET_KEY=... ADMIN_PASSWORD=... RUN_TOKEN=...`, then `fly deploy`. |
| Koyeb | **Create Web Service → GitHub**, builder **Buildpack**, run command = the start command, add the variables, health check path `/health`. |
| Azure App Service | Python 3.12 Linux web app. **Startup command** = the start command with `--bind 0.0.0.0:8000`. Add the variables under **Environment variables**. |
| Windows server | gunicorn doesn't run on Windows. `pip install waitress`, then `waitress-serve --listen=127.0.0.1:8000 --threads=4 wsgi:app`, behind IIS or another reverse proxy for HTTPS. |

#### After deploying: first login (all options)

1. Open `https://<app URL>/health`. It should show `ok`.
2. Open `https://<app URL>/`, log in with `ADMIN_PASSWORD`, and you'll see the empty dashboard.
   This first request also creates the tables if you skipped Part 1.4.
3. Click **Add service**, add one of your databases, click **Test connection** (you should see a
   green **ok** panel with the service's reply), then **Save**.
4. Click **Run all now** and check that the service turns `ok`.

The app works now. It just doesn't run on a schedule yet. That's Part 4.

---

### Part 4: Cloudflare Worker (the scheduled trigger)

A Cloudflare Worker is a small script that runs on Cloudflare's network. The one in
[cloudflare/](cloudflare/) has a **Cron Trigger**. On your schedule it calls
`<app URL>/cron/run` with your `RUN_TOKEN`, waits up to 3 minutes (enough for a sleeping host to
wake up), and retries once after 30 seconds if that fails. It's free and doesn't need a domain.

There are two ways to set it up. **4A (CLI)** keeps everything in the repo. **4B (dashboard)**
needs nothing installed.

#### Quick deploy: all commands

If you just want the commands, run these from the project root. Replace the two placeholders
first. Each step is explained in 4A below.

**Windows (PowerShell):**

```powershell
cd cloudflare

# 1. Point the Worker at your app (or edit wrangler.toml by hand)
(Get-Content wrangler.toml) -replace '^APP_URL = .*', 'APP_URL = "https://<your app URL>"' |
  Set-Content wrangler.toml

# 2. Log in to Cloudflare (opens the browser) and check the account
npx wrangler login
npx wrangler whoami

# 3. Deploy the Worker and its cron schedule
npx wrangler deploy

# 4. Store RUN_TOKEN as an encrypted secret (same value as on the app host).
#    It asks for the value: paste it and press Enter.
npx wrangler secret put RUN_TOKEN

# 5. Check that it's deployed
npx wrangler deployments list
npx wrangler secret list
```

**macOS / Linux (bash):**

```bash
cd cloudflare

# 1. Point the Worker at your app (or edit wrangler.toml by hand)
sed -i.bak 's|^APP_URL = .*|APP_URL = "https://<your app URL>"|' wrangler.toml

# 2. Log in to Cloudflare (opens the browser) and check the account
npx wrangler login
npx wrangler whoami

# 3. Deploy the Worker and its cron schedule
npx wrangler deploy

# 4. Store RUN_TOKEN as an encrypted secret (same value as on the app host)
printf '%s' '<your RUN_TOKEN>' | npx wrangler secret put RUN_TOKEN

# 5. Check that it's deployed
npx wrangler deployments list
npx wrangler secret list
```

Step 3 should print `schedule: 0 0 * * *` (or your schedule). If it does, the cron is live.
In bash, step 4 can also be run without the pipe, in which case it asks for the value. That keeps
the token out of your shell history. In PowerShell, always use the prompt: piping a string adds a
line break that would become part of the token.

#### Wrangler command reference

Run these from the `cloudflare` folder.

| Task | Command |
|---|---|
| Deploy the Worker, or redeploy after changing `wrangler.toml` / `src/index.js` | `npx wrangler deploy` |
| Update only the cron schedule (after editing `crons` in `wrangler.toml`) | `npx wrangler triggers deploy` |
| Set or change the token | `npx wrangler secret put RUN_TOKEN` |
| List secret names (values are never shown) | `npx wrangler secret list` |
| Remove the token | `npx wrangler secret delete RUN_TOKEN` |
| Stream live logs (each run prints `attempt 1: HTTP 200 ...`) | `npx wrangler tail` |
| Run locally and fire the cron once | `npx wrangler dev --test-scheduled`, then `curl "http://localhost:8787/__scheduled?cron=0+0+*+*+*"` |
| Show recent deployments | `npx wrangler deployments list` |
| Roll back to the previous version | `npx wrangler rollback` |
| Check which account you're logged in to | `npx wrangler whoami` |
| Log out | `npx wrangler logout` |
| Delete the Worker and its cron completely | `npx wrangler delete` |

**Deploying from CI (no browser login):** create an API token in the Cloudflare dashboard under
**My Profile → API Tokens → Create Token** using the **Edit Cloudflare Workers** template. Then:

```bash
export CLOUDFLARE_API_TOKEN=<api token>
export CLOUDFLARE_ACCOUNT_ID=<account id from "wrangler whoami">
cd cloudflare
npx wrangler deploy
printf '%s' "$RUN_TOKEN" | npx wrangler secret put RUN_TOKEN
```

#### 4A. Deploy with the `wrangler` CLI (recommended)

**1. Open a terminal in the `cloudflare` folder:**

```bash
cd cloudflare
npx wrangler --version      # first run downloads wrangler; answer "y" if asked
```

**2. Edit [cloudflare/wrangler.toml](cloudflare/wrangler.toml):**

```toml
name = "freetier-keepalive-trigger"    # the Worker's name in your Cloudflare account
main = "src/index.js"
compatibility_date = "2026-10-01"

[triggers]
crons = ["0 0 * * *"]                  # when to run, in UTC (see "Choosing your schedule")

[vars]
APP_URL = "https://<your app URL>"     # from Part 3, no /cron/run at the end
```

`RUN_TOKEN` does **not** go in this file: it's a secret, and it's set in step 5.

**3. Log in to Cloudflare:**

```bash
npx wrangler login       # opens your browser; click "Allow"
npx wrangler whoami      # shows your account name and ID
```

If your login has several Cloudflare accounts, wrangler asks which one to use. You can also add
`account_id = "<id from whoami>"` to `wrangler.toml`.

**4. Deploy the Worker:**

```bash
npx wrangler deploy
```

The first time, Cloudflare may ask you to choose a `workers.dev` subdomain. Any name is fine.
The output ends with something like:

```
Uploaded freetier-keepalive-trigger
Deployed freetier-keepalive-trigger triggers
  https://freetier-keepalive-trigger.<your-subdomain>.workers.dev
  schedule: 0 0 * * *
```

The `workers.dev` URL isn't used. The Worker only reacts to its schedule, not to visits.

**5. Store the token as a secret.** Use exactly the same `RUN_TOKEN` as on your app host:

```bash
npx wrangler secret put RUN_TOKEN
# paste the token and press Enter
```

The secret is encrypted by Cloudflare and applies right away, with no redeploy needed. You can
check it with `npx wrangler secret list`; the value is never shown.

**6. (Optional) Test it before the first scheduled run.** For local runs wrangler reads secrets
from a `.dev.vars` file. Create `cloudflare/.dev.vars`:

```
RUN_TOKEN=<your token>
```

> `.dev.vars` holds a secret. Add `.dev.vars` to [.gitignore](.gitignore) so it's never
> committed.

Then start the Worker locally and fire its cron handler once:

```bash
npx wrangler dev --test-scheduled
# in a second terminal:
curl "http://localhost:8787/__scheduled?cron=0+0+*+*+*"
```

The first terminal prints `attempt 1: HTTP 200 {"status": "done", ...}`, and the run appears on
your dashboard with trigger `cron`.

**7. Watch it work.** In the Cloudflare dashboard go to **Workers & Pages → freetier-keepalive-trigger**:

- **Settings → Triggers** (or **Triggers**) lists your Cron Trigger and its schedule.
- **Logs** shows each invocation with lines such as `attempt 1: HTTP 200 {...}`. Logging is
  already turned on in `wrangler.toml` under `[observability.logs]`.
- For a live stream from your terminal: `npx wrangler tail`.

To change the schedule or URL later, edit `wrangler.toml` and run `npx wrangler deploy` again.

#### 4B. Set it up in the Cloudflare dashboard (no CLI)

1. Go to https://dash.cloudflare.com → **Workers & Pages** → **Create** → **Create Worker**.
   Name it `freetier-keepalive-trigger` and click **Deploy** (this deploys a "Hello World" placeholder).
2. Click **Edit code**, replace everything with the contents of
   [cloudflare/src/index.js](cloudflare/src/index.js), and click **Deploy**.
3. Go to the Worker's **Settings → Variables and Secrets** → **Add**:
   - Type **Text**, name `APP_URL`, value = your app URL.
   - Type **Secret**, name `RUN_TOKEN`, value = your token.

   Click **Deploy** (or **Save**).
4. Go to **Settings → Triggers** → **Add** → **Cron Trigger**, enter `0 0 * * *` (or your own
   schedule), and click **Add Trigger**.
5. To test it, use the scheduled-event test in the Worker's code editor preview, if your
   dashboard offers one. Otherwise, temporarily set the trigger to `* * * * *` (every minute),
   check **Logs** after a minute, then set your real schedule again.

If you later deploy with wrangler (4A), `wrangler.toml` replaces the dashboard's variables and
triggers, so pick one method and stick to it.

#### 4C. Not using Cloudflare? Any scheduler works

The scheduler only has to send this request:

```
GET https://<app URL>/cron/run
Authorization: Bearer <RUN_TOKEN>
```

**System cron** on an always-on Linux machine (`crontab -e`):

```
0 0 * * * curl -fsS --max-time 180 -H "Authorization: Bearer <RUN_TOKEN>" https://<app URL>/cron/run > /dev/null
```

**GitHub Actions:** add `APP_URL` and `RUN_TOKEN` under **Settings → Secrets and variables →
Actions**, then create `.github/workflows/keepalive.yml`:

```yaml
name: keepalive
on:
  schedule:
    - cron: "0 0 * * *"      # UTC
  workflow_dispatch:          # adds a "Run workflow" button for testing
jobs:
  trigger:
    runs-on: ubuntu-latest
    steps:
      - run: >
          curl -fsS --max-time 180 --retry 1 --retry-delay 30
          -H "Authorization: Bearer ${{ secrets.RUN_TOKEN }}"
          "${{ secrets.APP_URL }}/cron/run"
```

GitHub may start scheduled workflows a few minutes late, and it turns them off in repositories
with no activity for 60 days.

**cron-job.org and similar services:** create a job for `https://<app URL>/cron/run`, add the
header `Authorization: Bearer <RUN_TOKEN>`, and set the timeout as high as the service allows.

Retries and duplicate calls are always safe. A call within `MIN_RUN_GAP_MINUTES` of the last
run is answered with `skipped` and does nothing.

---

### Choosing your schedule

The schedule is a standard cron expression. With the Cloudflare Worker it lives in
`wrangler.toml` (in UTC), and the default is once a day:

```toml
[triggers]
crons = ["0 0 * * *"]   # default: daily at 00:00 UTC
```

Set it to whatever suits your services (with the Worker, run `npx wrangler deploy` again). The
same expressions work in system cron and most other schedulers:

| You want | Cron expression | `RUN_INTERVAL_MINUTES` |
|---|---|---|
| Every hour | `0 * * * *` | `60` |
| Every 6 hours | `0 */6 * * *` | `360` |
| Every 12 hours | `0 0,12 * * *` | `720` |
| Daily at 09:30 | `30 9 * * *` | `1440` |
| Every 3 days | `0 0 */3 * *` | `4320` |
| Weekly, Monday 00:00 | `0 0 * * 1` | `10080` |

When you change the schedule, update two of the app's environment variables to match:

- **`RUN_INTERVAL_MINUTES`**: the time between runs. It's used only for the dashboard's
  **Next due** time.
- **`MIN_RUN_GAP_MINUTES`** (default 30): keep it **shorter than the time between runs**, or
  scheduled runs will be skipped. For schedules more often than every 30 minutes, lower it (e.g.
  `5` for a 10-minute schedule).

To pick a frequency, check how long your providers allow inactivity before pausing (Supabase
pauses after about a week, for example) and run comfortably more often than that.

---

### Part 5: Verify end to end

Work through this checklist once after deploying:

- [ ] `https://<app URL>/health` returns `ok`.
- [ ] You can log in, and **Test connection** shows a green panel for each service.
- [ ] **Run all now** turns every service `ok`, and the run appears under **Last executions**
      with trigger `manual`.
- [ ] Calling the endpoint the way the scheduler does returns JSON:
      ```bash
      curl -H "Authorization: Bearer <RUN_TOKEN>" https://<app URL>/cron/run
      ```
      You'll get `{"status": "done", ...}`, or `{"status": "skipped", "reason": "last run was N min ago"}`
      if you just clicked **Run all now**. Both mean it works. A `401` means the token is wrong.
- [ ] Without the token, `curl -i https://<app URL>/cron/run` returns `401`.
- [ ] After the first scheduled time: the dashboard shows a run with trigger `cron`, and the
      Worker's **Logs** show `attempt 1: HTTP 200`.
- [ ] In Supabase, `SELECT * FROM runs ORDER BY id DESC LIMIT 1;` shows the same run.

### Updating and maintenance

| Task | How |
|---|---|
| Deploy new code | Push to GitHub (Render and most PaaS redeploy automatically), or `git pull` and restart (Option B), or rebuild the image (Option C). |
| Change the schedule | Edit `crons` in `wrangler.toml` → `npx wrangler deploy`. Update `RUN_INTERVAL_MINUTES` on the app. |
| Change the app URL | Edit `APP_URL` in `wrangler.toml` → `npx wrangler deploy`. |
| Rotate `RUN_TOKEN` | Set the new value on the app host **and** run `npx wrangler secret put RUN_TOKEN`. Until both match, scheduled calls get `401`. |
| Change `ADMIN_PASSWORD` | Update it on the app host and restart. |
| Rotate `SECRET_KEY` | Avoid it. If you must, every stored secret becomes unreadable, and you have to edit each service and re-enter its secrets. |
| Back up | Supabase **Database → Backups**, or `pg_dump "$CONTROL_DB_URL" > freetier-keepalive.sql`. Backups contain encrypted secrets, which are useless without `SECRET_KEY`. |

---

## Using the dashboard

Log in at `/` with `ADMIN_PASSWORD`.

### Adding a service

1. Click **Add service** and choose a type (see [Service types](#service-types-and-their-fields)).
2. Enter a unique **Name** and fill in the fields. Required fields are marked.
3. Click **Test connection** to try the connection once without saving (no retries). A
   **Test result** panel appears below the buttons, and the page scrolls to it:
   - **ok:** a green badge, the connection time (e.g. *Connected in 68 ms*), and **what the
     service replied**: query rows, `PING -> PONG`, cluster health, HTTP status and body, and so
     on (see [What a check does](#what-a-check-actually-does)).
   - **failed:** a red badge and the error message.

   Secrets in the reply and in errors are masked as `***`.
4. Click **Save**. The service is included from the next run.

> Secret fields are never sent back to the browser, so after a test they're empty again. On a
> new service, re-enter them before clicking **Save**. When editing, blank secret fields keep
> their stored values, for both **Test connection** and **Save**.

### Editing a service

Secret fields (connection strings, HTTP headers) are **never shown again** after saving. When
editing, leave a secret field blank to keep the stored value, or type a new value to replace it.

### What you see

- **Alert banner:** shown when any service's latest check failed, with the failing names.
- **Summary bar:** last run time, next due time (`last run + RUN_INTERVAL_MINUTES`), and counts
  of ok / failing / pending / disabled services.
- **Services table**, one row per service:
  - **Status:** `ok`, `fail`, `pending` (never checked yet) or `disabled`.
  - **Last 5 checks:** a dot per check, green for ok and red for failed. Hover a dot to see its
    time, latency and error.
  - **Last result:** time and latency, or the error message.
  - **Actions:**
    - **Run** checks just this service now.
    - **Edit** changes its settings.
    - **Disable/Enable** pauses or resumes it in scheduled runs; its history is kept.
    - **Delete** removes it together with its history.
- **Run all now:** runs every enabled service immediately (trigger `manual`). It ignores the
  run gap but still respects the run lock.
- **Last 5 executions:** the run log with start time, trigger, status (`done`/`skipped` and the
  reason), checked/failed counts, duration, and the full JSON output under **View JSON**.

> A manual **Run all now** also updates `last_run_at`. If you click it less than
> `MIN_RUN_GAP_MINUTES` before the cron fires, that cron call is skipped. This is harmless,
> since your services were just checked.

---

## Service types and their fields

| Category | Type | Works with | Driver |
|---|---|---|---|
| Relational | `postgres` | PostgreSQL and compatible: Supabase, Aiven, Neon, CockroachDB, ... | `psycopg` |
| Relational | `mysql` | MySQL, MariaDB, TiDB Cloud, Aiven MySQL, ... | `PyMySQL` |
| Relational | `mssql` | Microsoft SQL Server, Azure SQL | `pymssql` |
| Relational | `oracle` | Oracle Database, Oracle Autonomous Database | `oracledb` (thin mode, no Oracle Client needed) |
| Cache | `redis` | Redis, Valkey, KeyDB, Dragonfly: Upstash, Redis Cloud, Aiven, ... | `redis` |
| NoSQL (document) | `mongodb` | MongoDB, MongoDB Atlas | `pymongo` |
| NoSQL (graph) | `neo4j` | Neo4j, Neo4j AuraDB | `neo4j` |
| NoSQL (search) | `elasticsearch` | Elasticsearch, OpenSearch: Elastic Cloud, Aiven, Bonsai, ... | `requests` |
| Anything | `http` | Any service with an HTTP endpoint: CouchDB/Cloudant, REST and Data APIs, web apps, ... | `requests` |

Each driver is imported only when a check of that type runs, so a missing driver affects only
that type (its checks fail with `ModuleNotFoundError`) and never stops the app.

**PostgreSQL**

| Field | Secret | Notes |
|---|---|---|
| Connection string | yes | e.g. `postgresql://user:pass@host:5432/db?sslmode=require` |
| Query | no | Default `SELECT 1`. Use something cheap and read-only. |

**MySQL / MariaDB**

| Field | Secret | Notes |
|---|---|---|
| Connection URL | yes | e.g. `mysql://user:pass@host:3306/dbname` (URL-encode special characters in the password) |
| TLS | no | `verify` (default): encrypted, certificate checked. `require`: encrypted, certificate not checked (for providers with their own CA, such as Aiven). `disable`: no TLS. |
| Query | no | Default `SELECT 1` |

**Microsoft SQL Server**

| Field | Secret | Notes |
|---|---|---|
| Server host | no | e.g. `myserver.database.windows.net` |
| Port | no | Default `1433` |
| Database, User | no | Required |
| Password | yes | Required |
| Query | no | Default `SELECT 1` |

**Oracle Database**

| Field | Secret | Notes |
|---|---|---|
| Connect string | no | Easy Connect (`host:port/service`) or a full TLS connect string. For Autonomous Database, enable TLS (walletless) access and copy a TLS connection string. |
| User | no | Required |
| Password | yes | Required |
| Query | no | Default `SELECT 1 FROM DUAL` |

**Redis / Valkey**

| Field | Secret | Notes |
|---|---|---|
| Connection URL | yes | e.g. `rediss://default:password@host:6379/0`. Use `rediss://` for TLS. |
| Key to read | no | Optional. If set, also runs `GET` on that key (a missing key is fine). |

The check sends `PING` and expects `PONG`.

**MongoDB**

| Field | Secret | Notes |
|---|---|---|
| Connection URI | yes | e.g. `mongodb+srv://user:pass@cluster.mongodb.net/` |
| Database | no | Optional. If set, also reads one document from its `keepalive` collection (an empty or missing collection is fine). |

**Neo4j**

| Field | Secret | Notes |
|---|---|---|
| Connection URI | no | e.g. `neo4j+s://xxxxxxxx.databases.neo4j.io` |
| User | no | Default `neo4j` |
| Password | yes | Required |
| Database | no | Optional. Blank means the default database. |
| Cypher query | no | Default `RETURN 1`. If your provider only counts writes as activity, use a small write, e.g. `MERGE (k:KeepAlive {id: 1}) SET k.at = timestamp()`. |

**Elasticsearch / OpenSearch**

| Field | Secret | Notes |
|---|---|---|
| Cluster URL | no | e.g. `https://my-cluster.example.com:9200` |
| Username / Password | password only | Optional, for basic auth |
| API key | yes | Optional, for Elasticsearch API keys (`Authorization: ApiKey ...`). Use either this or username/password. |

The check calls `GET /_cluster/health` and fails if it doesn't return HTTP 200 or if the
cluster status is `red`. For serverless offerings without that endpoint, use the **HTTP** type.

**HTTP URL** (web apps, REST APIs, CouchDB/Cloudant, Supabase REST, ...)

| Field | Secret | Notes |
|---|---|---|
| URL | no | e.g. `https://example.com/health` |
| Method | no | `GET`, `HEAD` or `POST` |
| Expected status | no | Default `200`. Any other status is a failure. |
| Headers | yes | One `Key: Value` per line, e.g. `apikey: ...` |

Tip: a plain `SELECT 1` usually counts as activity for Supabase. If you want a call to go through
Supabase's API, add an **HTTP** service pointing at
`https://<ref>.supabase.co/rest/v1/<table>?limit=1` with an `apikey: <anon key>` header.

---

## Configuration reference

All configuration comes from environment variables ([app/config.py](app/config.py)).

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `CONTROL_DB_URL` | yes | – | Postgres URL for the app's own storage |
| `SECRET_KEY` | yes | – | Encrypts stored secrets and signs login sessions |
| `ADMIN_PASSWORD` | yes | – | Dashboard login password |
| `RUN_TOKEN` | yes | – | Bearer token that `/cron/run` requires |
| `RUN_INTERVAL_MINUTES` | no | `1440` | Only used to show **Next due** on the dashboard. Match it to the cron. |
| `MIN_RUN_GAP_MINUTES` | no | `30` | `/cron/run` skips if the last run was more recent than this |
| `CHECK_TIMEOUT_SECONDS` | no | `15` | Timeout for each check attempt |
| `CHECK_RETRIES` | no | `2` | Extra attempts after a failed check |
| `RUN_BUDGET_SECONDS` | no | `90` | Maximum time for one whole run. Keep it under gunicorn's `--timeout 120`. |
| `HISTORY_PER_SERVICE` | no | `5` | Results kept per service, and runs kept in the run log |
| `COOKIE_SECURE` | no | `true` | Set to `false` only for local `http://` development |

### HTTP endpoints

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Health check for your host, returns `ok` (doesn't touch the database) |
| `GET /cron/run` | `RUN_TOKEN` (Bearer header or `?token=`) | Run all checks and return the JSON summary |
| `GET/POST /login`, `POST /logout` | – | Dashboard session |
| `GET /` | login | Dashboard |
| `POST /run` | login | Run all now |
| `/services/new`, `/services/<id>/edit` | login | Add or edit a service |
| `POST /services/<id>/run` · `/toggle` · `/delete` | login | Per-service actions |

---

## Running locally

You need Python 3.10 or newer. Every driver in [requirements.txt](requirements.txt) ships
prebuilt wheels for Windows, macOS and Linux, so nothing needs compiling.

**1. Create the virtualenv and install the packages** (one time):

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

**2. Set the environment variables.** Copy [.env.example](.env.example) to `.env` and fill it
in. Flask doesn't read `.env` on its own (that needs `python-dotenv`, which isn't a
dependency), so load it into your shell first:

```powershell
# PowerShell
Get-Content .env | Where-Object { $_ -match '^\s*[A-Z_]+=' } | ForEach-Object {
  $k, $v = $_ -split '=', 2; Set-Item "env:$($k.Trim())" $v.Trim() }
$env:COOKIE_SECURE = "false"         # needed for login over http://
```

```bash
# macOS/Linux
set -a; . ./.env; set +a
export COOKIE_SECURE=false
```

You can also set them one by one, e.g. `$env:ADMIN_PASSWORD = "admin"`. `CONTROL_DB_URL` can
point to any reachable Postgres, including your Supabase project.

**3. Start the app:**

```powershell
flask --app wsgi run
```

The dev server doesn't reload code changes; stop it with `Ctrl+C` and start it again.

Open http://127.0.0.1:5000 and log in. Trigger a cron-style run with:

```
curl -H "Authorization: Bearer dev-token" http://127.0.0.1:5000/cron/run
```

---

## Adding a new service type

Checkers are plugins. Add one file to [app/checkers/](app/checkers/). It is discovered
automatically at startup and appears in the **Add service** list with a form generated from its
`fields`:

```python
# app/checkers/memcached.py
from .base import Checker, Field, register

@register
class MemcachedChecker(Checker):
    type = "memcached"                 # stored in the database; don't rename it later
    label = "Memcached"                # shown in the UI
    fields = [Field("host", "Host", required=True), Field("port", "Port", default="11211")]

    def check(self, cfg, timeout):
        from pymemcache.client.base import Client   # import the driver here, not at the top
        client = Client((cfg["host"], int(cfg["port"] or 11211)), timeout=timeout)
        try:
            return "version " + client.version().decode()   # raise on failure
        finally:
            client.close()
```

- `check(cfg, timeout)` gets every field value in `cfg`, with secrets already decrypted.
  Returning normally means success. Raising any exception means failure, and the exception text
  is shown, with secrets masked.
- Optionally return what the service replied (a string, or any value such as a dict or list). It
  is shown on **Test connection**. `format_rows(columns, rows)` and `to_text(value)` in
  [base.py](app/checkers/base.py) help format query results.
- `Field` options: `secret`, `required`, `default`, `placeholder`, `help`, and
  `kind="text" | "textarea" | "select"` (with `choices=[...]` for selects).
- Import the driver inside `check()`, so the app still starts when it isn't installed.
- Add any new library to [requirements.txt](requirements.txt).
- Don't name the file after the driver package (e.g. use `redis_ping.py`, not `redis.py`).

Built-in types: `postgres`, `mysql`, `mssql`, `oracle`, `redis`, `mongodb`, `neo4j`,
`elasticsearch`, `http`.

---

## Project layout

```
app/
  __init__.py        Flask app factory, CSRF protection, cookie settings
  config.py          Environment variables and defaults
  auth.py            Password login, RUN_TOKEN check, @login_required
  crypto.py          Fernet encryption of secrets (key derived from SECRET_KEY)
  db.py              Postgres connection (pooler-safe) and schema creation
  repo.py            All SQL: services, results, runs, run lock
  runner.py          Runs checks: retries, timeouts, parallelism, masking of errors and replies
  routes.py          Web routes: dashboard, CRUD + Test connection, /cron/run, /health
  checkers/          Plugin checkers, one file per service type:
                       base.py (plugin API), postgres.py, mysql.py, mssql.py, oracle.py,
                       redis_ping.py, mongodb.py, neo4j_ping.py, elasticsearch.py, http_ping.py
  templates/         Jinja templates: dashboard, login, service type picker,
                       service form (with the Test result panel)
  static/style.css
cloudflare/
  src/index.js       Worker: scheduled trigger with 3-min timeout and one retry
  wrangler.toml      Worker name, cron schedule, APP_URL, logging
render.yaml          Render blueprint (optional, only used when deploying to Render)
wsgi.py              gunicorn entry point
.env.example         All environment variables, with comments
```

---

## Security notes

- Secret fields are encrypted with Fernet using a key derived from `SECRET_KEY`. They are never
  shown in the UI again, and they are masked in error messages and test replies.
- Test replies (e.g. query rows) are shown only to the logged-in admin on **Test connection**.
  They are never stored or returned by `/cron/run`. Keep your test queries cheap and avoid
  selecting sensitive data.
- **Changing `SECRET_KEY` makes stored secrets unreadable.** Those services then fail with
  *"Cannot decrypt stored secrets (SECRET_KEY changed?)"*, and you have to re-enter their
  connection strings.
- All dashboard forms are CSRF-protected. Session cookies are HttpOnly and SameSite=Lax, and
  Secure unless `COOKIE_SECURE=false`.
- The password and token are compared in constant time.
- Prefer the `Authorization: Bearer` header over `?token=` for `/cron/run`, since query strings
  can end up in logs.
- Not included: login rate limiting, multiple users, or automated tests. Use a strong
  `ADMIN_PASSWORD`.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| App won't start: *Missing required environment variables* | Set all four required variables on your host. |
| First page load takes up to a minute | Your free host had put the app to sleep. This is normal. |
| Can't connect to Supabase from your host | Your host has no IPv6, and you used Supabase's direct (IPv6-only) connection string. Use the **Session pooler** one. |
| Login works locally but the session doesn't stick | Set `COOKIE_SECURE=false` when running over `http://`. |
| Scheduler gets `HTTP 401` | The scheduler's `RUN_TOKEN` doesn't match the app's. For the Worker, run `wrangler secret put RUN_TOKEN` again. |
| Run shows `skipped: last run was N min ago` | Expected after a retry or a recent manual run. |
| Run shows `skipped: another run is in progress` | Another run holds the lock. It frees itself within 3 minutes. |
| A service shows *Timed out (run time budget exceeded)* | The service is slow or unreachable. Lower its retries/timeout or raise `RUN_BUDGET_SECONDS` (keep it below 120). |
| *Cannot decrypt stored secrets* | `SECRET_KEY` changed. Edit the service and re-enter its secrets. |
| A check fails with `ModuleNotFoundError: No module named '...'` | That type's driver isn't installed. Run `pip install -r requirements.txt` (on your host, redeploy). |
| **Test connection** says a required field is missing right after a test | Secret fields are cleared after each test on a new service. Type them in again. |
| MySQL fails with a certificate error | The provider uses its own CA (e.g. Aiven). Set **TLS** to `require`. |
| Code changes don't show up locally | The Flask dev server doesn't reload. Restart it. |
| No run appeared after the cron time | Check your scheduler's logs (for the Worker: **Workers > freetier-keepalive-trigger > Logs** in Cloudflare) and that the app URL is correct. |

## License

See [LICENSE](LICENSE).
