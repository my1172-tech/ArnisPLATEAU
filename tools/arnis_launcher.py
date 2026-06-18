"""
arnis_launcher.py
arnis v2.9.0 CLIモードラッパー
--bbox / --output-dir / --bedrock を渡してCLI直接起動し、stdoutを監視する。
wait_for_bbox は不要（CLI起動なのでbbox確定待機が存在しない）。
"""
import subprocess
import threading
import queue
import os
import sys
import json

# コンソールウィンドウを非表示にする Windows フラグ
_CREATE_NO_WINDOW = 0x08000000


class ArnisLauncher:
    def __init__(self):
        self.process = None
        self.log_queue = queue.Queue()
        self.generation_complete = threading.Event()
        self.world_path: str = None  # 完了ログから抽出した生成ワールドパス

    def launch(
        self,
        arnis_exe: str,
        bbox: dict,
        output_dir: str,
        bedrock: bool = True,
        spawn_lat: float = None,
        spawn_lon: float = None,
        save_json_path: str = None,
    ):
        """
        arnis-windows.exe を CLI モードで起動する。
        bbox:  {"min_lat", "min_lon", "max_lat", "max_lon"}
        output_dir: 既存ディレクトリ（arnis がワールドフォルダをその中に作成）
        bedrock: True → --bedrock フラグを渡す（.mcworld 互換形式）
        save_json_path: 指定すると --save-json-file でOSM生データを保存（GSI merge用）
        """
        # --bbox "min_lat,min_lng,max_lat,max_lng"
        bbox_str = (
            f"{bbox['min_lat']},{bbox['min_lon']},"
            f"{bbox['max_lat']},{bbox['max_lon']}"
        )
        args = [arnis_exe, "--bbox", bbox_str, "--output-dir", output_dir]
        if bedrock:
            args.append("--bedrock")
        if save_json_path:
            args += ["--save-json-file", save_json_path]
        if spawn_lat is not None and spawn_lon is not None:
            args += ["--spawn-lat", str(spawn_lat), "--spawn-lng", str(spawn_lon)]

        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = _CREATE_NO_WINDOW

        self.process = subprocess.Popen(args, **popen_kwargs)
        t = threading.Thread(target=self._read_output, daemon=True)
        t.start()

    def _read_output(self):
        for line in self.process.stdout:
            line = line.rstrip()
            self.log_queue.put(line)
            self._parse_line(line)
        self.process.wait()

    def _parse_line(self, line: str):
        # ワールドパスを "Created new world at: /path" から先行抽出（起動直後に出力される）
        line_lower = line.lower()
        if "created new world at:" in line_lower:
            idx = line_lower.index("created new world at:") + len("created new world at:")
            self.world_path = line[idx:].strip()

        # 完了パターン（arnis 2.9.0 実測に基づく）
        # - CLI Java:    "[7/7] Saving world..."  ← arnis が出力する最終行（プロセス終了直前）
        # - CLI Bedrock: "Done! Bedrock world saved to: /path"
        # - GUI モード:  "Done! World generation completed."
        COMPLETE_PATTERNS = [
            "Saving world",          # Java CLI: "[7/7] Saving world..." がプロセス終了直前の最終行
            "Done! Bedrock world saved to:",
            "Done! World generation completed.",
            "Generation complete",
            "World generation finished",
        ]
        for pattern in COMPLETE_PATTERNS:
            if pattern.lower() in line_lower:
                if "bedrock world saved to:" in line_lower:
                    idx = line_lower.index("bedrock world saved to:") + len("bedrock world saved to:")
                    self.world_path = line[idx:].strip()
                self.generation_complete.set()
                break

    def wait_for_complete(self, timeout: int = 3600) -> bool:
        return self.generation_complete.wait(timeout=timeout)

    def get_logs(self) -> list:
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
    candidates = ["arnis-windows.exe", "arnis-jp.exe", "arnis.exe"]
    for name in candidates:
        path = os.path.join(base_dir, name)
        if os.path.exists(path):
            return path
    return os.path.join(base_dir, candidates[0])


def run_dry_run_estimate(buildings_json: str) -> dict:
    """buildings.json から建物数・推定API費用を計算する（Street View API: $7/1000回）"""
    try:
        with open(buildings_json, "r", encoding="utf-8") as f:
            buildings = json.load(f)
        count = len(buildings)
        api_calls = int(count * 1.12)
        cost_usd = api_calls * 0.007
        return {
            "buildings": count,
            "api_calls": api_calls,
            "cost_usd": round(cost_usd, 2),
            "free_tier": cost_usd < 200,
        }
    except FileNotFoundError:
        return {"error": "buildings.json が見つかりません"}
    except Exception as e:
        return {"error": str(e)}
