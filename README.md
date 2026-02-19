# Stock Screener — Intraday SMS Picks

Runs each weekday morning and sends an SMS by 10 am ET with two intraday
trade candidates ranked by conviction.

## How it works

```
9:45 ET → screen → score → SMS
```

| Stage | What happens |
|-------|-------------|
| **SPY gate** | SPY must be positive. If SPY is red, you get a no-picks SMS. |
| **Gap screen** | Finviz screener pulls stocks gapping ≥ 2% on the day. |
| **Catalyst check** | Yahoo Finance news is scanned for earnings, FDA, analyst upgrade, or M&A headlines. |
| **Relative volume** | Today's volume (since open) vs. the 20-day average at the same elapsed time. Must be ≥ 2×. |
| **Scoring** | Each candidate is scored across 6 weighted sub-factors (see below). |
| **SMS** | Top 2 picks sent via Twilio with ticker, catalyst, relvol, entry zone, stop-loss, and ranking reason. |

## Quick start

### 1. Clone and install

```bash
git clone <repo-url>
cd stock_recommender
pip install -r requirements.txt
```

Requires Python 3.9+.

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your Twilio credentials and phone numbers
```

Required `.env` values:

```
TWILIO_ACCOUNT_SID=ACxxxx
TWILIO_AUTH_TOKEN=xxxx
TWILIO_FROM_NUMBER=+1555...
TWILIO_TO_NUMBER=+1555...
```

Optional (for real-time quotes):

```
ALPACA_API_KEY=PKxxxx
ALPACA_SECRET_KEY=xxxx
```

### 3. Test without sending SMS

```bash
python main.py --now --dry-run
```

This runs the full pipeline and prints the SMS text to stdout.

### 4. Run once (send real SMS)

```bash
python main.py --now
```

### 5. Start the built-in scheduler

```bash
python main.py --schedule
```

Keeps the process alive and fires at 9:45 ET every weekday.

### 6. Production: system cron (recommended)

Add this to your crontab (`crontab -e`):

```cron
# Fire at 9:45 ET (adjust for your server timezone)
45 9 * * 1-5 cd /path/to/stock_recommender && python main.py --now >> logs/screener.log 2>&1
```

If your server is in UTC:

```cron
45 14 * * 1-5 cd /path/to/stock_recommender && python main.py --now >> logs/screener.log 2>&1
```

## Tuning the scoring weights

All weights live in `config.py` under `ScoringWeights`. Increase a weight to
make that factor dominate the ranking:

```python
@dataclass
class ScoringWeights:
    gap_pct: float = 2.0          # size of the gap
    rel_vol: float = 2.5          # relative volume (default: most important)
    catalyst_quality: float = 2.0 # earnings > fda > acquisition > upgrade
    spy_tailwind: float = 0.5     # SPY strength bonus
    low_float_bonus: float = 0.5  # smaller float = bigger moves
    gap_held: float = 1.5         # bonus for not fading the gap
```

**Example: prioritise catalyst quality above all else**

```python
WEIGHTS = ScoringWeights(
    gap_pct=1.0,
    rel_vol=1.5,
    catalyst_quality=4.0,   # ← dominant factor
    spy_tailwind=0.5,
    low_float_bonus=0.3,
    gap_held=1.0,
)
```

Other tunable thresholds in `config.py`:

| Constant | Default | Meaning |
|----------|---------|---------|
| `MIN_GAP_PCT` | `0.02` | 2% minimum gap |
| `MIN_REL_VOL` | `2.0` | 2× minimum relative volume |
| `MIN_SPY_PCT` | `0.0` | SPY must be at least flat |
| `RELVOL_LOOKBACK_DAYS` | `20` | Days used to compute average volume |
| `STOP_LOSS_BELOW_LOW_PCT` | `0.02` | Stop 2% below day's low |
| `ENTRY_BUFFER_PCT` | `0.005` | ±0.5% entry zone |
| `NUM_PICKS` | `2` | Number of picks in SMS |

## SMS example

```
STOCK PICKS — Wed Feb 19

SPY: +0.42%

PICK 1 (Primary): NVDA
Catalyst: Earnings Beat — quarterly EPS smashed estimates by 18%
RelVol: 4.2x | Gap: +6.8%
Entry: $875.50–$884.25
Stop: $850.10
Why: Ranked #1 for a high-conviction Earnings Beat catalyst paired
with 4.2x relative volume — the strongest setup today.

PICK 2 (Backup): MRNA
Catalyst: FDA Catalyst — BLA approval for new mRNA therapy
RelVol: 3.1x | Gap: +5.2%
Entry: $95.20–$96.15
Stop: $88.40
Why: Ranked #2 as the strongest backup: 5.2% gap on 3.1x volume
with an FDA Catalyst — offers diversification if #1 stalls.

Generated 9:47 AM ET
```

## File overview

```
stock_recommender/
├── main.py           # Entry point — CLI + built-in scheduler
├── screener.py       # Filter pipeline (gap, catalyst, relvol, SPY gate)
├── scorer.py         # Weighted conviction scoring + rank-reason builder
├── data_sources.py   # Yahoo Finance, Finviz scraper, Alpaca integration
├── sms_sender.py     # Twilio SMS sender + message formatter
├── config.py         # All thresholds, weights, and keyword lists
├── requirements.txt
└── .env.example      # Credential template
```

## Data sources

| Source | What it provides | Cost |
|--------|-----------------|------|
| Finviz screener | Gap-up ticker list | Free (web scrape) |
| Yahoo Finance (yfinance) | OHLCV, news headlines | Free |
| Alpaca Markets | Real-time quotes (optional) | Free paper-trading tier |
| Twilio | SMS delivery | ~$0.0079/message |

## Notes on limitations

- **Finviz scraping**: Finviz may return stale data or change its HTML layout.
  If `get_gappers()` returns empty, check Finviz manually and inspect the logs.
- **yfinance latency**: During the first few minutes after 9:30, 1-minute bars
  may not yet be populated. The 9:45 run time gives data time to settle.
- **Relative volume accuracy**: The relvol calculation fetches 20 days of
  1-minute bars from yfinance on each run (~1-2 seconds per ticker). For large
  candidate lists this adds up; `MAX_CANDIDATES` in `config.py` caps the list.
