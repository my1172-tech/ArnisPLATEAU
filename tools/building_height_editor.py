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
from tkinter import messagebox
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


# ---------------------------------------------------------------------------
# BuildingHeightEditor ダイアログ
# ---------------------------------------------------------------------------

FILTER_OPTIONS = {"全て": 0, "10m以上": 10, "50m以上": 50, "100m以上": 100}


class BuildingHeightEditor:
    """bbox内の建物高さを確認・調整するモーダルダイアログ。"""

    def __init__(self, parent, bbox: dict, osm_data: dict = None, save_path: str = None):
        self.parent = parent
        self.bbox = bbox
        self.osm_data = osm_data or {}
        self.save_path = save_path
        self.rows = []
        self._adopt_vars = {}   # osm_id -> tk.StringVar（再描画時も保持）
        self._web_lbls = {}     # osm_id -> tk.Label（描画中のみ有効）
        self._filter_var = tk.StringVar(value="全て")

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("建物高さ調整")
        self.dialog.geometry("900x520")
        self.dialog.resizable(True, True)
        self.dialog.grab_set()

        self._build_dialog()
        threading.Thread(target=self._load_buildings, daemon=True).start()

    # ── UI構築 ────────────────────────────────────────────────────────────

    def _build_dialog(self):
        # ステータス
        self.lbl_status = tk.Label(
            self.dialog, text="建物データを取得中（Overpass API）...",
            anchor="w", font=("", 9)
        )
        self.lbl_status.pack(fill="x", padx=8, pady=(6, 2))

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
            })

        self.rows = rows
        plateau_count = sum(1 for r in rows if r["plateau_height"] is not None)
        self.dialog.after(0, lambda: self._on_buildings_loaded(plateau_count))

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
                })
            return buildings
        except Exception as e:
            print(f"[BuildingHeightEditor] Overpass取得エラー: {e}")
            return []

    def _on_buildings_loaded(self, plateau_count: int):
        self.lbl_status.config(
            text=f"取得状況: {len(self.rows)}棟 / PLATEAU {plateau_count}棟マッチ / Wikipedia取得中..."
        )
        self._render_rows()
        threading.Thread(target=self._fetch_web_heights_async, daemon=True).start()

    # ── テーブル描画 ─────────────────────────────────────────────────────

    def _render_rows(self):
        for w in self._table_frame.winfo_children():
            w.destroy()
        self._web_lbls.clear()

        threshold = FILTER_OPTIONS.get(self._filter_var.get(), 0)
        displayed = 0
        for row in self.rows:
            h_judge = row["adopt_height"] or row["plateau_height"] or row["osm_height"] or 0
            if h_judge < threshold:
                continue
            self._add_row_widget(row, displayed)
            displayed += 1

    def _add_row_widget(self, row: dict, idx: int):
        bg = "#f9fafb" if idx % 2 == 0 else "white"
        frame = tk.Frame(self._table_frame, bg=bg)
        frame.pack(fill="x", pady=1)

        # 建物名
        name_text = row["name"][:18] if len(row["name"]) > 18 else row["name"]
        tk.Label(
            frame, text=name_text, bg=bg, width=20, anchor="w", font=("", 8)
        ).pack(side="left", padx=2)

        # OSM高さ
        osm_text = f"{row['osm_height']:.0f}m" if row["osm_height"] is not None else "--"
        tk.Label(frame, text=osm_text, bg=bg, width=8, anchor="w", font=("", 8)).pack(side="left", padx=2)

        # PLATEAU高さ
        p_text = f"{row['plateau_height']:.0f}m" if row["plateau_height"] is not None else "取得失敗"
        tk.Label(frame, text=p_text, bg=bg, width=9, anchor="w", font=("", 8)).pack(side="left", padx=2)

        # Web高さ
        web_h = row.get("web_height")
        web_text = f"{web_h:.0f}m" if web_h is not None else "--"
        is_diff = self._is_significant_diff(row["plateau_height"], web_h)
        web_bg = "#fca5a5" if is_diff else bg
        web_lbl = tk.Label(
            frame, text=web_text, bg=web_bg, width=9, anchor="w", font=("", 8)
        )
        web_lbl.pack(side="left", padx=2)
        self._web_lbls[row["osm_id"]] = web_lbl

        # 採用値 Entry（再描画をまたいで値を保持）
        osm_id = row["osm_id"]
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

    def _apply_filter(self):
        self._render_rows()

    def _is_significant_diff(self, plateau_h, web_h) -> bool:
        if plateau_h is None or web_h is None:
            return False
        return abs(plateau_h - web_h) / max(plateau_h, web_h) > 0.20

    # ── Web高さ非同期取得 ───────────────────────────────────────────────

    def _fetch_web_heights_async(self):
        for row in self.rows:
            name = row["name"]
            if not name or name.startswith("建物("):
                continue
            height = fetch_height_from_wikipedia(name)
            if height is None:
                height = fetch_height_from_overpass(name, self.bbox)
            if height is not None:
                row["web_height"] = height
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
        web_h = row["web_height"]

        lbl = self._web_lbls.get(osm_id)
        if lbl:
            try:
                lbl.config(text=f"{web_h:.0f}m")
                if self._is_significant_diff(row["plateau_height"], web_h):
                    lbl.config(bg="#fca5a5")
            except tk.TclError:
                pass

        # 採用値が空かつ PLATEAU も OSM もない場合は Web 値を初期設定
        var = self._adopt_vars.get(osm_id)
        if var and not var.get() and row.get("adopt_height") is None:
            var.set(f"{web_h:.0f}")

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

            overrides.append({
                "osm_id": osm_id,
                "name": row["name"],
                "lat": row["lat"],
                "lon": row["lon"],
                "height_m": h,
                "source": source,
            })

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
