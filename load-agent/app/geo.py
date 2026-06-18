"""
Geo layer: static US city -> (lat, lng) lookup + haversine road-distance estimate.

Rationale (from build brief): the data uses a finite set of US cities, so we ship a
static lookup and compute distances ourselves. No external geo dependency = robust
for a live demo. Road miles are estimated as great-circle * 1.2 (circuity factor),
because straight-line underestimates real road distance.
"""
from __future__ import annotations
import math
import re
from functools import lru_cache

CIRCUITY = 1.2  # road miles / great-circle miles, a standard freight approximation

# Major US freight cities. Coordinates are fixed facts (decimal degrees).
CITY_COORDS: dict[str, tuple[float, float]] = {
    "atlanta, ga": (33.749, -84.388),
    "dallas, tx": (32.7767, -96.7970),
    "fort worth, tx": (32.7555, -97.3308),
    "houston, tx": (29.7604, -95.3698),
    "san antonio, tx": (29.4241, -98.4936),
    "austin, tx": (30.2672, -97.7431),
    "el paso, tx": (31.7619, -106.4850),
    "laredo, tx": (27.5306, -99.4803),
    "amarillo, tx": (35.2220, -101.8313),
    "lubbock, tx": (33.5779, -101.8552),
    "chicago, il": (41.8781, -87.6298),
    "kansas city, mo": (39.0997, -94.5786),
    "st. louis, mo": (38.6270, -90.1994),
    "los angeles, ca": (34.0522, -118.2437),
    "ontario, ca": (34.0633, -117.6509),
    "oakland, ca": (37.8044, -122.2712),
    "sacramento, ca": (38.5816, -121.4944),
    "stockton, ca": (37.9577, -121.2908),
    "fresno, ca": (36.7378, -119.7871),
    "san diego, ca": (32.7157, -117.1611),
    "memphis, tn": (35.1495, -90.0490),
    "nashville, tn": (36.1627, -86.7816),
    "knoxville, tn": (35.9606, -83.9207),
    "indianapolis, in": (39.7684, -86.1581),
    "columbus, oh": (39.9612, -82.9988),
    "cincinnati, oh": (39.1031, -84.5120),
    "cleveland, oh": (41.4993, -81.6944),
    "toledo, oh": (41.6528, -83.5379),
    "charlotte, nc": (35.2271, -80.8431),
    "raleigh, nc": (35.7796, -78.6382),
    "greensboro, nc": (36.0726, -79.7920),
    "phoenix, az": (33.4484, -112.0740),
    "denver, co": (39.7392, -104.9903),
    "pueblo, co": (38.2544, -104.6091),
    "salt lake city, ut": (40.7608, -111.8910),
    "seattle, wa": (47.6062, -122.3321),
    "spokane, wa": (47.6588, -117.4260),
    "portland, or": (45.5152, -122.6784),
    "boise, id": (43.6150, -116.2023),
    "minneapolis, mn": (44.9778, -93.2650),
    "oklahoma city, ok": (35.4676, -97.5164),
    "tulsa, ok": (36.1540, -95.9928),
    "little rock, ar": (34.7465, -92.2896),
    "new orleans, la": (29.9511, -90.0715),
    "jacksonville, fl": (30.3322, -81.6557),
    "miami, fl": (25.7617, -80.1918),
    "orlando, fl": (28.5383, -81.3792),
    "tampa, fl": (27.9506, -82.4572),
    "ocala, fl": (29.1872, -82.1401),
    "lakeland, fl": (28.0395, -81.9498),
    "savannah, ga": (32.0809, -81.0912),
    "birmingham, al": (33.5186, -86.8104),
    "louisville, ky": (38.2527, -85.7585),
    "lexington, ky": (38.0406, -84.5037),
    "detroit, mi": (42.3314, -83.0458),
    "grand rapids, mi": (42.9634, -85.6681),
    "pittsburgh, pa": (40.4406, -79.9959),
    "harrisburg, pa": (40.2732, -76.8867),
    "allentown, pa": (40.6084, -75.4902),
    "philadelphia, pa": (39.9526, -75.1652),
    "newark, nj": (40.7357, -74.1724),
    "new york, ny": (40.7128, -74.0060),
    "albany, ny": (42.6526, -73.7562),
    "buffalo, ny": (42.8864, -78.8784),
    "boston, ma": (42.3601, -71.0589),
    "bangor, me": (44.8012, -68.7778),
    "baltimore, md": (39.2904, -76.6122),
    "washington, dc": (38.9072, -77.0369),
    "richmond, va": (37.5407, -77.4360),
    "columbia, sc": (34.0007, -81.0348),
    "charleston, sc": (32.7765, -79.9311),
    "des moines, ia": (41.5868, -93.6250),
    "omaha, ne": (41.2565, -95.9345),
    "wichita, ks": (37.6872, -97.3301),
    "albuquerque, nm": (35.0844, -106.6504),
    "las vegas, nv": (36.1699, -115.1398),
    "reno, nv": (39.5296, -119.8138),
    "billings, mt": (45.7833, -108.5007),
    "fargo, nd": (46.8772, -96.7898),
    "sioux falls, sd": (43.5446, -96.7311),
    "milwaukee, wi": (43.0389, -87.9065),
    "green bay, wi": (44.5133, -88.0133),
}

