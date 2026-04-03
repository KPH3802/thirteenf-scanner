#!/usr/bin/env python3
"""
13F Institutional Initiations Scanner — Live Signal Generator
==============================================================
Runs daily on PythonAnywhere. Most days it exits immediately (not in a filing
window). During active 13F filing seasons it pulls SEC EDGAR, detects stocks
newly initiated by 3+ curated hedge funds in the same quarter, and fires a
BUY signal email to the IB AutoTrader.

Backtest results (2018-2025, 13-week hold, 3+ initiators):
  13w hold: +5.26% alpha  t=10.23***
  26w hold: +10.39% alpha t=12.55***
  4w hold:  NEGATIVE      -- do not use

Filing windows (45 days after each quarter end):
  Q1 (Mar 31) -> Apr 1 - May 15
  Q2 (Jun 30) -> Jul 1 - Aug 14
  Q3 (Sep 30) -> Oct 1 - Nov 14
  Q4 (Dec 31) -> Jan 1 - Feb 14

Usage:
  python3 thirteenf_scanner.py              # Normal daily run
  python3 thirteenf_scanner.py --test-email # Send test email
  python3 thirteenf_scanner.py --status     # Show DB stats
  python3 thirteenf_scanner.py --force-run  # Run even outside filing window (testing)
  python3 thirteenf_scanner.py --dry-run    # Detect signals, skip email
"""

import os
import sys
import json
import time
import sqlite3
import smtplib
import argparse
import traceback
import re
import urllib.request
import urllib.error
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import yfinance as yf

import config

# ============================================================
# CONSTANTS
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(SCRIPT_DIR, config.SCANNER_DB)

EDGAR_BASE        = 'https://data.sec.gov'
EDGAR_SUBMISSIONS = EDGAR_BASE + '/submissions/CIK{cik}.json'

OPENFIGI_BASE = 'https://api.openfigi.com/v3/mapping'

# Quarter end dates (month, day)
QUARTER_ENDS = [
    (3,  31),   # Q1
    (6,  30),   # Q2
    (9,  30),   # Q3
    (12, 31),   # Q4
]

# ============================================================
# FILING WINDOW LOGIC
# ============================================================
def get_quarter_end(d):
    """
    Return the most recent quarter-end date whose 45-day filing window
    includes today. Returns None if today is not in any filing window.
    """
    for years_back in range(2):
        for month, day in reversed(QUARTER_ENDS):
            try:
                qend = date(d.year - years_back, month, day)
            except ValueError:
                continue
            window_open  = qend + timedelta(days=1)
            window_close = qend + timedelta(days=config.FILING_WINDOW_DAYS)
            if window_open <= d <= window_close:
                return qend
    return None


def quarter_label(qend):
    """Return a label like '2026-Q1'."""
    q = {3: 'Q1', 6: 'Q2', 9: 'Q3', 12: 'Q4'}[qend.month]
    return '{}-{}'.format(qend.year, q)


def prev_quarter_end(qend):
    """Return the quarter-end one quarter before the given date."""
    if qend.month == 3:
        return date(qend.year - 1, 12, 31)
    elif qend.month == 6:
        return date(qend.year, 3, 31)
    elif qend.month == 9:
        return date(qend.year, 6, 30)
    else:
        return date(qend.year, 9, 30)


# ============================================================
# DATABASE
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            filer_cik     TEXT NOT NULL,
            filer_name    TEXT,
            quarter_end   TEXT NOT NULL,
            filing_date   TEXT,
            cusip         TEXT NOT NULL,
            ticker        TEXT,
            company_name  TEXT,
            security_type TEXT,
            value_usd     REAL,
            shares        INTEGER,
            PRIMARY KEY (filer_cik, quarter_end, cusip)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS emailed_signals (
            ticker        TEXT NOT NULL,
            quarter_end   TEXT NOT NULL,
            emailed_date  TEXT NOT NULL,
            initiators    INTEGER,
            filer_names   TEXT,
            PRIMARY KEY (ticker, quarter_end)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS scan_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date       TEXT,
            quarter_end     TEXT,
            in_window       INTEGER,
            filers_scanned  INTEGER,
            new_signals     INTEGER,
            email_sent      INTEGER,
            errors          TEXT
        )
    """)

    conn.commit()
    return conn


# ============================================================
# SEC EDGAR HELPERS
# ============================================================
def edgar_get(url):
    """Fetch JSON from SEC EDGAR with rate limiting."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': config.SEC_USER_AGENT,
                'Accept':     'application/json'
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        time.sleep(config.SEC_REQUEST_DELAY)
        return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print('    SEC rate limit -- sleeping 60s')
            time.sleep(60)
        else:
            print('    SEC HTTP {}: {}'.format(e.code, url))
        return None
    except Exception as e:
        print('    SEC fetch error: {}'.format(e))
        return None


