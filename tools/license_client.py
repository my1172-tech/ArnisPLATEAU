"""
license_client.py
ArnisPLATEAU Pro ライセンス管理クライアント
"""
import json
import time
import hashlib
import base64
import os
from pathlib import Path

CACHE_PATH = Path(os.path.expanduser("~")) / ".arnisplateau" / "license.cache"
MAX_TRIAL_RUNS = 3
TRIAL_COUNT_PATH = Path(os.path.expanduser("~")) / ".arnisplateau" / "trial.json"
LICENSE_SERVER = "https://arnisplateau-license.workers.dev"


def _encode(data: str) -> str:
    return base64.b64encode(data.encode()).decode()


def _decode(data: str) -> str:
    return base64.b64decode(data.encode()).decode()


def is_licensed() -> bool:
    """
    ライセンス認証済みかどうかを確認する。
    DEV_MODE=Trueの場合は常にTrueを返す（テスト用）。
    """
    try:
        from _build_config import DEV_MODE
        if DEV_MODE:
            return True
    except ImportError:
        pass

    try:
        if not CACHE_PATH.exists():
            return False
        raw = _decode(CACHE_PATH.read_text().strip())
        cache = json.loads(raw)
        cached_at = cache.get("cached_at", 0)
        if time.time() - cached_at > 86400:
            return False
        return cache.get("valid", False)
    except Exception:
        return False


def is_trial_expired() -> bool:
    return get_trial_count() >= MAX_TRIAL_RUNS


def get_trial_count() -> int:
    try:
        if not TRIAL_COUNT_PATH.exists():
            return 0
        data = json.loads(TRIAL_COUNT_PATH.read_text())
        return int(data.get("count", 0))
    except Exception:
        return 0


def _increment_trial():
    TRIAL_COUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    count = get_trial_count() + 1
    TRIAL_COUNT_PATH.write_text(json.dumps({"count": count}))


def activate(license_key: str) -> bool:
    """ライセンスキーをサーバーで認証してキャッシュに保存する"""
    try:
        import urllib.request
        payload = json.dumps({"key": license_key}).encode()
        req = urllib.request.Request(
            f"{LICENSE_SERVER}/activate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        valid = result.get("valid", False)
        _save_cache(valid)
        return valid
    except Exception:
        return False


def _save_cache(valid: bool):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"valid": valid, "cached_at": time.time()})
    CACHE_PATH.write_text(_encode(data))