# Fallback so an unrecognised "City, ST" never crashes the demo: use state centroid.
STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    "al": (32.8, -86.8), "ar": (34.9, -92.4), "az": (34.3, -111.7), "ca": (37.2, -119.3),
    "co": (39.0, -105.5), "ct": (41.6, -72.7), "dc": (38.9, -77.0), "de": (39.0, -75.5),
    "fl": (28.6, -82.4), "ga": (32.6, -83.4), "ia": (42.0, -93.5), "id": (44.2, -114.5),
    "il": (40.0, -89.2), "in": (39.9, -86.3), "ks": (38.5, -98.4), "ky": (37.5, -85.3),
    "la": (31.0, -92.0), "ma": (42.3, -71.8), "md": (39.0, -76.8), "me": (45.4, -69.2),
    "mi": (44.3, -85.4), "mn": (46.3, -94.3), "mo": (38.4, -92.5), "ms": (32.7, -89.7),
    "mt": (46.9, -110.0), "nc": (35.6, -79.4), "nd": (47.5, -100.5), "ne": (41.5, -99.8),
    "nh": (43.7, -71.6), "nj": (40.1, -74.7), "nm": (34.4, -106.1), "nv": (39.3, -116.6),
    "ny": (42.9, -75.5), "oh": (40.3, -82.8), "ok": (35.6, -97.5), "or": (44.0, -120.5),
    "pa": (40.9, -77.8), "ri": (41.7, -71.5), "sc": (33.9, -80.9), "sd": (44.4, -100.2),
    "tn": (35.9, -86.4), "tx": (31.5, -99.3), "ut": (39.3, -111.7), "va": (37.5, -78.9),
    "vt": (44.1, -72.7), "wa": (47.4, -120.5), "wi": (44.6, -89.9), "wv": (38.6, -80.6),
    "wy": (43.0, -107.6),
}


def normalize_city(city_st: str) -> str:
    # Canonicalize to 'city, st': collapse whitespace, then force exactly one
    # space after the comma so 'Baltimore,MD' and 'Baltimore,  MD' both match.
    s = " ".join(city_st.strip().lower().split())
    s = re.sub(r"\s*,\s*", ", ", s)
    return s


@lru_cache(maxsize=4096)
def get_coords(city_st: str) -> tuple[float, float] | None:
    """Resolve 'City, ST' -> (lat,lng). Falls back to state centroid, then None."""
    if not city_st:
        return None
    key = normalize_city(city_st)
    if key in CITY_COORDS:
        return CITY_COORDS[key]
    if "," in key:
        st = key.split(",")[-1].strip()
        if st in STATE_CENTROIDS:
            return STATE_CENTROIDS[st]
    return None


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in miles."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def road_dist(a_city: str, b_city: str) -> float | None:
    """Estimated road miles between two 'City, ST' strings, or None if unknown."""
    ca, cb = get_coords(a_city), get_coords(b_city)
    if ca is None or cb is None:
        return None
    return round(haversine(ca, cb) * CIRCUITY, 1)
