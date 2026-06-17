"""
arnis_colorize_gui.py
ArnisPLATEAU カラー適用・ワールド生成GUI — v2.9.0 Mosaic対応 / Free・Pro・ProDev 3ビルド対応
"""
import os
import sys
import subprocess
import threading
import zipfile
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from datetime import datetime

# ビルド設定読み込み (TASK 3 of EXE_v3)
try:
    from _build_config import PRO_MODE
    try:
        from _build_config import DEV_MODE
    except ImportError:
        DEV_MODE = False
except ImportError:
    PRO_MODE = os.environ.get("ARNISPLATEAU_PRO", "0") == "1"
    DEV_MODE = os.environ.get("ARNISPLATEAU_DEV", "0") == "1"

# Launcher接続 (TASK 4)
from arnis_launcher import ArnisLauncher, find_arnis_exe as _launcher_find_arnis_exe

# Pro版のみライセンス関数をインポート
if PRO_MODE:
    try:
        from license_client import (
            is_licensed, is_trial_expired, get_trial_count, MAX_TRIAL_RUNS,
            clip_bbox_to_trial, increment_trial, bbox_radius_m, MAX_TRIAL_RADIUS_M,
        )
    except ImportError:
        def is_licensed(): return False
        def is_trial_expired(): return False
        def get_trial_count(): return 0
        def clip_bbox_to_trial(bbox): return bbox
        def increment_trial(): pass
        def bbox_radius_m(bbox): return 0
        MAX_TRIAL_RUNS = 3
        MAX_TRIAL_RADIUS_M = 300


# v2.9.0以降のログパターン
COMPLETION_PATTERNS = [
    "Generation complete",
    "World generation finished",
    "Finished writing",
    "chunks written",
]


def is_generation_complete(line: str) -> bool:
    return any(p.lower() in line.lower() for p in COMPLETION_PATTERNS)


def find_arnis_exe(base_dir: str) -> str:
    """
    arnis本体exeを優先順位付きで検索する。
    v2.9.0以降は arnis-windows.exe が正式名称。
    ローカルビルド・旧版との互換性のため複数名を探索する。
    """
    candidates = [
        "arnis-windows.exe",   # v2.9.0+ 公式
        "arnis-jp.exe",        # ArnisPLATEAU fork旧名
        "arnis.exe",           # 汎用フォールバック
    ]
    for name in candidates:
        path = os.path.join(base_dir, name)
        if os.path.exists(path):
            return path
    return os.path.join(base_dir, candidates[0])


