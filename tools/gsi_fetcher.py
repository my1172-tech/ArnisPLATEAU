"""
国土地理院（GSI）タイルデータ取得モジュール
建物データ（optimal_bvmap）と標高データ（dem_png）をタイル座標で取得する。
本家arnisを改造せず、後処理として独立動作する。
"""
import os
import math
import requests
from pathlib import Path
from typing import List, Tuple

ZOOM = 16
GSI_BUILDING_URL = "https://cyberjapandata.gsi.go.jp/xyz/optimal_bvmap-v1/{z}/{x}/{y}.pbf"
GSI_DEM_URL = "https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png"

# タイルキャッシュ先（Windows）
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "ArnisPLATEAU" / "gsi_tiles"

# 建物種類コード → 階数換算
BUILDING_TYPE_FLOORS = {
    3101: 1,   # 普通建物（デフォルト高さ）
    3102: 1,   # 堅ろう建物（デフォルト高さ）
    3103: 5,   # 高層建物（5階建て相当）
}


def lat_lng_to_tile(lat: float, lng: float, zoom: int = ZOOM) -> Tuple[int, int]:
    """緯度経度をタイル座標(x, y)に変換する"""
    n = 2 ** zoom
    x = int((lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def get_tiles_for_bbox(bbox: dict, zoom: int = ZOOM) -> List[Tuple[int, int]]:
    """
    bbox = {"min_lat": ..., "max_lat": ..., "min_lon": ..., "max_lon": ...}
    の範囲をカバーするタイル座標一覧を返す
    """
    x1, y1 = lat_lng_to_tile(bbox["max_lat"], bbox["min_lon"], zoom)
    x2, y2 = lat_lng_to_tile(bbox["min_lat"], bbox["max_lon"], zoom)
    x_min, x_max = min(x1, x2), max(x1, x2)
    y_min, y_max = min(y1, y2), max(y1, y2)

    tiles = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            tiles.append((x, y))
    return tiles


def fetch_tile(x: int, y: int, zoom: int, url_template: str, ext: str) -> bytes:
    """タイルをダウンロード（キャッシュ優先）"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{zoom}_{x}_{y}.{ext}"

    if cache_path.exists():
        return cache_path.read_bytes()

    url = url_template.format(z=zoom, x=x, y=y)
    resp = requests.get(url, timeout=15)

    if resp.status_code == 404:
        # タイルが存在しない（海・国外等）
        return b""

    resp.raise_for_status()
    cache_path.write_bytes(resp.content)
    return resp.content


def fetch_gsi_buildings(bbox: dict) -> List[dict]:
    """
    bbox範囲のGSI建物データを取得し、OSM互換の建物リストを返す。
    各建物: {"id": int, "polygon": [(lat, lon), ...], "type_code": int, "floors": int}
    """
    import mapbox_vector_tile

    tiles = get_tiles_for_bbox(bbox)
    buildings = []
    next_id = 4_000_000_000  # IDが既存OSMデータと衝突しないよう40億以降を使用

    for x, y in tiles:
        try:
            pbf_data = fetch_tile(x, y, ZOOM, GSI_BUILDING_URL, "pbf")
            if not pbf_data:
                continue

            decoded = mapbox_vector_tile.decode(pbf_data)
            layer = decoded.get("BldA")
            if not layer:
                continue

            extent = layer.get("extent", 4096)

            for feature in layer["features"]:
                geom = feature.get("geometry")
                if not geom or geom.get("type") != "Polygon":
                    continue

                type_code = feature.get("properties", {}).get("ftCode", 3101)
                floors = BUILDING_TYPE_FLOORS.get(type_code, 1)

                # タイル内相対座標 → 緯度経度に変換
                ring = geom["coordinates"][0]
                latlon_ring = []
                for px, py in ring:
                    lon = (x + px / extent) / (2 ** ZOOM) * 360.0 - 180.0
                    n = math.pi - 2.0 * math.pi * (y + py / extent) / (2 ** ZOOM)
                    lat = math.degrees(math.atan(math.sinh(n)))
                    latlon_ring.append((lat, lon))

                # bbox範囲外を除外
                if not _polygon_in_bbox(latlon_ring, bbox):
                    continue

                buildings.append({
                    "id": next_id,
                    "polygon": latlon_ring,
                    "type_code": type_code,
                    "floors": floors,
                })
                next_id += 1

        except Exception as e:
            print(f"[gsi_fetcher] タイル({x},{y})取得エラー: {e}")
            continue

    return buildings


def _polygon_in_bbox(ring: List[Tuple[float, float]], bbox: dict) -> bool:
    """ポリゴンの中心点がbbox内にあるか簡易判定"""
    if not ring:
        return False
    avg_lat = sum(p[0] for p in ring) / len(ring)
    avg_lon = sum(p[1] for p in ring) / len(ring)
    return (bbox["min_lat"] <= avg_lat <= bbox["max_lat"] and
            bbox["min_lon"] <= avg_lon <= bbox["max_lon"])


def fetch_gsi_elevation(bbox: dict) -> dict:
    """
    bbox範囲の標高データを取得する。
    戻り値: {(x_tile, y_tile): PIL.Image} のタイル画像辞書
    """
    from PIL import Image
    import io

    tiles = get_tiles_for_bbox(bbox)
    elevation_tiles = {}

    for x, y in tiles:
        try:
            png_data = fetch_tile(x, y, ZOOM, GSI_DEM_URL, "png")
            if not png_data:
                continue
            img = Image.open(io.BytesIO(png_data)).convert("RGBA")
            elevation_tiles[(x, y)] = img
        except Exception as e:
            print(f"[gsi_fetcher] 標高タイル({x},{y})取得エラー: {e}")
            continue

    return elevation_tiles


def decode_elevation_pixel(r: int, g: int, b: int, a: int) -> float:
    """
    GSI標高PNGのRGBピクセルから高さ（メートル）を取り出す。
    alpha=0（透明）の場合は欠損データ。
    """
    if a == 0:
        return None
    raw = r * 65536 + g * 256 + b
    signed = raw - 16777216 if raw >= 8388608 else raw
    return signed * 0.01
