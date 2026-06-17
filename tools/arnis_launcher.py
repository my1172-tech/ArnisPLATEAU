"""
arnis_launcher.py
arnis v2.9.0 Mosaic対応ラッパー — stdout リアルタイム監視 + dry-run見積もり
"""
import subprocess
import threading
import queue
import os
import json


class ArnisLauncher:
    def __init__(self):
        self.process = None
        self.log_queue = queue.Queue()
        self.bbox_detected = threading.Event()
        self.generation_complete = threading.Event()
        self.bbox_info = {}

    def launch(self, arnis_exe: str, spawn_lat: float = None, spawn_lon: float = None):
        args = [arnis_exe]
        if spawn_lat is not None and spawn_lon is not None:
            args += ["--spawn-lat", str(spawn_lat), "--spawn-lng", str(spawn_lon)]

        self.process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1
        )
        t = threading.Thread(target=self._read_output, daemon=True)
        t.start()

    def _read_output(self):
        for line in self.process.stdout:
            line = line.rstrip()
            self.log_queue.put(line)
            self._parse_line(line)
        self.process.wait()

    def _parse_line(self, line: str):
        # v2.9.0対応: bbox/範囲確定の検知パターン
        BBOX_PATTERNS = [
            "選択を確認しました",          # 旧日本語版
            "bbox",                        # ログ内bbox情報
            "Bounding box",
            "Area selected",
            "Starting generation",         # 生成開始=bbox確定
        ]
        COMPLETE_PATTERNS = [
            "Generation complete",
            "Finished writing",
            "chunks written",
            "World generation finished",
        ]

        line_lower = line.lower()

        if not self.bbox_detected.is_set():
            if any(p.lower() in line_lower for p in BBOX_PATTERNS):
                # bbox情報をJSONから抽出試行
                try:
                    if "{" in line:
                        data = json.loads(line[line.index("{"):])
                        self.bbox_info = data
                except Exception:
                    pass
                self.bbox_detected.set()

        if any(p.lower() in line_lower for p in COMPLETE_PATTERNS):
            self.generation_complete.set()

    def wait_for_bbox(self, timeout=300) -> bool:
        return self.bbox_detected.wait(timeout=timeout)

    def wait_for_complete(self, timeout=3600) -> bool:
        return self.generation_complete.wait(timeout=timeout)

    def get_logs(self) -> list[str]:
        """キューに溜まったログ行をすべて取得して返す"""
        lines = []
        while not self.log_queue.empty():
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def terminate(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()


def find_arnis_exe(base_dir: str) -> str:
    """arnis本体exeを優先順位付きで検索する（v2.9.0以降は arnis-windows.exe が正式名称）"""
    candidates = [
        "arnis-windows.exe",
        "arnis-jp.exe",
        "arnis.exe",
    ]
    for name in candidates:
        path = os.path.join(base_dir, name)
        if os.path.exists(path):
            return path
    return os.path.join(base_dir, candidates[0])


def run_dry_run_estimate(buildings_json: str) -> dict:
    """
    buildings.jsonから建物数・推定API費用を計算する（Street View API: $7/1000回）
    """
    try:
        with open(buildings_json, "r", encoding="utf-8") as f:
            buildings = json.load(f)
        count = len(buildings)
        api_calls = int(count * 1.12)   # 道路判定含む係数
        cost_usd = api_calls * 0.007    # $7/1000calls
        return {
            "buildings": count,
            "api_calls": api_calls,
            "cost_usd": round(cost_usd, 2),
            "free_tier": cost_usd < 200  # 月$200無料枠内か
        }
    except FileNotFoundError:
        return {"error": "buildings.json が見つかりません"}
    except Exception as e:
        return {"error": str(e)}
