"""
PLATEAU高さデータと生成済みワールドの建物を対応付け、
world_height_writer.py に渡す補正リストを構築するモジュール。

footprint置き換えモード（footprint_mode）:
  "skip"     （デフォルト）IoU < 0.5 またはPLATEAU bboxが隣接建物と重複する場合、
                          footprint置き換えを見送りOSM元形状を維持。高さ補正は常に適用。
  "shrink"   重複検出時にPLATEAU bboxを5%縮小して再チェック。縮小後も重複すればskip扱い。
  "priority" PLATEAU bbox面積が大きい建物を優先。小建物が重複していても大建物のbboxを採用。
"""
import copy
import json
from typing import Dict, List, Optional, Tuple
from plateau_fetcher import fetch_plateau_buildings, find_building_for_footprint, _haversine_m
from osm_building_extractor import extract_buildings_with_polygons

FOOTPRINT_MODES = ("skip", "shrink", "priority")

ROAD_COLOR_MAP = {
    "black":      "black_concrete",
    "gray":       "gray_concrete",
    "light_gray": "light_gray_concrete",
}


def latlon_to_mc(lat: float, lon: float, metadata: dict) -> tuple:
    """metadata.jsonの範囲情報から緯度経度をMinecraft座標(x,z)に線形変換する。
    arnis の座標系: 北(maxGeoLat)→minMcZ=0, 南(minGeoLat)→maxMcZ
    Z軸は緯度と逆向き（Minecraft標準: 北=-Z, 南=+Z）のため (1-lat_ratio) で反転する。
    """
    lat_ratio = (lat - metadata["minGeoLat"]) / (metadata["maxGeoLat"] - metadata["minGeoLat"])
    lon_ratio = (lon - metadata["minGeoLon"]) / (metadata["maxGeoLon"] - metadata["minGeoLon"])
    x = metadata["minMcX"] + lon_ratio * (metadata["maxMcX"] - metadata["minMcX"])
    z = metadata["minMcZ"] + (1.0 - lat_ratio) * (metadata["maxMcZ"] - metadata["minMcZ"])
    return round(x), round(z)


def _bbox_to_polygon_latlon(bbox: dict) -> List[Tuple[float, float]]:
    """PLATEAU footprint bbox を反時計回り4頂点ポリゴン（緯度, 経度）に変換する"""
    return [
        (bbox["ymin"], bbox["xmin"]),
        (bbox["ymin"], bbox["xmax"]),
        (bbox["ymax"], bbox["xmax"]),
        (bbox["ymax"], bbox["xmin"]),
    ]


def _compute_iou(plateau_bbox: dict, osm_polygon: List[Tuple[float, float]]) -> float:
    """PLATEAU footprint bbox と OSM polygon の IoU を計算する（shapely使用）"""
    try:
        from shapely.geometry import box, Polygon
        p_box = box(plateau_bbox["xmin"], plateau_bbox["ymin"],
                    plateau_bbox["xmax"], plateau_bbox["ymax"])
        osm_lons = [p[1] for p in osm_polygon]
        osm_lats = [p[0] for p in osm_polygon]
        if len(osm_lons) < 3:
            return 0.0
        o_box = box(min(osm_lons), min(osm_lats), max(osm_lons), max(osm_lats))
        intersection = p_box.intersection(o_box).area
        union = p_box.union(o_box).area
        return intersection / union if union > 0 else 0.0
    except Exception:
        return 0.0


def _shrink_bbox(bbox: dict, factor: float = 0.05) -> dict:
    """bbox を factor 分率で中心に向かって縮小する"""
    cx = (bbox["xmin"] + bbox["xmax"]) / 2
    cy = (bbox["ymin"] + bbox["ymax"]) / 2
    hw = (bbox["xmax"] - bbox["xmin"]) / 2 * (1.0 - factor)
    hh = (bbox["ymax"] - bbox["ymin"]) / 2 * (1.0 - factor)
    return {"xmin": cx - hw, "xmax": cx + hw, "ymin": cy - hh, "ymax": cy + hh}


