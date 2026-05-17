import json
import os
import time
from typing import Optional

from config import BOOKWORK_CACHE_EXPIRY

STORAGE_DIR = "saved_cookies"
os.makedirs(STORAGE_DIR, exist_ok=True)

# ── Temp bookwork storage ────────────────────
_temp_bookwork: dict[int, dict[str, dict]] = {}


def save_bookwork_temp(user_id: int, code: str, task_id: str, answers: list[str]):
    if user_id not in _temp_bookwork:
        _temp_bookwork[user_id] = {}
    _temp_bookwork[user_id][code] = {
        "answers": answers,
        "task_id": task_id,
        "captured_at": int(time.time())
    }


def get_bookwork_temp(user_id: int, code: str) -> Optional[dict]:
    data = _temp_bookwork.get(user_id, {}).get(code)
    if data and (int(time.time()) - data["captured_at"]) > BOOKWORK_CACHE_EXPIRY:
        _temp_bookwork.get(user_id, {}).pop(code, None)
        return None
    return data


def get_all_bookwork_temp(user_id: int) -> dict[str, dict]:
    now = int(time.time())
    codes = _temp_bookwork.get(user_id, {})
    expired = [k for k, v in codes.items() if (now - v["captured_at"]) > BOOKWORK_CACHE_EXPIRY]
    for k in expired:
        codes.pop(k, None)
    return codes


def clear_bookwork_temp(user_id: int):
    _temp_bookwork.pop(user_id, None)


def remove_bookwork_temp(user_id: int, code: str):
    _temp_bookwork.get(user_id, {}).pop(code, None)


def has_bookwork_temp(user_id: int) -> bool:
    return bool(get_all_bookwork_temp(user_id))


def count_bookwork_temp(user_id: int) -> int:
    return len(get_all_bookwork_temp(user_id))


# ── Persistent session storage ───────────────

def _user_path(user_id: int) -> str:
    return os.path.join(STORAGE_DIR, f"{user_id}.json")


def get_account_count(user_id: int) -> int:
    data = load_session(user_id)
    if not data:
        return 0
    return len(data.get("accounts", []))


def save_session(user_id: int, cookie_string: str, info: dict) -> bool:
    path = _user_path(user_id)
    existing = load_session(user_id)
    accounts = existing.get("accounts", []) if existing else []

    email = info.get("email", "")
    for acct in accounts:
        if acct.get("info", {}).get("email") == email:
            acct["cookie"] = cookie_string
            acct["info"] = info
            acct["saved_at"] = int(time.time())
            with open(path, "w") as f:
                json.dump({"user_id": user_id, "accounts": accounts}, f)
            return True

    if len(accounts) >= 3:
        return False

    accounts.append({
        "cookie": cookie_string,
        "info": info,
        "saved_at": int(time.time()),
    })
    with open(path, "w") as f:
        json.dump({"user_id": user_id, "accounts": accounts}, f)
    return True


def load_session(user_id: int) -> Optional[dict]:
    path = _user_path(user_id)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def delete_session(user_id: int):
    path = _user_path(user_id)
    if os.path.exists(path):
        os.remove(path)
    clear_bookwork_temp(user_id)


def remove_account(user_id: int, email: str) -> bool:
    data = load_session(user_id)
    if not data:
        return False
    before = len(data.get("accounts", []))
    data["accounts"] = [a for a in data["accounts"] if a.get("info", {}).get("email") != email]
    if len(data["accounts"]) < before:
        with open(_user_path(user_id), "w") as f:
            json.dump(data, f)
        return True
    return False


def list_all_users() -> list[int]:
    files = os.listdir(STORAGE_DIR)
    ids = []
    for f in files:
        if f.endswith(".json"):
            try:
                ids.append(int(f.replace(".json", "")))
            except ValueError:
                continue
    return ids
