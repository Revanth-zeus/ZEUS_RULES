"""
FastAPI app for the Load Profitability Agent.

Endpoints:
  POST /recommend   {location, equipment, capabilities, available_date, cost_overrides?}
                    -> ranked loads, each with a full auditable cost breakdown
  GET  /markets/top -> top-N markets to start/reposition a carrier (board-derived)
  POST /ask         {query} -> free text -> structured -> engine -> plain-English why
  GET  /health      -> liveness + dataset stats

Principle: deterministic math for money, LLM only for interface + explanation.
"""
from __future__ import annotations
import os
from datetime import date
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .parse import parse_any, build_db
from .market import MarketModel
from .cost import CostParams
from .rank import recommend
from .agent import parse_query, explain
from .diesel import get_diesel_price

DATA_PATH = os.getenv("LOAD_DATA_PATH", "data/loads.csv")
STATE: dict = {}


def _make_params(overrides: dict | None, pay_model: str) -> CostParams:
    price, source = get_diesel_price()
    p = CostParams(diesel_price=price, diesel_source=source, pay_model=pay_model)
    for k, v in (overrides or {}).items():
        if hasattr(p, k) and v is not None:
            setattr(p, k, v)
    return p


@asynccontextmanager
async def lifespan(app: FastAPI):
    rows = parse_any(DATA_PATH)
    conn = build_db(rows)
    STATE["conn"] = conn
    STATE["market"] = MarketModel(conn)
    STATE["n_loads"] = len(rows)
    STATE["source"] = DATA_PATH
    # The agent's "now" defaults to the board's snapshot (earliest pickup), not the
    # wall clock -- a snapshot dated in the past must not look entirely expired.
    pus = [r["pu_date"] for r in rows if r.get("pu_date")]
    STATE["ref_date"] = min(pus) if pus else date.today().isoformat()
    yield
    conn.close()


app = FastAPI(title="Load Profitability Agent", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


class RecommendReq(BaseModel):
    location: str = Field(..., examples=["Dallas, TX"])
    equipment: str | None = Field(None, examples=["Dry Van"])
    capabilities: list[str] = Field(default_factory=list, examples=[["Hazmat"]])
    available_date: str | None = None
    pay_model: str = Field("carrier", pattern="^(carrier|owner_operator)$")
    cost_overrides: dict | None = None
    top_n: int = 10


class AskReq(BaseModel):
    query: str = Field(..., examples=["I'm in Dallas, dry van, no hazmat — what should I grab?"])
    pay_model: str = Field("carrier", pattern="^(carrier|owner_operator)$")
    top_n: int = 10


def _avail(s: str | None) -> date:
    try:
        if s:
            return date.fromisoformat(s)
        return date.fromisoformat(STATE.get("ref_date") or date.today().isoformat())
    except ValueError:
        return date.today()


@app.get("/health")
def health():
    return {"status": "ok", "loads": STATE.get("n_loads"),
            "markets": len(STATE["market"].markets) if "market" in STATE else 0,
            "ref_date": STATE.get("ref_date"),
            "source": STATE.get("source")}


@app.post("/recommend")
def recommend_endpoint(req: RecommendReq):
    if "conn" not in STATE:
        raise HTTPException(503, "data not loaded")
    params = _make_params(req.cost_overrides, req.pay_model)
    result = recommend(STATE["conn"], STATE["market"], req.location, req.equipment,
                       req.capabilities, _avail(req.available_date), params, req.top_n)
    if result["recommendations"]:
        result["explanation"] = explain(result["recommendations"][0],
                                         result.get("runner_up"))
    return result


@app.get("/markets/top")
def markets_top(n: int = 10):
    if "market" not in STATE:
        raise HTTPException(503, "data not loaded")
    return {"markets": STATE["market"].top_markets(n),
            "note": "Score 0-100 from board supply: log-scaled outbound demand (0.5), "
                    "lane balance (0.3), avg outbound RPM (0.2). High score = easy to "
                    "leave loaded; low score = a truck trap."}


@app.post("/ask")
def ask_endpoint(req: AskReq):
    if "conn" not in STATE:
        raise HTTPException(503, "data not loaded")
    parsed = parse_query(req.query)
    if not parsed["location"]:
        return {"parsed": parsed,
                "error": "Could not identify a city in the query. Try e.g. 'Dallas, TX'."}
    params = _make_params(None, req.pay_model)
    result = recommend(STATE["conn"], STATE["market"], parsed["location"],
                       parsed["equipment"], parsed["capabilities"],
                       _avail(parsed["available_date"]), params, req.top_n)
    result["parsed_query"] = parsed
    if result["recommendations"]:
        result["explanation"] = explain(result["recommendations"][0],
                                         result.get("runner_up"))
    return result


@app.get("/")
def root():
    return {"service": "Load Profitability Agent",
            "endpoints": ["/recommend", "/markets/top", "/ask", "/health", "/docs"]}
