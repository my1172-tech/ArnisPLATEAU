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

MINECRAFT_BLOCK_TO_COLOR = {
    # コンクリート系
    "white_concrete":           "#FFFFFF",
    "light_gray_concrete":      "#9D9D97",
    "gray_concrete":            "#474F52",
    "black_concrete":           "#1D1D21",
    "brown_concrete":           "#603C20",
    "red_concrete":             "#8E2020",
    "orange_concrete":          "#E06101",
    "yellow_concrete":          "#F0AF15",
    "lime_concrete":            "#5EA918",
    "green_concrete":           "#364B18",
    "cyan_concrete":            "#158991",
    "light_blue_concrete":      "#3AB3DA",
    "blue_concrete":            "#2C2F8F",
    "purple_concrete":          "#641F9C",
    "magenta_concrete":         "#BE49C9",
    "pink_concrete":            "#D5658F",
    # 石系
    "stone":                    "#7F7F7F",
    "stone_bricks":             "#6A6A6A",
    "smooth_stone":             "#9A9A9A",
    "cobblestone":              "#828282",
    "mossy_stone_bricks":       "#5F6B4A",
    "chiseled_stone_bricks":    "#737373",
    "cracked_stone_bricks":     "#686868",
    # 砂岩・テラコッタ
    "sandstone":                "#E0D5A0",
    "smooth_sandstone":         "#DDD196",
    "cut_sandstone":            "#D9CD8F",
    "terracotta":               "#985335",
    "white_terracotta":         "#D1B1A1",
    "light_gray_terracotta":    "#876B62",
    "gray_terracotta":          "#645452",
    "cyan_terracotta":          "#575C5C",
    # レンガ
    "bricks":                   "#9C4E37",
    "mud_bricks":               "#8E7355",
    # 木材系
    "oak_planks":               "#C49A3C",
    "spruce_planks":            "#7E5C2B",
    "birch_planks":             "#D7C185",
    "dark_oak_planks":          "#3C2412",
    "acacia_planks":            "#BA6637",
    "jungle_planks":            "#9D7040",
    # ガラス系（窓用）
    "glass_pane":               "#C0D8E8",
    "white_stained_glass":      "#FFFFFF",
    "light_blue_stained_glass": "#5EB7D5",
    "cyan_stained_glass":       "#157788",
    "blue_stained_glass":       "#253193",
    "gray_stained_glass":       "#3E3E3E",
    "black_stained_glass":      "#141414",
    # 金属・クォーツ系
    "iron_bars":                "#7F7F7F",
    "chiseled_quartz_block":    "#EAE6DC",
    "quartz_block":             "#EAE6DC",
    "smooth_quartz":            "#EAE6DC",
}


GLASS_RATIO_TO_BUILDING = [
    (0.70, "commercial"),
    (0.50, "office"),
    (0.30, "apartments"),
    (0.15, "residential"),
    (0.00, "industrial"),
]


def minecraft_block_to_colour(block_name: str) -> Optional[str]:
    """Minecraft ブロック名 → HEX 色文字列に変換"""
    if not block_name:
        return None
    block = block_name.replace("minecraft:", "").strip().lower()
    return MINECRAFT_BLOCK_TO_COLOR.get(block)


def glass_ratio_to_building_tag(glass_ratio: float) -> str:
    for threshold, tag in GLASS_RATIO_TO_BUILDING:
        if glass_ratio >= threshold:
            return tag
    return "industrial"


def get_overall_glass_ratio(detail: dict) -> Optional[float]:
    """building_detail から全体の glass_ratio を取得する"""
    overall = detail.get("glass_distribution", {}).get("overall", {})
    if overall.get("glass_ratio") is not None:
        return float(overall["glass_ratio"])
    floor_details = detail.get("floor_details", [])
    ratios = [
        f["glass_ratio"] for f in floor_details
        if f.get("glass_ratio") is not None
        and f.get("usage") not in ("parking", "none")
    ]
    if ratios:
        return sum(ratios) / len(ratios)
    return None


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def calibration_to_bbox(calib_points: dict) -> tuple:
    """calibration.points の min/max から bbox タプルを生成"""
    return (
        calib_points["minGeoLat"],
        calib_points["minGeoLon"],
        calib_points["maxGeoLat"],
        calib_points["maxGeoLon"],
    )


