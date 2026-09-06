# Deploying

## Read this first: Vercel cannot run the screener

Vercel is serverless, and three properties of this app are incompatible with
that — not awkward, incompatible:

| What the screener does | What Vercel gives you |
| --- | --- |
| A scan runs 2–22 minutes | Functions are killed at 60 s (Hobby) / 300 s (Pro) |
| Scan progress lives in process memory between polls | Every request may hit a different, cold instance |
| Caches ~340 MB of candles to disk | Read-only filesystem except `/tmp`, wiped per invocation |

Losing the cache is the fatal one. Without it every scan refetches ~9,000
candle requests and dies on the Upstox rate limit long before it finishes.

So: **the API runs on Render.** Vercel can serve the UI as a static site
pointed at that API, which is what `vercel.json` does. That is the whole of
"Vercel-friendly" here, and it is optional — Render already serves the UI.

---

## Render (the actual deployment)

I cannot click through your dashboard, so here is the exact path.

**1 — Create the service.** In Render: **New → Blueprint**, choose
`Ashbinbiju/SSing`, and Render reads [render.yaml](render.yaml). It creates a
web service named `swing-screener` in Singapore (Render's closest region to
Upstox's Indian endpoints; a cold NSE scan is ~9,000 round trips and latency
dominates).

**2 — Set `UPSTOX_TOKEN`.** Render prompts for it because the blueprint marks
it `sync: false`. Paste the token. It never goes in the repo.

**3 — Copy the generated `SCREENER_ACCESS_KEY`.** Render generates one. Open
the site once as:

```
https://<your-service>.onrender.com/?key=<the generated value>
```

The browser stores it and sends it as an `X-Screener-Key` header thereafter.
Without this anyone who finds the URL can start scans against your token and
exhaust your rate limit. Leave `SCREENER_ACCESS_KEY` unset only if you want
the service open to the world.

**4 — Check it came up.** `GET /api/health` returns `200` with
`{"ok": true, "market_date": "..."}`, or `503` with the reason if the token is
missing — so a bad deploy fails at the health check rather than at your first
scan.

### Free plan caveats, both of which bite this app

* **The instance sleeps after ~15 minutes idle.** A scan running when it
  sleeps is killed, and the next request waits through a cold start.
* **The filesystem is wiped on every deploy and restart**, taking the candle
  cache with it. The next scan is a cold one: ~2 minutes for the 210 F&O
  names, ~22 minutes for all 3,149 NSE names.

On free, use the **F&O universe**. To keep the cache, move to Starter and
uncomment the `disk:` and `SCREENER_DATA_DIR` blocks in `render.yaml` — a disk
cannot be attached to a free service.

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `UPSTOX_TOKEN` | yes | Market data. Never committed. |
| `SCREENER_ACCESS_KEY` | strongly advised | Gates every route except `/api/health`. |
| `SCREENER_DATA_DIR` | with a disk | Where candles cache. Point at the mount. |
| `SCREENER_CORS_ORIGINS` | only for Vercel | Comma-separated origins allowed to call the API. |
| `SCREENER_FOLD_STUB` | no | `0` reverts to Upstox's seven-bar sessions. Default `1` matches TradingView. |
| `PORT`, `HOST` | set by Render | The server binds to them. |

---

## Vercel (the UI only, optional)

**1** — Import the repo. `vercel.json` serves `screener/web` as static files;
no build step, no functions.

**2** — On Render, set `SCREENER_CORS_ORIGINS` to your Vercel origin, e.g.
`https://ssing.vercel.app`, and redeploy. Without it the browser blocks the
cross-origin calls.

**3** — Open the Vercel URL once with both parameters:

```
https://ssing.vercel.app/?api=https://swing-screener.onrender.com&key=<access key>
```

Both are stored in `localStorage`, so later visits need only the bare URL.

Understand the trade: the UI is on a CDN, every scan still runs on Render, and
you now maintain two deployments and a CORS rule. Serving the UI from Render
costs you nothing and needs none of that.

---

## Anywhere else

The app is a plain ASGI application with no platform coupling:

```
pip install -r requirements.txt
uvicorn screener.server:app --host 0.0.0.0 --port 8777
```

Anything that runs a long-lived process with a writable directory — Fly.io,
Railway, a VPS, Docker — works the same way. Set the same environment
variables.

## Running it locally

Nothing above changes local use. `python screen.py serve --open` still binds
`127.0.0.1:8777` and reads the token from `.env`.
