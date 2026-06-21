"""
brand_color_matcher.py
OSM の shop/amenity/name タグからブランドカラーDBを照合する
"""

import json
import os
from typing import Optional, Dict

OSM_TAG_TO_CATEGORY = {
    "convenience":      "convenience",
    "supermarket":      "supermarket",
    "pharmacy":         "pharmacy",
    "chemist":          "pharmacy",
    "drug_store":       "pharmacy",
    "clothes":          "retail",
    "department_store": "supermarket",
    "fast_food":        "fast_food",
    "restaurant":       "restaurant",
    "bank":             "bank",
    "fuel":             "gas_station",
    "hospital":         "hospital",
    "clinic":           "hospital",
    "school":           "school",
    "university":       "school",
    "hotel":            "hotel",
    "cafe":             "restaurant",
}

BLOCK_TO_HEX = {
    "white_concrete":       "#FFFFFF",
    "light_gray_concrete":  "#9D9D97",
    "gray_concrete":        "#474F52",
    "black_concrete":       "#1D1D21",
    "brown_concrete":       "#603C20",
    "red_concrete":         "#8E2020",
    "orange_concrete":      "#E06101",
    "yellow_concrete":      "#F0AF15",
    "lime_concrete":        "#5EA918",
    "green_concrete":       "#364B18",
    "cyan_concrete":        "#158991",
    "light_blue_concrete":  "#3AB3DA",
    "blue_concrete":        "#2C2F8F",
    "purple_concrete":      "#641F9C",
    "magenta_concrete":     "#BE49C9",
    "pink_concrete":        "#D5658F",
}


def load_brand_colors(tools_dir: str, custom_path: str = None) -> dict:
    """
    brand_colors_default.json を読み込み、
    custom_path が指定されていれば上書きマージして返す
    """
    default_path = os.path.join(tools_dir, "brand_colors_default.json")
    db = {}

    if os.path.exists(default_path):
        try:
            with open(default_path, encoding="utf-8") as f:
                db = json.load(f)
        except Exception:
            pass

    if custom_path and os.path.exists(custom_path):
        try:
            with open(custom_path, encoding="utf-8") as f:
                custom = json.load(f)
            for category, brands in custom.items():
                if category not in db:
                    db[category] = {}
                db[category].update(brands)
        except Exception:
            pass

    return db


def match_brand_color(
    tags: dict,
    brand_db: dict,
) -> Optional[Dict[str, str]]:
    """
    OSM タグ dict からブランドカラーを照合する
    戻り値: {"building:colour": "#RRGGBB", "roof:colour": "#RRGGBB"} or None
    """
    if not brand_db:
        return None

    category = None
    for tag_key in ("shop", "amenity", "building"):
        tag_val = tags.get(tag_key, "")
        if tag_val in OSM_TAG_TO_CATEGORY:
            category = OSM_TAG_TO_CATEGORY[tag_val]
            break

    if not category or category not in brand_db:
        return None

    cat_db = brand_db[category]

    brand_entry = None
    for name_key in ("name", "brand", "operator", "name:ja"):
        name_val = tags.get(name_key, "")
        if name_val and name_val in cat_db:
            brand_entry = cat_db[name_val]
            break

    if brand_entry is None:
        brand_entry = cat_db.get("_generic")

    if not brand_entry:
        return None

    result = {}
    wall_block = brand_entry.get("wall", "")
    roof_block = brand_entry.get("roof", "")

    if wall_block in BLOCK_TO_HEX:
        result["building:colour"] = BLOCK_TO_HEX[wall_block]
    if roof_block in BLOCK_TO_HEX:
        result["roof:colour"] = BLOCK_TO_HEX[roof_block]

    return result if result else None
