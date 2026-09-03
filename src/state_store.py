"""
State persistence for the AI Hedge Fund Bot.

Persistence priority (first available wins):
1. PostgreSQL (env DATABASE_URL) — used when running on Coolify next to
   hedgefund-pg on hedgefund-net. Survives redeploys and restarts.
2. Turso (libSQL HTTPS API, env TURSO_DB_TOKEN) — legacy cloud fallback.
3. Local JSON files — always written as cache; used when no remote store
   is reachable.

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
# Turso config (fallback cloud store)
# --------------------------------------------------------------------------
TURSO_DB_URL = os.environ.get(
    "TURSO_DB_URL",
    "https://hedgefund-state-dkpiec.aws-ap-south-1.turso.io",
).rstrip("/")

_SCHEMA_STATEMENTS = [
    "CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS open_orders (order_id TEXT PRIMARY KEY, "
    "order_json TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))",
]

_turso_schema_ready = False


def _turso_token() -> str:
    return os.environ.get("TURSO_DB_TOKEN") or os.environ.get("TURSO_AUTH_TOKEN") or ""


def _val(v: Any) -> Dict:
    """Wrap a Python value in Turso's internally-tagged Value enum format."""
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": int(v)}
    if isinstance(v, int):
        return {"type": "integer", "value": v}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def _pipeline(requests: List[Dict], timeout: int = 15) -> Optional[Dict]:
    global _turso_schema_ready
    token = _turso_token()
    if not token:
        return None

    from urllib.request import Request, urlopen

    if not _turso_schema_ready:
        schema_reqs = [
            {"type": "execute", "stmt": {"sql": s}} for s in _SCHEMA_STATEMENTS
        ]
        requests = schema_reqs + requests
        _turso_schema_ready = True

    payload = json.dumps({"requests": requests}).encode()
    req = Request(
        f"{TURSO_DB_URL}/v2/pipeline",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        _turso_schema_ready = False
        print(f"[STATE_STORE] Turso request failed: {e}", file=sys.stderr)
        return None


def _pipeline_ok(resp: Optional[Dict]) -> bool:
    if not resp:
        return False
    results = resp.get("results", [])
    return all(r.get("type") != "error" for r in results)


def _unwrap_row(row: Any, cols: List[Dict]) -> Dict[str, Any]:
    if isinstance(row, dict):
        return row
    if isinstance(row, list) and cols:
        result = {}
        for i, cell in enumerate(row):
            if i < len(cols):
                col_name = cols[i].get("name", f"col_{i}")
                if isinstance(cell, dict):
                    if cell.get("type") == "null":
                        result[col_name] = None
                    elif "value" in cell:
                        result[col_name] = cell["value"]
                    else:
                        result[col_name] = cell
                else:
                    result[col_name] = cell
        return result
    return {}


def _rows_from_response(resp: Optional[Dict]):
    if not resp:
        return [], []
    try:
        result = resp["results"][0]["response"]["result"]
        return result.get("rows", []), result.get("cols", [])
    except (KeyError, IndexError, TypeError):
        return [], []


def _turso_save_state(snapshot: Dict[str, Any]) -> bool:
    requests = [
        {
            "type": "execute",
            "stmt": {
                "sql": "INSERT INTO bot_state(key, value) VALUES(?, ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                "args": [_val(k), _val(json.dumps(v, default=str))],
            },
        }
        for k, v in snapshot.items()
    ]
    return _pipeline_ok(_pipeline(requests))


def _turso_load_state() -> Optional[Dict[str, Any]]:
    resp = _pipeline([
        {"type": "execute", "stmt": {"sql": "SELECT key, value FROM bot_state"}}
    ])
    if not _pipeline_ok(resp):
        return None
    rows, cols = _rows_from_response(resp)
    if not rows:
        return None
    out: Dict[str, Any] = {}
    for row in rows:
        d = _unwrap_row(row, cols)
        k = d.get("key")
        if k is None:
            continue
        try:
            v = json.loads(d.get("value", "null"))
        except Exception:
            continue
        if v is not None:
            out[k] = v
    if not out:
        return None
    print(f"[STATE_STORE] Loaded state from Turso ({len(out)} keys)", file=sys.stderr)
    return out


def _turso_save_orders(orders: Dict[str, Dict]) -> bool:
    requests: List[Dict] = [{"type": "execute", "stmt": {"sql": "DELETE FROM open_orders"}}]
    for oid, o in orders.items():
        requests.append({
            "type": "execute",
            "stmt": {
                "sql": "INSERT INTO open_orders(order_id, order_json) VALUES(?, ?)",
                "args": [_val(oid), _val(json.dumps(o, default=str))],
            },
        })
    return _pipeline_ok(_pipeline(requests))


def _turso_load_orders() -> Optional[Dict[str, Dict]]:
    resp = _pipeline([
        {"type": "execute", "stmt": {"sql": "SELECT order_id, order_json FROM open_orders"}}
    ])
    if not _pipeline_ok(resp):
        return None
    rows, cols = _rows_from_response(resp)
    orders: Dict[str, Dict] = {}
    for row in rows:
        d = _unwrap_row(row, cols)
        oid = d.get("order_id")
        if oid is None:
            continue
        try:
            orders[oid] = json.loads(d.get("order_json", "{}"))
        except Exception:
            continue
    print(f"[STATE_STORE] Loaded {len(orders)} open orders from Turso", file=sys.stderr)
    return orders


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

    # Remote: Postgres first, then Turso
    if _pg_save_state(snapshot):
        return
    if _turso_save_state(snapshot):
        print("[STATE_STORE] Saved state to Turso (PG unavailable)", file=sys.stderr)
        return
    print("[STATE_STORE] No remote store available — local cache only", file=sys.stderr)


def load_bot_state(into: Dict[str, Any]) -> bool:
    # Postgres first
    loaded = _pg_load_state()
    if loaded is None:
        loaded = _turso_load_state()

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
    if _turso_save_orders(orders):
        return
    print("[STATE_STORE] No remote store for orders — local cache only", file=sys.stderr)


def load_open_orders() -> Dict[str, Dict]:
    orders = _pg_load_orders()
    if orders is not None:
        return orders

    orders = _turso_load_orders()
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
    """No-op — persistence moved from git to Postgres/Turso."""
    return None