def _boxes_overlap(a: dict, b: dict) -> bool:
    """2つの軸平行 bbox が重なるか（接触は重なりとしない）"""
    return not (a["xmax"] <= b["xmin"] or a["xmin"] >= b["xmax"] or
                a["ymax"] <= b["ymin"] or a["ymin"] >= b["ymax"])


def _bbox_area(bbox: dict) -> float:
    return (bbox["xmax"] - bbox["xmin"]) * (bbox["ymax"] - bbox["ymin"])


def build_height_corrections(
    bbox: dict,
    osm_data: dict,
    metadata: dict,
    footprint_mode: str = "priority",
    max_dist_m: float = 50.0,
) -> List[Dict]:
    """
    osm_data（osm_raw.json または osm_merged.json の生データ）からPLATEAU高さ補正情報を構築する。
    footprint_mode: "skip" | "shrink" | "priority"
    高さ補正はfootprint置き換えの成否に関わらず常に適用する。
    """
    if footprint_mode not in FOOTPRINT_MODES:
        print(f"[plateau_height_merge] 不正なfootprint_mode={footprint_mode!r}、skipに変更します")
        footprint_mode = "skip"

    osm_buildings = extract_buildings_with_polygons(osm_data)
    if not osm_buildings:
        print("[plateau_height_merge] OSM建物データの抽出結果が0件のため補正をスキップします")
        return []

    plateau_buildings = fetch_plateau_buildings(bbox)
    if not plateau_buildings:
        print("[plateau_height_merge] PLATEAUデータ取得失敗のため高さ補正をスキップします")
        return []

    print(f"[plateau_height_merge] footprint_mode={footprint_mode}, max_dist_m={max_dist_m}")
    print(f"[plateau_height_merge] OSM建物: {len(osm_buildings)}棟 / PLATEAU建物: {len(plateau_buildings)}棟")

    # ---- マッチング ----
    candidates = []
    for osm_b in osm_buildings:
        polygon = osm_b.get("polygon", [])
        if len(polygon) < 3:
            continue
        center_lat = sum(p[0] for p in polygon) / len(polygon)
        center_lon = sum(p[1] for p in polygon) / len(polygon)

        match = find_building_for_footprint(plateau_buildings, center_lat, center_lon, max_dist_m=max_dist_m)
        if not match or match.get("measured_height") is None:
            continue

        plateau_bbox = match.get("bbox")
        iou = _compute_iou(plateau_bbox, polygon) if plateau_bbox else 0.0
        area = _bbox_area(plateau_bbox) if plateau_bbox else 0.0

        candidates.append({
            "osm_b": osm_b,
            "match": match,
            "iou": iou,
            "plateau_bbox": plateau_bbox,
            "plateau_area": area,
        })

    print(f"[plateau_height_merge] マッチング成功: {len(candidates)}棟 / OSM {len(osm_buildings)}棟中")

    # ---- footprint 置き換え解決 ----
    # priority モードのみ面積降順（大建物を先に処理して優先）
    if footprint_mode == "priority":
        candidates.sort(key=lambda c: c["plateau_area"], reverse=True)

    claimed_bboxes: List[dict] = []   # 適用済み PLATEAU bbox の一覧
    fp_replaced = 0
    fp_skipped_iou = 0
    fp_skipped_overlap = 0

    corrections = []
    for c in candidates:
        osm_polygon = c["osm_b"].get("polygon", [])
        height = c["match"]["measured_height"]
        iou = c["iou"]
        plateau_bbox = c["plateau_bbox"]

        use_plateau_fp = False
        effective_bbox = plateau_bbox

        if not plateau_bbox or iou < 0.5:
            # IoU不足またはbbox情報なし → footprint置き換えなし
            fp_skipped_iou += 1

        elif footprint_mode == "skip":
            has_overlap = any(_boxes_overlap(plateau_bbox, cb) for cb in claimed_bboxes)
            if has_overlap:
                fp_skipped_overlap += 1
            else:
                use_plateau_fp = True
                claimed_bboxes.append(plateau_bbox)

        elif footprint_mode == "shrink":
            candidate = plateau_bbox
            if any(_boxes_overlap(candidate, cb) for cb in claimed_bboxes):
                candidate = _shrink_bbox(plateau_bbox, 0.05)
                if any(_boxes_overlap(candidate, cb) for cb in claimed_bboxes):
                    fp_skipped_overlap += 1
                    candidate = None
            if candidate is not None:
                use_plateau_fp = True
                effective_bbox = candidate
                claimed_bboxes.append(candidate)

        elif footprint_mode == "priority":
            # 大→小の順で処理。小建物の既登録 bbox を削除して本建物を優先
            removed = [cb for cb in claimed_bboxes if _boxes_overlap(plateau_bbox, cb)]
            if removed:
                fp_skipped_overlap += len(removed)
                claimed_bboxes = [cb for cb in claimed_bboxes if not _boxes_overlap(plateau_bbox, cb)]
            use_plateau_fp = True
            claimed_bboxes.append(plateau_bbox)

        # footprint 決定（高さは常に適用）
        if use_plateau_fp and effective_bbox:
            fp_polygon = _bbox_to_polygon_latlon(effective_bbox)
            fp_replaced += 1
        else:
            fp_polygon = osm_polygon

        polygon_mc_xz = [latlon_to_mc(p[0], p[1], metadata) for p in fp_polygon]

        osm_polygon_mc_xz = None
        if use_plateau_fp:
            osm_polygon_mc_xz = [latlon_to_mc(p[0], p[1], metadata) for p in osm_polygon]

        corrections.append({
            "polygon_mc_xz": polygon_mc_xz,
            "target_height_m": height,
            "footprint_replaced": use_plateau_fp,
            "iou": round(iou, 3),
            "osm_polygon_mc_xz": osm_polygon_mc_xz,
        })

    total = len(corrections)
    print(f"[plateau_height_merge] 補正リスト: {total}棟")
    print(f"  footprint置き換え: {fp_replaced}棟 / IoU不足スキップ: {fp_skipped_iou}棟 "
          f"/ 重複スキップ: {fp_skipped_overlap}棟")
    return corrections


