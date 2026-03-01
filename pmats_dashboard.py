"""
PMATS — Live Trading Dashboard (Streamlit Cloud Edition)

Connects to Kalshi API for live balance/positions.
Reads from local SQLite for trade history and arb opportunities.
Deploys free on Streamlit Community Cloud.
"""

import json
import os
import sqlite3
import time
import base64
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="PMATS Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Try Streamlit secrets first, fall back to env vars
def get_secret(key, default=""):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

ENV = get_secret("KALSHI_ENV", "prod").lower()
KEY_ID = get_secret("KALSHI_KEY_ID", "")
PK_PEM = get_secret("KALSHI_PRIVATE_KEY_PEM", "")  # Full PEM content as string
PK_PATH = get_secret("KALSHI_PRIVATE_KEY_PATH", "")

# DB path — local only (Streamlit Cloud won't persist this, but it won't crash)
DB_PATH = get_secret("PMATS_DB_PATH", "pmats_data.db")

BASE_URLS = {
    "demo": "https://demo-api.kalshi.co/trade-api/v2",
    "prod": "https://api.elections.kalshi.com/trade-api/v2",
}
REST_BASE = BASE_URLS.get(ENV, BASE_URLS["prod"])


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

@st.cache_resource
def load_private_key():
    """Load RSA private key from secrets (PEM string) or file path."""
    if PK_PEM:
        pem_bytes = PK_PEM.encode("utf-8") if isinstance(PK_PEM, str) else PK_PEM
        return serialization.load_pem_private_key(pem_bytes, password=None)
    elif PK_PATH and os.path.exists(PK_PATH):
        with open(PK_PATH, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    return None


def make_auth_headers(method: str, path: str) -> dict:
    pk = load_private_key()
    if not pk or not KEY_ID:
        return {"Content-Type": "application/json"}
    ts = str(int(time.time() * 1000))
    msg = f"{ts}{method}{path}".encode()
    sig = pk.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "Content-Type": "application/json",
        "KALSHI-ACCESS-KEY": KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "KALSHI-ACCESS-TIMESTAMP": ts,
    }


def kalshi_get(path: str, params=None):
    """GET from Kalshi API with auth."""
    url = f"{REST_BASE}{path}"
    sign_path = f"/trade-api/v2{path}"
    headers = make_auth_headers("GET", sign_path)
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        CREATE TABLE IF NOT EXISTS pnl_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            balance     INTEGER NOT NULL,
            unrealized  INTEGER NOT NULL DEFAULT 0,
            realized    INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS strategy_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy        TEXT NOT NULL,
            opened_at       TEXT NOT NULL,
            closed_at       TEXT,
            ticker          TEXT NOT NULL,
            side            TEXT NOT NULL,
            action          TEXT NOT NULL,
            entry_price     INTEGER NOT NULL,
            exit_price      INTEGER,
            count           INTEGER NOT NULL,
            pnl_cents       INTEGER,
            order_id        TEXT,
            event_ticker    TEXT,
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS arb_opportunities (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            constraint_type TEXT NOT NULL,
            event_ticker    TEXT NOT NULL,
            details         TEXT NOT NULL,
            expected_profit INTEGER,
            traded          INTEGER NOT NULL DEFAULT 0,
            trade_ids       TEXT
        );
    """)
    conn.close()


def query_db(sql, params=()):
    """Run a SELECT and return list of dicts."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def insert_pnl_snapshot(balance_cents, unrealized=0, realized=0):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO pnl_snapshots (balance, unrealized, realized) VALUES (?, ?, ?)",
            (balance_cents, unrealized, realized),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def cents_to_dollars(c):
    if c is None:
        return "—"
    return f"${c / 100:.2f}"


def pnl_sign(val):
    if val is None:
        return "—"
    prefix = "+" if val > 0 else ""
    return f"{prefix}${val / 100:.2f}"


# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    /* Dark terminal theme */
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=Outfit:wght@300;400;500;600;700&display=swap');

    .stApp {
        background: #0a0a0a;
        color: #e4e4e7;
        font-family: 'Outfit', sans-serif;
    }

    /* Grid background */
    .stApp > div:first-child {
        background-image:
            linear-gradient(rgba(39, 39, 42, 0.25) 1px, transparent 1px),
            linear-gradient(90deg, rgba(39, 39, 42, 0.25) 1px, transparent 1px);
        background-size: 40px 40px;
    }

    /* Hide Streamlit chrome */
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}
    .stDeployButton {display: none;}

    /* Metric cards */
    [data-testid="stMetric"] {
        background: rgba(24, 24, 27, 0.8);
        border: 1px solid rgba(63, 63, 70, 0.5);
        border-radius: 8px;
        padding: 12px 16px;
        backdrop-filter: blur(12px);
    }
    [data-testid="stMetricLabel"] {
        font-family: 'Outfit', sans-serif;
        font-size: 11px !important;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: #71717a !important;
    }
    [data-testid="stMetricValue"] {
        font-family: 'JetBrains Mono', monospace;
        font-size: 22px !important;
        font-weight: 600;
        color: #e4e4e7 !important;
    }
    [data-testid="stMetricDelta"] {
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px !important;
    }

    /* Card container */
    .card-container {
        background: rgba(24, 24, 27, 0.8);
        border: 1px solid rgba(63, 63, 70, 0.5);
        border-radius: 12px;
        padding: 20px;
        backdrop-filter: blur(12px);
        box-shadow: 0 0 30px rgba(16, 185, 129, 0.02);
    }

    .card-title {
        font-family: 'Outfit', sans-serif;
        font-size: 12px;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: #a1a1aa;
        margin-bottom: 16px;
    }

    /* Header bar */
    .header-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px 0;
        border-bottom: 1px solid rgba(63, 63, 70, 0.5);
        margin-bottom: 24px;
    }

    .header-title {
        font-family: 'Outfit', sans-serif;
        font-size: 22px;
        font-weight: 700;
        letter-spacing: -0.02em;
    }

    .header-title .accent { color: #10b981; }
    .header-title .sub {
        color: #71717a;
        font-weight: 300;
        font-size: 13px;
        margin-left: 12px;
    }

    .live-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        color: #71717a;
    }

    .pulse-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #10b981;
        animation: pulse 2s ease-in-out infinite;
    }

    @keyframes pulse {
        0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.4); }
        50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }
    }

    /* Tables */
    .data-table {
        width: 100%;
        border-collapse: collapse;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
    }

    .data-table th {
        font-family: 'Outfit', sans-serif;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: #71717a;
        padding: 8px;
        text-align: left;
        border-bottom: 1px solid rgba(63, 63, 70, 0.5);
    }

    .data-table td {
        padding: 8px;
        border-bottom: 1px solid rgba(39, 39, 42, 0.5);
        color: #d4d4d8;
    }

    .data-table tr:hover td { background: rgba(39, 39, 42, 0.4); }

    .text-green { color: #10b981; }
    .text-red { color: #ef4444; }
    .text-yellow { color: #eab308; }
    .text-muted { color: #71717a; }
    .mono { font-family: 'JetBrains Mono', monospace; }

    .badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 9px;
        font-weight: 500;
        text-transform: uppercase;
        background: rgba(39, 39, 42, 0.8);
        color: #a1a1aa;
    }

    /* Empty state */
    .empty-state {
        text-align: center;
        padding: 40px 0;
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
        color: #52525b;
    }
    .empty-state .sub {
        font-size: 11px;
        color: #3f3f46;
        margin-top: 6px;
    }

    /* Streamlit overrides */
    .stTabs [data-baseweb="tab-list"] { gap: 0; }
    .stTabs [data-baseweb="tab"] {
        font-family: 'Outfit', sans-serif;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #71717a;
        background: transparent;
        border: none;
        padding: 8px 16px;
    }
    .stTabs [aria-selected="true"] {
        color: #10b981 !important;
        border-bottom: 2px solid #10b981;
    }

    /* Bar chart colors */
    .bar-pos {
        background: linear-gradient(to top, #065f46, #10b981);
        border-radius: 2px 2px 0 0;
        min-width: 4px;
    }
    .bar-neg {
        background: linear-gradient(to top, #991b1b, #ef4444);
        border-radius: 2px 2px 0 0;
        min-width: 4px;
    }
    .bar-zero {
        background: rgba(63, 63, 70, 0.5);
        border-radius: 2px 2px 0 0;
        min-width: 4px;
    }

    /* Footer */
    .footer {
        text-align: center;
        padding: 16px 0;
        margin-top: 32px;
        border-top: 1px solid rgba(63, 63, 70, 0.3);
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        color: #3f3f46;
    }

    /* Fix column gaps */
    [data-testid="stHorizontalBlock"] { gap: 12px; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Init DB
# ---------------------------------------------------------------------------
init_db()


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

now_str = datetime.now().strftime("%H:%M:%S")
st.markdown(f"""
<div class="header-bar">
    <div class="header-title">
        <span class="accent">PMATS</span>
        <span class="sub">Prediction Market Algorithmic Trading System</span>
    </div>
    <div class="live-badge">
        <div class="pulse-dot"></div>
        LIVE &nbsp;|&nbsp; {now_str} &nbsp;|&nbsp; {ENV.upper()}
    </div>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Fetch live data from Kalshi
# ---------------------------------------------------------------------------

@st.cache_data(ttl=25)
def fetch_balance():
    data = kalshi_get("/portfolio/balance")
    if data:
        return data.get("balance", 0), data.get("portfolio_value", 0)
    return 0, 0


@st.cache_data(ttl=25)
def fetch_positions():
    data = kalshi_get("/portfolio/positions")
    if data:
        return [p for p in data.get("market_positions", []) if p.get("position", 0) != 0]
    return []


balance_cents, portfolio_value = fetch_balance()

# Record snapshot
insert_pnl_snapshot(balance_cents)

# Load trades & arb opportunities from DB
trades = query_db("SELECT * FROM strategy_trades ORDER BY opened_at DESC LIMIT 100")
arb_opps = query_db("SELECT * FROM arb_opportunities ORDER BY ts DESC LIMIT 100")
positions = fetch_positions()

today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
total_trades = len(trades)
trades_today = sum(1 for t in trades if t.get("opened_at", "").startswith(today_str))
winning = [t for t in trades if (t.get("pnl_cents") or 0) > 0]
win_rate = (len(winning) / total_trades * 100) if total_trades > 0 else 0
total_pnl = sum(t.get("pnl_cents", 0) or 0 for t in trades)
daily_pnl = sum((t.get("pnl_cents", 0) or 0) for t in trades if t.get("opened_at", "").startswith(today_str))
avg_per_trade = (total_pnl / total_trades) if total_trades > 0 else 0

# Avg duration
durations = []
for t in trades:
    if t.get("opened_at") and t.get("closed_at"):
        try:
            o = datetime.fromisoformat(t["opened_at"].replace("Z", "+00:00"))
            c = datetime.fromisoformat(t["closed_at"].replace("Z", "+00:00"))
            durations.append((c - o).total_seconds())
        except Exception:
            pass
avg_duration_s = (sum(durations) / len(durations)) if durations else 0
if avg_duration_s > 3600:
    avg_dur_str = f"{avg_duration_s / 3600:.1f}h"
elif avg_duration_s > 60:
    avg_dur_str = f"{avg_duration_s / 60:.1f}m"
elif avg_duration_s > 0:
    avg_dur_str = f"{avg_duration_s:.0f}s"
else:
    avg_dur_str = "—"

# Starting capital for %
first_snap = query_db("SELECT balance FROM pnl_snapshots ORDER BY ts ASC LIMIT 1")
starting_capital = first_snap[0]["balance"] if first_snap else max(balance_cents, 1)
total_pnl_pct = (total_pnl / starting_capital * 100) if starting_capital > 0 else 0
daily_pnl_pct = (daily_pnl / starting_capital * 100) if starting_capital > 0 else 0


# ---------------------------------------------------------------------------
# KPI Cards
# ---------------------------------------------------------------------------

c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)

with c1:
    st.metric("Balance", cents_to_dollars(balance_cents), f"Portf: {cents_to_dollars(portfolio_value)}")
with c2:
    delta_str = f"{total_pnl_pct:+.1f}%" if total_pnl != 0 else None
    st.metric("Total P&L", pnl_sign(total_pnl), delta_str)
with c3:
    delta_str = f"{daily_pnl_pct:+.1f}%" if daily_pnl != 0 else None
    st.metric("Daily P&L", pnl_sign(daily_pnl), delta_str)
with c4:
    st.metric("Trades", f"{trades_today} / {total_trades}", "today / all")
with c5:
    st.metric("Avg / Trade", cents_to_dollars(int(avg_per_trade)))
with c6:
    st.metric("Avg Duration", avg_dur_str)
with c7:
    st.metric("Win Rate", f"{win_rate:.0f}%", f"{len(winning)}/{total_trades}")
with c8:
    st.metric("Positions", str(len(positions)), "open now")


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

chart_left, chart_right = st.columns(2)

with chart_left:
    st.markdown('<div class="card-container"><div class="card-title">Cumulative P&L</div>', unsafe_allow_html=True)
    snapshots = query_db("SELECT ts, balance FROM pnl_snapshots ORDER BY ts DESC LIMIT 60")
    snapshots.reverse()
    if len(snapshots) >= 2:
        base = snapshots[0]["balance"]
        values = [s["balance"] - base for s in snapshots]
        max_abs = max(abs(v) for v in values) if values else 1
        if max_abs == 0:
            max_abs = 1
        bars_html = '<div style="display:flex;align-items:flex-end;gap:2px;height:180px;width:100%">'
        for v in values:
            h = abs(v) / max_abs * 100
            h = max(h, 2)
            cls = "bar-pos" if v > 0 else ("bar-neg" if v < 0 else "bar-zero")
            bars_html += f'<div class="{cls}" style="height:{h}%;flex:1"></div>'
        bars_html += '</div>'
        st.markdown(bars_html, unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-state">No data yet<div class="sub">P&L will appear after first trades</div></div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with chart_right:
    st.markdown('<div class="card-container"><div class="card-title">Daily P&L</div>', unsafe_allow_html=True)
    if trades:
        daily = {}
        for t in trades:
            if t.get("pnl_cents") is not None:
                day = t["opened_at"][:10]
                daily[day] = daily.get(day, 0) + (t["pnl_cents"] or 0)
        if daily:
            days = sorted(daily.keys())[-30:]
            values = [daily[d] for d in days]
            max_abs = max(abs(v) for v in values) if values else 1
            if max_abs == 0:
                max_abs = 1
            bars_html = '<div style="display:flex;align-items:flex-end;gap:4px;height:180px;width:100%;justify-content:center">'
            for i, d in enumerate(days):
                v = values[i]
                h = abs(v) / max_abs * 80
                h = max(h, 4)
                cls = "bar-pos" if v > 0 else ("bar-neg" if v < 0 else "bar-zero")
                bars_html += f'<div style="flex:1;max-width:36px;display:flex;flex-direction:column;align-items:center;gap:4px"><div class="{cls}" style="height:{h}%;width:100%"></div><span class="mono" style="font-size:8px;color:#52525b;transform:rotate(-45deg)">{d[5:]}</span></div>'
            bars_html += '</div>'
            st.markdown(bars_html, unsafe_allow_html=True)
        else:
            st.markdown('<div class="empty-state">No closed trades yet</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-state">No trades yet<div class="sub">Daily P&L will appear here</div></div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

table_left, table_right = st.columns(2)

with table_left:
    st.markdown('<div class="card-container"><div class="card-title">Trade History</div>', unsafe_allow_html=True)
    if trades:
        rows_html = ""
        for t in trades[:50]:
            pnl = t.get("pnl_cents")
            if pnl is not None:
                pnl_str = pnl_sign(pnl)
                color = "text-green" if pnl > 0 else ("text-red" if pnl < 0 else "text-muted")
            else:
                pnl_str = "open"
                color = "text-yellow"
            opened = t.get("opened_at", "")[:19].replace("T", " ")
            rows_html += f"""<tr>
                <td class="text-muted">{opened}</td>
                <td>{t.get('ticker', '')[:28]}</td>
                <td><span class="badge">{t.get('strategy', '')[:12]}</span></td>
                <td>{t.get('side', '')} {t.get('action', '')}</td>
                <td>{t.get('entry_price', '')}¢</td>
                <td class="{color}">{pnl_str}</td>
            </tr>"""
        st.markdown(f"""
        <div style="max-height:280px;overflow-y:auto">
        <table class="data-table">
            <thead><tr><th>Time</th><th>Ticker</th><th>Strategy</th><th>Side</th><th>Entry</th><th>P&L</th></tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-state">No trades recorded yet<div class="sub">Trades appear when the arb engine executes</div></div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with table_right:
    st.markdown('<div class="card-container"><div class="card-title">Arb Opportunity Scanner</div>', unsafe_allow_html=True)
    if arb_opps:
        rows_html = ""
        for o in arb_opps[:50]:
            ts = o.get("ts", "")[:19].replace("T", " ")
            traded_str = "✓ TRADED" if o.get("traded") else "— skipped"
            traded_color = "text-green" if o.get("traded") else "text-muted"
            profit = cents_to_dollars(o.get("expected_profit")) if o.get("expected_profit") else "—"
            rows_html += f"""<tr>
                <td class="text-muted">{ts}</td>
                <td><span class="badge">{o.get('constraint_type', '')[:16]}</span></td>
                <td>{o.get('event_ticker', '')[:22]}</td>
                <td class="text-green">{profit}</td>
                <td class="{traded_color}">{traded_str}</td>
            </tr>"""
        st.markdown(f"""
        <div style="max-height:280px;overflow-y:auto">
        <table class="data-table">
            <thead><tr><th>Time</th><th>Type</th><th>Event</th><th>Est. Profit</th><th>Status</th></tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-state">No opportunities detected yet<div class="sub">Scanner will log constraint violations here</div></div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Open Positions
# ---------------------------------------------------------------------------

st.markdown('<div class="card-container"><div class="card-title">Open Positions</div>', unsafe_allow_html=True)
if positions:
    rows_html = ""
    for p in positions:
        ticker = p.get("market_ticker", "")
        pos = p.get("position", 0)
        side = "YES" if pos > 0 else "NO"
        side_color = "text-green" if pos > 0 else "text-red"
        qty = abs(pos)
        exposure = p.get("market_exposure", 0)
        realized = p.get("realized_pnl", 0)
        r_color = "text-green" if realized > 0 else ("text-red" if realized < 0 else "text-muted")
        rows_html += f"""<tr>
            <td>{ticker[:35]}</td>
            <td class="{side_color}">{side}</td>
            <td>{qty}</td>
            <td>{cents_to_dollars(exposure)}</td>
            <td class="{r_color}">{pnl_sign(realized)}</td>
        </tr>"""
    st.markdown(f"""
    <table class="data-table">
        <thead><tr><th>Ticker</th><th>Side</th><th>Qty</th><th>Exposure</th><th>Realized P&L</th></tr></thead>
        <tbody>{rows_html}</tbody>
    </table>""", unsafe_allow_html=True)
else:
    st.markdown('<div class="empty-state">No open positions<div class="sub">Positions will appear when the system trades</div></div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Footer + Auto-refresh
# ---------------------------------------------------------------------------

st.markdown(f"""
<div class="footer">
    PMATS v0.3.0 — Kalshi Algorithmic Trading &nbsp;|&nbsp; Auto-refresh: 30s &nbsp;|&nbsp; {ENV.upper()}
</div>
""", unsafe_allow_html=True)

# Auto-refresh every 30 seconds
st.markdown("""
<script>
    setTimeout(function() { window.location.reload(); }, 30000);
</script>
""", unsafe_allow_html=True)
