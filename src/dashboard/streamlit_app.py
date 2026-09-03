"""
Streamlit Dashboard — AI Hedge Fund Bot
=======================================
Alternative dashboard to the cyberpunk FastAPI UI.
Reads state from the FastAPI backend's /api/status endpoint.
Run separately on port 8501.

Usage:
  streamlit run src/dashboard/streamlit_app.py

Env vars:
  API_BASE — FastAPI backend URL (default https://ai-hedgefund-api-hp2i.onrender.com)
             Override via Streamlit secrets for local dev.
"""
import os
import streamlit as st
import requests
import pandas as pd
from datetime import datetime

API_BASE = os.getenv("API_BASE", "https://ai-hedgefund-api-hp2i.onrender.com")

# ============================================================================
# PAGE CONFIG
# ============================================================================
st.set_page_config(
    page_title="AI Hedge Fund — Streamlit",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================================
# CYBERPUNK STYLING
# ============================================================================
st.markdown("""
<style>
    .stApp { background-color: #0B120E; color: #c8e6c9; }
    h1, h2, h3 { color: #75E063 !important; text-shadow: 0 0 8px #75E06355; }
    .stButton button {
        background: transparent; color: #75E063; border: 1px solid #75E063;
        font-family: 'JetBrains Mono', monospace;
    }
    .stButton button:hover { background: #75E063; color: #0B120E; }
    [data-testid="stMetricValue"] { color: #75E063; }
    [data-testid="stMetricDelta"] { color: #FFD700; }
    .stDataFrame { background-color: #0B120E; }
    div[data-baseweb="select"] { background-color: #000; }
    /* Font-size reduction: render at 80% of default across the dashboard */
    html, body, [data-testid="stAppViewContainer"], .main, .block-container {
        font-size: 0.8em !important;
    }
    [data-testid="stMetricValue"] { font-size: 0.8em !important; }
    [data-testid="stMetricLabel"] { font-size: 0.8em !important; }
    [data-testid="stMetricDelta"]  { font-size: 0.8em !important; }
    [data-testid="stHeader"] { font-size: 0.8em !important; }
    h1 { font-size: 1.6em !important; }
    h2 { font-size: 1.3em !important; }
    h3 { font-size: 1.1em !important; }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# HELPERS — cache for 30s so fragment reruns don't hit Render on every tick.
# 30s matches the autorefresh interval, so the cache always misses
# exactly when we want a fresh fetch.
# ============================================================================
def _do_fetch_status() -> dict:
    """Raw fetch with no caching. Returns either a real status dict
    (on 200) or a sentinel dict with _transient / _disconnected."""
    try:
        r = requests.get(f"{API_BASE}/api/status", timeout=10)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            return {"_transient": True, "error": "Backend is busy; will retry next tick."}
        if 500 <= r.status_code < 600:
            return {"_transient": True, "error": f"Backend returned {r.status_code}; will retry next tick."}
        return {"_transient": True, "error": f"Backend returned {r.status_code}."}
    except requests.exceptions.RequestException:
        return {"_disconnected": True, "error": "Backend not reachable from this network."}


@st.cache_data(ttl=25)
def fetch_status() -> dict:
    """Cached fetch — used by the auto-refreshing live panel. 25s TTL
    is slightly less than the 30s fragment tick so the cache always
    misses when we want fresh data."""
    return _do_fetch_status()


def fetch_status_fresh() -> dict:
    """Bypass the cache. Used on cold load (top of page) and on user
    actions that MUST see current state (Initialize / Stop / Apply /
    Clear), so a previously-cached 'disconnected' from a transient
    cold-start doesn't keep the user locked out."""
    return _do_fetch_status()


@st.cache_data(ttl=300)  # model list rarely changes; 5 min is fine
def fetch_models():
    try:
        r = requests.get(f"{API_BASE}/api/models", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def post(path, payload=None):
    try:
        r = requests.post(f"{API_BASE}{path}", json=payload or {}, timeout=5)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# HEADER
# ============================================================================
st.title("⚡ AI HEDGE FUND — STREAMLIT CONSOLE")
# Top-level uses the uncached fetch so a previous session's stale
# 'disconnected' result doesn't lock the user out across reloads.
state = fetch_status_fresh()

# Only hard-stop on a *connection* error (no dict, or a dict flagged
# _disconnected). Transient 429 / 503 / 4xx keeps the page up so the
# user sees a soft "retrying" message inside the live panel.
def _is_hard_error(s):
    if not s or not isinstance(s, dict):
        return True
    return bool(s.get("_disconnected"))

if _is_hard_error(state):
    st.error("❌ Cannot reach the trading backend right now.")
    st.caption(
        "The backend may be starting up (Render free tier cold-starts can take 30–60s) "
        "or the API_BASE Streamlit secret may be misconfigured. "
        "Click below to retry, or wait — the live panel auto-rechecks every 30s."
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 Retry now", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with c2:
        st.link_button("Open backend", url="https://ai-hedgefund-api-hp2i.onrender.com/api/status", use_container_width=True)
    st.stop()

# Top status row (rendered by the live_stats fragment below for auto-refresh)
# Kept here as a placeholder so the layout matches the fragment's first row.

# ============================================================================
# SIDEBAR — CONTROLS
# ============================================================================
st.sidebar.header("🎛️ Controls")

if state.get("_transient") or not isinstance(state, dict):
    st.sidebar.info("⏳ Waiting for backend…")
    st.sidebar.caption(state.get("error", "No data yet") if isinstance(state, dict) else "")
    st.sidebar.caption("Controls will unlock on the next refresh (≤30s).")
elif state.get("is_running"):
    if st.sidebar.button("■ Stop Engine", use_container_width=True):
        post("/api/control", {"action": "stop", "interval": state.get("interval", 30)})
        st.cache_data.clear()
        st.rerun()
else:
    if st.sidebar.button("▶ Initialize Engine", use_container_width=True):
        post("/api/control", {"action": "start", "interval": 30})
        st.cache_data.clear()
        st.rerun()

st.sidebar.subheader("Interval")
_current_interval = state.get("interval") if isinstance(state, dict) else 30
interval = st.sidebar.selectbox(
    "Trading interval",
    [30, 60, 300, 3600],
    index=[30, 60, 300, 3600].index(_current_interval) if _current_interval in [30, 60, 300, 3600] else 0,
    format_func=lambda x: {30: "30 sec", 60: "1 min", 300: "5 min", 3600: "1 hour"}[x],
)
if interval != state.get("interval"):
    post("/api/control", {"action": "start", "interval": interval})
    st.cache_data.clear()
    st.rerun()

st.sidebar.subheader("AI Model")
models_data = fetch_models() or {}
free_models = models_data.get("free", ["openrouter/free"])
current = state.get("current_model", "openrouter/free")
if current not in free_models:
    free_models = [current] + free_models
selected = st.sidebar.selectbox("Model", free_models, index=free_models.index(current) if current in free_models else 0)
if st.sidebar.button("Apply Model"):
    post("/api/model", {"model": selected})
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption(f"Active: **{state.get('resolved_model', current)}**")

if st.sidebar.button("🗑️ Clear Trade History"):
    post("/api/reset")
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption("Backend: `Render FastAPI`")
st.sidebar.caption("Refresh: every 30 seconds (background)")

# ============================================================================
# LIVE PANEL — auto-refreshes every 30s in place.
#
# Uses @st.fragment(run_every="30s") — Streamlit's modern, built-in
# scheduled-rerun API (available in streamlit>=1.33). On Streamlit Cloud
# this reruns only the fragment, with no full-page "Running script..."
# overlay. fetch_status is cached for 30s (matching the interval), so
# the rerun is sub-50ms and effectively invisible.
# ============================================================================
@st.fragment(run_every="30s")
def live_panel():
    state = fetch_status()
    if not state or not isinstance(state, dict):
        st.warning("⏳ No response from backend; will retry in 30s.")
        return
    if state.get("_transient"):
        st.warning(f"⏳ {state.get('error', 'Backend is busy')}; will retry in 30s.")
        return
    if state.get("_disconnected"):
        st.error("❌ Cannot reach the trading backend. Check the API service is running.")
        return

    # --- Top status row (auto-refreshing) ---
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        status_label = "🟢 RUNNING" if state.get("is_running") else "🔴 IDLE"
        st.metric("Engine", status_label)
    with c2:
        bal = float(state.get("balance") or 0)
        st.metric("Balance Amount", f"${bal:,.2f}")
    with c3:
        equity = float(state.get("equity") or bal)
        st.metric("Equity", f"${equity:,.2f}")
    with c4:
        # Total portfolio balance = equity + notional tied up in open positions
        # (which is already in `equity` since equity = balance + unrealized PnL
        # and balance = free cash after notional deduction). Show as
        # starting_balance + realized PnL + unrealized open PnL.
        starting = float(state.get("starting_balance") or 0)
        # sum of realized pnl from closed trades
        realized_pnl = sum(
            float(t.get("pnl", 0))
            for t in (state.get("trade_history") or [])
            if isinstance(t, dict) and t.get("status") in ("TP_HIT", "SL_HIT")
            and t.get("pnl") is not None
        )
        # unrealized PnL = equity - balance
        unrealized_pnl = equity - bal
        total_portfolio = starting + realized_pnl + unrealized_pnl
        st.metric("Total Portfolio", f"${total_portfolio:,.2f}")
    with c5:
        # Realized P&L only — sum of closed trades' pnl
        sign = "+" if realized_pnl >= 0 else ""
        realized_pct = (realized_pnl / starting * 100) if starting else 0.0
        st.metric(
            "Total P&L (Realised)",
            f"{sign}${realized_pnl:,.2f}",
            delta=f"{sign}{realized_pct:.2f}%",
        )
    with c6:
        sig = state.get("last_signal", "HOLD")
        sig_color = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(sig, "⚪")
        st.metric("Signal", f"{sig_color} {sig}")
    # moved confidence + mode into a compact secondary row
    c7, c8, c9 = st.columns([1, 1, 4])
    with c7:
        st.metric("Confidence", f"{state.get('last_confidence', 0)}%")
    with c8:
        st.metric("Mode", "PAPER" if state.get("paper_mode") else "LIVE")
    with c9:
        st.caption(
            f"Starting balance: **${starting:,.2f}** | "
            f"Open positions: **{len(state.get('open_orders', []))}** | "
            f"Realised PnL: **${realized_pnl:,.2f}** | "
            f"Unrealised PnL: **${unrealized_pnl:,.2f}**"
        )
    st.markdown("---")

    # --- Detailed stats ---
    st.header("📊 Live Stats")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Cycles Completed", state.get("cycles_completed", 0))
    with col2:
        st.metric("Trades Recorded", len(state.get("trade_history", [])))
    with col3:
        st.metric("Last Symbol", state.get("last_symbol") or "—")
    with col4:
        started = state.get("started_at") or "—"
        started_time = started
        started_date = ""
        if started != "—":
            try:
                ts = pd.Timestamp(started)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                ts_ist = ts.tz_convert("Asia/Kolkata")
                started_time = ts_ist.strftime("%H:%M:%S")
                started_date = ts_ist.strftime("%d %b %Y")
            except Exception:
                started_time = started
        st.metric("Started At", f"{started_time} IST")
        if started_date:
            st.caption(started_date)

    st.subheader("🧠 AI Logic")
    st.info(state.get("last_logic", "Bot idle."))

    # --- Open positions (from open_orders, which the backend keeps separate
    # from filled/closed trade_history) ---
    open_orders = state.get("open_orders", []) or []
    st.subheader(f"📂 Open Positions ({len(open_orders)})")
    if not open_orders:
        st.caption("No open positions.")
    else:
        odf = pd.DataFrame(open_orders)
        # Normalize column names: open_orders uses symbol/side, trade_history uses asset/signal
        if "symbol" in odf.columns and "asset" not in odf.columns:
            odf["asset"] = odf["symbol"]
        if "side" in odf.columns and "signal" not in odf.columns:
            odf["signal"] = odf["side"]
        # Build a "time" column from placed_at/filled_at (Unix timestamps)
        for ts_col in ("filled_at", "placed_at"):
            if ts_col in odf.columns and "time" not in odf.columns:
                try:
                    odf["time"] = pd.to_datetime(odf[ts_col], unit="s", utc=True)
                except Exception:
                    pass
        if "time" in odf.columns:
            odf["time"] = (
                pd.to_datetime(odf["time"], utc=True, errors="coerce")
                .dt.tz_convert("Asia/Kolkata")
                .dt.strftime("%Y-%m-%d %H:%M:%S")
            )
        for col in ("price", "sl", "tp"):
            if col in odf.columns:
                odf[col] = odf[col].astype(float).round(5)
        odf = odf.iloc[::-1]  # newest first
        desired_o = ["time", "asset", "signal", "status", "confidence",
                     "price", "sl", "tp", "qty", "notional", "mode"]
        cols_o = [c for c in desired_o if c in odf.columns]
        st.dataframe(odf[cols_o], use_container_width=True, hide_index=True)

    # --- Execution feed (filled/closed trades) ---
    st.header("📜 Execution Feed")
    trades = state.get("trade_history", [])
    if not trades:
        st.warning("No trades yet. Initialize the engine to start.")
    else:
        df = pd.DataFrame(trades)
        df = df.iloc[::-1]  # newest first
        if "time" in df.columns:
            df["time"] = (
                pd.to_datetime(df["time"], utc=True)
                .dt.tz_convert("Asia/Kolkata")
                .dt.strftime("%Y-%m-%d %H:%M:%S")
            )
        for col in ("price", "sl", "tp"):
            if col in df.columns:
                df[col] = df[col].astype(float).round(5)

        desired = ["time", "asset", "signal", "status", "outcome", "pnl",
                   "balance_after", "confidence", "price", "sl", "tp", "mode"]
        cols = [c for c in desired if c in df.columns]
        st.dataframe(df[cols], use_container_width=True, hide_index=True)

    # Equity curve
    # Show realised PnL curve: starting_balance + cumulative sum of pnl from
    # closed trades sorted by time. If we have closed trades, also append the
    # current equity (realised + unrealised) as the latest point.
    trades_list = state.get("trade_history") or []
    if not isinstance(trades_list, list):
        trades_list = []
    closed_trades = [t for t in trades_list if isinstance(t, dict) and t.get("status") in ("TP_HIT", "SL_HIT")]
    open_trades = [t for t in trades_list if isinstance(t, dict) and t.get("status") not in ("TP_HIT", "SL_HIT")]
    st.header("📈 Equity Curve")

    if closed_trades:
        # Build realised equity curve from closed trades only.
        # starting_balance + cumsum(pnl) — does NOT include unrealized open-position notionals.
        df = pd.DataFrame(closed_trades)
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Kolkata")
            df = df.sort_values("time")
        if "pnl" in df.columns:
            df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0.0)
            starting = float(state.get("starting_balance") or 0)
            df["cum_pnl"] = df["pnl"].cumsum()
            df["equity"] = starting + df["cum_pnl"]
            # Latest realised equity (starting + total realised PnL)
            realized_equity = starting + float(df["pnl"].sum())
            now = pd.Timestamp.utcnow().tz_convert("Asia/Kolkata")
            if df["time"].iloc[-1] != now:
                df = pd.concat([
                    df,
                    pd.DataFrame([{"time": now, "equity": realized_equity}])
                ], ignore_index=True)
            eq_plot = df.set_index("time")[["equity"]].rename(columns={"equity": "Realised Equity ($)"})
            st.line_chart(eq_plot, height=300)
            pnl_total = float(df["pnl"].sum())
            sign = "+" if pnl_total >= 0 else ""
            st.caption(
                f"{len(closed_trades)} closed trade(s) plotted • {len(open_trades)} open. "
                f"Realised PnL: {sign}${pnl_total:.2f} • "
                f"Current realised equity: ${realized_equity:.2f} (start: ${starting:.2f})"
            )
        else:
            st.info("Closed trades recorded but missing `pnl`.")
    else:
        # No closed trades yet — show a flat line at starting balance so the
        # section isn't empty.
        starting = float(state.get("starting_balance") or 0)
        now = pd.Timestamp.utcnow().tz_convert("Asia/Kolkata")
        placeholder = pd.DataFrame(
            {"Realised Equity ($)": [starting, starting]},
            index=pd.to_datetime([now - pd.Timedelta(seconds=1), now]),
        )
        st.line_chart(placeholder, height=300)
        st.info(
            f"No closed trades yet — {len(open_trades)} open position(s). "
            "Curve will populate as trades close (TP/SL hit)."
        )

    st.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')} IST (auto, every 30s)")

live_panel()
