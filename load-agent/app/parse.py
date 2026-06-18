"""
ETL: parse a load board (CSV / PDF / free-text TXT) -> normalized rows -> SQLite.

Robustness (per brief): real load data is messy and multi-format. The organizer's
TXT export interleaves THREE different free-text record layouts (dotted-label
blocks, '=== Load Ref ===' blocks, and 'L-xxxx | A -> B' pipe blocks) in rotation.
Rather than special-case each, we use an anchor-and-extract strategy: split the
file on Load-ID boundaries, then pull every field with a battery of patterns that
each cover one or more layouts. A field is found if ANY pattern matches, so a
fourth layout the organizer invents tomorrow still mostly survives. The CSV/PDF
table path is unchanged and anchors on L-\\d{7} + the known equipment vocabulary.
"""
from __future__ import annotations
import csv, re, sqlite3, os
from .geo import normalize_city, road_dist

EQUIP_SET = ["Dry Van", "Reefer", "Flatbed", "Step Deck"]
EQUIP_RE = "|".join(re.escape(e) for e in EQUIP_SET)
LOADID_RE = re.compile(r"L-\d{7}")
# Known requirement flags; prefix-matched so 'Liftga' -> 'Liftgate'.
REQ_FLAGS = ["Hazmat", "Team", "Liftgate"]

# One full record on a single (possibly re-joined) line.
ROW_RE = re.compile(
    r"(?P<id>L-\d{7})\s+"
    r"(?P<origin>[A-Za-z .'\-]+,\s*[A-Z]{2})\s+"
    r"(?P<dest>[A-Za-z .'\-]+,\s*[A-Z]{2})\s+"
    r"(?P<equip>" + EQUIP_RE + r")\s+"
    r"(?P<pu>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<miles>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<weight>[\d,]+)\s+"
    r"\$\s*(?P<rate>[\d,]+)\s+"
    r"(?P<rpm>[\d.]+)\s*"
    r"(?P<req>.*)$"
)


def _num(s: str) -> float:
    return float(str(s).replace(",", "").replace("$", "").strip())


def parse_reqs(raw: str) -> list[str]:
    """Map a messy/truncated Req string to canonical flags via prefix match."""
    raw = (raw or "").strip()
    if not raw or raw == "-" or raw.lower() == "none":
        return []
    out = []
    for tok in re.split(r"[,/]", raw):
        tok = tok.strip()
        if not tok or tok.lower() == "none":
            continue
        for flag in REQ_FLAGS:
            if flag.lower().startswith(tok.lower()) or tok.lower().startswith(flag.lower()):
                if flag not in out:
                    out.append(flag)
                break
    return out


def _record(d: dict) -> dict | None:
    try:
        origin = re.sub(r"\s*,\s*", ", ", re.sub(r"\s+", " ", d["origin"]).strip())
        dest = re.sub(r"\s*,\s*", ", ", re.sub(r"\s+", " ", d["dest"]).strip())
        miles = round(_num(d["miles"]))
        rate = round(_num(d["rate"]))
        if miles <= 0 or rate <= 0:
            return None
        return {
            "load_id": d["id"],
            "origin": origin, "origin_key": normalize_city(origin),
            "dest": dest, "dest_key": normalize_city(dest),
            "equip": d["equip"].strip(), "pu_date": d["pu"],
            "miles": miles, "weight": round(_num(d["weight"])),
            "rate": rate, "posted_rpm": round(rate / miles, 2),
            "reqs": ",".join(parse_reqs(d.get("req", ""))),
        }
    except (ValueError, KeyError):
        return None


def parse_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rec = _record({
                "id": r.get("Load ID", ""), "origin": r.get("Origin", ""),
                "dest": r.get("Destination", ""), "equip": r.get("Equip", ""),
                "pu": r.get("PU Date", ""), "miles": r.get("Miles", ""),
                "weight": r.get("Weight", ""), "rate": r.get("Rate", ""),
                "req": r.get("Req", ""),
            })
            if rec:
                rows.append(rec)
    return rows


def parse_pdf(path: str) -> list[dict]:
    import pdfplumber
    rows, buf = [], ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                # Re-join wrapped continuation lines onto the open record.
                if LOADID_RE.match(line.strip()):
                    if buf:
                        m = ROW_RE.search(buf)
                        if m:
                            rec = _record(m.groupdict())
                            if rec:
                                rows.append(rec)
                    buf = line.strip()
                elif buf:
                    buf += " " + line.strip()
        if buf:
            m = ROW_RE.search(buf)
            if m:
                rec = _record(m.groupdict())
                if rec:
                    rows.append(rec)
    return rows


# --- Free-text intake -------------------------------------------------------
# City+state, tolerant of "Baltimore,MD" (no space) and "Fort Lauderdale, FL".
_CITY = r"[A-Za-z .'\-]+?,\s*[A-Z]{2}"

