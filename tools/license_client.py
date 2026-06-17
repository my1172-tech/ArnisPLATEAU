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
MAX_TRIAL_RADIUS_M = 300
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


def increment_trial():
    _increment_trial()


def bbox_radius_m(bbox: dict) -> float:
    """bboxの最大辺を半径(m)に換算して返す（簡易計算）"""
    import math
    lat_c = (bbox["min_lat"] + bbox["max_lat"]) / 2
    dlat = abs(bbox["max_lat"] - bbox["min_lat"]) * 111000
    dlon = abs(bbox["max_lon"] - bbox["min_lon"]) * 111000 * math.cos(math.radians(lat_c))
    return max(dlat, dlon) / 2


def clip_bbox_to_trial(bbox: dict) -> dict:
    """トライアル制限半径内にbboxを縮小して返す"""
    import math
    lat_c = (bbox["min_lat"] + bbox["max_lat"]) / 2
    lon_c = (bbox["min_lon"] + bbox["max_lon"]) / 2
    r = MAX_TRIAL_RADIUS_M
    dlat = r / 111000
    dlon = r / (111000 * math.cos(math.radians(lat_c)))
    return {
        "min_lat": lat_c - dlat,
        "max_lat": lat_c + dlat,
        "min_lon": lon_c - dlon,
        "max_lon": lon_c + dlon,
    }


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