def edgar_get_xml(url):
    """Fetch raw XML content from SEC EDGAR."""
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': config.SEC_USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        time.sleep(config.SEC_REQUEST_DELAY)
        return data
    except Exception as e:
        print('    XML fetch error: {}'.format(e))
        return None


def get_13f_filings(cik):
    """Return list of 13F-HR filings for a filer, sorted by filed date descending."""
    cik_padded = cik.lstrip('0').zfill(10)
    url = EDGAR_SUBMISSIONS.format(cik=cik_padded)
    data = edgar_get(url)
    if not data:
        return []

    filings = []
    recent      = data.get('filings', {}).get('recent', {})
    forms       = recent.get('form', [])
    accessions  = recent.get('accessionNumber', [])
    filed_dates = recent.get('filingDate', [])
    report_dates = recent.get('reportDate', [])

    for i, form in enumerate(forms):
        if form in ('13F-HR', '13F-HR/A'):
            filings.append({
                'accession':   accessions[i].replace('-', ''),
                'filed_date':  filed_dates[i],
                'report_date': report_dates[i] if i < len(report_dates) else '',
            })

    filings.sort(key=lambda x: x['filed_date'], reverse=True)
    return filings


def get_filing_for_quarter(filings, quarter_end):
    """Find the 13F-HR filing whose report_date matches the target quarter."""
    qe_str = quarter_end.strftime('%Y-%m-%d')
    for f in filings:
        if f['report_date'] == qe_str:
            return f
        try:
            rd = date.fromisoformat(f['report_date'])
            if abs((rd - quarter_end).days) <= 5:
                return f
        except Exception:
            pass
    return None


def parse_infotable_xml(xml_bytes):
    """Parse 13F infotable XML. Returns list of holding dicts."""
    holdings = []
    try:
        text = xml_bytes.decode('utf-8', errors='replace')
        # Remove namespace prefixes
        text = re.sub(r'<[^>]*?:', '<', text)
        text = re.sub(r'</[^>]*?:', '</', text)

        for block in re.findall(r'<infoTable>(.*?)</infoTable>', text, re.DOTALL | re.IGNORECASE):
            def extract(tag):
                m = re.search(r'<' + tag + r'[^>]*>(.*?)</' + tag + r'>', block, re.DOTALL | re.IGNORECASE)
                return m.group(1).strip() if m else ''

            cusip     = extract('cusip')
            name      = extract('nameOfIssuer')
            stype     = extract('titleOfClass')
            value_str = extract('value')
            shares_m  = re.search(r'<sshPrnamt[^>]*>(.*?)</sshPrnamt>', block, re.DOTALL | re.IGNORECASE)
            shares_str = shares_m.group(1).strip() if shares_m else '0'

            if not cusip:
                continue

            try:
                value_usd = float(value_str.replace(',', '')) * 1000
            except (ValueError, AttributeError):
                value_usd = 0.0

            try:
                shares = int(shares_str.replace(',', ''))
            except (ValueError, AttributeError):
                shares = 0

            if value_usd < config.MIN_POSITION_VALUE:
                continue

            # ETF / fund filter
            stype_upper = stype.upper()
            if any(x in stype_upper for x in ['ETF', 'FUND', 'INDEX', 'TRUST', 'NOTE', 'BOND', 'PREF']):
                continue

            holdings.append({
                'cusip':         cusip,
                'company_name':  name,
                'security_type': stype,
                'value_usd':     value_usd,
                'shares':        shares,
            })

    except Exception as e:
        print('    XML parse error: {}'.format(e))

    return holdings


