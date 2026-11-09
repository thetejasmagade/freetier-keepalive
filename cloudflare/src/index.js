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

async function trigger(env) {
  const url = `${env.APP_URL.replace(/\/$/, "")}/cron/run`;
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${env.RUN_TOKEN}` },
        signal: AbortSignal.timeout(180_000),
      });
      const body = await res.text();
      console.log(`attempt ${attempt}: HTTP ${res.status} ${body.slice(0, 500)}`);
      if (res.ok) return;
    } catch (err) {
      console.log(`attempt ${attempt} failed: ${err}`);
    }
    if (attempt < 2) await new Promise((r) => setTimeout(r, 30_000));
  }
}
