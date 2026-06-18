"""
Ranking: hard eligibility gates BEFORE scoring, then rank survivors by net_per_day.

Gates (a load is dropped, not penalized, if it fails):
  - equipment must match the truck's trailer
  - every Req flag (Hazmat/Team/Liftgate) must be in the driver's capabilities
  - pickup date not already expired relative to availability
Overweight (>45k lbs) is flagged, not dropped (it may just need a permit).
"""
from __future__ import annotations
from datetime import date
import sqlite3
from .cost import CostParams, score_load


def _eligible_rows(conn: sqlite3.Connection, equipment: str | None,
                   capabilities: list[str], available: date) -> list[dict]:
    caps = {c.lower() for c in capabilities}
    if equipment:
        cur = conn.execute("SELECT * FROM loads WHERE equip = ?", (equipment,))
    else:
        cur = conn.execute("SELECT * FROM loads")
    out = []
    for r in cur:
        row = dict(r)
        reqs = [x for x in (row["reqs"] or "").split(",") if x]
        if any(req.lower() not in caps for req in reqs):
            continue  # driver lacks a required endorsement/equipment
        try:
            if date.fromisoformat(row["pu_date"]) < available:
                continue  # pickup already expired
        except ValueError:
            pass
        out.append(row)
    return out


def recommend(conn, market, location: str, equipment: str | None,
              capabilities: list[str], available: date, params: CostParams,
              n: int = 10) -> dict:
    rows = _eligible_rows(conn, equipment, capabilities, available)
    scored = [s for s in (score_load(r, location, params, market, available)
                          for r in rows) if s]
    scored.sort(key=lambda s: s["net_per_day"], reverse=True)
    top = scored[:n]
    return {
        "query": {
            "location": location, "equipment": equipment,
            "capabilities": capabilities, "available_date": available.isoformat(),
            "pay_model": params.pay_model, "diesel_source": params.diesel_source,
        },
        "eligible_count": len(rows),
        "scored_count": len(scored),
        "recommendations": top,
        "runner_up": scored[1] if len(scored) > 1 else None,
    }