def get_infotable_url(cik, accession):
    """Find the infotable XML URL within a 13F filing index page."""
    cik_clean  = cik.lstrip('0')
    acc_dashed = '{}-{}-{}'.format(accession[:10], accession[10:12], accession[12:])
    index_url  = (
        'https://www.sec.gov/Archives/edgar/data/{}/{}/{}-index.htm'
        .format(cik_clean, accession, acc_dashed)
    )
    try:
        req = urllib.request.Request(
            index_url,
            headers={'User-Agent': config.SEC_USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode('utf-8', errors='replace')
        time.sleep(config.SEC_REQUEST_DELAY)

        patterns = [
            r'href="(/Archives/edgar/data/[^"]*?infotable[^"]*?\.xml)"',
            r'href="(/Archives/edgar/data/[^"]*?\.xml)"',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return 'https://www.sec.gov' + m.group(1)

        m = re.search(
            r'href="(/Archives/edgar/data/{}/{}/[^"]+\.xml)"'.format(cik_clean, accession),
            html, re.IGNORECASE
        )
        if m:
            return 'https://www.sec.gov' + m.group(1)

    except Exception as e:
        print('    Index fetch error: {}'.format(e))
    return None


# ============================================================
# CUSIP -> TICKER MAPPING (OpenFIGI)
# ============================================================
def map_cusips_to_tickers(cusips):
    """Map CUSIPs to tickers via OpenFIGI. Returns dict {cusip: ticker}."""
    if not cusips:
        return {}

    result = {}
    batch_size = 100

    headers = {'Content-Type': 'application/json'}
    if config.OPENFIGI_API_KEY:
        headers['X-OPENFIGI-APIKEY'] = config.OPENFIGI_API_KEY

    for i in range(0, len(cusips), batch_size):
        batch   = cusips[i:i + batch_size]
        payload = json.dumps([{'idType': 'ID_CUSIP', 'idValue': c} for c in batch])

        try:
            req = urllib.request.Request(
                OPENFIGI_BASE,
                data=payload.encode('utf-8'),
                headers=headers,
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print('    OpenFIGI error: {}'.format(e))
            time.sleep(config.OPENFIGI_REQUEST_DELAY)
            continue

        for j, item in enumerate(data):
            cusip = batch[j]
            if item and 'data' in item and item['data']:
                figi_list = item['data']
                ticker = None
                for figi in figi_list:
                    exch = figi.get('exchCode', '')
                    t    = figi.get('ticker', '')
                    if t and exch in ('US', 'UW', 'UN', 'UA', 'UR'):
                        ticker = t
                        break
                if not ticker and figi_list:
                    ticker = figi_list[0].get('ticker', '')
                if ticker:
                    result[cusip] = ticker

        time.sleep(config.OPENFIGI_REQUEST_DELAY)

    return result


# ============================================================
# HOLDINGS COLLECTION
# ============================================================
def collect_holdings_for_quarter(conn, quarter_end):
    """Pull 13F holdings from SEC EDGAR for all configured filers for a quarter."""
    qe_str = quarter_end.strftime('%Y-%m-%d')
    c = conn.cursor()
    success_count = 0

    for cik, name in config.HEDGE_FUND_FILERS:
        c.execute(
            'SELECT COUNT(*) FROM holdings WHERE filer_cik=? AND quarter_end=?',
            (cik, qe_str)
        )
        if c.fetchone()[0] > 0:
            print('  [{}] already in DB for {} -- skipping'.format(name, qe_str))
            success_count += 1
            continue

        print('  [{}] fetching 13F filings...'.format(name))
        filings = get_13f_filings(cik)
        if not filings:
            print('    No filings found')
            continue

        filing = get_filing_for_quarter(filings, quarter_end)
        if not filing:
            print('    No filing for quarter {}'.format(qe_str))
            continue

        print('    Filing: {} filed {}'.format(filing['accession'], filing['filed_date']))

        xml_url = get_infotable_url(cik, filing['accession'])
        if not xml_url:
            print('    Could not locate infotable XML')
            continue

        xml_bytes = edgar_get_xml(xml_url)
        if not xml_bytes:
            print('    XML download failed')
            continue

        holdings = parse_infotable_xml(xml_bytes)
        if not holdings:
            print('    0 holdings parsed')
            continue

        print('    {} holdings parsed'.format(len(holdings)))

        rows_inserted = 0
        for h in holdings:
            try:
                c.execute("""
                    INSERT OR IGNORE INTO holdings
                      (filer_cik, filer_name, quarter_end, filing_date,
                       cusip, company_name, security_type, value_usd, shares)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    cik, name, qe_str, filing['filed_date'],
                    h['cusip'], h['company_name'], h['security_type'],
                    h['value_usd'], h['shares']
                ))
                rows_inserted += 1
            except sqlite3.IntegrityError:
                pass

        conn.commit()
        success_count += 1
        print('    Stored {} holdings'.format(rows_inserted))

    return success_count


def map_cusips_in_db(conn, quarter_end):
    """Map unmapped CUSIPs to tickers for holdings in this quarter."""
    qe_str = quarter_end.strftime('%Y-%m-%d')
    c = conn.cursor()

    c.execute("""
        SELECT DISTINCT cusip FROM holdings
        WHERE quarter_end = ? AND (ticker IS NULL OR ticker = '')
    """, (qe_str,))
    unmapped = [row[0] for row in c.fetchall()]

    if not unmapped:
        print('  All CUSIPs already mapped for {}'.format(qe_str))
        return

    print('  Mapping {} CUSIPs via OpenFIGI...'.format(len(unmapped)))
    mapping = map_cusips_to_tickers(unmapped)
    print('  Mapped {} of {}'.format(len(mapping), len(unmapped)))

    for cusip, ticker in mapping.items():
        c.execute("""
            UPDATE holdings SET ticker = ?
            WHERE quarter_end = ? AND cusip = ?
        """, (ticker, qe_str, cusip))

    conn.commit()


# ============================================================
# SIGNAL DETECTION
# ============================================================
def detect_signals(conn, current_qend, prev_qend):
    """Find stocks with 3+ new initiations in the current quarter."""
    cqe = current_qend.strftime('%Y-%m-%d')
    pqe = prev_qend.strftime('%Y-%m-%d')
    c = conn.cursor()

    # Current quarter holdings
    c.execute("""
        SELECT filer_cik, filer_name, ticker, company_name, value_usd
        FROM holdings
        WHERE quarter_end = ? AND ticker IS NOT NULL AND ticker != ''
    """, (cqe,))
    current = c.fetchall()

    # Previous quarter holdings (for comparison)
    c.execute("""
        SELECT filer_cik, ticker FROM holdings
        WHERE quarter_end = ? AND ticker IS NOT NULL AND ticker != ''
    """, (pqe,))
    prev_set = {(row[0], row[1]) for row in c.fetchall()}

    # Find new initiations (in current but not previous, for this filer)
    new_initiations = {}
    for filer_cik, filer_name, ticker, company_name, value_usd in current:
        if (filer_cik, ticker) not in prev_set:
            if ticker not in new_initiations:
                new_initiations[ticker] = {'company_name': company_name, 'filers': []}
            new_initiations[ticker]['filers'].append((filer_cik, filer_name, value_usd))

    # Filter to MIN_NEW_INITIATIONS+
    signals = []
    for ticker, info in new_initiations.items():
        if len(info['filers']) >= config.MIN_NEW_INITIATIONS:
            filer_names = [f[1] for f in info['filers']]
            total_value = sum(f[2] for f in info['filers'])
            signals.append({
                'ticker':       ticker,
                'company_name': info['company_name'],
                'initiators':   len(info['filers']),
                'filer_names':  filer_names,
                'total_value':  total_value,
                'quarter_end':  cqe,
            })

    # Remove already-emailed tickers for this quarter
    c.execute('SELECT ticker FROM emailed_signals WHERE quarter_end = ?', (cqe,))
    already_sent = {row[0] for row in c.fetchall()}
    signals = [s for s in signals if s['ticker'] not in already_sent]

    signals.sort(key=lambda x: x['initiators'], reverse=True)
    return signals


# ============================================================
# VIX CHECK
# ============================================================
def get_vix():
    """Fetch current VIX level via yfinance. Returns float or None."""
    try:
        vix = yf.Ticker('^VIX')
        hist = vix.history(period='1d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception:
        pass
    return None


# ============================================================
# EMAIL
# ============================================================
def build_signal_subject(signals, vix):
    """Build the email subject parsed by ib_autotrader.py."""
    tickers = [s['ticker'] for s in signals]
    MAX_IN_SUBJECT = 8

    if len(tickers) <= MAX_IN_SUBJECT:
        ticker_str = ', '.join(tickers)
    else:
        shown = ', '.join(tickers[:MAX_IN_SUBJECT])
        ticker_str = '{} +{} more'.format(shown, len(tickers) - MAX_IN_SUBJECT)

    subject = '13F BULL: {}'.format(ticker_str)

    if vix is not None and vix >= 30:
        subject += ' [VIX={:.1f} KILL SWITCH -- autotrader will block]'.format(vix)

    return subject


def build_signal_html(signals, vix, ql_str, qe_str, pqe_str):
    """Build the HTML email body."""
    vix_str   = '{:.1f}'.format(vix) if vix is not None else 'N/A'
    vix_color = '#c0392b' if vix is not None and vix >= 30 else '#27ae60'

    kill_switch_banner = ''
    if vix is not None and vix >= 30:
        kill_switch_banner = (
            '<div style="background:#ffeeba;border:1px solid #e0a800;padding:10px 14px;'
            'margin-bottom:16px;border-radius:4px;">'
            '<strong>VIX KILL SWITCH ACTIVE ({:.1f})</strong> -- '
            'IB AutoTrader will block entries. Signals logged for when VIX drops below 30.'
            '</div>'.format(vix)
        )

    rows = ''
    for s in signals:
        filers_str = ', '.join(s['filer_names'])
        val_str = '${:.1f}M'.format(s['total_value'] / 1e6) if s['total_value'] > 0 else 'N/A'
        rows += (
            '<tr>'
            '<td style="padding:8px 12px;font-weight:bold;font-size:15px;">{}</td>'
            '<td style="padding:8px 12px;">{}</td>'
            '<td style="padding:8px 12px;text-align:center;font-weight:bold;color:#27ae60;">{}</td>'
            '<td style="padding:8px 12px;font-size:12px;">{}</td>'
            '<td style="padding:8px 12px;text-align:right;">{}</td>'
            '</tr>'
        ).format(s['ticker'], s['company_name'], s['initiators'], filers_str, val_str)

    body = """
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;">
<div style="max-width:900px;margin:0 auto;">

<h2 style="color:#1a3a5c;margin-bottom:4px;">
    13F Institutional Initiations &mdash; {ql}
</h2>
<p style="color:#555;margin-top:0;font-size:12px;">
    New positions in {qe} quarter | Compare vs {pqe} |
    VIX: <strong style="color:{vc};">{vs}</strong> |
    Min initiators: {mi} | Hold: {hd} days (13w)
</p>

{ks}

<table style="width:100%;border-collapse:collapse;border:1px solid #ddd;">
    <thead style="background:#1a3a5c;color:white;">
        <tr>
            <th style="padding:10px 12px;text-align:left;">Ticker</th>
            <th style="padding:10px 12px;text-align:left;">Company</th>
            <th style="padding:10px 12px;text-align:center;">Initiators</th>
            <th style="padding:10px 12px;text-align:left;">Funds</th>
            <th style="padding:10px 12px;text-align:right;">Combined Value</th>
        </tr>
    </thead>
    <tbody>
        {rows}
    </tbody>
</table>

<div style="margin-top:16px;padding:12px 14px;background:#f0f4f8;
            border-left:4px solid #1a3a5c;border-radius:2px;">
    <strong>Signal Rules:</strong>
    {ns} stock(s) with {mi}+ new initiators this quarter &rarr;
    <span style="color:#27ae60;font-weight:bold;">BUY</span> at next open |
    Hold {hd} days (13 weeks) | Exit: Day 91 or -40% catastrophic breaker |
    VIX kill switch at 30
</div>

<p style="font-size:11px;color:#aaa;margin-top:24px;">
    Grist Mill Capital -- 13F Scanner | Auto-generated {ts} UTC
</p>

</div></body></html>
""".format(
        ql=ql_str, qe=qe_str, pqe=pqe_str,
        vc=vix_color, vs=vix_str,
        mi=config.MIN_NEW_INITIATIONS, hd=config.HOLD_DAYS,
        ks=kill_switch_banner, rows=rows,
        ns=len(signals),
        ts=datetime.utcnow().strftime('%Y-%m-%d %H:%M')
    )
    return body


def send_email(subject, html_body):
    """Send alert via Gmail SMTP."""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = config.EMAIL_SENDER
    msg['To']      = config.EMAIL_RECEIVER
    msg.attach(MIMEText(html_body, 'html'))

    with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
        smtp.sendmail(config.EMAIL_SENDER, config.EMAIL_RECEIVER, msg.as_string())


def mark_signals_emailed(conn, signals):
    """Record emailed signals to prevent re-sending for the same quarter."""
    c = conn.cursor()
    today_str = date.today().strftime('%Y-%m-%d')
    for s in signals:
        c.execute("""
            INSERT OR IGNORE INTO emailed_signals
              (ticker, quarter_end, emailed_date, initiators, filer_names)
            VALUES (?,?,?,?,?)
        """, (
            s['ticker'], s['quarter_end'], today_str,
            s['initiators'], ', '.join(s['filer_names'])
        ))
    conn.commit()


# ============================================================
# STATUS REPORT
# ============================================================
def print_status(conn):
    c = conn.cursor()
    print('\n' + '='*60)
    print('13F SCANNER DB STATUS')
    print('='*60)

    c.execute('SELECT COUNT(*) FROM holdings')
    print('Total holdings rows: {}'.format(c.fetchone()[0]))
    c.execute('SELECT COUNT(DISTINCT filer_cik) FROM holdings')
    print('Unique filers:       {}'.format(c.fetchone()[0]))
    c.execute('SELECT COUNT(DISTINCT quarter_end) FROM holdings')
    print('Quarters in DB:      {}'.format(c.fetchone()[0]))
    c.execute('SELECT COUNT(*) FROM emailed_signals')
    print('Signals emailed:     {}'.format(c.fetchone()[0]))

    c.execute("""
        SELECT quarter_end, COUNT(DISTINCT filer_cik)
        FROM holdings GROUP BY quarter_end ORDER BY quarter_end DESC LIMIT 8
    """)
    print('\nQuarters x Filer coverage:')
    for row in c.fetchall():
        print('  {}  {} filers'.format(row[0], row[1]))

    c.execute("""
        SELECT ticker, quarter_end, initiators, emailed_date
        FROM emailed_signals ORDER BY emailed_date DESC LIMIT 20
    """)
    rows = c.fetchall()
    if rows:
        print('\nRecent emailed signals:')
        for row in rows:
            print('  {}  {:10s} Q={}  initiators={}'.format(row[3], row[0], row[1], row[2]))

    today = date.today()
    qend  = get_quarter_end(today)
    if qend:
        print('\nFiling window ACTIVE for quarter ending {}'.format(qend))
    else:
        print('\nNot in filing window.')
    print('='*60 + '\n')


# ============================================================
# LOG HELPER
# ============================================================
def _log_scan(conn, scan_date, qe_str, in_window, filers, signals, emailed, errors):
    c = conn.cursor()
    c.execute("""
        INSERT INTO scan_log
          (scan_date, quarter_end, in_window, filers_scanned, new_signals, email_sent, errors)
        VALUES (?,?,?,?,?,?,?)
    """, (scan_date, qe_str, int(in_window), filers, signals, int(emailed), errors))
    conn.commit()


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-email', action='store_true')
    parser.add_argument('--status',     action='store_true')
    parser.add_argument('--force-run',  action='store_true',
                        help='Run even outside the 13F filing window')
    parser.add_argument('--dry-run',    action='store_true',
                        help='Detect signals but do not send email')
    args = parser.parse_args()

    conn = init_db()

    if args.status:
        print_status(conn)
        return

    if args.test_email:
        print('Sending test email...')
        try:
            send_email(
                '13F Scanner -- Test Email',
                '<html><body><p>13F scanner test email. System operational.</p></body></html>'
            )
            print('Test email sent.')
        except Exception as e:
            print('FAILED: {}'.format(e))
        return

    today     = date.today()
    today_str = today.strftime('%Y-%m-%d')

    current_qend = get_quarter_end(today)
    in_window    = current_qend is not None

    if not in_window and not args.force_run:
        print('[{}] Not in 13F filing window -- no action needed.'.format(today_str))
        _log_scan(conn, today_str, '', False, 0, 0, False, '')
        return

    if args.force_run and not current_qend:
        # Use most recent quarter end
        m = today.month
        if m <= 3:
            current_qend = date(today.year - 1, 12, 31)
        elif m <= 6:
            current_qend = date(today.year, 3, 31)
        elif m <= 9:
            current_qend = date(today.year, 6, 30)
        else:
            current_qend = date(today.year, 9, 30)

    qe_str  = current_qend.strftime('%Y-%m-%d')
    ql_str  = quarter_label(current_qend)
    pqend   = prev_quarter_end(current_qend)
    pqe_str = pqend.strftime('%Y-%m-%d')

    print('[{}] 13F filing window active -- quarter {} ({})'.format(today_str, ql_str, qe_str))

    errors  = []
    filers_collected = 0
    signals = []

    try:
        print('\nCollecting current quarter ({})...'.format(qe_str))
        filers_collected = collect_holdings_for_quarter(conn, current_qend)
        print('  {} filers collected'.format(filers_collected))

        print('\nCollecting previous quarter ({}) for comparison...'.format(pqe_str))
        collect_holdings_for_quarter(conn, pqend)

        print('\nMapping CUSIPs for current quarter...')
        map_cusips_in_db(conn, current_qend)
        print('Mapping CUSIPs for previous quarter...')
        map_cusips_in_db(conn, pqend)

        print('\nDetecting initiation signals...')
        signals = detect_signals(conn, current_qend, pqend)
        print('  New signals found: {}'.format(len(signals)))
        for s in signals:
            print('    {:8s} {} initiators: {}'.format(
                s['ticker'], s['initiators'], ', '.join(s['filer_names'])))

        if not signals:
            print('No new signals to send.')
            _log_scan(conn, today_str, qe_str, True, filers_collected, 0, False, '')
            return

        vix     = get_vix()
        vix_str = '{:.1f}'.format(vix) if vix is not None else 'N/A'
        print('\nVIX: {}'.format(vix_str))
        if vix is not None and vix >= 30:
            print('  VIX kill switch active -- warning banner will appear in email')

        subject = build_signal_subject(signals, vix)
        html    = build_signal_html(signals, vix, ql_str, qe_str, pqe_str)

        if args.dry_run:
            print('\n[DRY RUN] Would send:')
            print('  Subject: {}'.format(subject))
            print('  Tickers: {}'.format([s['ticker'] for s in signals]))
            _log_scan(conn, today_str, qe_str, True, filers_collected, len(signals), False, '')
            return

        print('\nSending email: {}'.format(subject))
        send_email(subject, html)
        mark_signals_emailed(conn, signals)
        print('Email sent and signals marked.')
        _log_scan(conn, today_str, qe_str, True, filers_collected, len(signals), True, '')

    except Exception as e:
        err_msg = traceback.format_exc()
        print('ERROR: {}'.format(e))
        print(err_msg)
        errors.append(str(e))
        _log_scan(conn, today_str, qe_str, in_window, filers_collected, len(signals), False,
                  '; '.join(errors))


if __name__ == '__main__':
    main()