def calc_mc_coords(
    lat: float,
    lon: float,
    bbox: tuple,
    mc_width: int,
    mc_height: int,
) -> tuple:
    """緯度経度 → Minecraft X/Z 座標"""
    min_lat, min_lon, max_lat, max_lon = bbox
    if max_lon == min_lon or max_lat == min_lat:
        return 0, 0
    mc_x = int((lon - min_lon) / (max_lon - min_lon) * mc_width)
    mc_z = int((lat - min_lat) / (max_lat - min_lat) * mc_height)
    return mc_x, mc_z


def find_building_detail(
    buildings: list,
    center_lat: float,
    center_lon: float,
    max_dist_m: float = 50.0,
    bbox: tuple = None,
    mc_width: int = 2000,
    mc_height: int = 2000,
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

    if best and bbox:
        if best.get("mc_x") is None or best.get("mc_z") is None:
            mc_x, mc_z = calc_mc_coords(
                best["lat"], best["lon"], bbox, mc_width, mc_height
            )
            best = dict(best)
            best["mc_x"] = mc_x
            best["mc_z"] = mc_z

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

    floor_details = detail.get("floor_details", [])
    floor1 = next((f for f in floor_details if f.get("floor") == 1), None)

    # 壁色（優先順位: minecraft_wall_block > building_colour > exterior.material）
    wall_colour = None
    if floor1:
        wall_colour = minecraft_block_to_colour(floor1.get("minecraft_wall_block", ""))
    if not wall_colour:
        wall_colour = detail.get("building_colour")
    if not wall_colour:
        material = detail.get("exterior", {}).get("material", "")
        wall_colour = MATERIAL_TO_COLOR.get(material)
    if wall_colour:
        tags["building:colour"] = wall_colour

    # 屋根色
    roof_colour = detail.get("roof_colour")
    if roof_colour:
        tags["roof:colour"] = roof_colour

    # 窓色（minecraft_window_block → カスタムタグ）
    if floor1:
        window_colour = minecraft_block_to_colour(floor1.get("minecraft_window_block", ""))
        if window_colour:
            tags["building:colour:windows"] = window_colour

    # 窓パターン（explicit > glass_ratio 派生）
    windows = detail.get("windows", {})
    if windows.get("density"):
        tags["window:density"] = str(windows["density"])
    elif floor1 and floor1.get("glass_ratio") is not None:
        ratio = float(floor1["glass_ratio"])
        if ratio >= 0.70:
            density = 5
        elif ratio >= 0.50:
            density = 4
        elif ratio >= 0.30:
            density = 3
        elif ratio >= 0.15:
            density = 2
        else:
            density = 1
        tags["window:density"] = str(density)
    if windows.get("size"):
        tags["window:size"] = windows["size"]
    if windows.get("pattern"):
        tags["window:pattern"] = windows["pattern"]

    # フロアのガラス色 → roof:colour（未設定の場合のみ）
    if not tags.get("roof:colour"):
        glass_floor = next(
            (f for f in floor_details
             if f.get("glass_color") and f.get("glass_ratio", 0) > 0.3),
            None,
        )
        if glass_floor:
            tags["roof:colour"] = glass_floor["glass_color"]

    # building タグ（building_type > glass_ratio 判定 > floor_usage 判定）
    building_tag = None
    building_type = detail.get("building_type")
    if building_type:
        building_tag = building_type
    if not building_tag or building_tag in ("yes", None):
        glass_ratio = get_overall_glass_ratio(detail)
        if glass_ratio is not None:
            building_tag = glass_ratio_to_building_tag(glass_ratio)
    if not building_tag:
        floor_usage = detail.get("floor_usage", {})
        if floor_usage:
            usage_counts: dict = {}
            for usage in floor_usage.values():
                usage_counts[usage] = usage_counts.get(usage, 0) + 1
            dominant_usage = max(usage_counts, key=usage_counts.get)
            building_tag = USAGE_TO_BUILDING_TAG.get(dominant_usage, "yes")
    if building_tag:
        tags["building"] = building_tag

    # 駐車場ルーバーフロア数
    louver_floors = [f for f in floor_details if f.get("window_pattern") == "louver"]
    if louver_floors:
        tags["building:parking_floors"] = str(len(louver_floors))
