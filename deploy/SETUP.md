# TradingAgents + n8n: Phase 1 Setup (Alerts Only)

This pack turns your TradingAgents fork into a service n8n can call, and gives you an
importable n8n workflow that runs a watchlist on a schedule and messages you the
decisions. No money, no order execution. That comes in Phase 2.

Files in this pack:

- `api_server.py` - HTTP wrapper. Goes in the root of your repo (next to `main.py`).
- `requirements-api.txt` - the two extra Python deps (FastAPI + uvicorn).
- `Dockerfile.api` - to run the wrapper as a container.
- `.env.example` - low-cost model/config starter. Copy to `.env` and add your key.
- `n8n_phase1_alerts.json` - the importable n8n workflow.

Everything below assumes your branch: `github.com/ryanamcc88-prog/TradingAgents`.

---

## Bayside server specifics (read this, it overrides the generic bits below)

Your n8n runs in Docker on the server, on the `bayside-net` network (containers `n8n`
and `n8n-postgres`), bound to `127.0.0.1:5678`. Do all of this in the Remote Desktop
session on the server. Run the engine on the SAME network so n8n calls it by name.

Run the engine (after building, see Step 2):

```
docker run -d --name tradingagents-api --network bayside-net -p 127.0.0.1:8000:8000 --env-file .env tradingagents-api
```

Then in the n8n workflow set `TA_API_URL` to:

```
http://tradingagents-api:8000
```

(container-to-container on `bayside-net`, no internet hop). The `-p 127.0.0.1:8000:8000`
is only so you can smoke-test from the server host with `curl http://localhost:8000/health`.

Two house rules to keep this consistent with the rest of your ecosystem:
- Provider is Anthropic (Claude). Generate the engine's `ANTHROPIC_API_KEY` inside the
  "Bayside n8n" Anthropic workspace so the US$30 cap governs it.
- Set this workflow's Error Workflow to "00 - Global Error Handler" (Workflow settings),
  same as your other flows.

---

## Step 1 - Add the wrapper files to your repo

Copy `api_server.py`, `requirements-api.txt`, `Dockerfile.api` and `.env.example`
into the root of your local clone of the repo, commit, and push. (I cannot push to
your GitHub from here, so this step is yours.)

```
git clone https://github.com/ryanamcc88-prog/TradingAgents.git
cd TradingAgents
# copy the four files from this pack into here
git add api_server.py requirements-api.txt Dockerfile.api .env.example
git commit -m "Add n8n HTTP wrapper (Phase 1)"
git push
```

## Step 2 - Configure and run the engine

```
cp .env.example .env
# edit .env: paste your OPENAI_API_KEY (or switch provider). Keep the cheap models to start.
```

Run it directly:

```
pip install .
pip install -r requirements-api.txt
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

Or with Docker:

```
docker build -f Dockerfile.api -t tradingagents-api .
docker run --env-file .env -p 8000:8000 tradingagents-api
```

Check it is alive:

```
curl http://localhost:8000/health
```

Smoke-test one analysis (this WILL spend a little on LLM calls and takes a few minutes):

```
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","date":"2026-06-16","asset_type":"stock"}'
```

You should get back JSON with `rating`, `action` (BUY/HOLD/SELL), `confidence` and `reasoning`.

## Step 3 - Import the n8n workflow

1. In n8n: Workflows -> Import from File -> choose `n8n_phase1_alerts.json`.
2. Set an environment variable (or n8n variable) `TA_API_URL` pointing at the wrapper,
   for example `http://localhost:8000` or `http://tradingagents-api:8000` if both run
   in the same Docker network. (n8n cannot reach `localhost` of the host from inside a
   container, so use the container name or host IP in that case.)
3. Open the **Build Jobs** node and edit your watchlist and caps.
4. Optional: set up the **Telegram Alert** node (create a bot via @BotFather, add the
   credential, put your chat id in `TELEGRAM_CHAT_ID`). Or delete it and drop in an
   Email / Slack node.
5. Optional: set up the **Log to Google Sheet** node (add a Google Sheets credential,
   create a sheet with header row `date, ticker, rating, action, confidence,
   suggested_position, reasoning`, paste the sheet id). Or delete the node.
6. Click **Test workflow** to run it once. Then activate it to run on the schedule.

## What the workflow does

```
Schedule (weekdays 8am)
  -> Build Jobs            (your watchlist + caps, one item per ticker)
  -> Loop Over Tickers     (one at a time, so the engine isn't hit in parallel)
       -> Analyze          (POST to the TradingAgents API)
       -> Format & Risk Gate (build the alert text, flag BUY/SELL vs HOLD, advisory size)
       -> Log to Google Sheet (every decision, for honest review later)
       -> Actionable?      (BUY or SELL -> alert; HOLD -> just logged)
            -> Telegram Alert
  ->