"""
arnis_version_manager.py
arnisのバージョン確認・自動更新チェック・CLIフラグ動的検出モジュール。
GitHub API結果は24時間キャッシュして過剰リクエストを防ぐ。
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "ArnisPLATEAU"

_CREATE_NO_WINDOW = 0x08000000


class ArnisVersionManager:
    GITHUB_API = "https://api.github.com/repos/louis-e/arnis/releases/latest"
    CACHE_PATH = CACHE_DIR / "version_cache.json"
    CACHE_TTL_HOURS = 24

    def get_current_version(self, arnis_exe_path: str) -> str | None:
        """arnis --version の出力からバージョン文字列を取得する。"""
        try:
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = _CREATE_NO_WINDOW
            result = subprocess.run(
                [arnis_exe_path, "--version"],
                capture_output=True, text=True, timeout=10, **kwargs
            )
            output = (result.stdout + result.stderr).strip()
            m = re.search(r"(\d+\.\d+\.\d+)", output)
            if m:
                return m.group(1)
        except Exception:
            pass
        return None

    def get_latest_release(self) -> dict:
        """GitHub Releases APIから最新リリース情報を取得する（24hキャッシュあり）。
        返却: {"tag_name": "v2.x.x", "assets": [...], "checked_at": "..."}
        ネットワーク不可時はキャッシュまたは空dictを返す。
        """
        cached = self._load_cache()
        if cached:
            return cached

        try:
            req = urllib.request.Request(
                self.GITHUB_API,
                headers={"User-Agent": "ArnisPLATEAU/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            result = {
                "tag_name": data.get("tag_name", ""),
                "assets": data.get("assets", []),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save_cache(result)
            return result
        except Exception as e:
            print(f"[ArnisVersionManager] GitHub API取得失敗（キャッシュなし）: {e}")
            return {"tag_name": "", "assets": [], "checked_at": ""}

    def is_update_available(self, arnis_exe_path: str) -> tuple[bool, str]:
        """現行バージョンと最新バージョンを比較する。
        返却: (更新あり: bool, 最新バージョン文字列: str)
        バージョン不明の場合は (False, latest_str) を返す。
        """
        current = self.get_current_version(arnis_exe_path)
        release = self.get_latest_release()
        latest = release.get("tag_name", "").lstrip("v")
        if not current or not latest:
            return False, latest
        try:
            cur_tuple = tuple(int(x) for x in current.split("."))
            lat_tuple = tuple(int(x) for x in latest.split("."))
            return lat_tuple > cur_tuple, latest
        except ValueError:
            return False, latest

    def download_latest(self, dest_dir: str, progress_callback=None) -> str:
        """最新版Windows用exeをダウンロードして dest_dir に保存する。
        progress_callback(percent: float, message: str) で進捗を通知する。
        返却: ダウンロードしたexeのフルパス
        """
        release = self.get_latest_release()
        assets = release.get("assets", [])
        win_assets = [
            a for a in assets
            if "windows" in a.get("name", "").lower()
            and a.get("name", "").endswith(".exe")
        ]
        if not win_assets:
            raise RuntimeError("Windows用exeがリリースアセットに見つかりません")

        url = win_assets[0]["browser_download_url"]
        name = win_assets[0]["name"]
        dest = os.path.join(dest_dir, name)

        if progress_callback:
            progress_callback(0.0, f"ダウンロード開始: {name}")

        req = urllib.request.Request(url, headers={"User-Agent": "ArnisPLATEAU/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk = b""
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total > 0:
                        pct = downloaded / total * 100
                        progress_callback(pct, f"{downloaded//1024}KB / {total//1024}KB")

        if progress_callback:
            progress_callback(100.0, f"ダウンロード完了: {dest}")
        return dest

    def check_cli_flags(self, arnis_exe_path: str) -> dict[str, bool]:
        """arnis --help の出力をパースして利用可能なフラグを返す。
        バージョンアップでフラグが追加・廃止されても動的に吸収するための仕組み。
        返却: {"--bbox": True, "--terrain": True, ...}
        """
        known_flags = [
            "--bbox", "--path", "--output-dir", "--bedrock", "--terrain",
            "--scale", "--ground-level", "--spawn-lat", "--spawn-lng",
            "--save-json-file", "--timeout",
        ]
        result = {flag: False for flag in known_flags}
        try:
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = _CREATE_NO_WINDOW
            proc = subprocess.run(
                [arnis_exe_path, "--help"],
                capture_output=True, text=True, timeout=10, **kwargs
            )
            help_text = proc.stdout + proc.stderr
            for flag in known_flags:
                result[flag] = flag in help_text
        except Exception:
            pass
        return result

    def _load_cache(self) -> dict | None:
        try:
            if not self.CACHE_PATH.exists():
                return None
            with open(self.CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            checked_at = data.get("checked_at", "")
            if not checked_at:
                return None
            age_hours = (
                datetime.now(timezone.utc) - datetime.fromisoformat(checked_at)
            ).total_seconds() / 3600
            if age_hours < self.CACHE_TTL_HOURS:
                return data
        except Exception:
            pass
        return None

    def _save_cache(self, data: dict):
        try:
            self.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self.CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ArnisVersionManager] キャッシュ保存失敗: {e}")
