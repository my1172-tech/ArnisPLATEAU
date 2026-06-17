"""
OSM rawデータ（elements形式）から、緯度経度ポリゴンを持つ建物リストを抽出するモジュール。
osm_raw.json は標準的なOSM Overpass形式（elements配列、node/way/relation）であり、
way要素はノードID参照のみで座標を直接持たないため、nodes辞書と組み合わせて解決する。
"""
import json
from typing import List, Dict


def extract_buildings_with_polygons(osm_data: dict) -> List[Dict]:
    """
    OSM raw形式（{"elements": [...]})から、座標付きの建物ポリゴンリストを抽出する。
    GSI統合済みデータ（{"buildings": [{"polygon": [...]}, ...]})の場合はそのまま使う。

    戻り値: [{"id": ..., "polygon": [(lat, lon), ...]}, ...]
    """
    # 既にGSI統合済み・処理済み形式の場合（"buildings"キーがあり、polygon構造を持つ）
    if "buildings" in osm_data and osm_data["buildings"]:
        first = osm_data["buildings"][0]
        if "polygon" in first:
            return osm_data["buildings"]

    # OSM raw形式（"elements"キー）の場合
    if "elements" not in osm_data:
        print("[osm_building_extractor] 未知のデータ形式です（elements/buildingsキーなし）")
        return []

    elements = osm_data["elements"]

    # nodeのid→(lat,lon)辞書を構築
    nodes = {}
    for el in elements:
        if el.get("type") == "node":
            nodes[el["id"]] = (el.get("lat"), el.get("lon"))

    buildings = []
    for el in elements:
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        if "building" not in tags:
            continue

        node_ids = el.get("nodes", [])
        polygon = []
        for nid in node_ids:
            coord = nodes.get(nid)
            if coord and coord[0] is not None and coord[1] is not None:
                polygon.append(coord)

        if len(polygon) < 3:
            continue

        buildings.append({
            "id": el.get("id"),
            "polygon": polygon,
        })

    print(f"[osm_building_extractor] OSM raw形式から{len(buildings)}件の建物ポリゴンを抽出しました")
    return buildings
