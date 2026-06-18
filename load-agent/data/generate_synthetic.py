"""
Generate a realistic synthetic load board for development/demo, matching the schema
in the build brief. Emits BOTH:
  - loads.csv         (clean path)
  - load_board.pdf    (messy path: truncated Req, comma'd numbers, the documented header)

This is TEST DATA so the pipeline runs before the real organizer dataset lands.
Rates are calibrated so that, after the cost engine runs, good picks net positive and
loads dumping into dead markets net negative -- i.e. thin/realistic margins, not a toy.
Swap in the real Drive file and nothing downstream changes.
"""
from __future__ import annotations
import csv, random, sys, os
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.geo import CITY_COORDS, road_dist

random.seed(7)

EQUIP = ["Dry Van", "Reefer", "Flatbed", "Step Deck"]
BASE_RPM = {"Dry Van": 2.05, "Reefer": 2.45, "Flatbed": 2.35, "Step Deck": 2.45}

# Outbound supply tiers (how easy to GET a load leaving here) and inbound pull.
STRONG = ["atlanta, ga", "dallas, tx", "fort worth, tx", "chicago, il", "kansas city, mo",
          "ontario, ca", "los angeles, ca", "houston, tx", "memphis, tn", "indianapolis, in",
          "columbus, oh", "charlotte, nc"]
MID = ["nashville, tn", "phoenix, az", "denver, co", "st. louis, mo", "oklahoma city, ok",
       "savannah, ga", "jacksonville, fl", "birmingham, al", "louisville, ky", "cincinnati, oh",
       "harrisburg, pa", "allentown, pa", "columbia, sc", "raleigh, nc", "salt lake city, ut",
       "stockton, ca", "fresno, ca", "san antonio, tx", "austin, tx", "laredo, tx", "tulsa, ok",
       "des moines, ia", "omaha, ne", "milwaukee, wi", "grand rapids, mi", "richmond, va",
       "knoxville, tn", "greensboro, nc", "lexington, ky", "toledo, oh", "wichita, ks"]
WEAK = ["ocala, fl", "bangor, me", "pueblo, co", "spokane, wa", "boise, id", "billings, mt",
        "fargo, nd", "sioux falls, sd", "green bay, wi", "buffalo, ny", "albany, ny",
        "lubbock, tx", "amarillo, tx", "el paso, tx", "reno, nv", "lakeland, fl"]

ALL = list(CITY_COORDS.keys())

def title_city(k: str) -> str:
    city, st = k.rsplit(",", 1)
    parts = [w.capitalize() if w != "st." else "St." for w in city.split()]
    return f"{' '.join(parts)},{st.upper()}"

def origin_weight(k):  # how much outbound supply originates here
    return 8 if k in STRONG else 3 if k in MID else 1
def dest_weight(k):    # inbound pull -- weak markets suck freight IN
    return 5 if k in WEAK else 4 if k in MID else 3 if k in STRONG else 3

def make_rows(n=2500):
    origins = ALL; o_w = [origin_weight(k) for k in origins]
    dests = ALL;   d_w = [dest_weight(k) for k in dests]
    rows = []
    today = date(2026, 6, 15)
    for i in range(1, n + 1):
        o = random.choices(origins, weights=o_w)[0]
        d = random.choices(dests, weights=d_w)[0]
        if d == o:
            continue
        miles = road_dist(o, d)
        if miles is None or miles < 80:
            continue
        equip = random.choice(EQUIP)
        rpm = BASE_RPM[equip] + random.uniform(-0.35, 0.35)
        if miles > 1200: rpm -= 0.12          # long hauls pay less per mile
        if d in WEAK: rpm += random.uniform(0.10, 0.45)  # pay UP to drag a truck into a trap
        rate = round(miles * rpm)
        weight = random.choice([random.randint(2000, 44000),
                                random.randint(2000, 44000),
                                random.randint(45001, 48000)])  # ~ some overweight
        reqs = []
        if random.random() < 0.10: reqs.append("Hazmat")
        if random.random() < 0.08: reqs.append("Team")
        if random.random() < 0.12: reqs.append("Liftgate")
        req = ", ".join(reqs) if reqs else "-"
        pu = today + timedelta(days=random.randint(0, 3))
        rows.append({
            "Load ID": f"L-{i:07d}",
            "Origin": title_city(o), "Destination": title_city(d),
            "Equip": equip, "PU Date": pu.isoformat(),
            "Miles": miles, "Weight": weight, "Rate": rate,
            "$/mi": round(rate / miles, 2), "Req": req,
        })
    return rows

def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["Miles"] = f"{r['Miles']:,.0f}"; r["Weight"] = f"{r['Weight']:,}"
            r["Rate"] = f"${r['Rate']:,}"
            w.writerow(r)

def write_pdf(rows, path):
    """Messy PDF: monospace rows, comma'd numbers, some truncated Req (mimics export bug)."""
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    c = canvas.Canvas(path, pagesize=landscape(letter))
    W, H = landscape(letter)
    per_page = 40
    sheet = 1
    def header(y):
        c.setFont("Courier-Bold", 11)
        c.drawString(0.4 * inch, y, f"NATIONWIDE LOAD BOARD  -  Daily Export Sheet #{sheet:03d}")
        c.setFont("Courier", 6.5)
        c.drawString(0.4 * inch, y - 12, "Snapshot 2026-06-15")
        c.setFont("Courier-Bold", 6.5)
        c.drawString(0.4 * inch, y - 26,
                     "LoadID    Origin            Destination       Equip      PU Date    Miles  Weight   Rate    $/mi  Req")
        return y - 38
    y = header(H - 0.5 * inch)
    c.setFont("Courier", 6.5)
    cnt = 0
    for r in rows:
        if cnt and cnt % per_page == 0:
            c.showPage(); sheet += 1
            y = header(H - 0.5 * inch); c.setFont("Courier", 6.5)
        req = r["Req"]
        # simulate the documented truncation bug ("Team, Liftga")
        if req != "-" and len(req) > 7 and random.random() < 0.18:
            req = req[: random.randint(6, len(req) - 1)]  # documented "Team, Liftga" bug
        line = (f"{r['Load ID']:<9} {r['Origin']:<17.17} {r['Destination']:<17.17} "
                f"{r['Equip']:<10} {r['PU Date']} {r['Miles']:>5,} {r['Weight']:>7,} "
                f"${r['Rate']:>6,} {r['$/mi']:>5} {req}")
        c.drawString(0.4 * inch, y, line)
        y -= 9.2; cnt += 1
    c.showPage(); c.save()

if __name__ == "__main__":
    rows = make_rows(2500)
    here = os.path.dirname(os.path.abspath(__file__))
    write_csv(rows, os.path.join(here, "loads.csv"))
    write_pdf(rows, os.path.join(here, "load_board.pdf"))
    print(f"generated {len(rows)} loads -> loads.csv + load_board.pdf")
