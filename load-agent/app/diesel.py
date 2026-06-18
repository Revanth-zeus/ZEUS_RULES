"""
Live diesel price via Tavily (the headline sponsor) -> honest, cited fuel cost.

Honors the brief's two demo-safety rules:
  1. Cache the result (TTL) so a flaky conference wifi can't kill the demo.
  2. Fall back to a stated default and label provenance, never fabricate.

Set TAVILY_API_KEY in the environment. Without it (or on any failure) we use the
documented default and say so in the breakdown's `diesel_source`.
"""
from __future__ import annotations
import os, re, time, json, tempfile, urllib.request, urllib.error

DEFAULT_DIESEL = 3.95
CACHE_PATH = os.path.join(tempfile.gettempdir(), "diesel_cache.json")
TTL_SECONDS = 6 * 3600
TAVILY_URL = "https://api.tavily.com/search"


def _read_cache():
    try:
        with open(CACHE_PATH) as f:
            c = json.load(f)
        if time.time() - c["ts"] < TTL_SECONDS:
            return c
    except Exception:
        pass
    return None


def _write_cache(price, source):
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump({"price": price, "source": source, "ts": time.time()}, f)
    except Exception:
        pass


def _query_tavily(region: str) -> tuple[float, str] | None:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return None
    q = f"current average diesel fuel price per gallon {region} United States EIA"
    body = json.dumps({
        "api_key": key, "query": q, "search_depth": "basic",
        "include_answer": True, "max_results": 5,
    }).encode()
    req = urllib.request.Request(TAVILY_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    text = (data.get("answer") or "") + " " + " ".join(
        r.get("content", "") for r in data.get("results", []))
    # pull a plausible $/gal figure (3.something or 4.something)
    m = re.search(r"\$?\s*([34]\.\d{1,3})\s*(?:/?\s*gal|per gallon|dollars)?", text)
    if not m:
        return None
    price = float(m.group(1))
    src = data.get("results", [{}])[0].get("url", "Tavily/EIA")
    return price, f"Tavily live (EIA-sourced): {src}"


def get_diesel_price(region: str = "national") -> tuple[float, str]:
    """Returns (price_per_gal, provenance_string). Cache -> Tavily -> default."""
    cached = _read_cache()
    if cached:
        return cached["price"], cached["source"] + " [cached]"
    live = _query_tavily(region)
    if live:
        _write_cache(*live)
        return live
    return DEFAULT_DIESEL, f"default ${DEFAULT_DIESEL}/gal (set TAVILY_API_KEY for live)"
