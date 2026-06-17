"""
GSI建物データをMinecraftワールドにマージするモジュール。
本家arnisが生成したワールドに対し、後処理でGSI建物を追加する。
"""
import json
from typing import List
from gsi_fetcher import fetch_gsi_buildings


def merge_gsi_into_osm_json(osm_json_path: str, bbox: dict, output_path: str) -> dict:
    """
    既存のosm_raw.json（本家arnis生成）にGSI建物を追加合体する。
    OSMの建物形式と同じ構造に変換してマージする。

    戻り値: {"osm_buildings": int, "gsi_buildings": int, "total": int}
    """
    with open(osm_json_path, "r", encoding="utf-8") as f:
        osm_data = json.load(f)

    osm_building_count = len(osm_data.get("buildings", []))

    gsi_buildings = fetch_gsi_buildings(bbox)

    for gsi_b in gsi_buildings:
        osm_format_building = _convert_gsi_to_osm_format(gsi_b)
        osm_data.setdefault("buildings", []).append(osm_format_building)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(osm_data, f, ensure_ascii=False, indent=2)

    return {
        "osm_buildings": osm_building_count,
        "gsi_buildings": len(gsi_buildings),
        "total": osm_building_count + len(gsi_buildings),
    }


def _convert_gsi_to_osm_format(gsi_building: dict) -> dict:
    """GSI建物データをOSM建物データと同じ構造の辞書に変換する"""
    return {
        "id": gsi_building["id"],
        "type": "building",
        "source": "gsi",
        "polygon": gsi_building["polygon"],
        "floors": gsi_building["floors"],
        "type_code": gsi_building["type_code"],
        "height": gsi_building["floors"] * 3.0,  # 1階=3m換算（暫定）
    }
