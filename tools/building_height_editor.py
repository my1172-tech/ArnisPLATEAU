"""
building_height_editor.py
bbox内の建物高さを確認・手動調整するダイアログ。
PLATEAU / Wikipedia / Overpass の順に高さを自動取得し、手動上書きも可能。
確定した高さは height_overrides.json に保存され、build_osm_height_patch() で優先適用される。
"""
import json
import os
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import urllib.parse
import urllib.request


# ---------------------------------------------------------------------------
# モジュールレベル関数: Wikipedia / Overpass 高さ取得
# ---------------------------------------------------------------------------

def fetch_height_from_wikipedia(building_name: str):
    """Wikipedia API (ja → en) で建物高さ(m)を取得する。取得できなければ None。"""
    for lang in ["ja", "en"]:
        try:
            search_url = (
                f"https://{lang}.wikipedia.org/w/api.php"
                f"?action=query&list=search"
                f"&srsearch={urllib.parse.quote(building_name)}"
                f"&format=json&srlimit=1&utf8=1"
            )
            req = urllib.request.Request(
                search_url, headers={"User-Agent": "ArnisPLATEAU/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            results = data.get("query", {}).get("search", [])
            if not results:
                continue
            title = results[0]["title"]

            page_url = (
                f"https://{lang}.wikipedia.org/w/api.php"
                f"?action=query&prop=revisions&rvprop=content"
                f"&titles={urllib.parse.quote(title)}&format=json&utf8=1"
            )
            req2 = urllib.request.Request(
                page_url, headers={"User-Agent": "ArnisPLATEAU/1.0"}
            )
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                data2 = json.loads(resp2.read().decode("utf-8"))

            for page in data2.get("query", {}).get("pages", {}).values():
                content = page.get("revisions", [{}])[0].get("*", "")
                if not content:
                    continue
                patterns = [
                    r"高さ\s*=\s*([\d.]+)\s*m",
                    r"建物高さ\s*=\s*([\d.]+)",
                    r"最高部\s*=\s*([\d.]+)\s*m",
                    r"\|\s*height_m\s*=\s*([\d.]+)",
                    r"\|\s*height\s*=\s*([\d.]+)\s*m",
                ]
                for pat in patterns:
                    m = re.search(pat, content, re.IGNORECASE)
                    if m:
                        return float(m.group(1))
        except Exception:
            continue
    return None


def fetch_height_from_overpass(building_name: str, bbox: dict):
    """Overpass APIでbbox内の同名建物の height タグを取得する。取得できなければ None。"""
    if not building_name or len(building_name) < 2:
        return None
    try:
        query = (
            f'[out:json][timeout:15];'
            f'way["name"="{building_name}"]["building"]'
            f'({bbox["min_lat"]},{bbox["min_lon"]},{bbox["max_lat"]},{bbox["max_lon"]});'
            f'out tags;'
        )
        url = "https://overpass-api.de/api/interpreter"
        data = urllib.parse.urlencode({"data": query}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"User-Agent": "ArnisPLATEAU/1.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        for elem in result.get("elements", []):
            h_str = elem.get("tags", {}).get("height")
            if h_str:
                m = re.search(r"[\d.]+", h_str)
                if m:
                    return float(m.group())
    except Exception:
        pass
    return None


def _parse_building_levels(val):
    """building:levels タグを整数に変換する。失敗時は None。"""
    if val is None:
        return None
    try:
        return int(float(str(val)))
    except (ValueError, TypeError):
        return None


def is_unnamed_building(name: str) -> bool:
    """「建物(数値)」パターン（例: 建物(6034)）か判定する。"""
    return bool(re.match(r'^建物\(\d+\)$', name or ''))


# ---------------------------------------------------------------------------
# BuildingHeightEditor ダイアログ
# ---------------------------------------------------------------------------

FILTER_OPTIONS = {"全て": 0, "10m以上": 10, "50m以上": 50, "100m以上": 100}

BUILDING_TYPE_OPTIONS = [
    ("自動（高さ閾値で判定）", ""),
    ("オフィス（灰色・モダン）",  "office"),
    ("マンション（レンガ系）",    "apartments"),
    ("商業（ガラス張り）",        "commercial"),
    ("工業（倉庫風）",            "industrial"),
]


class BuildingHeightEditor:
    """bbox内の建物高さを確認・調整するモーダルダイアログ。"""

    def __init__(self, parent, bbox: dict, osm_data: dict = None, save_path: str = None):
        self.parent = parent
        self.bbox = bbox
        self.osm_data = osm_data or {}
        self.save_path = save_path
        self.rows = []
        self._adopt_vars = {}    # osm_id -> tk.StringVar（再描画時も保持）
        self._web_lbls = {}      # osm_id -> tk.Label（描画中のみ有効）
        self._web_fetch_done = set()  # Webフェッチ完了済み osm_id のセット
        self._filter_var = tk.StringVar(value="全て")
        self.auto_fetch_var = tk.BooleanVar(value=False)
        self.building_type_var = tk.StringVar(value=BUILDING_TYPE_OPTIONS[0][0])
        self.building_details = []
        self.calibration_data: dict = {}

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("建物高さ調整")
        self.dialog.geometry("900x580")
        self.dialog.resizable(True, True)
        self.dialog.grab_set()

        self._build_dialog()
        if self.auto_fetch_var.get():
            threading.Thread(target=self._load_buildings, daemon=True).start()
        else:
            threading.Thread(target=self._load_from_overrides_only, daemon=True).start()

    # ── UI構築 ────────────────────────────────────────────────────────────

    def _build_dialog(self):
        # ステータス
        self.lbl_status = tk.Label(
            self.dialog, text="読み込み中...",
            anchor="w", font=("", 9)
        )
        self.lbl_status.pack(fill="x", padx=8, pady=(6, 2))

        # 自動取得チェックボックス（デフォルトOFF）
        tk.Checkbutton(
            self.dialog,
            text="自動取得（Overpass / Wikipedia / Web）を実行する",
            variable=self.auto_fetch_var,
            font=("", 9),
        ).pack(anchor="w", padx=8, pady=(0, 4))

        # 建物詳細JSON取り込みボタン
        frame_bd = tk.Frame(self.dialog)
        frame_bd.pack(anchor="w", padx=8, pady=(0, 2), fill="x")
        tk.Button(
            frame_bd,
            text="📂 建物詳細JSON取り込み",
            command=self._load_building_details_dialog,
            font=("", 9),
        ).pack(side="left")
        self.bd_status_label = tk.Label(
            frame_bd, text="読み込みなし", fg="gray", font=("", 9)
        )
        self.bd_status_label.pack(side="left", padx=8)
        self.bd_names_label = tk.Label(
            self.dialog, text="", fg="gray",
            wraplength=600, justify="left", font=("", 8)
        )
        self.bd_names_label.pack(anchor="w", padx=8)

        # フィルタ行
        filter_frame = tk.Frame(self.dialog)
        filter_frame.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(filter_frame, text="フィルタ:").pack(side="left")
        for label in FILTER_OPTIONS:
            tk.Radiobutton(
                filter_frame, text=label,
                variable=self._filter_var, value=label,
                command=self._apply_filter,
            ).pack(side="left", padx=4)

        # ヘッダー行
        hdr = tk.Frame(self.dialog, bg="#d1d5db")
        hdr.pack(fill="x", padx=8, pady=(0, 1))
        for text, w in [
            ("建物名", 20), ("OSM高さ", 8), ("PLATEAU", 9),
            ("Web", 9), ("採用値(m)", 10), ("", 9),
        ]:
            tk.Label(
                hdr, text=text, bg="#d1d5db", width=w,
                anchor="w", font=("", 8, "bold"), pady=3
            ).pack(side="left", padx=2)

        # スクロール可能テーブル
        table_outer = tk.Frame(self.dialog)
        table_outer.pack(fill="both", expand=True, padx=8)
        self._canvas = tk.Canvas(table_outer, highlightthickness=0)
        sb = tk.Scrollbar(table_outer, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._table_frame = tk.Frame(self._canvas)
        self._cw = self._canvas.create_window(
            (0, 0), window=self._table_frame, anchor="nw"
        )
        self._table_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        )
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfig(self._cw, width=e.width)
        )

        # 座標直接入力フォーム
        self._build_coord_input_form()

        # ボタン行
        btn_frame = tk.Frame(self.dialog)
        btn_frame.pack(fill="x", padx=8, pady=6)
        tk.Button(
            btn_frame, text="この高さで生成",
            command=self._on_confirm,
            bg="#166534", fg="white", font=("Arial", 10, "bold"),
            relief="flat", padx=10, pady=4,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            btn_frame, text="キャンセル",
            command=self.dialog.destroy
        ).pack(side="left")

    def _build_coord_input_form(self):
        """座標直接指定で建物を追加するフォーム"""
        frame = tk.LabelFrame(
            self.dialog, text="建物を直接指定して追加",
            padx=8, pady=4, font=("", 8)
        )
        frame.pack(fill="x", padx=8, pady=(4, 0))

        row1 = tk.Frame(frame)
        row1.pack(fill="x", pady=(0, 3))
        tk.Label(row1, text="座標:", width=12, anchor="w", font=("", 8)).pack(side="left")
        self.coord_entry = tk.Entry(row1, font=("", 8))
        self.coord_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Label(
            row1, text="例: 35.67034681710347, 139.7501661061619",
            fg="#6b7280", font=("", 7)
        ).pack(side="left")

        row2 = tk.Frame(frame)
        row2.pack(fill="x")
        tk.Label(row2, text="建物名（任意）:", width=12, anchor="w", font=("", 8)).pack(side="left")
        self.name_entry = tk.Entry(row2, font=("", 8), width=20)
        self.name_entry.pack(side="left", padx=(0, 8))
        tk.Label(row2, text="高さ(m):", font=("", 8)).pack(side="left")
        self.height_entry = tk.Entry(row2, font=("", 8), width=8)
        self.height_entry.pack(side="left", padx=(2, 8))
        tk.Button(
            row2, text="追加", command=self._on_add_manual,
            font=("", 8), padx=6, pady=2
        ).pack(side="left")

        row3 = tk.Frame(frame)
        row3.pack(fill="x", pady=(2, 0))
        tk.Label(row3, text="ビル外観:", width=12, anchor="w", font=("", 8)).pack(side="left")
        type_cb = ttk.Combobox(
            row3,
            textvariable=self.building_type_var,
            values=[label for label, _ in BUILDING_TYPE_OPTIONS],
            state="readonly",
            width=24,
            font=("", 8),
        )
        type_cb.current(0)
        type_cb.pack(side="left", padx=4)

    # ── データ取得（バックグラウンド） ───────────────────────────────────

    def _load_buildings(self):
        overpass_buildings = self._fetch_buildings_from_overpass(self.bbox)

        try:
            from plateau_fetcher import fetch_plateau_buildings, find_building_for_footprint
            plateau_buildings = fetch_plateau_buildings(self.bbox)
        except Exception:
            plateau_buildings = []
            find_building_for_footprint = None

        rows = []
        for ob in overpass_buildings:
            lat, lon = ob["lat"], ob["lon"]
            plateau_h = None
            if plateau_buildings and find_building_for_footprint:
                try:
                    match = find_building_for_footprint(
                        plateau_buildings, lat, lon, max_dist_m=100
                    )
                    if match:
                        plateau_h = match.get("measured_height")
                except Exception:
                    pass

            adopt = plateau_h if plateau_h is not None else ob.get("osm_height")
            rows.append({
                "osm_id": ob["osm_id"],
                "name": ob.get("name") or f"建物({str(ob['osm_id'])[-4:]})",
                "osm_height": ob.get("osm_height"),
                "plateau_height": plateau_h,
                "web_height": None,
                "adopt_height": adopt,
                "lat": lat,
                "lon": lon,
                "building_levels": ob.get("building_levels"),
            })

        self.rows = rows
        plateau_count = sum(1 for r in rows if r["plateau_height"] is not None)
        self.dialog.after(0, lambda: self._on_buildings_loaded(plateau_count))

    def _load_from_overrides_only(self):
        """自動取得OFF: height_overrides.json の既存データのみ行に変換して表示する。
        Overpass / PLATEAU / Web へのネットワーク通信は一切行わない。"""
        rows = []
        if self.save_path and os.path.isfile(self.save_path):
            try:
                with open(self.save_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for o in data.get("overrides", []):
                    osm_id = o.get("osm_id")
                    if osm_id is None:
                        continue
                    height_m = o.get("height_m")
                    row = {
                        "osm_id": osm_id,
                        "name": o.get("name", f"建物({osm_id})"),
                        "osm_height": None,
                        "plateau_height": None,
                        "web_height": None,
                        "adopt_height": height_m,
                        "lat": o.get("lat"),
                        "lon": o.get("lon"),
                        "building_levels": None,
                        "building_type": o.get("building_type", ""),
                    }
                    # 採用値を事前セット（再描画時も _adopt_vars が初期化されるよう）
                    if osm_id not in self._adopt_vars:
                        self._adopt_vars[osm_id] = tk.StringVar(
                            value=f"{height_m:.0f}" if height_m is not None else ""
                        )
                    rows.append(row)
            except Exception as e:
                print(f"[BuildingHeightEditor] height_overrides.json 読み込みエラー: {e}")

        self.rows = rows
        try:
            self.dialog.after(0, lambda: self._on_overrides_loaded(len(rows)))
        except Exception:
            pass

    def _on_overrides_loaded(self, count: int):
        self.lbl_status.config(
            text=f"既存の登録: {count}棟（自動取得OFF — チェックボックスをONにすると取得を実行）"
        )
        self._render_rows()

    def _fetch_buildings_from_overpass(self, bbox: dict) -> list:
        try:
            query = (
                f"[out:json][timeout:30];"
                f'way["building"]'
                f'({bbox["min_lat"]},{bbox["min_lon"]},{bbox["max_lat"]},{bbox["max_lon"]});'
                f"out tags center;"
            )
            url = "https://overpass-api.de/api/interpreter"
            data = urllib.parse.urlencode({"data": query}).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"User-Agent": "ArnisPLATEAU/1.0"}
            )
            with urllib.request.urlopen(req, timeout=35) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            buildings = []
            for elem in result.get("elements", []):
                tags = elem.get("tags", {})
                center = elem.get("center", {})
                if not center:
                    continue
                h_str = tags.get("height")
                h = None
                if h_str:
                    m = re.search(r"[\d.]+", h_str)
                    if m:
                        try:
                            h = float(m.group())
                        except ValueError:
                            pass
                buildings.append({
                    "osm_id": elem.get("id"),
                    "name": tags.get("name"),
                    "osm_height": h,
                    "lat": center.get("lat"),
                    "lon": center.get("lon"),
                    "building_levels": _parse_building_levels(tags.get("building:levels")),
                })
            return buildings
        except Exception as e:
            print(f"[BuildingHeightEditor] Overpass取得エラー: {e}")
            return []

    def _on_buildings_loaded(self, plateau_count: int):
        if self.auto_fetch_var.get():
            status_suffix = "Wikipedia取得中..."
        else:
            status_suffix = "（自動取得OFF）"
        self.lbl_status.config(
            text=f"取得状況: {len(self.rows)}棟 / PLATEAU {plateau_count}棟マッチ / {status_suffix}"
        )
        self._render_rows()
        if self.auto_fetch_var.get():
            threading.Thread(target=self._fetch_web_heights_async, daemon=True).start()

    # ── テーブル描画 ─────────────────────────────────────────────────────

    def _render_rows(self):
        for w in self._table_frame.winfo_children():
            w.destroy()
        self._web_lbls.clear()

        threshold = FILTER_OPTIONS.get(self._filter_var.get(), 0)
        displayed = 0
        unnamed_count = 0
        for row in self.rows:
            if is_unnamed_building(row.get("name", "")):
                unnamed_count += 1
                continue
            h_judge = row["adopt_height"] or row["plateau_height"] or row["osm_height"] or 0
            if h_judge < threshold:
                continue
            self._add_row_widget(row, displayed)
            displayed += 1

        if unnamed_count > 0:
            tk.Label(
                self._table_frame,
                text=f"名称不明  {unnamed_count}件（非表示中）",
                fg="gray", font=("", 8),
            ).pack(anchor="w", padx=8, pady=2)

    def _refresh_table(self):
        self._render_rows()

    def _add_row_widget(self, row: dict, idx: int):
        bg = "#f9fafb" if idx % 2 == 0 else "white"
        frame = tk.Frame(self._table_frame, bg=bg)
        frame.pack(fill="x", pady=1)

        osm_id = row["osm_id"]

        # 建物名
        name_text = row["name"][:18] if len(row["name"]) > 18 else row["name"]
        tk.Label(
            frame, text=name_text, bg=bg, width=20, anchor="w", font=("", 8)
        ).pack(side="left", padx=2)

        # OSM高さ
        osm_text = f"{row['osm_height']:.0f}m" if row["osm_height"] is not None else "--"
        tk.Label(frame, text=osm_text, bg=bg, width=8, anchor="w", font=("", 8)).pack(side="left", padx=2)

        # PLATEAU高さ
        if row["plateau_height"] is not None:
            p_text = f"{row['plateau_height']:.0f}m"
        elif not self.auto_fetch_var.get():
            p_text = "--"
        else:
            p_text = "取得失敗"
        tk.Label(frame, text=p_text, bg=bg, width=9, anchor="w", font=("", 8)).pack(side="left", padx=2)

        # Web高さ（状態に応じて「--」「取得中...」「Xm」「🔍推測」を切り替え）
        web_h = row.get("web_height")
        is_done = osm_id in self._web_fetch_done

        if not self.auto_fetch_var.get():
            web_lbl = tk.Label(
                frame, text="--", bg=bg, width=9, anchor="w", font=("", 8), fg="#9ca3af"
            )
        elif web_h is not None:
            is_diff = self._is_significant_diff(row["plateau_height"], web_h)
            web_bg = "#fca5a5" if is_diff else bg
            web_lbl = tk.Label(
                frame, text=f"{web_h:.0f}m", bg=web_bg, width=9, anchor="w", font=("", 8)
            )
        elif is_done:
            web_lbl = tk.Label(
                frame, text="🔍推測", bg=bg, width=9, anchor="w",
                font=("", 8), fg="#2563eb", cursor="hand2"
            )
            web_lbl.bind("<Button-1>", lambda e, r=row: self._show_height_estimate(r))
        else:
            web_lbl = tk.Label(
                frame, text="取得中...", bg=bg, width=9, anchor="w",
                font=("", 8), fg="#9ca3af"
            )
        web_lbl.pack(side="left", padx=2)
        self._web_lbls[osm_id] = web_lbl

        # 採用値 Entry（再描画をまたいで値を保持）
        if osm_id not in self._adopt_vars:
            adopt_init = row["adopt_height"]
            self._adopt_vars[osm_id] = tk.StringVar(
                value=f"{adopt_init:.0f}" if adopt_init is not None else ""
            )
        adopt_var = self._adopt_vars[osm_id]
        tk.Entry(
            frame, textvariable=adopt_var, width=10, font=("", 8)
        ).pack(side="left", padx=2)

        # Web採用ボタン（Web高さがある場合のみ）
        if web_h is not None:
            tk.Button(
                frame, text="Web採用", font=("", 7), padx=2, pady=0,
                bg="#eff6ff",
                command=lambda v=adopt_var, h=web_h: v.set(f"{h:.0f}")
            ).pack(side="left", padx=2)

    def _load_building_details_dialog(self):
        """ファイル選択ダイアログで building_details.json を読み込む"""
        from tkinter import filedialog
        import sys

        initial = (
            os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False)
            else os.path.expanduser("~\\Desktop")
        )
        path = filedialog.askopenfilename(
            title="建物詳細JSONを選択",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=initial,
        )
        if not path:
            return

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            buildings = data.get("buildings", [])
            if not buildings:
                messagebox.showwarning(
                    "読み込み失敗",
                    "buildings キーが見つかりません。\nJSON形式を確認してください。"
                )
                return
            self._raw_building_json = data
            self.building_details = buildings
            self.calibration_data = data.get("calibration", {})
            count = len(buildings)
            calib_suffix = ""
            if self.calibration_data and len(self.calibration_data.get("points", {})) >= 4:
                calib_suffix = " ・キャリブレーションあり"
            names = "、".join(b.get("name", "不明") for b in buildings[:5])
            if count > 5:
                names += f" 他{count - 5}棟"
            self.bd_status_label.config(
                text=f"読み込み済み: {count}棟{calib_suffix}", fg="green"
            )
            self.bd_names_label.config(text=names)
        except Exception as e:
            messagebox.showerror("読み込みエラー", f"JSONの読み込みに失敗しました:\n{e}")

    def _get_calibration_data(self) -> dict:
        """読み込み済み building_details から calibration データを取得"""
        if not hasattr(self, "_raw_building_json"):
            return {}
        return self._raw_building_json.get("calibration", {})

    def _apply_filter(self):
        self._render_rows()

    def _is_significant_diff(self, plateau_h, web_h) -> bool:
        if plateau_h is None or web_h is None:
            return False
        return abs(plateau_h - web_h) / max(plateau_h, web_h) > 0.20

    # ── Web高さ非同期取得 ───────────────────────────────────────────────

    def _fetch_web_heights_async(self):
        for row in self.rows:
            osm_id = row["osm_id"]
            name = row["name"]
            skip = (
                not name
                or name.startswith("建物(")
                or name.startswith("手動追加(")
            )
            if not skip:
                height = fetch_height_from_wikipedia(name)
                if height is None:
                    height = fetch_height_from_overpass(name, self.bbox)
                if height is not None:
                    row["web_height"] = height

            self._web_fetch_done.add(osm_id)
            try:
                self.dialog.after(0, lambda r=row: self._update_single_web(r))
            except Exception:
                pass

        try:
            plateau_count = sum(1 for r in self.rows if r["plateau_height"] is not None)
            self.dialog.after(0, lambda: self.lbl_status.config(
                text=f"取得状況: {len(self.rows)}棟 / PLATEAU {plateau_count}棟マッチ / Wikipedia取得完了"
            ))
        except Exception:
            pass

    def _update_single_web(self, row: dict):
        """1棟のWeb高さをラベルに反映（ウィジェット再生成なし）"""
        osm_id = row["osm_id"]
        web_h = row.get("web_height")

        lbl = self._web_lbls.get(osm_id)
        if lbl:
            try:
                if web_h is not None:
                    lbl.config(text=f"{web_h:.0f}m", fg="black", cursor="")
                    lbl.unbind("<Button-1>")
                    if self._is_significant_diff(row["plateau_height"], web_h):
                        lbl.config(bg="#fca5a5")
                else:
                    lbl.config(text="🔍推測", fg="#2563eb", cursor="hand2")
                    lbl.bind("<Button-1>", lambda e, r=row: self._show_height_estimate(r))
            except tk.TclError:
                pass

        # 採用値が空かつ PLATEAU も OSM もない場合は Web 値を初期設定
        var = self._adopt_vars.get(osm_id)
        if var and not var.get() and row.get("adopt_height") is None and web_h is not None:
            var.set(f"{web_h:.0f}")

        # Web取得失敗 + building_levels あり → 推定値を採用値に自動入力（空欄のみ）
        if web_h is None:
            levels = row.get("building_levels")
            if levels:
                var = self._adopt_vars.get(osm_id)
                if var and not var.get().strip():
                    var.set(str(round(levels * 3.75)))

    # ── 高さ推定ポップアップ ─────────────────────────────────────────────

    def _show_height_estimate(self, row: dict):
        """building:levels から高さ推定を表示する小ウィンドウ"""
        levels = row.get("building_levels")

        win = tk.Toplevel(self.dialog)
        win.title("高さ推定")
        win.resizable(False, False)
        win.grab_set()

        if levels:
            est_min = round(levels * 3.5)
            est_max = round(levels * 4.0)
            text = (
                f"※ Webで高さデータが見つかりませんでした\n\n"
                f"建物情報から推測する場合の参考:\n"
                f"・地上 {levels}階建て\n"
                f"・一般的なオフィスビルの階高: 約3.5〜4m/階\n\n"
                f"▶ 推定: 約{est_min}〜{est_max}m程度\n"
                f"  ({est_min}〜{est_max}ブロック)"
            )
        else:
            text = (
                f"※ Webで高さデータが見つかりませんでした\n\n"
                f"階数情報も取得できませんでした。\n"
                f"手動で高さを入力してください。\n\n"
                f"参考: 一般的なオフィスビルは\n"
                f"  1階あたり約3.5〜4m"
            )

        tk.Label(win, text=text, justify="left", padx=16, pady=16).pack()
        tk.Button(win, text="閉じる", command=win.destroy).pack(pady=8)

    # ── 座標直接指定 ─────────────────────────────────────────────────────

    def _parse_coords(self, text: str):
        """緯度・経度のペアを様々な形式から解析する。失敗時は None。"""
        nums = re.findall(r"[-+]?\d+\.?\d*", text)
        if len(nums) >= 2:
            try:
                return float(nums[0]), float(nums[1])
            except ValueError:
                return None
        return None

    def _on_add_manual(self):
        """「追加」ボタン: 座標指定で建物行をリストに追加する"""
        coords = self._parse_coords(self.coord_entry.get())
        if coords is None:
            self.coord_entry.config(bg="lightcoral")
            return
        self.coord_entry.config(bg="white")

        lat, lon = coords
        name = self.name_entry.get().strip() or None
        height_str = self.height_entry.get().strip()
        height = None
        if height_str:
            try:
                height = float(height_str)
            except ValueError:
                pass

        selected_label = self.building_type_var.get()
        building_type = next(
            (tag for label, tag in BUILDING_TYPE_OPTIONS if label == selected_label), ""
        )

        osm_id = f"manual_{lat:.5f}_{lon:.5f}"
        row = {
            "osm_id": osm_id,
            "name": name or f"手動追加({lat:.4f},{lon:.4f})",
            "lat": lat,
            "lon": lon,
            "osm_height": None,
            "plateau_height": None,
            "web_height": None,
            "adopt_height": height,
            "building_levels": None,
            "building_type": building_type,
        }
        # 手動追加行はWebフェッチ対象外として即 done にマーク
        self._web_fetch_done.add(osm_id)
        self.rows.append(row)
        self._render_rows()

        self.coord_entry.delete(0, tk.END)
        self.name_entry.delete(0, tk.END)
        self.height_entry.delete(0, tk.END)

    # ── 確定・保存 ───────────────────────────────────────────────────────

    def _on_confirm(self):
        overrides = []
        for row in self.rows:
            osm_id = row["osm_id"]
            var = self._adopt_vars.get(osm_id)
            if var is None:
                continue
            val_str = var.get().strip()
            if not val_str:
                continue
            try:
                h = float(val_str)
            except ValueError:
                continue

            source = "manual"
            if row.get("web_height") is not None and abs(h - row["web_height"]) < 0.5:
                source = "web"
            elif row.get("plateau_height") is not None and abs(h - row["plateau_height"]) < 0.5:
                source = "plateau"

            entry = {
                "osm_id": osm_id,
                "name": row["name"],
                "lat": row["lat"],
                "lon": row["lon"],
                "height_m": h,
                "source": source,
            }
            if row.get("building_type"):
                entry["building_type"] = row["building_type"]
            overrides.append(entry)

        if self.save_path:
            try:
                out = {"version": 1, "overrides": overrides}
                with open(self.save_path, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=2)
            except Exception as e:
                messagebox.showerror(
                    "保存エラー",
                    f"height_overrides.json の保存に失敗しました:\n{e}"
                )
                return

        self.dialog.destroy()
