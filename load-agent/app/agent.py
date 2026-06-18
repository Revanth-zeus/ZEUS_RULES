"""
Agent / NL layer. The LLM is a SHELL over the deterministic engine:
  - parse_query: free text -> structured params (rule-based; Gemini optional).
  - explain: why the top pick beats the runner-up, IN PLAIN ENGLISH, using ONLY
    numbers the engine returned. The LLM never invents a figure.

Both paths degrade gracefully: no GEMINI_API_KEY -> deterministic parse + template.
"""
from __future__ import annotations
import os, re, json, urllib.request, urllib.error
from datetime import date
from .geo import CITY_COORDS

EQUIP_KEYWORDS = {
    "dry van": "Dry Van", "dryvan": "Dry Van", "van": "Dry Van",
    "reefer": "Reefer", "refrigerated": "Reefer",
    "flatbed": "Flatbed", "flat bed": "Flatbed",
    "step deck": "Step Deck", "stepdeck": "Step Deck",
}
CITY_NAMES = sorted(CITY_COORDS.keys(), key=len, reverse=True)  # longest-match first


def parse_query(text: str) -> dict:
    """Free text -> {location, equipment, capabilities, available_date}."""
    t = text.lower()

    location = None
    for key in CITY_NAMES:
        city = key.split(",")[0]
        if city in t:
            c, s = key.rsplit(",", 1)
            location = f"{c.title()}, {s.upper()}"   # 'atlanta, ga' -> 'Atlanta, GA'
            break

    equipment = None
    for kw, canon in EQUIP_KEYWORDS.items():
        if kw in t:
            equipment = canon
            break

    # capabilities the DRIVER HAS (used to keep, not drop, gated loads)
    caps = []
    if re.search(r"\bhazmat\b", t) and not re.search(r"no hazmat|without hazmat|don't have hazmat|no haz", t):
        caps.append("Hazmat")
    if re.search(r"\bteam\b", t) and not re.search(r"no team|solo", t):
        caps.append("Team")
    if "liftgate" in t and "no liftgate" not in t:
        caps.append("Liftgate")

    return {
        "location": location, "equipment": equipment,
        "capabilities": caps, "available_date": None,  # None -> caller uses board snapshot date
        "_parser": "rule-based",
    }


def _gemini(prompt: str) -> str | None:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash:generateContent?key={key}")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, IndexError):
        return None


def _template_explanation(top: dict, runner: dict | None) -> str:
    p = top["dest_penalty"]
    s = (f"Top pick {top['load_id']}: {top['origin']} -> {top['dest']} "
         f"({top['equip']}). It nets ${top['net_profit']:,} "
         f"(${top['net_per_day']:,}/day over {top['days']} days). "
         f"Deadhead in is {top['deadhead_miles']} mi; the destination market scores "
         f"{top['dest_market_score']}/100")
    if p["empty_miles"] and p["reposition_to"]:
        s += (f", so we charge ~{p['empty_miles']:.0f} empty mi (${p['penalty']:,.0f}) "
              f"to reposition toward {p['reposition_to']}")
    s += "."
    if runner:
        delta = top["net_per_day"] - runner["net_per_day"]
        s += (f" It beats runner-up {runner['load_id']} ({runner['origin']} -> "
              f"{runner['dest']}, posted ${runner['posted_rpm']}/mi) by "
              f"${delta:,}/day")
        if (runner.get("dest_market_score") or 100) < (top.get("dest_market_score") or 0):
            s += " — the runner-up pays more per mile but drops into a weaker market."
        else:
            s += "."
    return s


def explain(top: dict, runner: dict | None = None) -> str:
    """Plain-English rationale. Numbers come ONLY from the engine output."""
    base = _template_explanation(top, runner)
    if not os.getenv("GEMINI_API_KEY"):
        return base
    facts = {k: top.get(k) for k in
             ("load_id", "origin", "dest", "equip", "net_profit", "net_per_day",
              "days", "deadhead_miles", "dest_market_score", "posted_rpm")}
    facts["dest_penalty"] = top["dest_penalty"]
    if runner:
        facts["runner_up"] = {k: runner.get(k) for k in
                              ("load_id", "origin", "dest", "net_per_day",
                               "posted_rpm", "dest_market_score")}
    prompt = (
        "You are a freight dispatcher explaining a recommendation to an owner-operator. "
        "Use ONLY the numbers in this JSON; do not invent or alter any figure. "
        "2-3 sentences, concrete, no fluff.\n\n" + json.dumps(facts, indent=2))
    out = _gemini(prompt)
    return out or base
