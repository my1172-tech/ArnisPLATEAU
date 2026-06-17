"""road_analyzer.py — OSMデータから道路ネットワークを解析するモジュール"""
import json


def analyze_roads(osm_json_path: str) -> list[dict]:
    with open(osm_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    roads = [e for e in data.get("elements", []) if e.get("type") == "way"
             and "highway" in e.get("tags", {})]
    return roads
