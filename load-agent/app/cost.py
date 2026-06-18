"""
Freight cost engine -- deterministic. The LLM never produces a number; everything a
recommendation reports is computed here and traceable to these inputs.

Net profit per load:
    deadhead_miles = road_dist(driver -> origin)         inbound empty miles (measured)
    total_miles    = loaded + deadhead
    operating_cost = variable_cpm * total_miles
    idle_cost      = idle_days * daily_fixed             pickup later than availability
    dest_penalty   = empty_miles_to_nearest_hub * variable_cpm   (from market model)
    net_profit     = rate - operating_cost - idle_cost - dest_penalty

Rank on net_per_day, NOT posted $/mi:
    transit_days   = total_miles / 550   (HOS-limited daily miles)
    days           = transit_days + load_unload + idle_days
    net_per_day    = net_profit / days
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import date
from .geo import road_dist, get_coords, haversine, CIRCUITY

NORTHEAST = {"ny", "nj", "ct", "ma", "pa", "ri", "nh", "vt", "me", "md", "de"}
MILES_PER_DAY = 550.0          # loaded miles/day under Hours-of-Service
DEADHEAD_MPD = 600.0           # empty repositioning miles/day
LOAD_UNLOAD_DAYS = 0.75        # midpoint of brief's 0.5-1.0
LEGAL_WEIGHT = 45000           # lbs payload ceiling on 80k GVW (above -> permit flag)


@dataclass
class CostParams:
    diesel_price: float = 3.95            # $/gal -- overridden live via Tavily/diesel.py
    mpg_dry: float = 6.5
    mpg_other: float = 6.0                # reefer / flatbed / step deck (loaded)
    reefer_cpm: float = 0.07
    driver_cpm: float = 0.62
    maint_cpm: float = 0.18
    insurance_cpm: float = 0.10
    tolls_cpm: float = 0.04               # x2.5 on Northeast lanes
    overhead_cpm: float = 0.30
    daily_fixed: float = 225.0            # idle-day cost
    pay_model: str = "carrier"            # "carrier" (driver pay=cost) | "owner_operator"
    diesel_source: str = "default"        # provenance string for the breakdown

    def fuel_cpm(self, equip: str) -> float:
        mpg = self.mpg_dry if equip.lower() == "dry van" else self.mpg_other
        return self.diesel_price / mpg


def _state(city: str) -> str:
    return city.split(",")[-1].strip().lower() if "," in city else ""


def variable_cpm(params: CostParams, equip: str, lane_touches_ne: bool) -> dict:
    fuel = params.fuel_cpm(equip)
    tolls = params.tolls_cpm * (2.5 if lane_touches_ne else 1.0)
    reefer = params.reefer_cpm if equip.lower() == "reefer" else 0.0
    components = {
        "fuel_cpm": round(fuel, 3), "driver_cpm": params.driver_cpm,
        "maint_cpm": params.maint_cpm, "insurance_cpm": params.insurance_cpm,
        "tolls_cpm": round(tolls, 3), "overhead_cpm": params.overhead_cpm,
        "reefer_cpm": round(reefer, 3),
    }
    components["total_cpm"] = round(sum(components.values()), 3)
    return components


def days_to_reach(deadhead_miles: float) -> int:
    import math
    return math.ceil(deadhead_miles / DEADHEAD_MPD)


def score_load(load: dict, driver_city: str, params: CostParams, market,
               available: date) -> dict | None:
    """Full economics for one load given a driver location. None if origin unreachable."""
    deadhead = road_dist(driver_city, load["origin"])
    if deadhead is None:
        deadhead = 150.0  # unknown origin: assume a modest reposition rather than crash
    loaded = float(load["miles"])
    total = loaded + deadhead

    lane_ne = _state(load["origin"]) in NORTHEAST or _state(load["dest"]) in NORTHEAST
    cpm = variable_cpm(params, load["equip"], lane_ne)
    var_cpm = cpm["total_cpm"]
    operating = var_cpm * total

    # idle: pickup later than (availability + travel to origin)
    try:
        pu = date.fromisoformat(load["pu_date"])
    except (ValueError, KeyError):
        pu = available
    earliest = days_to_reach(deadhead)
    idle_days = max(0, (pu - available).days - earliest)
    idle_cost = idle_days * params.daily_fixed

    pen = market.dest_penalty(load["dest_key"], var_cpm)

    rate = float(load["rate"])
    carrier_net = rate - operating - idle_cost - pen["penalty"]

    # pay-model lens: for an owner-operator, driver wages are take-home, not a cost.
    driver_pay = params.driver_cpm * total
    if params.pay_model == "owner_operator":
        net = carrier_net + driver_pay         # margin + wages = what the driver keeps
        net_label = "owner-operator take-home"
    else:
        net = carrier_net                      # carrier margin after paying the driver
        net_label = "carrier margin"

    transit_days = total / MILES_PER_DAY
    days = transit_days + LOAD_UNLOAD_DAYS + idle_days
    net_per_day = net / days if days > 0 else net

    return {
        "load_id": load["load_id"], "origin": load["origin"], "dest": load["dest"],
        "equip": load["equip"], "pu_date": load["pu_date"], "reqs": load["reqs"],
        "rate": round(rate), "loaded_miles": round(loaded),
        "deadhead_miles": round(deadhead), "total_miles": round(total),
        "cpm_breakdown": cpm,
        "operating_cost": round(operating),
        "idle_days": idle_days, "idle_cost": round(idle_cost),
        "dest_market_score": (market.get(load["dest_key"]) or {}).get("market_score"),
        "dest_penalty": pen,
        "net_profit": round(net), "net_label": net_label,
        "carrier_margin": round(carrier_net),
        "driver_pay": round(driver_pay),
        "days": round(days, 2), "net_per_day": round(net_per_day),
        "net_per_loaded_mile": round(net / loaded, 2) if loaded else None,
        "posted_rpm": load["posted_rpm"],
        "margin_pct": round(100 * carrier_net / rate, 1) if rate else None,
        "weight": load["weight"],
        "overweight_flag": load["weight"] > LEGAL_WEIGHT,
    }
