"""colorize.py — Street Viewカラーをワールドブロックにマッピングするコーディネーター (Pro)"""
from __future__ import annotations
import os
from block_color_map import BLOCK_COLOR_MAP


def nearest_block(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    best_block, best_dist = "white_concrete", float("inf")
    for block, (br, bg, bb) in BLOCK_COLOR_MAP.items():
        dist = (r - br) ** 2 + (g - bg) ** 2 + (b - bb) ** 2
        if dist < best_dist:
            best_dist = dist
            best_block = block
    return best_block


def colorize_world(world_folder: str, buildings_json: str, api_key: str):
    """Street View取得→色抽出→ブロック置換のパイプライン"""
    import json
    from streetview_fetcher import fetch_image
    from color_extractor import extract_dominant_color

    with open(buildings_json, "r", encoding="utf-8") as f:
        buildings = json.load(f)

    results = {}
    for b in buildings:
        lat = b.get("lat")
        lng = b.get("lng")
        if lat is None or lng is None:
            continue
        try:
            img_bytes = fetch_image(lat, lng, api_key)
            color = extract_dominant_color(img_bytes)
            block = nearest_block(color)
            results[b.get("id", f"{lat},{lng}")] = {"color": color, "block": block}
        except Exception as e:
            print(f"[WARN] {lat},{lng}: {e}")

    return results
