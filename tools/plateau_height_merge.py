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

BUILDING_SKIP_TAGS = {
    "office", "commercial", "retail", "industrial",
    "warehouse", "hotel", "apartments", "university",
    "hospital", "school", "church", "cathedral",
    "government", "civic", "public", "train_station",
    "transportation", "stadium", "sports_hall",
}

HOUSE_TAGS = {
    "house", "detached", "semidetached_house", "terrace",
    "bungalow", "farm", "cabin", "yes",
}

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


def _get_category(tags: dict) -> str:
    from brand_color_matcher import OSM_TAG_TO_CATEGORY
    for key in ("shop", "amenity", "building"):
        val = tags.get(key, "")
        if val in OSM_TAG_TO_CATEGORY:
            return OSM_TAG_TO_CATEGORY[val]
    return ""


def build_osm_height_patch(
    bbox: dict,
    osm_data: dict,
    footprint_mode: str = "priority",
    max_dist_m: float = 50.0,
    height_overrides: list = None,
    road_color: str = "",
    apply_roof_color: bool = False,
    apply_building_color: bool = False,
    streetview_api_key: str = "",
    sv_limit: int = 50,
    building_details: list = None,
    calibration_data: dict = None,
    brand_db: dict = None,
    building_threshold: int = None,
    result_output_path: str = None,
    log_fn=None,
) -> tuple:
    """
    OSMデータの建物height属性をPLATEAU実測値で上書きした新しいosm_dataと更新棟数を返す。
    osm_dataはOSM raw形式（elements配列）である必要がある（arnis --file で使用するため）。
    metadata不要（MC座標変換は行わない）。
    height_overrides: height_overrides.json の overrides リスト。osm_idが一致する建物を最優先で適用。

    Returns:
        (patched_osm_data: dict, patch_count: int)
    """
    if log_fn is None:
        log_fn = print
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
    if not plateau_buildings and total_overrides == 0 and not building_details and not brand_db:
        log_fn("[plateau_height_merge] PLATEAUデータなし・overridesなし・building_detailsなし・brand_dbなし → OSMパッチをスキップ")
        return patched, 0

    osm_buildings = extract_buildings_with_polygons(osm_data)
    if not osm_buildings:
        log_fn("[plateau_height_merge] OSM建物なし → OSMパッチをスキップ")
        return patched, 0

    log_fn(f"[plateau_height_merge] build_osm_height_patch: "
           f"OSM {len(osm_buildings)}棟 / PLATEAU {len(plateau_buildings)}棟"
           f" / manual_coord {len(manual_coord_overrides)} / manual_id {len(manual_id_overrides)}"
           f" / plateau_id {len(plateau_id_overrides)}")

    # 要素検索を高速化するため id → elem の逆引き辞書を作成
    elem_by_id = {e.get("id"): e for e in patched.get("elements", []) if e.get("id") is not None}

    # 中心座標計算用: node id → (lat, lon)（center フィールド不在時のフォールバック用・常時作成）
    _bd_node_map = {
        e["id"]: (e.get("lat", 0.0), e.get("lon", 0.0))
        for e in patched.get("elements", [])
        if e.get("type") == "node"
    }

    # 屋根色取得用: node id → (lon, lat) の逆引き辞書
    node_map = {
        e["id"]: (e.get("lon", 0.0), e.get("lat", 0.0))
        for e in patched.get("elements", [])
        if e.get("type") == "node"
    } if apply_roof_color else {}

    # building_details 用: calibration_data から bbox を計算
    _bd_bbox = None
    _bd_mc_width = 2000
    _bd_mc_height = 2000
    if calibration_data:
        from building_details_loader import calibration_to_bbox
        _calib_pts = calibration_data.get("points", {})
        if _calib_pts.get("minGeoLat") is not None:
            _bd_bbox = calibration_to_bbox(_calib_pts)
            _bd_mc_width = int(_calib_pts.get("mcWidth", 2000))
            _bd_mc_height = int(_calib_pts.get("mcHeight", 2000))

    # Street View 壁色: 関数スコープキャッシュと取得済み件数
    _sv_cache: dict = {}
    sv_count = 0
    sv_level_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    brand_results = []
    patch_count = 0
    for osm_b in osm_buildings:
        osm_id = osm_b.get("id")
        if osm_id is None:
            continue

        polygon = osm_b.get("polygon", [])
        center_lat = sum(p[0] for p in polygon) / len(polygon) if len(polygon) >= 3 else None
        center_lon = sum(p[1] for p in polygon) / len(polygon) if len(polygon) >= 3 else None

        # polygon が空の場合: center フィールド → ノード重心 の順でフォールバック
        if center_lat is None:
            _raw = elem_by_id.get(osm_id)
            if _raw is not None:
                _c = _raw.get("center", {})
                if _c:
                    center_lat = _c.get("lat")
                    center_lon = _c.get("lon")
                else:
                    _pts = [_bd_node_map[nid] for nid in _raw.get("nodes", []) if nid in _bd_node_map]
                    if _pts:
                        center_lat = sum(p[0] for p in _pts) / len(_pts)
                        center_lon = sum(p[1] for p in _pts) / len(_pts)

        # 優先0: building_details.json（座標マッチング・最高優先）
        if building_details and center_lat is not None:
            target_bd = elem_by_id.get(osm_id)
            if target_bd is not None:
                from building_details_loader import find_building_detail, apply_building_detail
                detail = find_building_detail(
                    building_details, center_lat, center_lon,
                    max_dist_m=max_dist_m,
                    bbox=_bd_bbox, mc_width=_bd_mc_width, mc_height=_bd_mc_height,
                )
                if detail:
                    apply_building_detail(target_bd, detail)
                    patch_count += 1
                    _t = target_bd.get("tags", {})
                    log_fn(
                        f"[building_details] {detail.get('name', '?')} → "
                        f"壁色:{_t.get('building:colour', 'なし')} "
                        f"高さ:{detail.get('height_m', '?')}m "
                        f"タイプ:{_t.get('building', '?')} "
                        f"窓密度:{_t.get('window:density', '?')}"
                    )
                    continue

        height = None
        matched_override = None
        brand_applied = False

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

        # 優先4: ブランドカラーDB（色のみ設定・高さは後続で取得）
        if height is None and brand_db:
            _brand_elem = elem_by_id.get(osm_id)
            if _brand_elem is not None:
                from brand_color_matcher import match_brand_color
                brand_colours = match_brand_color(_brand_elem.get("tags", {}), brand_db)
                if brand_colours:
                    _brand_elem.setdefault("tags", {})
                    for _k, _v in brand_colours.items():
                        _brand_elem["tags"][_k] = _v
                    if "building:colour" in brand_colours:
                        if _brand_elem["tags"].get("building", "yes") in ("yes", ""):
                            _brand_elem["tags"]["building"] = "commercial"
                    brand_applied = True
                    brand_results.append({
                        "osm_id": osm_id,
                        "name": _brand_elem.get("tags", {}).get("name", ""),
                        "lat": round(center_lat, 6) if center_lat is not None else None,
                        "lon": round(center_lon, 6) if center_lon is not None else None,
                        "category": _get_category(_brand_elem.get("tags", {})),
                        "building_colour": brand_colours.get("building:colour", ""),
                        "roof_colour": brand_colours.get("roof:colour", ""),
                        "roof_shape": brand_colours.get("roof:shape", ""),
                        "building_type": _brand_elem.get("tags", {}).get("building", ""),
                        "source": "brand_db",
                    })
                    log_fn(
                        f"[brand_colors] {_brand_elem.get('tags', {}).get('name', '?')}: "
                        f"building:colour={brand_colours.get('building:colour', 'なし')} "
                        f"roof:shape={brand_colours.get('roof:shape', 'なし')}"
                    )

        # 優先5: PLATEAU APIマッチング
        if height is None and plateau_buildings and center_lat is not None:
            match = find_building_for_footprint(
                plateau_buildings, center_lat, center_lon, max_dist_m=max_dist_m
            )
            height = match.get("measured_height") if match else None

        # 優先1-5でのheight取得有無を記録（屋根色はPLATEAUなし建物のみ対象）
        has_priority_height = height is not None

        # 優先6: building:levels × 3m 補完（いずれもなし・PLATEAU未収録建物のみ）
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

        # 一軒家/ビル切替え（ブランドカラー適用済み・既存ビル系タグはスキップ）
        if (not brand_applied
                and building_threshold is not None
                and height is not None
                and height >= building_threshold):
            current_building = target_elem["tags"].get("building", "yes")
            if current_building in HOUSE_TAGS:
                target_elem["tags"]["building"] = "office"

        # Street View 壁色取得（上限棟数以内・全建物対象）
        if apply_building_color and streetview_api_key and sv_count < sv_limit and center_lat is not None:
            cache_key = f"{round(center_lat, 4)}_{round(center_lon, 4)}"
            if cache_key not in _sv_cache:
                try:
                    from streetview_building_color import get_building_colors_from_streetview
                    _sv_cache[cache_key] = get_building_colors_from_streetview(
                        center_lat, center_lon, streetview_api_key
                    )
                except Exception:
                    _sv_cache[cache_key] = None
            sv_result = _sv_cache.get(cache_key)
            if sv_result:
                target_elem["tags"]["building:colour"] = sv_result["building_colour"]
                target_elem["tags"]["roof:colour"] = sv_result["roof_colour"]
                if not building_type:  # 手動 building_type が指定されている場合は上書きしない
                    target_elem["tags"]["building"] = sv_result["building_type"]
                target_elem["tags"]["window:density"] = str(sv_result["window_level"])
                target_elem["tags"]["window:size"] = sv_result["window_size"]
                sv_level_counts[sv_result["window_level"]] = sv_level_counts.get(sv_result["window_level"], 0) + 1
                sv_count += 1

        # 屋根色: Street View未取得の建物のみ衛星画像から取得
        if apply_roof_color and not has_priority_height and "roof:colour" not in target_elem.get("tags", {}):
            try:
                from satellite_roof_color import get_roof_color_from_polygon
                way_nodes = target_elem.get("nodes", [])
                polygon_lonlat = [node_map[nid] for nid in way_nodes if nid in node_map]
                if len(polygon_lonlat) >= 3:
                    color = get_roof_color_from_polygon(polygon_lonlat)
                    if color:
                        target_elem["tags"]["roof:colour"] = color
            except Exception:
                pass

        patch_count += 1

    log_fn(f"[plateau_height_merge] OSMパッチ完了: {patch_count}棟にPLATEAU/override/levels高さを設定")
    if sv_count > 0:
        lv = sv_level_counts
        log_fn(f"[plateau_height_merge] Street View 壁色・窓パターン: {sv_count}棟 "
               f"Lv1={lv[1]} Lv2={lv[2]} Lv3={lv[3]} Lv4={lv[4]} Lv5={lv[5]}")

    # 道路色の一括適用
    if road_color and road_color in ROAD_COLOR_MAP:
        surface_val = ROAD_COLOR_MAP[road_color]
        road_count = 0
        for elem in patched.get("elements", []):
            if elem.get("type") == "way" and "highway" in elem.get("tags", {}):
                elem["tags"]["surface"] = surface_val
                road_count += 1
        log_fn(f"[plateau_height_merge] 道路色: {road_count}件のhighway wayにsurface={surface_val}を設定")

    # brand_results を JSON に出力
    if result_output_path and brand_results:
        import datetime
        result_data = {
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_matched": len(brand_results),
            "buildings": brand_results,
        }
        try:
            with open(result_output_path, "w", encoding="utf-8") as f:
                json.dump(result_data, f, ensure_ascii=False, indent=2)
            log_fn(f"[brand_colors] 結果を保存: {result_output_path}（{len(brand_results)}棟）")
        except Exception as e:
            log_fn(f"[brand_colors] 結果保存失敗: {e}")

    return patched, patch_count