class ArnisColorizeGUI:
    def __init__(self, root: tk.Tk):
        self.root = root

        # ウィンドウタイトル
        if PRO_MODE:
            title = "ArnisPLATEAU Pro v0.2.0" if not DEV_MODE else "ArnisPLATEAU Pro v0.2.0 [DEV]"
        else:
            title = "ArnisPLATEAU v0.2.0"
        self.root.title(title)
        self.root.resizable(True, True)

        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.arnis_exe = find_arnis_exe(self.base_dir)

        # 出力設定変数
        self.world_folder = tk.StringVar(value="")
        self.custom_output_enabled = tk.BooleanVar(value=False)
        self.custom_output_path = tk.StringVar(value="")
        self.mcworld_enabled = tk.BooleanVar(value=False)

        # bbox入力用変数 (TASK 2)
        self.bbox_min_lat = tk.StringVar(value="")
        self.bbox_max_lat = tk.StringVar(value="")
        self.bbox_min_lon = tk.StringVar(value="")
        self.bbox_max_lon = tk.StringVar(value="")

        # GSI設定（デフォルトON）(TASK 4)
        self.gsi_enabled = tk.BooleanVar(value=True)

        # スポーン地点
        self.spawn_lat = None
        self.spawn_lon = None

        self._build_ui()

    def _build_ui(self):
        # TASK 5: セクション呼び出し順序
        self._build_status_bar(self.root)
        self._build_bbox_section(self.root)       # TASK 2
        self._build_gsi_section(self.root)        # TASK 4
        self._build_world_gen_section(self.root)  # TASK 3
        self._build_output_section(self.root)

        if PRO_MODE:
            self._build_license_section(self.root)
            self._build_api_key_section(self.root)
            self._build_colorize_section(self.root)

        self._build_generate_section(self.root)   # 色付けセクション（末尾固定）

    # ── ステータスバー ────────────────────────────────────────────────────────

    def _build_status_bar(self, parent):
        frame = tk.Frame(parent, bg="#1E3A5F", pady=4)
        frame.pack(fill="x")

        if not PRO_MODE:
            tk.Label(frame,
                     text="ArnisPLATEAU Free  —  リアルな日本の街をMinecraftで再現",
                     bg="#1E3A5F", fg="#FFFFFF",
                     font=("Arial", 10, "bold")).pack(side="left", padx=12)
            tk.Button(frame, text="Pro版を見る",
                      command=lambda: __import__('webbrowser').open("https://gumroad.com/"),
                      bg="#2563AE", fg="white", font=("Arial", 9),
                      relief="flat", padx=8).pack(side="right", padx=12)
            return

        if DEV_MODE:
            msg, fg = "DEVモード  ライセンス認証スキップ中", "#FDE68A"
        elif is_licensed():
            msg, fg = "製品版  ライセンス有効", "#86EFAC"
        elif is_trial_expired():
            msg, fg = f"トライアル回数を使い切りました ({MAX_TRIAL_RUNS}/{MAX_TRIAL_RUNS}回)", "#FCA5A5"
        else:
            remaining = MAX_TRIAL_RUNS - get_trial_count()
            msg, fg = f"トライアルモード  残り {remaining}/{MAX_TRIAL_RUNS} 回  半径300m制限", "#FDE68A"

        tk.Label(frame, text=msg, bg="#1E3A5F", fg=fg,
                 font=("Arial", 10, "bold")).pack(side="left", padx=12)

        if not DEV_MODE and not is_licensed():
            tk.Button(frame, text="ライセンスを購入",
                      command=lambda: __import__('webbrowser').open("https://gumroad.com/"),
                      bg="#F59E0B", fg="white", font=("Arial", 9, "bold"),
                      relief="flat", padx=8).pack(side="right", padx=12)

    # ── bbox入力セクション (TASK 2) ───────────────────────────────────────────

    def _build_bbox_section(self, parent):
        frame = tk.LabelFrame(parent, text="生成エリア選択", padx=8, pady=8)
        frame.pack(fill="x", padx=10, pady=5)

        tk.Button(
            frame, text="地図でエリアを選ぶ（ブラウザが開きます）",
            command=self._open_map_picker,
            bg="#2563AE", fg="white", relief="flat", padx=10, pady=4
        ).grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 8))

        tk.Label(frame, text="最小緯度:").grid(row=1, column=0, sticky="w")
        tk.Entry(frame, textvariable=self.bbox_min_lat, width=14).grid(row=1, column=1, padx=4)
        tk.Label(frame, text="最大緯度:").grid(row=1, column=2, sticky="w")
        tk.Entry(frame, textvariable=self.bbox_max_lat, width=14).grid(row=1, column=3, padx=4)

        tk.Label(frame, text="最小経度:").grid(row=2, column=0, sticky="w")
        tk.Entry(frame, textvariable=self.bbox_min_lon, width=14).grid(row=2, column=1, padx=4)
        tk.Label(frame, text="最大経度:").grid(row=2, column=2, sticky="w")
        tk.Entry(frame, textvariable=self.bbox_max_lon, width=14).grid(row=2, column=3, padx=4)

        tk.Label(frame,
                 text="※ bbox.dev または geojson.io で範囲を選び、表示された座標を入力してください",
                 fg="gray", font=("", 8)
                 ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self.lbl_spawn_info = tk.Label(
            frame,
            text="スポーン地点: 未指定（範囲の中心が使用されます）",
            fg="gray", font=("", 9)
        )
        self.lbl_spawn_info.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

    def _open_map_picker(self):
        if hasattr(self, 'lbl_gen_status'):
            self.lbl_gen_status.config(text="地図ウィンドウを開いています...")
        self.root.update()

        def run_picker():
            from map_picker import open_map_picker
            result = open_map_picker()
            self.root.after(0, lambda: self._on_map_picker_result(result))

        threading.Thread(target=run_picker, daemon=True).start()

    def _on_map_picker_result(self, result):
        if not result or not result.get("bbox"):
            return

        bbox = result["bbox"]
        self.bbox_min_lat.set(f"{bbox['min_lat']:.6f}")
        self.bbox_max_lat.set(f"{bbox['max_lat']:.6f}")
        self.bbox_min_lon.set(f"{bbox['min_lon']:.6f}")
        self.bbox_max_lon.set(f"{bbox['max_lon']:.6f}")

        spawn = result.get("spawn")
        if spawn:
            self.spawn_lat = spawn["lat"]
            self.spawn_lon = spawn["lon"]
            if hasattr(self, 'lbl_spawn_info'):
                self.lbl_spawn_info.config(
                    text=f"スポーン地点: {spawn['lat']:.6f}, {spawn['lon']:.6f}"
                )
        else:
            self.spawn_lat = None
            self.spawn_lon = None

    def _get_current_bbox(self) -> dict:
        """入力欄からbboxを取得・検証する"""
        try:
            return {
                "min_lat": float(self.bbox_min_lat.get()),
                "max_lat": float(self.bbox_max_lat.get()),
                "min_lon": float(self.bbox_min_lon.get()),
                "max_lon": float(self.bbox_max_lon.get()),
            }
        except ValueError:
            return None

    # ── GSIセクション (TASK 4) ───────────────────────────────────────────────

    def _build_gsi_section(self, parent):
        frame = tk.LabelFrame(parent, text="日本向け拡張", padx=8, pady=8)
        frame.pack(fill="x", padx=10, pady=5)

        tk.Checkbutton(
            frame, text="国土地理院（GSI）建物データを使用する",
            variable=self.gsi_enabled
        ).pack(anchor="w")

        tk.Label(
            frame,
            text="※ OSMだけでは少ない日本の住宅地の建物密度を国土地理院データで補います",
            fg="gray", font=("", 8)
        ).pack(anchor="w")

    # ── ワールド生成セクション (TASK 3) ──────────────────────────────────────

    def _build_world_gen_section(self, parent):
        frame = tk.LabelFrame(parent, text="ワールド生成", padx=8, pady=8)
        frame.pack(fill="x", padx=10, pady=5)

        self.btn_generate = tk.Button(
            frame, text="ワールド生成を開始",
            command=self._on_generate_click,
            bg="#166534", fg="white", font=("Arial", 11, "bold"),
            relief="flat", padx=12, pady=8
        )
        self.btn_generate.pack(fill="x")

        self.lbl_gen_status = tk.Label(frame, text="", fg="#374151")
        self.lbl_gen_status.pack(fill="x", pady=(6, 0))

    # ── 生成処理本体 (TASK 4) ─────────────────────────────────────────────────

    def _on_generate_click(self):
        bbox = self._get_current_bbox()
        if bbox is None:
            messagebox.showerror("入力エラー", "緯度・経度を正しく入力してください。")
            return

        if PRO_MODE and not DEV_MODE:
            if not is_licensed():
                if is_trial_expired():
                    messagebox.showerror("トライアル終了",
                        "トライアル回数を使い切りました。ライセンスを購入してください。")
                    return
                if bbox_radius_m(bbox) > MAX_TRIAL_RADIUS_M:
                    messagebox.showwarning("範囲制限",
                        f"トライアルモードでは半径{MAX_TRIAL_RADIUS_M}m以内に制限されます。自動的に縮小します。")
                bbox = clip_bbox_to_trial(bbox)

        self.lbl_gen_status.config(text="arnis起動中...")
        self.btn_generate.config(state="disabled")
        self.root.update()

        t = threading.Thread(target=self._run_generation, args=(bbox,), daemon=True)
        t.start()

    def _run_generation(self, bbox: dict):
        try:
            base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            arnis_exe = find_arnis_exe(base_dir)

            if not os.path.exists(arnis_exe):
                self.root.after(0, lambda: self._on_generation_error(
                    f"arnis本体が見つかりません: {arnis_exe}"))
                return

            self.output_dir = base_dir

            launcher = ArnisLauncher()
            # スポーン地点のbbox範囲内チェック（arnis側エラーになる前にPython側で検証）
            spawn_lat = self.spawn_lat
            spawn_lon = self.spawn_lon
            if spawn_lat is not None and spawn_lon is not None:
                if not (bbox["min_lat"] <= spawn_lat <= bbox["max_lat"] and
                        bbox["min_lon"] <= spawn_lon <= bbox["max_lon"]):
                    self.root.after(0, lambda: self.lbl_spawn_info.config(
                        text="スポーン地点がbbox範囲外のため無視されます（範囲中心を使用）",
                        fg="orange"
                    ))
                    spawn_lat = None
                    spawn_lon = None
            launcher.launch(arnis_exe, spawn_lat=spawn_lat, spawn_lon=spawn_lon)

            self.root.after(0, lambda: self.lbl_gen_status.config(text="bbox確定待機中..."))
            launcher.wait_for_bbox(timeout=600)

            self.root.after(0, lambda: self.lbl_gen_status.config(text="ワールド生成中..."))
            launcher.wait_for_complete(timeout=3600)

            # GSI建物データのマージ（デフォルトON）(TASK 5)
            if self.gsi_enabled.get():
                self.root.after(0, lambda: self.lbl_gen_status.config(text="国土地理院データを取得中..."))
                try:
                    from gsi_merge import merge_gsi_into_osm_json
                    osm_json_path = os.path.join(self.output_dir, "osm_raw.json")
                    merged_path = os.path.join(self.output_dir, "osm_merged.json")
                    if os.path.exists(osm_json_path):
                        result = merge_gsi_into_osm_json(osm_json_path, bbox, merged_path)
                        self.root.after(0, lambda r=result: self.lbl_gen_status.config(
                            text=f"GSI統合完了: OSM {r['osm_buildings']}棟 + GSI {r['gsi_buildings']}棟 = 合計{r['total']}棟"
                        ))
                except Exception as e:
                    print(f"[GSI統合] エラー（スキップして続行）: {e}")

            self.root.after(0, self._on_generation_complete)

        except Exception as e:
            self.root.after(0, lambda: self._on_generation_error(str(e)))

    def _on_generation_complete(self):
        self.lbl_gen_status.config(text="ワールド生成完了")
        self.btn_generate.config(state="normal")

        if PRO_MODE and not DEV_MODE:
            if not is_licensed():
                increment_trial()
                self._refresh_status_bar()

        if PRO_MODE:
            do_colorize = messagebox.askyesno("色付け確認",
                "ワールド生成が完了しました。色付けも実行しますか？")
            if do_colorize:
                self._run_colorize()
        else:
            messagebox.showinfo("完了", "ワールド生成が完了しました。")

    def _on_generation_error(self, message: str):
        self.lbl_gen_status.config(text=f"エラー: {message}")
        self.btn_generate.config(state="normal")
        messagebox.showerror("生成エラー", message)

    def _refresh_status_bar(self):
        # ステータスバーを再構築する（既存の_build_status_barを呼び直す想定）
        pass

    # ── 出力設定（Free/Pro共通） ────────────────────────────────────────────

    def _build_output_section(self, parent):
        frame = tk.LabelFrame(parent, text="出力設定", padx=8, pady=8)
        frame.pack(fill="x", padx=10, pady=5)

        cb_custom = tk.Checkbutton(
            frame,
            text="保存先を指定する",
            variable=self.custom_output_enabled,
            command=self._on_custom_output_toggle
        )
        cb_custom.grid(row=0, column=0, sticky="w")

        self.btn_browse_output = tk.Button(
            frame,
            text="フォルダ選択...",
            command=self._browse_output_dir,
            state="disabled"
        )
        self.btn_browse_output.grid(row=0, column=1, padx=5)

        self.lbl_output_path = tk.Label(
            frame,
            textvariable=self.custom_output_path,
            fg="gray",
            width=40,
            anchor="w"
        )
        self.lbl_output_path.grid(row=0, column=2, sticky="w")

        self.mcworld_enabled = tk.BooleanVar(value=False)
        cb_mcworld = tk.Checkbutton(
            frame,
            text="統合版（Bedrock）.mcworld として保存",
            variable=self.mcworld_enabled,
            command=self._on_mcworld_toggle
        )
        cb_mcworld.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.lbl_mcworld_note = tk.Label(
            frame,
            text="※ Bedrock世界フォルダをzip圧縮して .mcworld に変換します",
            fg="gray",
            font=("", 8)
        )
        self.lbl_mcworld_note.grid(row=2, column=0, columnspan=3, sticky="w")

    # ── Pro専用セクション ────────────────────────────────────────────────────

    def _build_license_section(self, parent):
        frame = tk.LabelFrame(parent, text="ライセンス", padx=8, pady=8)
        frame.pack(fill="x", padx=10, pady=5)

        self.license_key_var = tk.StringVar()
        tk.Label(frame, text="ライセンスキー:").grid(row=0, column=0, sticky="w")
        tk.Entry(frame, textvariable=self.license_key_var, width=36, show="*").grid(
            row=0, column=1, padx=5)
        tk.Button(frame, text="認証", command=self._activate_license).grid(
            row=0, column=2, padx=5)

    def _build_api_key_section(self, parent):
        frame = tk.LabelFrame(parent, text="Google Street View APIキー", padx=8, pady=8)
        frame.pack(fill="x", padx=10, pady=5)

        self.api_key_var = tk.StringVar()
        tk.Label(frame, text="APIキー:").grid(row=0, column=0, sticky="w")
        tk.Entry(frame, textvariable=self.api_key_var, width=50, show="*").grid(
            row=0, column=1, padx=5)

    def _build_colorize_section(self, parent):
        frame = tk.LabelFrame(parent, text="Street Viewカラー適用（Pro）", padx=8, pady=8)
        frame.pack(fill="x", padx=10, pady=5)

        tk.Label(frame, text="半径 (m):").grid(row=0, column=0, sticky="w")
        self.radius_var = tk.StringVar(value="500")
        tk.Entry(frame, textvariable=self.radius_var, width=8).grid(row=0, column=1, padx=5, sticky="w")

    # ── 色付けセクション（Free/Pro共通・末尾） ──────────────────────────────

    def _build_generate_section(self, parent):
        frame_world = tk.LabelFrame(parent, text="ワールドフォルダ（色付け対象）", padx=8, pady=8)
        frame_world.pack(fill="x", padx=10, pady=5)

        tk.Entry(frame_world, textvariable=self.world_folder, width=50).grid(row=0, column=0, padx=5)
        tk.Button(
            frame_world, text="選択...", command=self._browse_world
        ).grid(row=0, column=1, padx=5)

        tk.Button(
            parent,
            text="カラー適用を実行",
            command=self._run_colorize,
            bg="#4a90d9",
            fg="white",
            font=("", 11, "bold"),
            padx=16,
            pady=6
        ).pack(pady=10)

        frame_log = tk.LabelFrame(parent, text="ログ", padx=8, pady=8)
        frame_log.pack(fill="both", expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(
            frame_log, height=12, state="disabled", font=("Courier New", 9)
        )
        self.log_text.pack(fill="both", expand=True)

    # ── コールバック ──────────────────────────────────────────────────────────

    def _on_custom_output_toggle(self):
        if self.custom_output_enabled.get():
            self.btn_browse_output.config(state="normal")
        else:
            self.btn_browse_output.config(state="disabled")
            self.custom_output_path.set("")

    def _browse_output_dir(self):
        path = filedialog.askdirectory(title="保存先フォルダを選択")
        if path:
            self.custom_output_path.set(path)

    def _on_mcworld_toggle(self):
        if self.mcworld_enabled.get():
            self.custom_output_enabled.set(True)
            self.btn_browse_output.config(state="normal")
            if not self.custom_output_path.get():
                desktop = os.path.join(os.path.expanduser("~"), "Desktop")
                if not os.path.exists(desktop):
                    desktop = os.path.join(os.path.expanduser("~"), "OneDrive", "デスクトップ")
                self.custom_output_path.set(desktop)

    def _browse_world(self):
        path = filedialog.askdirectory(title="ワールドフォルダを選択")
        if path:
            self.world_folder.set(path)

    def _activate_license(self):
        key = getattr(self, 'license_key_var', tk.StringVar()).get().strip()
        if not key:
            messagebox.showwarning("入力エラー", "ライセンスキーを入力してください。")
            return
        try:
            from license_client import activate
            result = activate(key)
            if result:
                messagebox.showinfo("認証成功", "ライセンス認証が完了しました。")
            else:
                messagebox.showerror("認証失敗", "ライセンスキーが無効です。")
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    # ── ログ・色付け実行 ──────────────────────────────────────────────────────

    def _log(self, message: str):
        self.log_text.config(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")
        self.root.update_idletasks()

    def _run_colorize(self):
        world = self.world_folder.get()
        if not world or not os.path.isdir(world):
            messagebox.showerror("エラー", "有効なワールドフォルダを選択してください。")
            return

        apply_script = os.path.join(self.base_dir, "apply_colors.py")
        if not os.path.exists(apply_script):
            self._log(f"[WARN] apply_colors.py が見つかりません: {apply_script}")
            self._log("apply_colors.py なしでカラー適用をシミュレートします。")
            self._on_colorize_complete(world)
            return

        self._log(f"カラー適用開始: {world}")

        def worker():
            try:
                proc = subprocess.Popen(
                    ["python", apply_script, world],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace"
                )
                for line in proc.stdout:
                    self.root.after(0, self._log, line.rstrip())
                proc.wait()
                if proc.returncode == 0:
                    self.root.after(0, self._log, "カラー適用完了")
                    self.root.after(0, self._on_colorize_complete, world)
                else:
                    self.root.after(0, self._log, f"[ERROR] returncode={proc.returncode}")
            except Exception as e:
                self.root.after(0, self._log, f"[ERROR] {e}")

        threading.Thread(target=worker, daemon=True).start()

    # ── mcworld保存 ───────────────────────────────────────────────────────────

    def save_as_mcworld(self, world_folder: str, output_dir: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        world_name = os.path.basename(world_folder.rstrip("/\\"))
        mcworld_name = f"{world_name}_{timestamp}.mcworld"
        mcworld_path = os.path.join(output_dir, mcworld_name)

        self._log(f"mcworld作成中: {mcworld_name}")

        with zipfile.ZipFile(mcworld_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(world_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, world_folder)
                    zf.write(file_path, arcname)

        size_mb = os.path.getsize(mcworld_path) / (1024 * 1024)
        self._log(f"mcworld保存完了: {mcworld_path} ({size_mb:.1f} MB)")
        return mcworld_path

    def _on_colorize_complete(self, world_folder: str):
        output_dir = self.custom_output_path.get() if self.custom_output_enabled.get() else None

        if self.mcworld_enabled.get():
            if not output_dir:
                output_dir = os.path.join(os.path.expanduser("~"), "Desktop")
            try:
                mcworld_path = self.save_as_mcworld(world_folder, output_dir)
                subprocess.Popen(["explorer", f'/select,"{mcworld_path}"'])
            except Exception as e:
                self._log(f"[ERROR] mcworld作成失敗: {e}")

        elif output_dir and os.path.exists(output_dir):
            dst = os.path.join(output_dir, os.path.basename(world_folder))
            try:
                shutil.copytree(world_folder, dst, dirs_exist_ok=True)
                self._log(f"ワールドコピー完了: {dst}")
            except Exception as e:
                self._log(f"[ERROR] コピー失敗: {e}")


def main():
    root = tk.Tk()
    app = ArnisColorizeGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
