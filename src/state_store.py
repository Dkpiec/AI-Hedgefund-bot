"""
State persistence for the AI Hedge Fund Bot.

Persistence priority (first available wins):
1. PostgreSQL (env DATABASE_URL) — used when running on Coolify next to
   hedgefund-pg on hedgefund-net. Survives redeploys and restarts.
2. Local JSON files — always written as cache; used when PostgreSQL
   is not reachable.

Real-world continuity rule: open positions, trade history and booked PnL
must survive process restarts. Never reset state on startup unless the
user explicitly calls /api/reset.
"""
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# Paths (local fallback cache)
# --------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = DATA_DIR / "state.json"
ORDERS_FILE = DATA_DIR / "paper_orders.json"
BALANCE_FILE = DATA_DIR / "paper_balance.txt"

# Fields in bot_state we persist.
_PERSISTED_BOT_KEYS = [
    "starting_balance", "balance", "equity", "free",
    "pnl", "pnl_pct", "trade_history", "cancelled_orders",
    "cycles_completed", "current_model", "resolved_model",
    "scan_interval", "interval", "chart_timeframe",
    "started_at", "is_running", "universe",
    "ib15_positions", "ib15_trade_log", "ib15_pnl", "ib15_pending_approvals",
]

# --------------------------------------------------------------------------
# PostgreSQL config (primary store on Coolify)
# --------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")

_pg_schema_ready = False


def _pg_conn():
    if not DATABASE_URL:
        return None
    try:
        import psycopg2
    except ImportError:
        print("[STATE_STORE] psycopg2 not installed — PG disabled", file=sys.stderr)
        return None
    try:
        return psycopg2.connect(DATABASE_URL, connect_timeout=10)
    except Exception as e:
        print(f"[STATE_STORE] PG connect failed: {e}", file=sys.stderr)
        return None


def _pg_ensure_schema(conn) -> None:
    global _pg_schema_ready
    if _pg_schema_ready:
        return
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS bot_state "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS open_orders "
            "(order_id TEXT PRIMARY KEY, order_json TEXT NOT NULL, "
            " updated_at TIMESTAMPTZ DEFAULT now())"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS trade_history "
            "(id SERIAL PRIMARY KEY, trade_json TEXT NOT NULL, "
            " created_at TIMESTAMPTZ DEFAULT now())"
        )
    conn.commit()
    _pg_schema_ready = True


def _pg_save_state(snapshot: Dict[str, Any]) -> bool:
    conn = _pg_conn()
    if conn is None:
        return False
    try:
        _pg_ensure_schema(conn)
        with conn.cursor() as cur:
            for k, v in snapshot.items():
                cur.execute(
                    "INSERT INTO bot_state(key, value) VALUES(%s, %s) "
                    "ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
                    (k, json.dumps(v, default=str)),
                )
        conn.commit()
        print(f"[STATE_STORE] Saved state to Postgres ({len(snapshot)} keys)", file=sys.stderr)
        return True
    except Exception as e:
        print(f"[STATE_STORE] PG save failed: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()


def _pg_load_state() -> Optional[Dict[str, Any]]:
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _pg_ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT key, value FROM bot_state")
            rows = cur.fetchall()
        if not rows:
            return None  # empty DB -> let caller fall through
        out: Dict[str, Any] = {}
        for k, raw in rows:
            try:
                v = json.loads(raw)
            except Exception:
                continue
            if v is not None:
                out[k] = v
        print(f"[STATE_STORE] Loaded state from Postgres ({len(out)} keys)", file=sys.stderr)
        return out
    except Exception as e:
        print(f"[STATE_STORE] PG load failed: {e}", file=sys.stderr)
        return None
    finally:
        conn.close()


def _pg_save_orders(orders: Dict[str, Dict]) -> bool:
    conn = _pg_conn()
    if conn is None:
        return False
    try:
        _pg_ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM open_orders")
            for oid, o in orders.items():
                cur.execute(
                    "INSERT INTO open_orders(order_id, order_json) VALUES(%s, %s)",
                    (oid, json.dumps(o, default=str)),
                )
        conn.commit()
        print(f"[STATE_STORE] Saved {len(orders)} open orders to Postgres", file=sys.stderr)
        return True
    except Exception as e:
        print(f"[STATE_STORE] PG orders save failed: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()


def _pg_load_orders() -> Optional[Dict[str, Dict]]:
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        _pg_ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT order_id, order_json FROM open_orders")
            rows = cur.fetchall()
        orders: Dict[str, Dict] = {}
        for oid, raw in rows:
            try:
                orders[oid] = json.loads(raw)
            except Exception:
                continue
        print(f"[STATE_STORE] Loaded {len(orders)} open orders from Postgres", file=sys.stderr)
        return orders
    except Exception as e:
        print(f"[STATE_STORE] PG orders load failed: {e}", file=sys.stderr)
        return None
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Local atomic file helpers (cache of last resort)
# --------------------------------------------------------------------------
def _atomic_write(path: Path, payload: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(payload)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[STATE_STORE] Failed to write {path}: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# Public API: bot_state
# --------------------------------------------------------------------------
def save_bot_state(state: Dict[str, Any]) -> None:
    snapshot = {k: state.get(k) for k in _PERSISTED_BOT_KEYS if k in state}

    # Local cache (always)
    _atomic_write(STATE_FILE, json.dumps(snapshot, indent=2, default=str))

    # Remote: Postgres
    if _pg_save_state(snapshot):
        return
    print("[STATE_STORE] Postgres save skipped — local cache used", file=sys.stderr)


def load_bot_state(into: Dict[str, Any]) -> bool:
    loaded = _pg_load_state()

    if loaded:
        for k, v in loaded.items():
            if v is not None:
                into[k] = v
        return True

    # Fallback: local file
    if not STATE_FILE.exists():
        return False
    try:
        snap = json.loads(STATE_FILE.read_text())
    except Exception as e:
        print(f"[STATE_STORE] state.json unreadable, ignoring: {e}", file=sys.stderr)
        return False
    for k, v in snap.items():
        if v is not None:
            into[k] = v
    return True


# --------------------------------------------------------------------------
# Public API: open orders
# --------------------------------------------------------------------------
def save_open_orders(orders: Dict[str, Dict]) -> None:
    _atomic_write(ORDERS_FILE, json.dumps(orders, indent=2, default=str))

    if _pg_save_orders(orders):
        return
    print("[STATE_STORE] Postgres unavailable for orders — local cache only", file=sys.stderr)


def load_open_orders() -> Dict[str, Dict]:
    orders = _pg_load_orders()
    if orders is not None:
        return orders

    if not ORDERS_FILE.exists():
        return {}
    try:
        return json.loads(ORDERS_FILE.read_text())
    except Exception as e:
        print(f"[STATE_STORE] paper_orders.json unreadable, ignoring: {e}", file=sys.stderr)
        return {}


# --------------------------------------------------------------------------
# Legacy compatibility
# --------------------------------------------------------------------------
def pull_state_from_git() -> None:
    """No-op — persistence moved from git to Postgres."""
    return None