def build_osm_height_patch(
    bbox: dict,
    osm_data: dict,
    footprint_mode: str = "priority",
    max_dist_m: float = 50.0,
    height_overrides: list = None,
    road_color: str = "",
) -> tuple:
    """
    OSMデータの建物height属性をPLATEAU実測値で上書きした新しいosm_dataと更新棟数を返す。
    osm_dataはOSM raw形式（elements配列）である必要がある（arnis --file で使用するため）。
    metadata不要（MC座標変換は行わない）。
    height_overrides: height_overrides.json の overrides リスト。osm_idが一致する建物を最優先で適用。

    Returns:
        (patched_osm_data: dict, patch_count: int)
    """
    patched = copy.deepcopy(osm_data)

    # height_overrides を3種に分類
    # - manual_coord_overrides: "manual_..."文字列id（座標直接入力）— 最高優先
    # - manual_id_overrides:    source=manual かつ整数id（テーブルからのチェック選択）
    # - plateau_id_overrides:   source=plateau かつ整数id
    manual_coord_overrides = []
    manual_id_overrides = {}
    plateau_id_overrides = {}
    if height_overrides:
        for o in height_overrides:
            oid = o.get("osm_id")
            if isinstance(oid, str) and oid.startswith("manual_"):
                manual_coord_overrides.append(o)
            elif oid is not None:
                if o.get("source") == "manual":
                    manual_id_overrides[oid] = o
                else:
                    plateau_id_overrides[oid] = o

    total_overrides = len(manual_coord_overrides) + len(manual_id_overrides) + len(plateau_id_overrides)
    plateau_buildings = fetch_plateau_buildings(bbox)
    if not plateau_buildings and total_overrides == 0:
        print("[plateau_height_merge] PLATEAUデータなし・overridesなし → OSMパッチをスキップ")
        return patched, 0

    osm_buildings = extract_buildings_with_polygons(osm_data)
    if not osm_buildings:
        print("[plateau_height_merge] OSM建物なし → OSMパッチをスキップ")
        return patched, 0

    print(f"[plateau_height_merge] build_osm_height_patch: "
          f"OSM {len(osm_buildings)}棟 / PLATEAU {len(plateau_buildings)}棟"
          f" / manual_coord {len(manual_coord_overrides)} / manual_id {len(manual_id_overrides)}"
          f" / plateau_id {len(plateau_id_overrides)}")

    # 要素検索を高速化するため id → elem の逆引き辞書を作成
    elem_by_id = {e.get("id"): e for e in patched.get("elements", []) if e.get("id") is not None}

    patch_count = 0
    for osm_b in osm_buildings:
        osm_id = osm_b.get("id")
        if osm_id is None:
            continue

        polygon = osm_b.get("polygon", [])
        center_lat = sum(p[0] for p in polygon) / len(polygon) if len(polygon) >= 3 else None
        center_lon = sum(p[1] for p in polygon) / len(polygon) if len(polygon) >= 3 else None

        height = None
        matched_override = None

        # 優先1: 手動座標入力（"manual_..."id）— 近傍50m以内の最近傍を採用（最高優先）
        if manual_coord_overrides and center_lat is not None:
            best_dist = float("inf")
            best_override = None
            for o in manual_coord_overrides:
                if o.get("lat") is None or o.get("lon") is None:
                    continue
                dist = _haversine_m(center_lat, center_lon, o["lat"], o["lon"])
                if dist <= 50.0 and dist < best_dist:
                    best_dist = dist
                    best_override = o
            if best_override:
                height = best_override.get("height_m")
                matched_override = best_override

        # 優先2: source=manual かつ整数 osm_id（テーブルのチェック選択）
        if height is None and osm_id in manual_id_overrides:
            matched_override = manual_id_overrides[osm_id]
            height = matched_override.get("height_m")

        # 優先3: source=plateau かつ整数 osm_id
        if height is None and osm_id in plateau_id_overrides:
            matched_override = plateau_id_overrides[osm_id]
            height = matched_override.get("height_m")

        # 優先4: PLATEAU APIマッチング
        if height is None and plateau_buildings and center_lat is not None:
            match = find_building_for_footprint(
                plateau_buildings, center_lat, center_lon, max_dist_m=max_dist_m
            )
            height = match.get("measured_height") if match else None

        # 優先5: building:levels × 3m 補完（いずれもなし・PLATEAU未収録建物のみ）
        target_elem = elem_by_id.get(osm_id)
        if height is None and target_elem is not None:
            levels_str = target_elem.get("tags", {}).get("building:levels")
            if levels_str:
                try:
                    height = float(levels_str) * 3.0
                except ValueError:
                    pass

        if height is None:
            continue

        if target_elem is None:
            continue
        if "tags" not in target_elem:
            target_elem["tags"] = {}
        target_elem["tags"]["height"] = str(round(height, 1))
        # arnisが building:levels 等を優先してheightを無視するのを防ぐ
        for _k in ("building:levels", "building:levels:underground",
                   "roof:height", "roof:levels", "min_height"):
            target_elem["tags"].pop(_k, None)
        # building_type 明示指定（手動追加時のビル外観選択）
        building_type = matched_override.get("building_type", "") if matched_override else ""
        if building_type:
            target_elem["tags"]["building"] = building_type
        patch_count += 1

    print(f"[plateau_height_merge] OSMパッチ完了: {patch_count}棟にPLATEAU/override/levels高さを設定")

    # 道路色の一括適用
    if road_color and road_color in ROAD_COLOR_MAP:
        surface_val = ROAD_COLOR_MAP[road_color]
        road_count = 0
        for elem in patched.get("elements", []):
            if elem.get("type") == "way" and "highway" in elem.get("tags", {}):
                elem["tags"]["surface"] = surface_val
                road_count += 1
        print(f"[plateau_height_merge] 道路色: {road_count}件のhighway wayにsurface={surface_val}を設定")

    return patched, patch_count
