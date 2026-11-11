// Cloudflare Worker that triggers the Flask app's daily run.
// The schedule is in wrangler.toml. APP_URL is a var; RUN_TOKEN is a Worker secret.
//
// Render's free instance sleeps when idle and can take up to a minute to wake,
// so we wait up to 3 minutes for a reply and retry once after 30 seconds.
// The app skips a run that comes too soon after the last one, so a retry can't double-run.

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(trigger(env));
  },
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function trigger(env) {
  const base = env.APP_URL.replace(/\/$/, "");
  const url = `${base}/cron/run`;

  // Wake the app first. Render returns 502/503 while a sleeping instance boots
  // (can exceed a minute), so poll /health until it answers 200 (up to ~4 min).
  let awake = false;
  for (let i = 1; i <= 24 && !awake; i++) {
    try {
      const res = await fetch(`${base}/health`, { signal: AbortSignal.timeout(30_000) });
      awake = res.ok;
      console.log(`wake ${i}: HTTP ${res.status}`);
    } catch (err) {
      console.log(`wake ${i} failed: ${err}`);
    }
    if (!awake) await sleep(10_000);
  }
  console.log(`app awake: ${awake}`);

  // Then trigger the run; a few retries cover any remaining hiccup.
  const maxAttempts = awake ? 3 : 8;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${env.RUN_TOKEN}` },
        signal: AbortSignal.timeout(60_000),
      });
      const body = await res.text();
      console.log(`attempt ${attempt}: HTTP ${res.status} ${body.slice(0, 200)}`);
      if (res.ok) return;
      if (res.status < 500) return; // 401/404 etc. won't fix themselves
    } catch (err) {
      console.log(`attempt ${attempt} failed: ${err}`);
    }
    if (attempt < maxAttempts) await sleep(Math.min(15_000 * attempt, 60_000));
  }
}
