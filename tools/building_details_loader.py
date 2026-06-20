"""
building_details_loader.py
building_details.json の読み込みと座標マッチング
"""

import math
from typing import Optional, Dict

USAGE_TO_BUILDING_TAG = {
    "retail_large": "commercial",
    "retail":       "commercial",
    "office":       "office",
    "restaurant":   "commercial",
    "foodcourt":    "commercial",
    "parking":      "industrial",
    "residential":  "apartments",
    "hotel":        "hotel",
    "other":        "yes",
}

MATERIAL_TO_COLOR = {
    "glass":    "#A8C8E8",
    "concrete": "#A0A0A0",
    "aluminum": "#C0C8D0",
    "tile":     "#D4A87C",
    "brick":    "#B05030",
    "mixed":    "#B0A898",
}


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def find_building_detail(
    buildings: list,
    center_lat: float,
    center_lon: float,
    max_dist_m: float = 50.0,
) -> Optional[Dict]:
    best_dist = float("inf")
    best = None
    for b in buildings:
        lat = b.get("lat")
        lon = b.get("lon")
        if lat is None or lon is None:
            continue
        dist = _haversine_m(center_lat, center_lon, lat, lon)
        if dist <= max_dist_m and dist < best_dist:
            best_dist = dist
            best = b
    return best


def apply_building_detail(elem: dict, detail: dict) -> None:
    tags = elem.setdefault("tags", {})

    # 高さ
    height = detail.get("height_m")
    if height:
        tags["height"] = str(float(height))
        for k in ("building:levels", "building:levels:underground",
                  "roof:height", "roof:levels", "min_height"):
            tags.pop(k, None)

    # 外装色
    material = detail.get("exterior", {}).get("material", "")
    if material in MATERIAL_TO_COLOR:
        tags["building:colour"] = MATERIAL_TO_COLOR[material]

    # 窓パターン
    windows = detail.get("windows", {})
    if windows.get("density"):
        tags["window:density"] = str(windows["density"])
    if windows.get("size"):
        tags["window:size"] = windows["size"]
    if windows.get("pattern"):
        tags["window:pattern"] = windows["pattern"]

    # building タグ（フロア用途の最多から判定）
    floor_usage = detail.get("floor_usage", {})
    if floor_usage:
        usage_counts: dict = {}
        for usage in floor_usage.values():
            usage_counts[usage] = usage_counts.get(usage, 0) + 1
        dominant_usage = max(usage_counts, key=usage_counts.get)
        tags["building"] = USAGE_TO_BUILDING_TAG.get(dominant_usage, "yes")

    # 駐車場ルーバーフロア数
    floor_details = detail.get("floor_details", [])
    louver_floors = [f for f in floor_details if f.get("window_pattern") == "louver"]
    if louver_floors:
        tags["building:parking_floors"] = str(len(louver_floors))
