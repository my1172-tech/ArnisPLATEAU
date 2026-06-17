"""
PLATEAU高さデータと生成済みワールドの建物を対応付け、
world_height_writer.py に渡す補正リストを構築するモジュール。
"""
import json
from typing import Dict, List
from plateau_fetcher import fetch_plateau_buildings, find_building_for_footprint
from osm_building_extractor import extract_buildings_with_polygons


def latlon_to_mc(lat: float, lon: float, metadata: dict) -> tuple:
    """metadata.jsonの範囲情報から緯度経度をMinecraft座標(x,z)に線形変換する"""
    lat_ratio = (lat - metadata["minGeoLat"]) / (metadata["maxGeoLat"] - metadata["minGeoLat"])
    lon_ratio = (lon - metadata["minGeoLon"]) / (metadata["maxGeoLon"] - metadata["minGeoLon"])
    x = metadata["minMcX"] + lon_ratio * (metadata["maxMcX"] - metadata["minMcX"])
    z = metadata["minMcZ"] + lat_ratio * (metadata["maxMcZ"] - metadata["minMcZ"])
    return round(x), round(z)


def build_height_corrections(bbox: dict, osm_data: dict, metadata: dict) -> List[Dict]:
    """
    osm_data（osm_raw.json または osm_merged.json の生データ）からPLATEAU高さ補正情報を構築する。
    OSM raw形式（elements配列）とGSI統合済み形式（buildings配列）の両方に対応する。
    元の建物ポリゴン（壁の形）はそのまま座標変換するだけで、形は変更しない。
    """
    osm_buildings = extract_buildings_with_polygons(osm_data)

    if not osm_buildings:
        print("[plateau_height_merge] OSM建物データの抽出結果が0件のため補正をスキップします")
        return []

    plateau_buildings = fetch_plateau_buildings(bbox)
    if not plateau_buildings:
        print("[plateau_height_merge] PLATEAUデータ取得失敗のため高さ補正をスキップします")
        return []

    corrections = []
    for osm_b in osm_buildings:
        polygon = osm_b.get("polygon", [])
        if len(polygon) < 3:
            continue
        center_lat = sum(p[0] for p in polygon) / len(polygon)
        center_lon = sum(p[1] for p in polygon) / len(polygon)

        match = find_building_for_footprint(plateau_buildings, center_lat, center_lon)
        if not match:
            continue

        polygon_mc_xz = [latlon_to_mc(p[0], p[1], metadata) for p in polygon]

        corrections.append({
            "polygon_mc_xz": polygon_mc_xz,
            "target_height_m": match["measured_height"],
        })

    print(f"[plateau_height_merge] {len(osm_buildings)}棟のOSM建物中、{len(corrections)}棟がPLATEAUデータと対応付けられました")
    return corrections
