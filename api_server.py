"""
api_server.py - thin HTTP wrapper around TradingAgents for n8n.

Drop this file in the root of your TradingAgents repo (next to main.py).
It exposes one endpoint that n8n calls per ticker:

    POST /analyze   { "ticker": "AAPL", "date": "2026-06-16", "asset_type": "stock" }

and returns:

    {
      "ticker": "AAPL",
      "date": "2026-06-16",
      "rating": "Overweight",          # the raw 5-tier rating from the engine
      "action": "BUY",                  # simplified BUY / HOLD / SELL for the n8n risk gate
      "confidence": "medium",           # coarse confidence derived from the rating tier
      "reasoning": "...markdown...",     # the Portfolio Manager's final decision text
      "reports": { ...analyst reports... },
      "elapsed_seconds": 73.2
    }

It also exposes GET /health for n8n / uptime checks.

Run locally:
    pip install -r requirements-api.txt      # fastapi + uvicorn, on top of the repo's own deps
    uvicorn api_server:app --host 0.0.0.0 --port 8000

All model / cost settings come from environment variables (see .env.example),
so you never edit code to change models or debate depth.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import date as _date
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

# ---------------------------------------------------------------------------
# Map the engine's 5-tier rating to a simple action + confidence for the
# n8n risk gate. The risk gate (in n8n) is what actually decides position
# size and whether to act - this is just a clean, machine-readable signal.
# ---------------------------------------------------------------------------
RATING_TO_ACTION = {
    "Buy": ("BUY", "high"),
    "Overweight": ("BUY", "medium"),
    "Hold": ("HOLD", "low"),
    "Underweight": ("SELL", "medium"),
    "Sell": ("SELL", "high"),
}

REPORT_KEYS = [
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "trader_investment_plan",
    "investment_plan",
    "final_trade_decision",
]

# Which analysts to run. Fewer analysts = fewer LLM calls = lower cost.
# Override with TA_SELECTED_ANALYSTS="market,news" etc.
_analysts_env = os.getenv("TA_SELECTED_ANALYSTS", "market,social,news,fundamentals")
SELECTED_ANALYSTS = tuple(a.strip() for a in _analysts_env.split(",") if a.strip())

app = FastAPI(title="TradingAgents API", version="1.0")

# Build the graph once at startup and reuse it. propagate() is not assumed
# thread-safe, so we serialise calls with a lock. The engine is heavy anyway;
# running one analysis at a time is the right behaviour.
_graph: TradingAgentsGraph | None = None
_lock = threading.Lock()


def _get_graph() -> TradingAgentsGraph:
    global _graph
    if _graph is None:
        config = DEFAULT_CONFIG.copy()  # already absorbs TRADINGAGENTS_* env vars
        _graph = TradingAgentsGraph(
            selected_analysts=SELECTED_ANALYSTS,
            debug=False,
            config=config,
        )
    return _graph


class AnalyzeRequest(BaseModel):
    ticker: str = Field(..., examples=["AAPL"])
    date: str | None = Field(default=None, description="YYYY-MM-DD; defaults to today")
    asset_type: str = Field(default="stock", description="stock or crypto")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "analysts": list(SELECTED_ANALYSTS),
        "llm_provider": DEFAULT_CONFIG.get("llm_provider"),
        "deep_think_llm": DEFAULT_CONFIG.get("deep_think_llm"),
        "quick_think_llm": DEFAULT_CONFIG.get("quick_think_llm"),
        "max_debate_rounds": DEFAULT_CONFIG.get("max_debate_rounds"),
    }


@app.post("/analyze")
def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    trade_date = req.date or _date.today().isoformat()
    ticker = req.ticker.strip().upper()

    started = time.time()
    with _lock:
        graph = _get_graph()
        final_state, rating = graph.propagate(
            ticker, trade_date, asset_type=req.asset_type
        )
    elapsed = round(time.time() - started, 1)

    action, confidence = RATING_TO_ACTION.get(rating, ("HOLD", "low"))

    reports = {}
    for key in REPORT_KEYS:
        val = final_state.get(key)
        if val:
            reports[key] = val

    return {
        "ticker": ticker,
        "date": trade_date,
        "asset_type": req.asset_type,
        "rating": rating,
        "action": action,
        "confidence": confidence,
        "reasoning": final_state.get("final_trade_decision", ""),
        "reports": reports,
        "elapsed_seconds": elapsed,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_server:app",
        host=os.getenv("TA_API_HOST", "0.0.0.0"),
        port=int(os.getenv("TA_API_PORT", "8000")),
    )
