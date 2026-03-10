# Stock Screener — Daily Email Picks

Runs each weekday morning at 8:30 AM ET via GitHub Actions and emails two intraday trade candidates ranked by conviction.

## How it works

```
8:30 ET → screen → score → email
```

| Stage | What happens |
|-------|-------------|
| **Reddit scan** | Hot posts from r/wallstreetbets, r/stocks, r/options, r/pennystocks scanned for ticker mentions |
| **StockTwits** | Top 30 trending symbols pulled from public API |
| **SEC EDGAR** | Recent 8-K filings checked for earnings, FDA, M&A, and deal catalysts |
| **Merge & score** | All three sources merged; tickers scored across 8 weighted factors |
| **Email** | Top 2 picks sent to cfiess@gmail.com with catalyst, sentiment, sources, and ranking reason |

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
# Edit .env with your Gmail credentials
```

Required `.env` values:

```
GMAIL_USER=your.address@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

Get an App Password at https://myaccount.google.com/apppasswords (requires 2-Step Verification).

### 3. Run manually

```bash
cd /home/user/stock_recommender
python main.py
```

### 4. Other options

```bash
python main.py --dry-run      # preview email in terminal, don't send
python main.py --verbose      # show scoring detail for each pick
python main.py --picks 3      # get 3 picks instead of 2
```

## GitHub Actions (automated daily run)

The workflow at `.github/workflows/daily-picks.yml` fires at **8:30 AM ET (12:30 UTC)** on weekdays.

Required GitHub secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|--------|-------|
| `GMAIL_USER` | your Gmail address |
| `GMAIL_APP_PASSWORD` | 16-char App Password from Google |

To trigger a run manually: GitHub → Actions → Daily Stock Picks → Run workflow.

## Tuning the scoring weights

All weights live in `config.py` under `SignalWeights`. Increase a weight to make that factor dominate the ranking:

```python
@dataclass
class SignalWeights:
    sec_catalyst:  float = 3.5   # hard 8-K catalyst (earnings, FDA, M&A)
    cross_source:  float = 2.5   # ticker confirmed by multiple sources
    wsb_mentions:  float = 2.0   # r/wallstreetbets mention count
    stocktwits_rank: float = 2.0 # StockTwits trending rank
    st_bullish:    float = 1.5   # % of StockTwits messages tagged bullish
    reddit_sentiment: float = 1.2
    reddit_quality:   float = 1.0
    news_sentiment:   float = 1.0
```

Other tunable thresholds in `config.py`:

| Constant | Default | Meaning |
|----------|---------|---------|
| `NUM_PICKS` | `2` | Number of picks in email |
| `MAX_CANDIDATES` | `15` | Max candidates to enrich (limits API calls) |
| `MIN_SOURCES` | `1` | Min signal sources to qualify (set to 2 for cross-confirmation) |
| `REDDIT_LOOKBACK_HOURS` | `8` | Hours back to scan Reddit |
| `SEC_LOOKBACK_HOURS` | `24` | Hours back to scan SEC 8-K filings |

## File overview

```
stock_recommender/
├── main.py           # Entry point — CLI + built-in scheduler
├── screener.py       # Screening pipeline — gathers and merges signals
├── scorer.py         # Weighted conviction scoring + rank-reason builder
├── signals.py        # Reddit, StockTwits, SEC EDGAR, Yahoo Finance data fetchers
├── email_sender.py   # Gmail SMTP sender + HTML/plain-text email formatter
├── config.py         # All thresholds, weights, and keyword lists
├── requirements.txt
└── .env.example      # Credential template
```

## Data sources

| Source | What it provides | Cost |
|--------|-----------------|------|
| Reddit (public JSON) | Ticker mention velocity across WSB, r/stocks, r/options, r/pennystocks | Free |
| StockTwits (public API) | Trending symbols + bullish/bearish sentiment | Free |
| SEC EDGAR | Recent 8-K filings (earnings, FDA, M&A, deals) | Free |
| Yahoo Finance | Company names, news headlines, catalyst classification | Free |
| Gmail SMTP | Email delivery | Free |