# Per-field pattern batteries. First match wins; order = most-specific first.
_F_ORIGIN = [
    re.compile(r"L-\d{7}\s*\|\s*(" + _CITY + r")\s*->", re.M),     # C header
    re.compile(r"Origin\.*\s*:\s*(" + _CITY + r")", re.M),          # A
    re.compile(r"\bPU\s*:\s*(" + _CITY + r")", re.M),               # B
]
_F_DEST = [
    re.compile(r"->\s*(" + _CITY + r")", re.M),                     # C header
    re.compile(r"Destination\.*\s*:\s*(" + _CITY + r")", re.M),     # A
    re.compile(r"\bDEL\s*:\s*(" + _CITY + r")", re.M),              # B
]
_F_EQUIP = [
    re.compile(r"Equip(?:ment)?\.*\s*:?\s*(" + EQUIP_RE + r")", re.M),
    re.compile(r"(" + EQUIP_RE + r")"),  # vocab fallback (no commodity collides)
]
_F_PU = [
    re.compile(r"Pickup\.*\s*:\s*(\d{4}-\d{2}-\d{2})", re.M),       # A
    re.compile(r"\bPU\s*:[^|\n]*\|\s*(\d{4}-\d{2}-\d{2})", re.M),   # B
    re.compile(r"\bpu\s+(\d{4}-\d{2}-\d{2})", re.M),                # C
    re.compile(r"(\d{4}-\d{2}-\d{2})"),                            # earliest-date fallback
]
_F_MILES = [
    re.compile(r"Miles\.*\s*:\s*([\d,]+(?:\.\d+)?)", re.M),         # A
    re.compile(r"([\d,]+)\s*mi\b"),                                # B "279 mi" / C "398mi"
]
_F_WEIGHT = [
    re.compile(r"/\s*([\d,]+)\s*lbs", re.I),                        # A
    re.compile(r"([\d,]+)\s*#"),                                   # B "34,200#"
    re.compile(r"\bwt\s*([\d,]+)", re.I),                           # C
]
_F_RATE = [
    re.compile(r"Rate\.*\s*:\s*\$\s*([\d,]+)", re.M),              # A
    re.compile(r"\bPay\s+\$\s*([\d,]+)", re.M),                     # B
    re.compile(r"\brate\s+\$\s*([\d,]+)", re.M),                    # C
    re.compile(r"\$\s*([\d,]+)"),                                  # first-$ fallback
]
_F_REQ = [
    re.compile(r"Requirements\s*:\s*(.+)", re.M),                   # A
    re.compile(r"Notes\s*:\s*(.+)", re.M),                          # B
    re.compile(r"req\[([^\]]*)\]", re.I),                           # C
]


def _first(patterns, block: str) -> str | None:
    for pat in patterns:
        m = pat.search(block)
        if m:
            return m.group(1).strip()
    return None


def _split_records(text: str) -> list[str]:
    """Segment on Load-ID boundaries. Each layout names the ID once, at the top,
    so 'every line bearing an L-id starts a record' is format-agnostic."""
    blocks, cur = [], []
    for line in text.splitlines():
        if LOADID_RE.search(line) and cur:
            blocks.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return [b for b in blocks if LOADID_RE.search(b)]


def parse_text_records(path: str) -> list[dict]:
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    rows = []
    for block in _split_records(text):
        mid = LOADID_RE.search(block)
        req_raw = _first(_F_REQ, block) or ""
        rec = _record({
            "id": mid.group(0),
            "origin": _first(_F_ORIGIN, block) or "",
            "dest": _first(_F_DEST, block) or "",
            "equip": _first(_F_EQUIP, block) or "",
            "pu": _first(_F_PU, block) or "",
            "miles": _first(_F_MILES, block) or "0",
            "weight": _first(_F_WEIGHT, block) or "0",
            "rate": _first(_F_RATE, block) or "0",
            "req": req_raw,
        })
        if rec:
            rows.append(rec)
    return rows


def _is_delimited_table(path: str) -> bool:
    """A .txt that is really a CSV/TSV export has a comma/tab header naming the
    columns. Free-text exports don't. Sniff the first few non-empty lines."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = [ln for ln in (next(f, "") for _ in range(8)) if ln.strip()]
    except OSError:
        return False
    for ln in head:
        low = ln.lower()
        if ("load id" in low or "load_id" in low) and (
            ln.count(",") >= 5 or ln.count("\t") >= 5):
            return True
    return False


def parse_any(path: str) -> list[dict]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return parse_pdf(path)
    if ext in (".csv", ".tsv"):
        return parse_csv(path)
    if ext in (".txt", ".text", ""):
        # Could be a delimited export saved as .txt, or a free-text dump.
        return parse_csv(path) if _is_delimited_table(path) else parse_text_records(path)
    return parse_csv(path)


def build_db(rows: list[dict], db_path: str = ":memory:") -> sqlite3.Connection:
    """Normalize rows into an indexed SQLite `loads` table. Scales to 100k+ rows."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        DROP TABLE IF EXISTS loads;
        CREATE TABLE loads (
            load_id TEXT PRIMARY KEY, origin TEXT, origin_key TEXT,
            dest TEXT, dest_key TEXT, equip TEXT, pu_date TEXT,
            miles INTEGER, weight INTEGER, rate INTEGER, posted_rpm REAL, reqs TEXT
        );
    """)
    conn.executemany(
        """INSERT OR IGNORE INTO loads VALUES
           (:load_id,:origin,:origin_key,:dest,:dest_key,:equip,:pu_date,
            :miles,:weight,:rate,:posted_rpm,:reqs)""", rows)
    conn.executescript("""
        CREATE INDEX idx_origin ON loads(origin_key);
        CREATE INDEX idx_dest   ON loads(dest_key);
        CREATE INDEX idx_equip  ON loads(equip);
    """)
    conn.commit()
    return conn


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "data/load_board.pdf"
    rows = parse_any(p)
    print(f"parsed {len(rows)} rows from {p}")
    for r in rows[:3]:
        print(r)
