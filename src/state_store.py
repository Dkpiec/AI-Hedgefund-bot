"""
State persistence for the AI Hedge Fund Bot.

Primary store: Turso (libSQL) over its HTTPS pipeline API — survives
Render redeploys because state lives in an external database (Mumbai
region), not on Render's ephemeral filesystem.

Local JSON files are still written on every save as a fallback/cache,
and are used if Turso is unreachable.

No third-party dependencies — uses only the Python stdlib (urllib).

Env vars (set on Render):
  TURSO_DB_TOKEN  – database auth token (required for remote persistence)
  TURSO_DB_URL    – override DB URL (default baked in below)
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
# Turso config
# --------------------------------------------------------------------------
TURSO_DB_URL = os.environ.get(
    "TURSO_DB_URL",
    "https://hedgefund-state-dkpiec.aws-ap-south-1.turso.io",
).rstrip("/")

_SCHEMA_STATEMENTS = [
    "CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS trade_history (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "trade_json TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')))",
    "CREATE TABLE IF NOT EXISTS open_orders (order_id TEXT PRIMARY KEY, "
    "order_json TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))",
]

_schema_ready = False


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
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "value": list(v)}
    return {"type": "text", "value": str(v)}


def _pipeline(requests: List[Dict], timeout: int = 15) -> Optional[Dict]:
    """POST a batch of statements to the Turso v2 pipeline API.

    Returns the parsed response, or None if unavailable/failed.
    Each request: {"type": "execute", "stmt": {"sql": ..., "args": [...]}}
    """
    global _schema_ready
    token = _turso_token()
    if not token:
        return None

    from urllib.request import Request, urlopen

    if not _schema_ready:
        schema_reqs = [
            {"type": "execute", "stmt": {"sql": s}} for s in _SCHEMA_STATEMENTS
        ]
        requests = schema_reqs + requests
        _schema_ready = True  # optimistic; retried next call on failure

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
        _schema_ready = False
        print(f"[STATE_STORE] Turso request failed: {e}", file=sys.stderr)
        return None


def _pipeline_ok(resp: Optional[Dict]) -> bool:
    if not resp:
        return False
    results = resp.get("results", [])
    return all(r.get("type") != "error" for r in results)


def _unwrap_row(row: Any, cols: List[Dict]) -> Dict[str, Any]:
    """Convert positional tagged-value array into a dict keyed by column name."""
    if isinstance(row, dict):
        # Already a dict (older API formats)
        return row
    if isinstance(row, list) and cols:
        result = {}
        for i, cell in enumerate(row):
            if i < len(cols):
                col_name = cols[i].get("name", f"col_{i}")
                # Unwrap the tagged value format
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


def _rows_from_response(resp: Optional[Dict]) -> tuple[List[Dict], List[Dict]]:
    """Extract rows and column metadata from a pipeline response."""
    if not resp:
        return [], []
    try:
        result = resp["results"][0]["response"]["result"]
        rows = result.get("rows", [])
        cols = result.get("cols", [])
        return rows, cols
    except (KeyError, IndexError):
        return [], []


# --------------------------------------------------------------------------
# Local atomic file helpers (fallback cache)
# --------------------------------------------------------------------------
def _atomic_write(path: Path, payload: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(payload)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[STATE_STORE] Failed to write {path}: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# bot_state
# --------------------------------------------------------------------------
def save_bot_state(state: Dict[str, Any]) -> None:
    snapshot = {k: state.get(k) for k in _PERSISTED_BOT_KEYS if k in state}

    # Local cache (always)
    _atomic_write(STATE_FILE, json.dumps(snapshot, indent=2, default=str))

    # Remote (Turso)
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
    if _pipeline_ok(_pipeline(requests)):
        print(f"[STATE_STORE] Saved state to Turso ({len(snapshot)} keys)", file=sys.stderr)
    else:
        print("[STATE_STORE] Turso save failed — local cache only", file=sys.stderr)


def load_bot_state(into: Dict[str, Any]) -> bool:
    # Try Turso first
    resp = _pipeline([
        {"type": "execute", "stmt": {"sql": "SELECT key, value FROM bot_state"}}
    ])
    if _pipeline_ok(resp):
        rows, cols = _rows_from_response(resp)
        if rows:
            applied = 0
            for row in rows:
                row_dict = _unwrap_row(row, cols)
                k = row_dict.get("key")
                if k is None:
                    continue
                try:
                    v = json.loads(row_dict.get("value", "null"))
                except Exception:
                    continue
                if v is not None:
                    into[k] = v
                    applied += 1
            print(f"[STATE_STORE] Loaded state from Turso ({applied} keys)", file=sys.stderr)
            return applied > 0
        print("[STATE_STORE] Turso state empty — falling back to local", file=sys.stderr)

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
# open orders
# --------------------------------------------------------------------------
def save_open_orders(orders: Dict[str, Dict]) -> None:
    # Local cache (always)
    _atomic_write(ORDERS_FILE, json.dumps(orders, indent=2, default=str))

    # Remote: full replace in one atomic batch
    requests: List[Dict] = [{"type": "execute", "stmt": {"sql": "DELETE FROM open_orders"}}]
    for order_id, order in orders.items():
        requests.append({
            "type": "execute",
            "stmt": {
                "sql": "INSERT INTO open_orders(order_id, order_json) VALUES(?, ?)",
                "args": [_val(order_id), _val(json.dumps(order, default=str))],
            },
        })
    if _pipeline_ok(_pipeline(requests)):
        print(f"[STATE_STORE] Saved {len(orders)} open orders to Turso", file=sys.stderr)
    else:
        print("[STATE_STORE] Turso orders save failed — local cache only", file=sys.stderr)


def load_open_orders() -> Dict[str, Dict]:
    resp = _pipeline([
        {"type": "execute", "stmt": {"sql": "SELECT order_id, order_json FROM open_orders"}}
    ])
    if _pipeline_ok(resp):
        rows, cols = _rows_from_response(resp)
        orders: Dict[str, Dict] = {}
        for row in rows:
            row_dict = _unwrap_row(row, cols)
            order_id = row_dict.get("order_id")
            if order_id is None:
                continue
            try:
                orders[order_id] = json.loads(row_dict.get("order_json", "{}"))
            except Exception:
                continue
        print(f"[STATE_STORE] Loaded {len(orders)} open orders from Turso", file=sys.stderr)
        return orders

    # Fallback: local file
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
    """No-op — persistence moved from git to Turso."""
    return None