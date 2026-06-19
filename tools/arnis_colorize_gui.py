"""
arnis_colorize_gui.py
ArnisPLATEAU カラー適用・ワールド生成GUI — v2.9.0 Mosaic対応 / Free・Pro・ProDev 3ビルド対応
"""
import json
import os
import sys
import subprocess
import threading
import zipfile
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import time
import tempfile
from datetime import datetime
from desktop_path import get_desktop_path

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


def _extract_metadata_from_mcworld(mcworld_path: str) -> dict:
    """
    .mcworld (zip) ファイルから metadata.json を取り出して返す。
    見つからない場合は {} を返す。
    """
    try:
        with zipfile.ZipFile(mcworld_path, "r") as zf:
            if "metadata.json" in zf.namelist():
                with zf.open("metadata.json") as f:
                    return json.load(f)
    except Exception as e:
        print(f"[PLATEAU] metadata.json取得失敗（mcworld）: {e}")
    return {}


def _get_metadata_from_world(world_path: str) -> dict:
    """
    ワールドフォルダまたは .mcworld ファイルから metadata.json を読む。
    フォルダの場合はその中の metadata.json、.mcworld の場合は zip 内から取得する。
    """
    if not world_path:
        return {}
    if world_path.lower().endswith(".mcworld") and os.path.isfile(world_path):
        return _extract_metadata_from_mcworld(world_path)
    meta_path = os.path.join(world_path, "metadata.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[PLATEAU] metadata.json読み込みエラー: {e}")
    return {}


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

        # 出力形式設定（Java / 統合版 / Luanti）
        self.output_format = tk.StringVar(value="java")
        self.mcworld_enabled = tk.BooleanVar(value=True)
        self.mcworld_save_dir = tk.StringVar(value="")
        self.luanti_output_dir = tk.StringVar(value="")

        # bbox入力用変数 (TASK 2)
        self.bbox_min_lat = tk.StringVar(value="")
        self.bbox_max_lat = tk.StringVar(value="")
        self.bbox_min_lon = tk.StringVar(value="")
        self.bbox_max_lon = tk.StringVar(value="")

        # GSI設定（デフォルトON）(TASK 4)
        self.gsi_enabled = tk.BooleanVar(value=True)
        self.plateau_height_enabled = tk.BooleanVar(value=True)
        self.plateau_footprint_mode = tk.StringVar(value="priority")

        # PLATEAU距離フィルタ設定
        self.plateau_dist_m = tk.IntVar(value=50)

        # 検証用基準点
        self.calib_rows = []

        # スポーン地点
        self.spawn_lat = None
        self.spawn_lon = None

        # config.json から設定を復元
        self._config_path = os.path.join(self.base_dir, "config.json")
        self._load_config()

        self._build_ui()

    def _build_ui(self):
        self._build_status_bar(self.root)
        self._build_top_row(self.root)            # 生成エリア（左）＋ 出力形式（右）
        self._build_calibration_section(self.root)
        self._build_gsi_section(self.root)
        self._build_world_gen_section(self.root)

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
                     text="ArnisPLATEAU  —  リアルな日本の街をMinecraftで再現",
                     bg="#1E3A5F", fg="#FFFFFF",
                     font=("Arial", 10, "bold")).pack(side="left", padx=12)
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

    # ── 横並びトップ行（生成エリア左 + 出力形式右） ─────────────────────────

    def _build_top_row(self, parent):
        container = tk.Frame(parent)
        container.pack(fill="x", padx=10, pady=5)

        self._build_bbox_section(
            container,
            pack_kwargs={"side": "left", "fill": "both", "expand": True, "padx": (0, 3)},
        )
        self._build_output_format_section(
            container,
            pack_kwargs={"side": "left", "fill": "y", "anchor": "n", "padx": (3, 0)},
        )

    # ── bbox入力セクション (TASK 2) ───────────────────────────────────────────

    def _build_bbox_section(self, parent, pack_kwargs=None):
        frame = tk.LabelFrame(parent, text="生成エリア選択", padx=8, pady=8)
        if pack_kwargs is None:
            frame.pack(fill="x", padx=10, pady=5)
        else:
            frame.pack(**pack_kwargs)

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
        import tempfile
        import json as _json

        if hasattr(self, 'lbl_gen_status'):
            self.lbl_gen_status.config(text="地図ウィンドウを開いています...")
        self.root.update()

        def run_picker():
            result_file = os.path.join(tempfile.gettempdir(), "arnisplateau_map_result.json")
            if os.path.exists(result_file):
                os.remove(result_file)

            # frozen(exe)時は自分自身を --map-picker モードで起動
            # 開発時は sys.executable(python.exe) + map_picker.py を起動
            if getattr(sys, 'frozen', False):
                cmd = [sys.executable, "--map-picker"]
            else:
                picker_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "map_picker.py"
                )
                cmd = [sys.executable, picker_path]

            try:
                subprocess.run(cmd, timeout=660)
            except subprocess.TimeoutExpired:
                pass
            except Exception as e:
                print(f"[map_picker] 起動エラー: {e}")

            result = None
            if os.path.exists(result_file):
                try:
                    with open(result_file, "r", encoding="utf-8") as f:
                        result = _json.load(f)
                except Exception:
                    pass

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

        tk.Checkbutton(
            frame, text="PLATEAU実測データで高さ・壁の形を補正する",
            variable=self.plateau_height_enabled,
            command=self._on_plateau_toggle
        ).pack(anchor="w", pady=(6, 0))
        tk.Label(
            frame, text="※ 屋根形状は対象外。高さと建物外形の精度のみ向上します",
            fg="gray", font=("", 8)
        ).pack(anchor="w")

        # 形状重複時の処理モード選択
        fp_frame = tk.Frame(frame)
        fp_frame.pack(anchor="w", padx=(20, 0), pady=(4, 0))

        tk.Label(fp_frame, text="形状の重複時の処理:").grid(row=0, column=0, sticky="w")

        fp_modes = [
            ("優先度判定（大きい建物を優先、推奨）", "priority"),
            ("縮小して回避（5%縮小して再判定）",   "shrink"),
            ("変更を見送る（元の形状を維持）",      "skip"),
        ]
        self._fp_mode_radios = []
        for i, (label, value) in enumerate(fp_modes):
            rb = tk.Radiobutton(
                fp_frame, text=label,
                variable=self.plateau_footprint_mode, value=value,
            )
            rb.grid(row=i + 1, column=0, sticky="w")
            self._fp_mode_radios.append(rb)

        tk.Label(
            fp_frame,
            text="複数の建物の実測データが重なった場合の優先順位です。高さの補正には影響しません。",
            fg="gray", font=("", 8),
        ).grid(row=len(fp_modes) + 1, column=0, sticky="w", pady=(2, 0))

        self._fp_frame = fp_frame

        # マッチング距離上限スライダー
        dist_frame = tk.Frame(frame)
        dist_frame.pack(anchor="w", padx=(20, 0), pady=(8, 0))

        tk.Label(dist_frame, text="マッチング距離上限:").grid(row=0, column=0, sticky="w")
        self._dist_slider = tk.Scale(
            dist_frame,
            variable=self.plateau_dist_m,
            from_=10, to=200, resolution=10,
            orient="horizontal", length=180,
            command=self._on_dist_slider_change,
        )
        self._dist_slider.grid(row=0, column=1, padx=(6, 0))
        self._dist_label = tk.Label(dist_frame, text="50 m", width=6, anchor="w")
        self._dist_label.grid(row=0, column=2, padx=(4, 0))
        tk.Label(
            dist_frame, text="10m                   200m",
            fg="gray", font=("", 7),
        ).grid(row=1, column=1, sticky="w")

        self._dist_frame = dist_frame
        self._on_plateau_toggle()  # 初期状態を反映

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
            # arnis-windows.exe の場所を特定（exeと同じフォルダ優先）
            base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            arnis_exe = find_arnis_exe(base_dir)

            if not os.path.exists(arnis_exe):
                self.root.after(0, lambda: self._on_generation_error(
                    f"arnis本体が見つかりません。\n"
                    f"arnis-windows.exe を {base_dir} に置いてください。\n"
                    f"（探したパス: {arnis_exe}）"))
                return

            # 出力ディレクトリ（exeと同じフォルダ固定）
            output_dir = base_dir
            os.makedirs(output_dir, exist_ok=True)
            self.output_dir = output_dir

            fmt = self.output_format.get()

            # スポーン地点のbbox範囲内チェック
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

            # OSM生データ保存パス（GSIマージ用）
            osm_raw_path = os.path.join(output_dir, "osm_raw.json")

            # arnis を CLI モードで起動（出力形式に応じてフラグを切り替え）
            gen_start_time = time.time()
            launcher = ArnisLauncher()

            if fmt == "luanti":
                luanti_dir = self.luanti_output_dir.get()
                if not luanti_dir:
                    self.root.after(0, lambda: self._on_generation_error(
                        "Luanti出力先を指定してください"))
                    return
                launcher.launch(
                    arnis_exe,
                    bbox=bbox,
                    output_dir=luanti_dir,
                    bedrock=False,
                    luanti=True,
                    spawn_lat=spawn_lat,
                    spawn_lon=spawn_lon,
                    save_json_path=osm_raw_path,
                )
            elif fmt == "bedrock" and not self.mcworld_enabled.get():
                # 直接Bedrock出力（PLATEAUなし）
                launcher.launch(
                    arnis_exe,
                    bbox=bbox,
                    output_dir=output_dir,
                    bedrock=True,
                    spawn_lat=spawn_lat,
                    spawn_lon=spawn_lon,
                    save_json_path=osm_raw_path,
                )
            else:
                # Java形式（またはBedrock+mcworld: Java経由でPLATEAU補正後にChunker変換）
                launcher.launch(
                    arnis_exe,
                    bbox=bbox,
                    output_dir=output_dir,
                    bedrock=False,
                    spawn_lat=spawn_lat,
                    spawn_lon=spawn_lon,
                    save_json_path=osm_raw_path,
                )

            self.root.after(0, lambda: self.lbl_gen_status.config(text="ワールド生成中..."))
            ok = launcher.wait_for_complete(timeout=3600)

            if not ok:
                self.root.after(0, lambda: self._on_generation_error(
                    "タイムアウト: 1時間以内に生成が完了しませんでした。\n"
                    "arnis-windows.exe が正常に終了しているか確認してください。"))
                return

            # "Saving world..." 検知後も arnis はファイル書き込みを続けているため、
            # プロセス完全終了を待つ（region/*.mca・metadata.json の書き込み完了を保証）
            self.root.after(0, lambda: self.lbl_gen_status.config(text="ワールドファイルを書き込み中..."))
            exited = launcher.wait_until_exit(timeout=120)
            if not exited:
                self._log("[WARNING] arnis プロセスの終了待機がタイムアウトしました（120秒）")
                self._log("ワールドファイルが不完全な可能性があります。処理を中断します。")
                self.root.after(0, lambda: self._on_generation_error(
                    "ワールド生成が完了しませんでした（ファイル書き込みタイムアウト 120秒）。\n"
                    "arnis-windows.exe が正常に終了しているか確認してください。"))
                return

            # 生成されたワールドを特定（Java Edition: フォルダのみ）
            _TILE_CACHE_KEYWORDS = ("tile-cache", "gsi_tiles", "sat_cache", "osm_cache")
            found_world = False

            if launcher.world_path:
                wp = launcher.world_path
                if os.path.isdir(wp):
                    self.world_folder.set(wp)
                    found_world = True

            if not found_world:
                # フォールバック: output_dir の最新サブフォルダを探す
                # タイルキャッシュ系フォルダを除外し、生成開始時刻以降のものを優先する
                try:
                    subdirs = [
                        d for d in os.scandir(output_dir)
                        if d.is_dir()
                        and d.name not in ("__pycache__", ".git")
                        and not any(kw in d.name.lower() for kw in _TILE_CACHE_KEYWORDS)
                        and d.stat().st_mtime >= gen_start_time - 5
                    ]
                    if not subdirs:
                        # 時刻フィルタで候補なし → タイルキャッシュのみ除外で再試行
                        subdirs = [
                            d for d in os.scandir(output_dir)
                            if d.is_dir()
                            and d.name not in ("__pycache__", ".git")
                            and not any(kw in d.name.lower() for kw in _TILE_CACHE_KEYWORDS)
                        ]
                    if subdirs:
                        newest = max(subdirs, key=lambda d: d.stat().st_mtime)
                        self.world_folder.set(newest.path)
                except Exception:
                    pass

            # GSI建物データのマージ（デフォルトON）
            if self.gsi_enabled.get():
                self.root.after(0, lambda: self.lbl_gen_status.config(text="国土地理院データを取得中..."))
                try:
                    from gsi_merge import merge_gsi_into_osm_json
                    merged_path = os.path.join(output_dir, "osm_merged.json")
                    if os.path.exists(osm_raw_path):
                        result = merge_gsi_into_osm_json(osm_raw_path, bbox, merged_path)
                        self.root.after(0, lambda r=result: self.lbl_gen_status.config(
                            text=f"GSI統合完了: OSM {r['osm_buildings']}棟 + GSI {r['gsi_buildings']}棟 = 合計{r['total']}棟"
                        ))
                except Exception as e:
                    print(f"[GSI統合] エラー（スキップして続行）: {e}")

            corrections_for_calib = None
            metadata_for_calib = None

            # PLATEAU補正はJava Editionワールド(.mca)が必要なため、直接Bedrock/Luanti出力時はスキップ
            skip_plateau = (fmt == "luanti") or (fmt == "bedrock" and not self.mcworld_enabled.get())
            if self.plateau_height_enabled.get() and not skip_plateau:
                fp_mode = self.plateau_footprint_mode.get()
                fp_mode_label = {"priority": "優先度判定", "shrink": "縮小して回避", "skip": "変更を見送る"}.get(fp_mode, fp_mode)
                self._log(f"形状補正モード: {fp_mode}（{fp_mode_label}）")
                self._log("PLATEAU実測データを取得中...")
                self.root.after(0, lambda: self.lbl_gen_status.config(text="PLATEAU実測データを取得中..."))
                try:
                    from plateau_height_merge import build_height_corrections
                    from world_height_writer import apply_height_corrections

                    # osm_merged.json優先、なければ osm_raw.json
                    plateau_merged = os.path.join(self.output_dir, "osm_merged.json")
                    plateau_source = plateau_merged if os.path.exists(plateau_merged) else os.path.join(self.output_dir, "osm_raw.json")

                    # metadata.json をワールドフォルダまたは .mcworld zip 内から取得
                    world_path_for_plateau = self.world_folder.get()
                    metadata = _get_metadata_from_world(world_path_for_plateau)
                    metadata_for_calib = metadata

                    if os.path.exists(plateau_source) and metadata:
                        with open(plateau_source, "r", encoding="utf-8") as f:
                            osm_data = json.load(f)

                        corrections = build_height_corrections(
                            bbox, osm_data, metadata,
                            footprint_mode=fp_mode,
                            max_dist_m=self.plateau_dist_m.get(),
                        )
                        corrections_for_calib = corrections
                        if corrections:
                            self._log(f"PLATEAU対応建物: {len(corrections)}棟を補正します")
                            # 常に Java Edition フォルダに直接適用（Java world editor が region/*.mca を編集）
                            result = apply_height_corrections(world_path_for_plateau, corrections)
                            msg = f"PLATEAU高さ補正完了: {result['corrected']}棟（エラー{result.get('errors', 0)}棟）"
                            self._log(msg)
                            self.root.after(0, lambda m=msg: self.lbl_gen_status.config(text=m))
                        else:
                            self._log("PLATEAU補正: 対応データなし（osm-PLATEAU間でマッチした建物が0件）")
                            self.root.after(0, lambda: self.lbl_gen_status.config(text="PLATEAU補正: 対応データなし"))
                    else:
                        self._log(f"PLATEAU補正スキップ: source={os.path.exists(plateau_source)}, metadata={bool(metadata)}")
                        self.root.after(0, lambda: self.lbl_gen_status.config(
                            text="PLATEAU補正: metadata.jsonまたはOSMデータが見つかりません"
                        ))
                        print(f"[PLATEAU高さ補正] source={plateau_source}(exists={os.path.exists(plateau_source)}), "
                              f"metadata={'あり' if metadata else 'なし'}, world={world_path_for_plateau}")
                except Exception as e:
                    self._log(f"[PLATEAU補正] エラー（スキップして続行）: {e}")
                    print(f"[PLATEAU補正] エラー: {e}")

            # キャリブレーション（PLATEAU補正直後、mcworld変換前）
            calib_pts = self._get_calibration_points()
            if calib_pts and corrections_for_calib is not None and metadata_for_calib:
                self._run_calibration(corrections_for_calib, metadata_for_calib, calib_pts)

            # 出力形式に応じた後処理
            if fmt == "bedrock" and self.mcworld_enabled.get():
                save_dir = self.mcworld_save_dir.get() or self.output_dir
                self._convert_to_mcworld(save_dir)

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
            wf = self.world_folder.get()
            msg = "ワールド生成が完了しました。"
            if wf:
                msg += f"\n\n生成先: {wf}"
            messagebox.showinfo("完了", msg)

    def _on_generation_error(self, message: str):
        self.lbl_gen_status.config(text=f"エラー: {message}")
        self.btn_generate.config(state="normal")
        messagebox.showerror("生成エラー", message)

    def _refresh_status_bar(self):
        # ステータスバーを再構築する（既存の_build_status_barを呼び直す想定）
        pass

    # ── 出力形式選択（右上パネル） ──────────────────────────────────────────

    def _get_downloads_dir(self) -> str:
        import winreg
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ) as key:
                return winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
        except Exception:
            from pathlib import Path
            return str(Path.home() / "Downloads")

    def _build_output_format_section(self, parent, pack_kwargs=None):
        frame = tk.LabelFrame(parent, text="出力形式", padx=8, pady=8)
        if pack_kwargs is None:
            frame.pack(fill="x", padx=10, pady=5)
        else:
            frame.pack(**pack_kwargs)

        tk.Radiobutton(
            frame, text="Java版",
            variable=self.output_format, value="java",
            command=self._on_output_format_change,
        ).pack(anchor="w")

        tk.Radiobutton(
            frame, text="統合版（Bedrock）",
            variable=self.output_format, value="bedrock",
            command=self._on_output_format_change,
        ).pack(anchor="w", pady=(4, 0))

        # 統合版サブUI
        self._bedrock_sub = tk.Frame(frame)
        self._bedrock_sub.pack(anchor="w", padx=(18, 0))

        tk.Checkbutton(
            self._bedrock_sub, text=".mcworld形式で保存",
            variable=self.mcworld_enabled,
            command=self._update_mcworld_dir_visibility,
        ).pack(anchor="w")

        self._mcworld_dir_frame = tk.Frame(self._bedrock_sub)
        self._mcworld_dir_frame.pack(anchor="w", padx=(18, 0))
        tk.Label(self._mcworld_dir_frame, text="保存先:").pack(side="left")
        tk.Entry(
            self._mcworld_dir_frame, textvariable=self.mcworld_save_dir, width=18,
        ).pack(side="left", padx=(4, 0))
        tk.Button(
            self._mcworld_dir_frame, text="参照...",
            command=self._on_mcworld_save_dir_browse, font=("", 8),
        ).pack(side="left", padx=(2, 0))

        tk.Radiobutton(
            frame, text="Luanti",
            variable=self.output_format, value="luanti",
            command=self._on_output_format_change,
        ).pack(anchor="w", pady=(4, 0))

        # LuantiサブUI
        self._luanti_sub = tk.Frame(frame)
        self._luanti_sub.pack(anchor="w", padx=(18, 0))
        tk.Label(self._luanti_sub, text="出力先:").pack(side="left")
        tk.Entry(
            self._luanti_sub, textvariable=self.luanti_output_dir, width=18,
        ).pack(side="left", padx=(4, 0))
        tk.Button(
            self._luanti_sub, text="参照...",
            command=self._on_luanti_dir_browse, font=("", 8),
        ).pack(side="left", padx=(2, 0))

        self._on_output_format_change()  # 初期状態を反映

    def _on_output_format_change(self):
        fmt = self.output_format.get()
        if fmt == "java":
            self._bedrock_sub.pack_forget()
            self._luanti_sub.pack_forget()
        elif fmt == "bedrock":
            if not self._bedrock_sub.winfo_ismapped():
                self._bedrock_sub.pack(anchor="w", padx=(18, 0))
            self._luanti_sub.pack_forget()
            self._update_mcworld_dir_visibility()
        elif fmt == "luanti":
            self._bedrock_sub.pack_forget()
            if not self._luanti_sub.winfo_ismapped():
                self._luanti_sub.pack(anchor="w", padx=(18, 0))
        self._save_config()

    def _update_mcworld_dir_visibility(self):
        if self.mcworld_enabled.get():
            if not self._mcworld_dir_frame.winfo_ismapped():
                self._mcworld_dir_frame.pack(anchor="w", padx=(18, 0))
        else:
            self._mcworld_dir_frame.pack_forget()
        self._save_config()

    def _on_mcworld_save_dir_browse(self):
        path = filedialog.askdirectory(title=".mcworld保存先フォルダを選択")
        if path:
            self.mcworld_save_dir.set(path)
            self._save_config()

    def _on_luanti_dir_browse(self):
        path = filedialog.askdirectory(title="Luanti出力先フォルダを選択")
        if path:
            self.luanti_output_dir.set(path)
            self._save_config()

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
        if PRO_MODE:
            frame_world = tk.LabelFrame(parent, text="ワールドフォルダ（色付け対象）", padx=8, pady=8)
            frame_world.pack(fill="x", padx=10, pady=5)
            tk.Entry(frame_world, textvariable=self.world_folder, width=50).grid(row=0, column=0, padx=5)
            tk.Button(
                frame_world, text="選択...", command=self._browse_world
            ).grid(row=0, column=1, padx=5)

        if PRO_MODE:
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

    # ── 検証用基準点セクション ────────────────────────────────────────────────

    def _build_calibration_section(self, parent):
        _MAX = 5
        self.calib_outer_frame = tk.LabelFrame(parent, text="検証用基準点", padx=8, pady=8)
        self.calib_outer_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(
            self.calib_outer_frame,
            text="離れた場所の建物を3か所程度指定すると、位置ズレの検出精度が上がります",
            fg="gray", font=("", 8),
        ).pack(anchor="w", pady=(0, 2))

        self.calib_warn_lbl = tk.Label(self.calib_outer_frame, text="", fg="#dc2626", font=("", 8))
        self.calib_warn_lbl.pack(anchor="w")

        # ヘッダー行
        hdr = tk.Frame(self.calib_outer_frame)
        hdr.pack(fill="x", pady=(2, 0))
        tk.Label(hdr, text="建物名",  width=16, anchor="w", font=("", 9, "bold")).grid(row=0, column=0, padx=2)
        tk.Label(hdr, text="緯度",    width=14, anchor="w", font=("", 9, "bold")).grid(row=0, column=1, padx=2)
        tk.Label(hdr, text="経度",    width=14, anchor="w", font=("", 9, "bold")).grid(row=0, column=2, padx=2)

        # 行コンテナ
        self.calib_rows_frame = tk.Frame(self.calib_outer_frame)
        self.calib_rows_frame.pack(fill="x")

        # 追加ボタン
        btn_row = tk.Frame(self.calib_outer_frame)
        btn_row.pack(anchor="w", pady=(4, 0))
        self.btn_add_calib = tk.Button(
            btn_row, text="+ 基準点を追加",
            command=self._add_calibration_row, font=("", 9),
        )
        self.btn_add_calib.pack(side="left")
        self.calib_limit_lbl = tk.Label(btn_row, text="", fg="gray", font=("", 8))
        self.calib_limit_lbl.pack(side="left", padx=(6, 0))

        self._on_calibration_changed()

    def _add_calibration_row(self, name: str = "", lat: str = "", lon: str = ""):
        _MAX = 5
        if len(self.calib_rows) >= _MAX:
            return
        name_var = tk.StringVar(value=name)
        lat_var  = tk.StringVar(value=lat)
        lon_var  = tk.StringVar(value=lon)

        row_frame = tk.Frame(self.calib_rows_frame)
        row_frame.pack(fill="x", pady=2)

        # ── お気に入りから選択 ──────────────────────────────────────
        fav_frame = tk.Frame(row_frame)
        fav_frame.pack(anchor="w", pady=(0, 1))
        tk.Label(fav_frame, text="お気に入りから選択:", font=("", 8), fg="gray").pack(side="left")
        favs = self._load_favorites()
        fav_var = tk.StringVar(value="（お気に入りから選択）")
        fav_options = ["（お気に入りから選択）"] + [f["name"] for f in favs]

        def on_fav_select(val, _nv=name_var, _lv=lat_var, _lov=lon_var, _fv=fav_var, _fs=favs):
            if val.startswith("（"):
                return
            for f in _fs:
                if f["name"] == val:
                    _nv.set(f["name"])
                    _lv.set(str(f["lat"]))
                    _lov.set(str(f["lon"]))
                    break
            _fv.set("（お気に入りから選択）")

        om = tk.OptionMenu(fav_frame, fav_var, *fav_options, command=on_fav_select)
        om.config(font=("", 8), width=24)
        om["menu"].config(font=("", 8))
        om.pack(side="left", padx=(2, 0))

        # ── 入力行 ──────────────────────────────────────────────────
        data_frame = tk.Frame(row_frame)
        data_frame.pack(anchor="w")

        name_entry = tk.Entry(data_frame, textvariable=name_var, width=14)
        name_entry.grid(row=0, column=0, padx=2)

        def on_star(_nv=name_var, _lv=lat_var, _lov=lon_var):
            n = _nv.get().strip()
            if not n:
                messagebox.showwarning("入力エラー", "建物名を入力してください")
                return
            try:
                la = float(_lv.get())
                lo = float(_lov.get())
            except ValueError:
                messagebox.showwarning("入力エラー", "有効な緯度・経度を入力してください")
                return
            self._save_favorite(n, la, lo)
            messagebox.showinfo("保存完了", f"「{n}」をお気に入りに登録しました")

        tk.Button(data_frame, text="★", command=on_star, font=("", 8), padx=2, pady=0).grid(
            row=0, column=1, padx=1)

        lat_entry = tk.Entry(data_frame, textvariable=lat_var, width=14)
        lat_entry.grid(row=0, column=2, padx=2)

        lon_entry = tk.Entry(data_frame, textvariable=lon_var, width=14)
        lon_entry.grid(row=0, column=3, padx=2)

        row_data = {"name": name_var, "lat": lat_var, "lon": lon_var, "frame": row_frame}
        self.calib_rows.append(row_data)

        def on_delete(rd=row_data):
            self._remove_calibration_row(rd)

        tk.Button(data_frame, text="削除", command=on_delete, font=("", 8)).grid(
            row=0, column=4, padx=2)

        # ── カンマ区切りペースト対応 ────────────────────────────────
        paste_handler = self._make_paste_handler(lat_var, lon_var)
        lat_entry.bind("<<Paste>>", paste_handler)
        lon_entry.bind("<<Paste>>", paste_handler)

        self._on_calibration_changed()

    def _remove_calibration_row(self, row_data: dict):
        if row_data in self.calib_rows:
            row_data["frame"].pack_forget()
            row_data["frame"].destroy()
            self.calib_rows.remove(row_data)
            self._on_calibration_changed()

    def _on_calibration_changed(self):
        _MAX = 5
        n = len(self.calib_rows)
        if n >= _MAX:
            self.btn_add_calib.config(state="disabled")
            self.calib_limit_lbl.config(text=f"（上限{_MAX}件）")
        else:
            self.btn_add_calib.config(state="normal")
            self.calib_limit_lbl.config(text="")
        if n == 0:
            self.calib_warn_lbl.config(
                text="基準点が0件です。未登録でも生成可能ですが、キャリブレーションはスキップされます。"
            )
        else:
            self.calib_warn_lbl.config(text="")

    def _get_calibration_points(self) -> list:
        """有効な基準点リストを返す（緯度経度が数値として解析できるもののみ）"""
        result = []
        for rd in self.calib_rows:
            name = rd["name"].get().strip() or "基準点"
            try:
                lat = float(rd["lat"].get())
                lon = float(rd["lon"].get())
                result.append({"name": name, "lat": lat, "lon": lon})
            except ValueError:
                continue
        return result

    def _run_calibration(self, corrections: list, metadata: dict, calib_points: list):
        """キャリブレーション結果をログに出力する"""
        from calibration import run_calibration
        run_calibration(corrections, metadata, calib_points, log_fn=self._log)

    # ── お気に入り管理 ─────────────────────────────────────────────────────────

    def _get_favorites_path(self) -> str:
        return os.path.join(self.base_dir, "favorites.json")

    def _load_favorites(self) -> list:
        defaults = [
            {"name": "大同生命霞が関ビル",       "lat": 35.670517,           "lon": 139.751560},
            {"name": "虎ノ門ヒルズ森タワー",     "lat": 35.66689741570721,   "lon": 139.749623094962},
            {"name": "西新橋スクエア",           "lat": 35.66934286571219,   "lon": 139.75487694678765},
        ]
        path = self._get_favorites_path()
        if not os.path.isfile(path):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(defaults, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            return defaults
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
        return defaults

    def _save_favorite(self, name: str, lat: float, lon: float):
        favs = self._load_favorites()
        for fav in favs:
            if fav["name"] == name:
                fav["lat"] = lat
                fav["lon"] = lon
                break
        else:
            favs.append({"name": name, "lat": lat, "lon": lon})
        path = self._get_favorites_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(favs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("エラー", f"お気に入りの保存に失敗しました: {e}")

    def _make_paste_handler(self, lat_var: tk.StringVar, lon_var: tk.StringVar):
        """緯度経度欄へのカンマ区切りペースト（例: '35.670517, 139.751560'）を自動分割するハンドラを返す"""
        def handler(event):
            def check():
                val = event.widget.get()
                if "," in val:
                    parts = val.split(",", 1)
                    try:
                        lat_s = parts[0].strip()
                        lon_s = parts[1].strip()
                        float(lat_s)
                        float(lon_s)
                        lat_var.set(lat_s)
                        lon_var.set(lon_s)
                    except ValueError:
                        pass
            event.widget.after_idle(check)
        return handler

    # ── コールバック ──────────────────────────────────────────────────────────

    def _on_plateau_toggle(self):
        state = "normal" if self.plateau_height_enabled.get() else "disabled"
        for rb in getattr(self, "_fp_mode_radios", []):
            rb.config(state=state)
        if hasattr(self, "_dist_slider"):
            self._dist_slider.config(state=state)

    def _on_dist_slider_change(self, val):
        if hasattr(self, "_dist_label"):
            self._dist_label.config(text=f"{val} m")
        self._save_config()

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

        # .ldb / .log は LevelDB ファイルで圧縮済みのため ZIP_STORED を使う
        # その他のファイルは ZIP_DEFLATED で圧縮する
        _STORED_EXTS = {".ldb", ".log"}
        with zipfile.ZipFile(mcworld_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(world_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, world_folder)
                    ext = os.path.splitext(file)[1].lower()
                    compress = zipfile.ZIP_STORED if ext in _STORED_EXTS else zipfile.ZIP_DEFLATED
                    zf.write(file_path, arcname, compress_type=compress)

        size_mb = os.path.getsize(mcworld_path) / (1024 * 1024)
        self._log(f"mcworld保存完了: {mcworld_path} ({size_mb:.1f} MB)")
        return mcworld_path

    def _convert_to_mcworld(self, save_dir: str):
        """
        Java Editionワールドを Chunker CLI で Bedrock に変換し、.mcworld ZIP として保存する。
        arnis が Java 形式で出力した後に呼び出す（PLATEAU補正済み状態を変換）。
        """
        java_world = self.world_folder.get()
        if not java_world or not os.path.isdir(java_world):
            self._log("[WARNING] Javaワールドフォルダが見つかりません。.mcworld変換をスキップします。")
            self.root.after(0, lambda: self.lbl_gen_status.config(
                text="mcworld変換スキップ（Javaワールドフォルダが見つかりません）"))
            return
        os.makedirs(save_dir, exist_ok=True)
        try:
            from chunker_converter import convert_java_to_bedrock
            self._log("Bedrock形式に変換中（Chunker CLI）...")
            self.root.after(0, lambda: self.lbl_gen_status.config(
                text="Bedrock形式に変換中（数十秒〜数分かかります）..."))

            conv = convert_java_to_bedrock(
                java_world, save_dir,
                progress_callback=self._log,
            )
            if conv["success"]:
                mcworld_path = self.save_as_mcworld(conv["output_path"], save_dir)
                shutil.rmtree(conv["output_path"], ignore_errors=True)
                msg = f".mcworld保存完了: {os.path.basename(mcworld_path)}"
                self._log(msg)
                self.root.after(0, lambda m=msg: self.lbl_gen_status.config(text=m))
            else:
                self._log(f"[WARNING] Bedrock変換失敗: {conv['error']}")
                self._log("Java版ワールドフォルダをそのまま使用します。")
                self.root.after(0, lambda: self.lbl_gen_status.config(
                    text="Bedrock変換失敗（Java版フォルダを保存済み）"))
        except Exception as e:
            self._log(f"[ERROR] Bedrock変換中に例外: {e}")
            self.root.after(0, lambda: self.lbl_gen_status.config(
                text="Bedrock変換失敗（Java版フォルダを保存済み）"))

    def _load_config(self):
        try:
            if os.path.isfile(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.plateau_dist_m.set(int(cfg.get("plateau_dist_m", 50)))
                self.output_format.set(cfg.get("output_format", "java"))
                self.mcworld_enabled.set(bool(cfg.get("mcworld_enabled", True)))
                self.mcworld_save_dir.set(
                    cfg.get("mcworld_save_dir", self._get_downloads_dir()))
                self.luanti_output_dir.set(cfg.get("luanti_output_dir", ""))
        except Exception:
            pass
        # デフォルトのダウンロードフォルダを未設定時に補完
        if not self.mcworld_save_dir.get():
            self.mcworld_save_dir.set(self._get_downloads_dir())

    def _save_config(self):
        try:
            cfg = {}
            if os.path.isfile(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["plateau_dist_m"] = self.plateau_dist_m.get()
            cfg["output_format"] = self.output_format.get()
            cfg["mcworld_enabled"] = self.mcworld_enabled.get()
            cfg["mcworld_save_dir"] = self.mcworld_save_dir.get()
            cfg["luanti_output_dir"] = self.luanti_output_dir.get()
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_colorize_complete(self, world_folder: str):
        self._log(f"カラー適用完了: {world_folder}")


def main():
    root = tk.Tk()
    app = ArnisColorizeGUI(root)
    root.mainloop()


if __name__ == "__main__":
    # frozen exe を --map-picker モードで起動した場合は地図ウィンドウのみ表示して終了
    if "--map-picker" in sys.argv:
        from map_picker import run_picker
        run_picker()
        sys.exit(0)
    main()
