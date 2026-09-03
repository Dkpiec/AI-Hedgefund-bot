"""
State persistence for the AI Hedge Fund Bot.

Uses the GitHub Contents API (HTTPS) to persist state across Render redeploys.
No git CLI, no branch switching, no stash - just HTTP PUT/GET against the
state branch. Works on any ephemeral filesystem.

Local atomic JSON writes still happen for in-deploy restarts.
"""
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

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


def _get_gh_token() -> Optional[str]:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        tok = os.environ.get(key)
        if tok:
            return tok
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        m = re.search(r"x-access-token:([^@]+)@", url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _get_repo() -> str:
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "Dkpiec/AI-Hedgefund-bot"


def _gh_api(method: str, path: str, body: Optional[dict] = None, token: Optional[str] = None) -> Optional[dict]:
    if not token:
        token = _get_gh_token()
    if not token:
        print("[STATE_STORE] No GitHub token available", file=sys.stderr)
        return None
    url = f"https://api.github.com/repos/{_get_repo()}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as resp:
            if resp.status == 204:
                return {}
            return json.loads(resp.read())
    except HTTPError as e:
        print(f"[STATE_STORE] GitHub API {method} {path} -> {e.code}: {e.reason}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[STATE_STORE] GitHub API {method} {path} failed: {e}", file=sys.stderr)
        return None


def _gh_get_file(path: str, branch: str = _STATE_BRANCH) -> Optional[str]:
    result = _gh_api("GET", f"/contents/{path}?ref={branch}")
    if not result or "content" not in result:
        return None
    try:
        return base64.b64decode(result["content"]).decode("utf-8")
    except Exception as e:
        print(f"[STATE_STORE] Failed to decode {path}: {e}", file=sys.stderr)
        return None


def _gh_put_file(path: str, content: str, message: str, branch: str = _STATE_BRANCH) -> bool:
    existing = _gh_api("GET", f"/contents/{path}?ref={branch}")
    sha = existing.get("sha") if existing and "sha" in existing else None
    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    result = _gh_api("PUT", f"/contents/{path}", body=body)
    return result is not None


def pull_state_from_git() -> None:
    token = _get_gh_token()
    if not token:
        print("[STATE_STORE] No GitHub token; skipping pull", file=sys.stderr)
        return
    files_map = {
        "data/state.json": STATE_FILE,
        "data/paper_orders.json": ORDERS_FILE,
        "data/paper_balance.txt": BALANCE_FILE,
    }
    restored = 0
    for remote_path, local_path in files_map.items():
        content = _gh_get_file(remote_path, _STATE_BRANCH)
        if content is not None:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(content)
            restored += 1
    if restored:
        print(f"[STATE_STORE] Restored {restored}/{len(files_map)} files from {_STATE_BRANCH} via API", file=sys.stderr)
    else:
        print(f"[STATE_STORE] No state files found on {_STATE_BRANCH} branch", file=sys.stderr)


def _push_state_to_github_async(message: str = "auto: state update") -> None:
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
            files_map = {
                "data/state.json": STATE_FILE,
                "data/paper_orders.json": ORDERS_FILE,
                "data/paper_balance.txt": BALANCE_FILE,
            }
            pushed = 0
            for remote_path, local_path in files_map.items():
                if not local_path.exists():
                    continue
                content = local_path.read_text()
                if _gh_put_file(remote_path, content, message, _STATE_BRANCH):
                    pushed += 1
            _last_git_push_ts = time.time()
            if pushed:
                print(f"[STATE_STORE] Pushed {pushed} files to {_STATE_BRANCH} via API", file=sys.stderr)

    t = threading.Thread(target=_do, daemon=True)
    t.start()


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
    _push_state_to_github_async(f"auto: balance={bal}, trades={trades}")


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
    _push_state_to_github_async(f"auto: {len(orders)} open orders")


def load_open_orders() -> Dict[str, Dict]:
    if not ORDERS_FILE.exists():
        return {}
    try:
        return json.loads(ORDERS_FILE.read_text())
    except Exception as e:
        print(f"[STATE_STORE] paper_orders.json unreadable, ignoring: {e}", file=sys.stderr)
        return {}
