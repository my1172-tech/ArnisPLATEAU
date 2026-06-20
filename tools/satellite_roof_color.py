"""
satellite_roof_color.py
国土地理院シームレス衛星画像タイルから建物フットプリント内の
平均RGB色を取得して roof:colour タグに書き込む
"""
import math
import io
import urllib.request
from typing import Optional, Tuple, List

try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


GSI_TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
ZOOM = 18  # 衛星画像ズームレベル（1タイル≒約1m/px）
TILE_SIZE = 256

# タイルキャッシュ（同セッション内で再利用）
_tile_cache: dict = {}


def _lon_lat_to_tile(lon: float, lat: float, zoom: int) -> Tuple[int, int]:
    """経緯度 → タイル座標（整数）"""
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _lon_lat_to_pixel(lon: float, lat: float, zoom: int) -> Tuple[float, float]:
    """経緯度 → グローバルピクセル座標（小数）"""
    n = 2 ** zoom
    px = (lon + 180.0) / 360.0 * n * TILE_SIZE
    lat_rad = math.radians(lat)
    py = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n * TILE_SIZE
    return px, py


def _fetch_tile(tx: int, ty: int, zoom: int) -> Optional["Image.Image"]:
    """GSI衛星画像タイルを取得（同セッション内キャッシュあり）"""
    key = (tx, ty, zoom)
    if key in _tile_cache:
        return _tile_cache[key]
    url = GSI_TILE_URL.format(z=zoom, x=tx, y=ty)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ArnisPLATEAU/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            img = Image.open(io.BytesIO(resp.read())).convert("RGB")
        _tile_cache[key] = img
        return img
    except Exception:
        return None


def get_roof_color_from_polygon(
    nodes: List[Tuple[float, float]]  # [(lon, lat), ...]
) -> Optional[str]:
    """
    建物フットプリントのノード座標リスト（lon, lat 順）から
    衛星画像の平均RGB色を返す。
    戻り値: "#RRGGBB" 形式の文字列 or None
    """
    if not PIL_AVAILABLE:
        return None
    if len(nodes) < 3:
        return None

    # グローバルピクセル座標を計算
    pixels = [_lon_lat_to_pixel(lon, lat, ZOOM) for lon, lat in nodes]

    px_min = min(p[0] for p in pixels)
    px_max = max(p[0] for p in pixels)
    py_min = min(p[1] for p in pixels)
    py_max = max(p[1] for p in pixels)

    # 小さすぎる建物はスキップ（3px未満）
    if (px_max - px_min) < 3 or (py_max - py_min) < 3:
        return None

    # 必要なタイル範囲を特定
    tx_min = int(px_min // TILE_SIZE)
    tx_max = int(px_max // TILE_SIZE)
    ty_min = int(py_min // TILE_SIZE)
    ty_max = int(py_max // TILE_SIZE)

    # タイルを結合してキャンバスを作成
    canvas_w = (tx_max - tx_min + 1) * TILE_SIZE
    canvas_h = (ty_max - ty_min + 1) * TILE_SIZE
    canvas = Image.new("RGB", (canvas_w, canvas_h))

    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            tile = _fetch_tile(tx, ty, ZOOM)
            if tile is None:
                return None
            ox = (tx - tx_min) * TILE_SIZE
            oy = (ty - ty_min) * TILE_SIZE
            canvas.paste(tile, (ox, oy))

    # キャンバス内のローカル座標に変換
    offset_x = tx_min * TILE_SIZE
    offset_y = ty_min * TILE_SIZE
    local_pixels = [(px - offset_x, py - offset_y) for px, py in pixels]

    # ポリゴンマスクを生成
    mask = Image.new("L", (canvas_w, canvas_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(int(x), int(y)) for x, y in local_pixels], fill=255)

    # マスク内のRGB平均を計算
    canvas_arr = list(canvas.getdata())
    mask_arr = list(mask.getdata())

    r_sum = g_sum = b_sum = count = 0
    for i, m in enumerate(mask_arr):
        if m > 128:
            r, g, b = canvas_arr[i]
            r_sum += r
            g_sum += g
            b_sum += b
            count += 1

    if count == 0:
        return None

    r_avg = r_sum // count
    g_avg = g_sum // count
    b_avg = b_sum // count

    return f"#{r_avg:02X}{g_avg:02X}{b_avg:02X}"
