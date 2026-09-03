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
</style>
""", unsafe_allow_html=True)

# ============================================================================
# HELPERS
# ============================================================================
@st.cache_data(ttl=2)
def fetch_status():
    try:
        r = requests.get(f"{API_BASE}/api/status", timeout=5)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        return {"error": str(e)}


@st.cache_data(ttl=60)
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
state = fetch_status()

if not state or "error" in state:
    st.error(f"❌ Cannot reach FastAPI backend at {API_BASE}. Start it or set API_BASE in Streamlit secrets.")
    st.stop()

# Top status row (rendered by the live_stats fragment below for auto-refresh)
# Kept here as a placeholder so the layout matches the fragment's first row.

# ============================================================================
# SIDEBAR — CONTROLS
# ============================================================================
st.sidebar.header("🎛️ Controls")

if state.get("is_running"):
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
interval = st.sidebar.selectbox(
    "Trading interval",
    [30, 60, 300, 3600],
    index=[30, 60, 300, 3600].index(state.get("interval", 30)) if state.get("interval") in [30,60,300,3600] else 0,
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
st.sidebar.caption("Refresh: every 10 seconds (background)")

# ============================================================================
# LIVE STATS FRAGMENT — auto-refreshes in the background without rerunning
# the whole page (no flicker, no dimming, no input-focus loss).
# ============================================================================
@st.fragment(run_every="10s")
def live_stats():
    # Re-fetch (uses the 2s cache; fragment runs every 10s, so cache always misses)
    state = fetch_status()
    if not state or "error" in state:
        st.error(f"❌ Cannot reach FastAPI backend at {API_BASE}. Start it or set API_BASE in Streamlit secrets.")
        return

    # --- Top status row (auto-refreshing) ---
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        status_label = "🟢 RUNNING" if state.get("is_running") else "🔴 IDLE"
        st.metric("Engine", status_label)
    with c2:
        bal = float(state.get("balance") or 0)
        start = float(state.get("starting_balance") or 0)
        pnl = float(state.get("pnl") or 0)
        delta_pct = float(state.get("pnl_pct") or 0)
        sign = "+" if pnl >= 0 else ""
        st.metric(
            "Balance",
            f"${bal:,.2f}",
            delta=f"{sign}${pnl:,.2f} ({sign}{delta_pct:.2f}%)",
        )
    with c3:
        sig = state.get("last_signal", "HOLD")
        sig_color = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(sig, "⚪")
        st.metric("Signal", f"{sig_color} {sig}")
    with c4:
        st.metric("Confidence", f"{state.get('last_confidence', 0)}%")
    with c5:
        st.metric("Mode", "PAPER" if state.get("paper_mode") else "LIVE")
    st.caption(
        f"Starting balance: **${start:,.2f}** | Current: **${bal:,.2f}** | "
        f"Equity: **${state.get('equity', bal):,.2f}**"
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
        if started != "—":
            try:
                started = (
                    pd.Timestamp(started)
                    .tz_localize("UTC")
                    .tz_convert("Asia/Kolkata")
                    .strftime("%Y-%m-%d %H:%M:%S")
                )
            except Exception:
                try:
                    started = datetime.fromisoformat(started).strftime("%H:%M:%S")
                except Exception:
                    pass
        st.metric("Started At (IST)", started)

    st.subheader("🧠 AI Logic")
    st.info(state.get("last_logic", "Bot idle."))

    # Execution feed
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
    if trades:
        st.header("📈 Equity Curve")
        eq_df = pd.DataFrame(trades)
        if "time" in eq_df.columns:
            eq_df["time"] = pd.to_datetime(eq_df["time"])
            eq_df = eq_df.sort_values("time")
        if "balance_after" in eq_df.columns:
            eq_plot = eq_df.set_index("time")[["balance_after"]].rename(
                columns={"balance_after": "Equity ($)"}
            )
            st.line_chart(eq_plot, height=300)
        else:
            st.info("Equity curve will appear after the first trade.")

    st.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')} IST (auto, every 10s)")

live_stats()
