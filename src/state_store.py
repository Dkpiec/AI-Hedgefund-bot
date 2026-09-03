"""
State persistence for the AI Hedge Fund Bot.

Solves TWO problems:
1. Process restart within same deploy -> local JSON files (atomic writes)
2. Full redeploy (git push -> Render rebuild) -> state committed to a
   dedicated state branch on GitHub, pulled back on startup.

Render only auto-deploys from main, so pushing to state never
triggers a redeploy loop.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = DATA_DIR / "state.json"
ORDERS_FILE = DATA_DIR / "paper_orders.json"
BALANCE_FILE = DATA_DIR / "paper_balance.txt"

_PERSISTED_BOT_KEYS = [
    "starting_balance", "balance", "equity", "free",
    "pnl", "pnl_pct", "trade_history", "cancelled_orders",
    "cycles_completed", "current_model", "resolved_model",
    "scan_interval", "interval", "chart_timeframe",
    "started_at", "is_running", "universe",
]

_STATE_BRANCH = "state"
_GIT_LOCK = threading.Lock()
_last_git_push_ts: float = 0.0
_GIT_PUSH_MIN_INTERVAL = 60.0


def _run_git(*args: str, timeout: int = 30) -> bool:
    try:
        subprocess.run(
            ["git"] + list(args),
            cwd=str(_REPO_ROOT),
            capture_output=True,
            timeout=timeout,
        )
        return True
    except Exception as e:
        print(f"[STATE_STORE] git {chr(32).join(args)} failed: {e}", file=sys.stderr)
        return False


def _git_push_state_async(message: str = "auto: state update") -> None:
    global _last_git_push_ts
    now = time.time()
    if now - _last_git_push_ts < _GIT_PUSH_MIN_INTERVAL:
        return

    def _do():
        global _last_git_push_ts
        with _GIT_LOCK:
            now2 = time.time()
            if now2 - _last_git_push_ts < _GIT_PUSH_MIN_INTERVAL:
                return
            current_branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            _run_git("stash", "--include-untracked", "-q")
            if not _run_git("checkout", _STATE_BRANCH, "-q"):
                _run_git("checkout", current_branch, "-q")
                _run_git("stash", "pop", "-q")
                return
            _run_git("add", "data/state.json", "data/paper_orders.json", "data/paper_balance.txt")
            status = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=str(_REPO_ROOT), capture_output=True,
            )
            if status.returncode == 0:
                _run_git("checkout", current_branch, "-q")
                _run_git("stash", "pop", "-q")
                _last_git_push_ts = time.time()
                return
            _run_git("commit", "-m", message, "-q")
            _run_git("push", "origin", _STATE_BRANCH, "-q")
            _run_git("checkout", current_branch, "-q")
            _run_git("stash", "pop", "-q")
            _last_git_push_ts = time.time()
            print(f"[STATE_STORE] Pushed state to {_STATE_BRANCH} branch", file=sys.stderr)

    t = threading.Thread(target=_do, daemon=True)
    t.start()


def pull_state_from_git() -> None:
    with _GIT_LOCK:
        if not _run_git("fetch", "origin", _STATE_BRANCH, "-q"):
            print("[STATE_STORE] Could not fetch state branch", file=sys.stderr)
            return
        for f in ["data/state.json", "data/paper_orders.json", "data/paper_balance.txt"]:
            _run_git("checkout", f"origin/{_STATE_BRANCH}", "--", f)
        print(f"[STATE_STORE] Restored data files from {_STATE_BRANCH} branch", file=sys.stderr)


def _atomic_write(path: Path, payload: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(payload)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[STATE_STORE] Failed to write {path}: {e}", file=sys.stderr)


def save_bot_state(state: Dict[str, Any]) -> None:
    snapshot = {k: state.get(k) for k in _PERSISTED_BOT_KEYS if k in state}
    _atomic_write(STATE_FILE, json.dumps(snapshot, indent=2, default=str))
    trades = len(snapshot.get("trade_history", []))
    bal = snapshot.get("balance", "?")
    _git_push_state_async(f"auto: balance={bal}, trades={trades}")


def load_bot_state(into: Dict[str, Any]) -> bool:
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


def save_open_orders(orders: Dict[str, Dict]) -> None:
    _atomic_write(ORDERS_FILE, json.dumps(orders, indent=2, default=str))
    _git_push_state_async(f"auto: {len(orders)} open orders")


def load_open_orders() -> Dict[str, Dict]:
    if not ORDERS_FILE.exists():
        return {}
    try:
        return json.loads(ORDERS_FILE.read_text())
    except Exception as e:
        print(f"[STATE_STORE] paper_orders.json unreadable, ignoring: {e}", file=sys.stderr)
        return {}
