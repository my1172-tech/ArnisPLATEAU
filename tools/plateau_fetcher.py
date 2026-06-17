"""
PLATEAU建物データ取得モジュール（高さ・外形のみ）。
GraphQL → tileset.json → b3dm の順で取得し、measuredHeight と footprint を抽出する。
本家arnisを改造せず、後処理として独立動作する。
屋根形状（RoofSurface）は対象外。
"""
import os
import json
import struct
import requests
from pathlib import Path
from typing import List, Dict, Optional

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "ArnisPLATEAU" / "plateau_cache"
GRAPHQL_URL = "https://api.plateauview.mlit.go.jp/datacatalog/graphql"

PREF_CODE_BY_LATLON = [
    (35.6895, 139.6917, "13", "東京都"),
    (34.6937, 135.5023, "27", "大阪府"),
    (35.0116, 135.7681, "26", "京都府"),
    (35.1815, 136.9066, "23", "愛知県"),
    (33.5904, 130.4017, "40", "福岡県"),
    (33.2494, 130.2988, "41", "佐賀県"),
]


def _nearest_pref_code(lat: float, lon: float) -> str:
    best_code = "13"
    best_dist = float("inf")
    for p_lat, p_lon, code, _name in PREF_CODE_BY_LATLON:
        dist = (lat - p_lat) ** 2 + (lon - p_lon) ** 2
        if dist < best_dist:
            best_dist = dist
            best_code = code
    return best_code


def fetch_plateau_buildings(bbox: dict) -> List[Dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = f"{bbox['min_lat']:.4f}_{bbox['min_lon']:.4f}_{bbox['max_lat']:.4f}_{bbox['max_lon']:.4f}"
    cache_path = CACHE_DIR / f"{cache_key}.json"

    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    try:
        buildings = _fetch_pipeline(bbox)
    except Exception as e:
        print(f"[plateau_fetcher] 取得エラー（PLATEAU補正なしで続行）: {e}")
        return []

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(buildings, f, ensure_ascii=False)

    return buildings


def _fetch_pipeline(bbox: dict) -> List[Dict]:
    center_lat = (bbox["min_lat"] + bbox["max_lat"]) / 2
    center_lon = (bbox["min_lon"] + bbox["max_lon"]) / 2
    pref_code = _nearest_pref_code(center_lat, center_lon)

    dataset_items = _query_graphql_datasets(pref_code)
    if not dataset_items:
        print(f"[plateau_fetcher] 都道府県コード{pref_code}のデータセットが見つかりません")
        return []

    print(f"[plateau_fetcher] {len(dataset_items)}件のLOD1データセットをbbox検索します")
    buildings = []
    for item_url in dataset_items:  # 全データセットをbbox filteredで走査
        tileset = _fetch_tileset(item_url)
        if not tileset:
            continue
        b3dm_urls = _filter_tiles_by_bbox(tileset, item_url, bbox)
        if not b3dm_urls:
            continue  # このtilesetはbboxに重ならない
        print(f"[plateau_fetcher] bbox内タイル: {len(b3dm_urls)}件")
        for b3dm_url in b3dm_urls[:20]:  # タイル数の上限（処理時間対策）
            buildings.extend(_parse_b3dm(b3dm_url, bbox))
        if buildings:
            break  # データが取れたら早期終了

    return buildings


def _query_graphql_datasets(pref_code: str) -> List[str]:
    # areaCodes は AreaCode スカラー型のリストを要求するため変数型を [AreaCode!] に合わせる
    query = """
    query($areaCodes: [AreaCode!], $includeTypes: [String!]) {
      datasets(input: { areaCodes: $areaCodes, includeTypes: $includeTypes }) {
        ... on PlateauDataset {
          id
          items {
            ... on PlateauDatasetItem {
              url
              format
              lod
            }
          }
        }
      }
    }
    """
    variables = {"areaCodes": [pref_code], "includeTypes": ["bldg"]}
    resp = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, timeout=20)
    if resp.status_code != 200:
        print(f"[plateau_fetcher] GraphQLエラー: status={resp.status_code}, body={resp.text[:200]}")
        return []

    data = resp.json()
    if "errors" in data:
        print(f"[plateau_fetcher] GraphQLエラー: {data['errors']}")
        return []
    datasets = data.get("data", {}).get("datasets", [])

    tileset_urls = []
    for ds in datasets:
        for item in ds.get("items", []):
            if item.get("format") == "CESIUM3DTILES" and item.get("lod") == 1:
                tileset_urls.append(item["url"])

    return tileset_urls


