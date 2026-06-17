"""osm_to_json.py — OSM OverpassクエリをJSONに変換するモジュール"""
import json
import urllib.request


OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def fetch_osm(bbox: tuple[float, float, float, float]) -> dict:
    south, west, north, east = bbox
    query = f"""
[out:json][timeout:60];
(
  way["building"]({south},{west},{north},{east});
  way["highway"]({south},{west},{north},{east});
);
out body; >; out skel qt;
"""
    data = query.encode("utf-8")
    req = urllib.request.Request(OVERPASS_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())
