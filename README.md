# thirteenf-scanner

Quarterly institutional initiation scanner. Monitors 13F filings from 17 curated hedge funds via SEC EDGAR. When three or more funds initiate a new position in the same stock in the same quarter, it fires a BUY signal to the IB AutoTrader.

Part of the Grist Mill Capital systematic trading infrastructure. Deployed on PythonAnywhere; runs daily with an internal filing window gate — takes no action outside of active 13F seasons.

---

## What It Detects

A stock qualifies as a signal when:

- It appears as a **new position** (not held in the previous quarter) in **3 or more** curated hedge fund 13F filings for the same quarter
- The position is **common stock only** (ETFs, bonds, preferred shares excluded)
- Each initiating position is **$1M+** in reported value
- **VIX is below 30** (kill switch — autotrader blocks execution above this level)

Signal direction: BULL only. The 4-week return is negative. The validated edge is at 13 weeks and beyond.

---

## Backtest Results

Tested on 1,848 trades across 17 curated funds, 2018–2025.

| Hold Period | Alpha   | t-stat | Significance |
|-------------|---------|--------|--------------|
| 4 weeks     | -0.77%  | —      | Do not use   |
| 13 weeks    | +5.26%  | 10.23  | ***          |
| 26 weeks    | +10.39% | 12.55  | ***          |

Deploy rules: 3+ initiators, 13-week hold, VIX kill switch at 30. Signal degrades significantly in bear markets (2022 wipes it) — the VIX kill switch provides partial protection.

---

## Filing Windows

13F filings are due within 45 days of each quarter end. The scanner only does real work during these windows:

| Quarter     | Window          |
|-------------|-----------------|
| Q1 (Mar 31) | Apr 1 – May 15  |
| Q2 (Jun 30) | Jul 1 – Aug 14  |
| Q3 (Sep 30) | Oct 1 – Nov 14  |
| Q4 (Dec 31) | Jan 1 – Feb 14  |

On days outside a window, the script exits immediately after a single log line.

---

## Architecture

```
thirteenf_scanner/
├── thirteenf_scanner.py     # Main scanner — daily PA task
├── config.py                # Live credentials (not committed)
├── config_example.py        # Template for setup
├── requirements.txt
└── thirteenf_scanner.db     # SQLite: holdings, signals, scan log (auto-created)
```

### Flow

1. Check if today is in an active 13F filing window — exit if not
2. For each of 17 curated hedge funds, pull current quarter 13F from SEC EDGAR
3. Pull previous quarter 13F for comparison
4. Map CUSIPs to tickers via OpenFIGI (free API, no key required)
5. Find stocks newly initiated (present in current quarter but not previous) by 3+ funds
6. Deduplicate: skip any ticker already signaled for this quarter
7. Check VIX — add kill switch banner to email if >= 30
8. Send `13F BULL: TICK1, TICK2` email to IB AutoTrader
9. Mark signals in DB to prevent re-sending

### SEC EDGAR Integration

No API key required. Uses the free public EDGAR data APIs:
- `data.sec.gov/submissions/CIK{n}.json` — filing history per filer
- `sec.gov/Archives/edgar/data/` — raw 13F infotable XML

Respects SEC rate limits (10 req/sec max). Configured at 0.12s per request.

### Hedge Fund Universe

17 concentrated stock-pickers with verified CIKs. Quant and multi-strategy pod shops (Renaissance, Citadel, AQR, Millennium, D.E. Shaw, Balyasny) are excluded — their "new initiations" are algorithmic rotation noise, not conviction signals.

Included funds: Berkshire Hathaway, Pershing Square, Viking Global, Coatue, Lone Pine, Third Point, Appaloosa, Maverick Capital, Greenlight Capital, Baupost Group, Glenview Capital, Farallon Capital, Carl Icahn, Tiger Global, Soros Fund Management, Point72, Paulson & Co.

---

## Setup

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Configure credentials:**
```bash
cp config_example.py config.py
# Edit config.py -- add Gmail app password and your SEC User-Agent string
```

**Test email:**
```bash
python3 thirteenf_scanner.py --test-email
```

**Check database status:**
```bash
python3 thirteenf_scanner.py --status
```

**Force a run outside the filing window (testing):**
```bash
python3 thirteenf_scanner.py --force-run --dry-run
```

**Normal daily run:**
```bash
python3 thirteenf_scanner.py
```

---

## PythonAnywhere Deployment

Schedule as a daily task at `03:00 UTC`. The filing window gate means it is a no-op approximately 230 days per year.

```
Daily at 03:00 UTC:
cd /home/KPH3802/thirteenf_scanner && python3 thirteenf_scanner.py
```

---

## IB AutoTrader Integration

The scanner sends emails with subject format:

```
13F BULL: TICK1, TICK2, TICK3
```

The IB AutoTrader (`ib_autotrader.py`) parses this subject line, places BUY orders at the next open, and tracks positions with a 91-day (13-week) hold period and a -40% catastrophic circuit breaker.

---

## Disclaimer

For research and educational purposes. Not financial advice. Past backtest performance does not guarantee future results. All trading involves risk of loss.

MIT License
