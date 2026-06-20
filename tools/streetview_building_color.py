"""
streetview_building_color.py
Google Street View Static API から建物壁面の色を k-means で抽出する
v2: ガラス率30%以上の建物はガラス色を roof:colour に使用
v3: 窓密度（5段階）と窓サイズ（FFTゼロクロッシング）を追加
"""

import io
import math
import urllib.request
import urllib.parse
from typing import Optional, Tuple, Dict

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


STREETVIEW_URL = "https://maps.googleapis.com/maps/api/streetview"

# 窓密度レベル定義（glass_ratio → level, building_type）
WINDOW_DENSITY_LEVELS = [
    (0.70, 5, "commercial"),   # ガラス全面
    (0.50, 4, "office"),       # 窓多い
    (0.30, 3, "apartments"),   # 窓普通
    (0.15, 2, "residential"),  # 窓少ない
    (0.00, 1, "industrial"),   # 窓極少
]


def glass_ratio_to_level(glass_ratio: float) -> tuple:
    """glass_ratio → (level: int 1-5, building_type: str)"""
    for threshold, level, building_type in WINDOW_DENSITY_LEVELS:
        if glass_ratio >= threshold:
            return level, building_type
    return 1, "industrial"


def detect_window_size(crop_img) -> str:
    """
    輝度プロファイルのゼロクロッシング間隔から窓の繰り返しサイズを検出する
    戻り値: "large" / "medium" / "small"
    """
    gray = crop_img.convert("L")
    w, h = gray.size

    mid_row = [gray.getpixel((x, h // 2)) for x in range(w)]
    avg = sum(mid_row) / len(mid_row)

    crossings = []
    prev_above = mid_row[0] > avg
    for i, v in enumerate(mid_row):
        above = v > avg
        if above != prev_above:
            crossings.append(i)
        prev_above = above

    if len(crossings) < 2:
        return "large"

    intervals = [crossings[i + 1] - crossings[i] for i in range(len(crossings) - 1)]
    avg_interval = sum(intervals) / len(intervals)
    relative = avg_interval / w

    if relative > 0.15:
        return "large"
    elif relative > 0.08:
        return "medium"
    else:
        return "small"


def _kmeans_3(pixels: list, iterations: int = 10) -> list:
    """
    シンプルな k-means 3クラスタ（純Python実装）
    pixels: [(R,G,B), ...] のリスト
    戻り値: [center1, center2, center3] RGB タプルのリスト（ピクセル数降順）
    """
    if len(pixels) < 3:
        return [(128, 128, 128)] * 3

    step = len(pixels) // 3
    centers = [pixels[0], pixels[step], pixels[step * 2]]

    for _ in range(iterations):
        clusters = [[], [], []]
        for p in pixels:
            dists = [
                sum((p[i] - c[i]) ** 2 for i in range(3))
                for c in centers
            ]
            clusters[dists.index(min(dists))].append(p)

        new_centers = []
        for i, cluster in enumerate(clusters):
            if cluster:
                r = sum(p[0] for p in cluster) // len(cluster)
                g = sum(p[1] for p in cluster) // len(cluster)
                b = sum(p[2] for p in cluster) // len(cluster)
                new_centers.append((r, g, b))
            else:
                new_centers.append(centers[i])
        centers = new_centers

    # クラスタサイズでソート（大きい順）
    cluster_sizes = []
    for c in centers:
        size = sum(
            1 for p in pixels
            if sum((p[i] - c[i]) ** 2 for i in range(3)) ==
            min(sum((p[i] - c2[i]) ** 2 for i in range(3)) for c2 in centers)
        )
        cluster_sizes.append((size, c))
    cluster_sizes.sort(reverse=True)
    return [c for _, c in cluster_sizes]


def _is_glass_color(rgb: Tuple[int, int, int]) -> bool:
    """青み・反射系の色かどうか判定（ガラス面の特徴）"""
    r, g, b = rgb
    return (b > r + 15 and b > g) or (r > 180 and g > 180 and b > 180)


def _color_distance(c1: Tuple, c2: Tuple) -> float:
    return math.sqrt(sum((c1[i] - c2[i]) ** 2 for i in range(3)))


def get_building_colors_from_streetview(
    lat: float,
    lon: float,
    api_key: str,
    heading: Optional[float] = None,
) -> Optional[Dict]:
    """
    Street View から建物の色情報を抽出する

    戻り値:
    {
        "building_colour": "#RRGGBB",   # 壁色1（building:colour）
        "roof_colour": "#RRGGBB",       # ガラス色 or 壁色2（roof:colour に流用）
        "building_type": "commercial",  # office / apartments / commercial
        "glass_ratio": 0.35,            # ガラス面割合（デバッグ用）
    }
    or None
    """
    if not PIL_AVAILABLE or not api_key:
        return None

    params = {
        "size": "640x640",
        "location": f"{lat},{lon}",
        "fov": 90,
        "pitch": 10,
        "key": api_key,
    }
    if heading is not None:
        params["heading"] = int(heading)

    url = f"{STREETVIEW_URL}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ArnisPLATEAU/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            img = Image.open(io.BytesIO(resp.read())).convert("RGB")
    except Exception:
        return None

    w, h = img.size

    # 壁面部分を切り出し（上20%〜80%・左右中央40%）
    crop = img.crop((int(w * 0.30), int(h * 0.20),
                     int(w * 0.70), int(h * 0.80)))
    pixels = list(crop.getdata())

    # 画像なし判定（グレー単色 = Street View 未収録エリア）
    r_avg = sum(p[0] for p in pixels) // len(pixels)
    g_avg = sum(p[1] for p in pixels) // len(pixels)
    b_avg = sum(p[2] for p in pixels) // len(pixels)
    if max(abs(r_avg - g_avg), abs(g_avg - b_avg), abs(r_avg - b_avg)) < 10:
        return None

    # k-means 3クラスタ（1/4 間引きで高速化）
    sampled = pixels[::4]
    clusters = _kmeans_3(sampled)
    c1, c2, c3 = clusters[0], clusters[1], clusters[2]

    # ガラス面割合を計算
    glass_count = sum(1 for p in sampled if _is_glass_color(p))
    glass_ratio = glass_count / len(sampled) if sampled else 0

    def to_hex(rgb):
        return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"

    # ガラス色クラスタを特定（青み系・高輝度のクラスタ）
    glass_cluster = next(
        (c for c in clusters if _is_glass_color(c)), None
    )

    # 窓密度レベルと building タグ（密度ベース）
    window_level, building_type_from_density = glass_ratio_to_level(glass_ratio)

    # 窓サイズ検出
    window_size = detect_window_size(crop)

    # building タグ判定 + roof:colour 決定
    color_diff = _color_distance(c1, c2)

    if glass_ratio > 0.30:
        # ガラスが30%以上 → 密度ベースの building タグ、ガラス色を roof:colour に使用
        building_type = building_type_from_density
        roof_color = to_hex(glass_cluster) if glass_cluster else to_hex(c2)
    elif color_diff > 60:
        # 2色が明確に違う → apartments
        building_type = "apartments"
        roof_color = to_hex(c2)
    else:
        # 単色系 → 密度ベースの building タグ
        building_type = building_type_from_density
        roof_color = to_hex(c2)

    return {
        "building_colour": to_hex(c1),
        "roof_colour": roof_color,
        "building_type": building_type,
        "glass_ratio": round(glass_ratio, 2),
        "window_level": window_level,
        "window_size": window_size,
    }


def get_heading_to_building(
    road_lat: float, road_lon: float,
    building_lat: float, building_lon: float
) -> float:
    """道路から建物方向の heading（度）を計算"""
    dlat = building_lat - road_lat
    dlon = building_lon - road_lon
    angle = math.degrees(math.atan2(dlon, dlat))
    return (angle + 360) % 360
