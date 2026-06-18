# Load Profitability Agent — Backend

Buildathon Dallas, Statement 6. Given a driver's location, equipment, and capabilities,
recommend the **most profitable load available right now — net, not gross** — and rank
the **top US markets to start/reposition a carrier**.

**Core principle: deterministic math for money, the LLM only for interface + explanation.**
Every dollar a recommendation reports is computed by the cost engine and traceable to its
inputs. The LLM never produces a number that the engine didn't return.

---

## Why this isn't a chatbot

It's a decision engine with a thin NL shell. Three non-obvious things make it credible:

1. **Ranks by net $/day, not posted $/mi.** A short high-RPM haul looks great and pays
   poorly per day once fixed costs and deadhead are counted.
2. **Dead-market penalty, computed from the board's own supply.** A load that drops the
   truck in a weak market is charged the expected empty miles to the nearest strong hub,
   priced at the carrier's variable cost. Inbound deadhead is *measured* from geo;
   outbound deadhead is *modeled* from lane balance. That's "deadhead in both directions."
3. **Margins are realistically thin (often negative).** Small-carrier net margins run
   3–6%. If every load looked profitable, the cost model would be wrong.

Example (Dallas, dry van): **Dallas→Buffalo posts $2.56/mi but loses to Dallas→Milwaukee
at $2.26/mi** on net/day, because Buffalo (market score 23.7/100) costs ~$673 to escape.

---

## Architecture

```
parse.py    ETL: auto-detect CSV / PDF / free-text TXT -> anchored parse -> indexed SQLite
geo.py      static US city -> (lat,lng); haversine x 1.2 circuity. No external geo dep.
market.py   market_score, lane_balance, nearest-strong-hub, dest_penalty
cost.py     deterministic net_profit + net_per_day, full breakdown, pay-model toggle
rank.py     hard eligibility gates -> score -> rank by net/day
diesel.py   live regional diesel via Tavily (cited), TTL-cached, safe fallback
agent.py    free-text -> params (rule-based; Gemini optional) + plain-English "why"
main.py     FastAPI: /recommend, /markets/top, /ask, /health
```

Money flows one direction: parse → engine → rank → (agent explains). The agent is the
only place an LLM touches anything, and it's fenced to the engine's numbers.

---

## Run locally

```bash
pip install -r requirements.txt
python data/generate_synthetic.py        # makes data/loads.csv + data/load_board.pdf
uvicorn app.main:app --reload --port 8000
# http://localhost:8000/docs
```

Point at the real organizer dataset (CSV, PDF, or TXT) by setting `LOAD_DATA_PATH`:

```bash
LOAD_DATA_PATH=/path/to/loads_part_000.txt uvicorn app.main:app --port 8000
```

The parser auto-detects format by extension and content:

- **CSV / TSV** — lossless column read.
- **PDF** — anchors on `L-\d{7}` and the known equipment set; re-joins wrapped
  rows; survives `$ 1,700`, comma'd numbers, decimal miles, and truncated `Req`
  values like `Team, Liftga`.
- **TXT (free text)** — the organizer's `.txt` export interleaves *three* different
  record layouts in rotation (dotted-label `LOAD` blocks, `=== Load Ref ===`
  blocks, and `L-xxxx | A -> B` pipe blocks). Rather than special-case each, the
  parser splits on Load-ID boundaries and extracts every field with a battery of
  patterns covering all layouts — so a fourth layout still mostly survives. A
  `.txt` that is really a delimited export (CSV saved as `.txt`) is detected by its
  header and read as a table instead.

Verified on the real organizer files: **8,000 / 8,000 records (100%)** recovered
from `loads_part_000.txt` with every field populated, and 200/200 from the broker
PDF. City strings without a space after the comma (`Baltimore,MD`) are canonicalized
so they geocode to the same point as `Baltimore, MD` — important, since one of the
three text layouts omits that space and it silently breaks deadhead math otherwise.

## Deploy (Render)

`render.yaml` is included. Push to GitHub, create a Render web service from the repo. Set
`TAVILY_API_KEY` (live diesel) and `GEMINI_API_KEY` (NL phrasing) as env vars — both are
**optional**; without them the app uses a stated default diesel price and a deterministic
template explanation, so the demo never hard-fails on conference wifi.

---

## API

### `POST /recommend`
```json
{
  "location": "Dallas, TX",
  "equipment": "Dry Van",
  "capabilities": ["Hazmat"],
  "available_date": "2026-06-15",
  "pay_model": "carrier",
  "cost_overrides": { "diesel_price": 4.25 },
  "top_n": 10
}
```
Returns ranked loads. Each carries a full breakdown: deadhead miles, per-CPM cost
components, operating cost, idle cost, dest-market score + reposition penalty, net profit,
net/day, margin %, and the owner-operator vs carrier decomposition.

### `GET /markets/top?n=10`
Top markets to start/reposition a carrier, with outbound count, inbound count, lane
balance, avg outbound RPM, and a 0–100 score. Powers the heatmap/table view.

### `POST /ask`
```json
{ "query": "I'm in Ontario CA with a reefer, no hazmat — what should I grab?",
  "pay_model": "owner_operator" }
```
Parses free text, runs the engine, and explains why the top pick beats the runner-up.

### `GET /health`
Liveness + dataset stats.

---

## Demo script (real load IDs from the synthetic board)

1. **Dallas, dry van, available now.** #1 = Dallas→Milwaukee (+$199, 8.6%, $55/day). It
   beats Dallas→Buffalo despite Buffalo's higher posted $/mi — show the $673 dead-market
   penalty doing the work.
2. **Reefer in Ontario, CA.** #1 = Ontario→Houston reefer (+$1,614, $440/day). Show
   equipment filtering, the reefer cost adder, and a strong CA outbound market.
3. **Top-10 markets.** DFW / Atlanta / Memphis / Indy / Houston / Ontario on top; Florida
   interior, Sioux Falls, NYC, Oakland flagged as traps (low lane balance).
4. **Stress it live:** flip `pay_model` to `owner_operator` (margin → take-home), and push
   `diesel_price` to $5.25 to watch the #1 pick fall to breakeven — all auditable.

---

## Cost model (defaults — tunable per request via `cost_overrides`)

| Component | Default | Notes |
|---|---|---|
| diesel | $3.95/gal | live via Tavily when key set |
| mpg | 6.5 dry van / 6.0 other | |
| driver_cpm | $0.62/mi | cost (carrier) or take-home (owner-op) |
| maint / insurance | $0.18 / $0.10 | |
| tolls | $0.04/mi | ×2.5 on Northeast lanes |
| overhead | $0.30/mi | truck payment, ELD, dispatch, factoring |
| reefer adder | +$0.07/mi | reefer loads only |
| daily_fixed | $225/day | idle-day cost only |

Variable cost lands ~$1.85/mi, anchored against ATRI's ~$2.27/mi marginal cost (2023).

## Honesty guarantees (the builder's standing rule)
- No fabricated numbers; every output is computed and shown with its breakdown.
- The LLM is fenced to engine outputs and cannot introduce a figure.
- Diesel price carries its provenance string (live+cited / cached / stated default).
- `data/` is clearly synthetic test data; swap in the real board with one env var.

## Notes / open items
- Driver-pay model defaults to `carrier`; toggle per request.
- `dest_penalty` is empty-miles-to-nearest-hub. A hook for an expected-idle-days term
  exists in `market.py` but is intentionally off (avoids double-counting / over-tuning).
- Synthetic generator uses `reportlab` (dev only) to build the messy demo PDF.