def _fetch_tileset(tileset_url: str) -> Optional[dict]:
    try:
        resp = requests.get(tileset_url, timeout=20)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as e:
        print(f"[plateau_fetcher] tileset取得エラー: {e}")
        return None


def _filter_tiles_by_bbox(tileset: dict, base_url: str, bbox: dict) -> List[str]:
    import math
    result = []

    def walk(node, base):
        bv = node.get("boundingVolume", {}).get("region")
        if bv:
            west, south, east, north = [math.degrees(v) for v in bv[:4]]
            overlap = not (east < bbox["min_lon"] or west > bbox["max_lon"] or
                           north < bbox["min_lat"] or south > bbox["max_lat"])
            if not overlap:
                return

        content = node.get("content", {})
        uri = content.get("uri") or content.get("url")
        if uri and uri.endswith(".b3dm"):
            full_url = uri if uri.startswith("http") else _join_url(base, uri)
            result.append(full_url)

        for child in node.get("children", []):
            walk(child, base)

    root = tileset.get("root", {})
    walk(root, base_url)
    return result


def _join_url(base_url: str, relative: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base_url, relative)


def _parse_b3dm(b3dm_url: str, bbox: dict) -> List[Dict]:
    try:
        resp = requests.get(b3dm_url, timeout=30)
        if resp.status_code != 200:
            return []
        data = resp.content
    except Exception as e:
        print(f"[plateau_fetcher] b3dm取得エラー: {e}")
        return []

    if data[:4] != b"b3dm":
        return []

    header = struct.unpack_from("<4sIIIIII", data, 0)
    ft_json_len = header[3]
    ft_bin_len = header[4]
    bt_json_len = header[5]

    offset = 28
    feature_table_json = json.loads(data[offset:offset + ft_json_len].decode("utf-8")) if ft_json_len else {}
    offset += ft_json_len + ft_bin_len

    batch_table_json = json.loads(data[offset:offset + bt_json_len].decode("utf-8")) if bt_json_len else {}

    heights = batch_table_json.get("bldg:measuredHeight") or batch_table_json.get("measuredHeight") or []
    rtc_center = feature_table_json.get("RTC_CENTER")

    buildings = []
    for h in heights:
        if h is None:
            continue
        try:
            height_val = float(h)
        except (TypeError, ValueError):
            continue

        if rtc_center and len(rtc_center) >= 2:
            approx_lat, approx_lon = _ecef_approx_to_latlon(rtc_center)
        else:
            approx_lat = (bbox["min_lat"] + bbox["max_lat"]) / 2
            approx_lon = (bbox["min_lon"] + bbox["max_lon"]) / 2

        buildings.append({
            "measured_height": height_val,
            "footprint": [(approx_lat, approx_lon)],
        })

    return buildings


def _ecef_approx_to_latlon(ecef: List[float]) -> tuple:
    import math
    x, y, z = ecef[0], ecef[1], ecef[2]
    lon = math.degrees(math.atan2(y, x))
    r = math.sqrt(x * x + y * y)
    lat = math.degrees(math.atan2(z, r))
    return lat, lon


def find_building_for_footprint(buildings: List[Dict], target_lat: float, target_lon: float, max_dist_deg: float = 0.002) -> Optional[Dict]:
    best = None
    best_dist = float("inf")
    for b in buildings:
        fp = b.get("footprint", [])
        if not fp:
            continue
        avg_lat = sum(p[0] for p in fp) / len(fp)
        avg_lon = sum(p[1] for p in fp) / len(fp)
        dist = (avg_lat - target_lat) ** 2 + (avg_lon - target_lon) ** 2
        if dist < best_dist:
            best_dist = dist
            best = b
    if best and best_dist > max_dist_deg ** 2:
        return None
    return best