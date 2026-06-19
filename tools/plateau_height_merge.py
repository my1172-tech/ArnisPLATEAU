"""
PLATEAU高さデータと生成済みワールドの建物を対応付け、
world_height_writer.py に渡す補正リストを構築するモジュール。

footprint置き換えモード（footprint_mode）:
  "skip"     （デフォルト）IoU < 0.5 またはPLATEAU bboxが隣接建物と重複する場合、
                          footprint置き換えを見送りOSM元形状を維持。高さ補正は常に適用。
  "shrink"   重複検出時にPLATEAU bboxを5%縮小して再チェック。縮小後も重複すればskip扱い。
  "priority" PLATEAU bbox面積が大きい建物を優先。小建物が重複していても大建物のbboxを採用。
"""
import json
from typing import Dict, List, Optional, Tuple
from plateau_fetcher import fetch_plateau_buildings, find_building_for_footprint
from osm_building_extractor import extract_buildings_with_polygons

FOOTPRINT_MODES = ("skip", "shrink", "priority")


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
