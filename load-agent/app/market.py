"""
Market model -- the differentiating insight.

For every market (city) we measure, from the board's OWN supply:
  outbound_count   loads originating here       (can you GET a load out?)
  inbound_count    loads terminating here       (does freight pile up here?)
  lane_balance     outbound / max(inbound,1)    (>1 easy to leave, <1 a trap)
  avg_outbound_rpm typical pay leaving here
  market_score     0-100 composite (log-scaled demand + balance + rate)

A load that drops the truck in a weak market costs the EXPECTED empty miles to the
nearest strong market, priced at the carrier's variable cost per mile. Inbound deadhead
is measured directly from geo; outbound deadhead is modeled here. That is the
"deadhead in both directions" the statement demands.
"""
from __future__ import annotations
import math, sqlite3
from .geo import get_coords, haversine, CIRCUITY

# Composite weights (tunable). Demand dominates, balance guards traps, rate is a tiebreak.
W_DEMAND, W_BALANCE, W_RATE = 0.50, 0.30, 0.20
STRONG_THRESHOLD = 60.0          # market_score at/above this = a "strong" hub
BALANCE_CAP = 3.0                # clip lane_balance so a tiny-inbound city can't dominate


def _norm(x, lo, hi):
    return 0.0 if hi <= lo else max(0.0, min(1.0, (x - lo) / (hi - lo)))


class MarketModel:
    def __init__(self, conn: sqlite3.Connection):
        self.markets: dict[str, dict] = {}
        self._build(conn)

    def _build(self, conn: sqlite3.Connection):
        rows = {}
        for r in conn.execute(
            "SELECT origin_key AS k, origin AS name, COUNT(*) c, AVG(posted_rpm) rpm "
            "FROM loads GROUP BY origin_key"):
            rows[r["k"]] = {"key": r["k"], "name": r["name"],
                            "outbound_count": r["c"], "inbound_count": 0,
                            "avg_outbound_rpm": round(r["rpm"], 2)}
        for r in conn.execute(
            "SELECT dest_key AS k, dest AS name, COUNT(*) c FROM loads GROUP BY dest_key"):
            m = rows.setdefault(r["k"], {"key": r["k"], "name": r["name"],
                                         "outbound_count": 0, "inbound_count": 0,
                                         "avg_outbound_rpm": 0.0})
            m["inbound_count"] = r["c"]

        log_out = [math.log1p(m["outbound_count"]) for m in rows.values()]
        rpms = [m["avg_outbound_rpm"] for m in rows.values() if m["avg_outbound_rpm"] > 0]
        lo_d, hi_d = min(log_out), max(log_out)
        lo_r, hi_r = (min(rpms), max(rpms)) if rpms else (0, 1)

        for m in rows.values():
            m["lane_balance"] = round(m["outbound_count"] / max(m["inbound_count"], 1), 2)
            d = _norm(math.log1p(m["outbound_count"]), lo_d, hi_d)
            b = _norm(min(m["lane_balance"], BALANCE_CAP), 0, BALANCE_CAP)
            rt = _norm(m["avg_outbound_rpm"], lo_r, hi_r)
            m["market_score"] = round(100 * (W_DEMAND * d + W_BALANCE * b + W_RATE * rt), 1)
        self.markets = rows
        self._strong = [m for m in rows.values() if m["market_score"] >= STRONG_THRESHOLD]

    def get(self, city_key: str) -> dict | None:
        return self.markets.get(city_key)

    def nearest_strong(self, city_key: str) -> tuple[str | None, float]:
        """(name, empty_miles) to nearest strong hub. Self counts -> 0 miles if strong."""
        here = self.markets.get(city_key)
        if here and here["market_score"] >= STRONG_THRESHOLD:
            return here["name"], 0.0
        c = get_coords(city_key)
        if c is None or not self._strong:
            return None, 250.0  # unknown: assume a long-ish reposition
        best, best_mi = None, float("inf")
        for s in self._strong:
            sc = get_coords(s["key"])
            if sc is None:
                continue
            mi = haversine(c, sc) * CIRCUITY
            if mi < best_mi:
                best, best_mi = s["name"], mi
        return best, round(best_mi, 1)

    def dest_penalty(self, dest_key: str, variable_cpm: float) -> dict:
        """Expected repositioning cost out of the destination market."""
        hub, empty_mi = self.nearest_strong(dest_key)
        return {
            "reposition_to": hub,
            "empty_miles": empty_mi,
            "penalty": round(empty_mi * variable_cpm, 2),
        }

    def top_markets(self, n: int = 10) -> list[dict]:
        ranked = sorted(self.markets.values(),
                        key=lambda m: m["market_score"], reverse=True)
        return ranked[:n]
