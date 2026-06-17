"""
PLATEAU建物データ取得モジュール（高さ・外形のみ）。
GraphQL → tileset.json → b3dm の順で取得し、measuredHeight と footprint を抽出する。
本家arnisを改造せず、後処理として独立動作する。
屋根形状（RoofSurface）は対象外。

[修正] _parse_b3dm: BatchTable バイナリフィールド _x/_y/_xmin〜_ymax から
       建物個別の緯度経度・footprint bboxを取得（旧RTC_CENTER共用座標バグを修正）
[修正] find_building_for_footprint: bbox内包判定（案B）優先 + 重心近傍フォールバック（案A）
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
            data = json.load(f)
        # 旧形式キャッシュ（bbox なし）を検出して無効化
        if data and "bbox" not in data[0]:
            print(f"[plateau_fetcher] 旧形式キャッシュを破棄して再取得: {cache_path.name}")
            cache_path.unlink()
        else:
            return data

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
    for item_url in dataset_items:
        tileset = _fetch_tileset(item_url)
        if not tileset:
            continue
        b3dm_urls = _filter_tiles_by_bbox(tileset, item_url, bbox)
        if not b3dm_urls:
            continue
        print(f"[plateau_fetcher] bbox内タイル: {len(b3dm_urls)}件")
        for b3dm_url in b3dm_urls[:20]:
            buildings.extend(_parse_b3dm(b3dm_url, bbox))
        if buildings:
            break

    unique_coords = len(set((b["footprint"][0][0], b["footprint"][0][1]) for b in buildings if b.get("footprint")))
    print(f"[plateau_fetcher] 取得建物: {len(buildings)}棟 / ユニーク座標: {unique_coords}種類")
    return buildings


def _query_graphql_datasets(pref_code: str) -> List[str]:
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


def _read_bt_doubles(bt_bin: bytes, field_meta, count: int) -> Optional[List[float]]:
    """BatchTable バイナリフィールドを DOUBLE 配列として読み出す"""
    if not isinstance(field_meta, dict) or "byteOffset" not in field_meta:
        return None
    if not bt_bin:
        return None
    off = field_meta["byteOffset"]
    needed = off + count * 8
    if needed > len(bt_bin):
        return None
    return list(struct.unpack_from(f"<{count}d", bt_bin, off))


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

    _, _, _, ft_json_len, ft_bin_len, bt_json_len, bt_bin_len = struct.unpack_from("<4sIIIIII", data, 0)

    offset = 28
    feature_table_json = json.loads(data[offset:offset + ft_json_len].decode("utf-8")) if ft_json_len else {}
    offset += ft_json_len + ft_bin_len
    batch_table_json = json.loads(data[offset:offset + bt_json_len].decode("utf-8")) if bt_json_len else {}
    offset += bt_json_len
    bt_bin = data[offset:offset + bt_bin_len] if bt_bin_len else b""

    batch_length = feature_table_json.get("BATCH_LENGTH", 0)
    heights = batch_table_json.get("bldg:measuredHeight") or batch_table_json.get("measuredHeight") or []

    # BatchTable バイナリ: _x=経度, _y=緯度（WGS84度数）、footprint AABB
    lons = _read_bt_doubles(bt_bin, batch_table_json.get("_x"), batch_length)
    lats = _read_bt_doubles(bt_bin, batch_table_json.get("_y"), batch_length)
    xmins = _read_bt_doubles(bt_bin, batch_table_json.get("_xmin"), batch_length)
    xmaxs = _read_bt_doubles(bt_bin, batch_table_json.get("_xmax"), batch_length)
    ymins = _read_bt_doubles(bt_bin, batch_table_json.get("_ymin"), batch_length)
    ymaxs = _read_bt_doubles(bt_bin, batch_table_json.get("_ymax"), batch_length)

    # RTC_CENTER は使用しない（全建物が同一座標になるバグの原因）
    rtc_center = feature_table_json.get("RTC_CENTER")

    buildings = []
    for i, h in enumerate(heights):
        if h is None or i >= batch_length:
            continue
        try:
            height_val = float(h)
        except (TypeError, ValueError):
            continue

        # 建物個別の緯度経度（_x/_y バイナリ優先）
        if lats and lons and i < len(lats) and i < len(lons):
            lat, lon = lats[i], lons[i]
        elif rtc_center and len(rtc_center) >= 3:
            lat, lon = _ecef_approx_to_latlon(rtc_center)
        else:
            lat = (bbox["min_lat"] + bbox["max_lat"]) / 2
            lon = (bbox["min_lon"] + bbox["max_lon"]) / 2

        # Footprint AABB（_xmin/_xmax/_ymin/_ymax バイナリ）
        fp_bbox = None
        if (xmins and xmaxs and ymins and ymaxs and
                i < len(xmins) and i < len(xmaxs) and i < len(ymins) and i < len(ymaxs)):
            fp_bbox = {
                "xmin": xmins[i], "xmax": xmaxs[i],
                "ymin": ymins[i], "ymax": ymaxs[i],
            }

        buildings.append({
            "measured_height": height_val,
            "footprint": [(lat, lon)],
            "bbox": fp_bbox,
        })

    return buildings


def _ecef_approx_to_latlon(ecef: List[float]) -> tuple:
    import math
    x, y, z = ecef[0], ecef[1], ecef[2]
    lon = math.degrees(math.atan2(y, x))
    r = math.sqrt(x * x + y * y)
    lat = math.degrees(math.atan2(z, r))
    return lat, lon


def find_building_for_footprint(
    buildings: List[Dict],
    target_lat: float,
    target_lon: float,
    fallback_dist_deg: float = 0.001,
) -> Optional[Dict]:
    """
    案B優先: OSM建物重心がPLATEAU建物のfootprint bbox内に含まれるか判定。
    bbox内に含まれる建物がなければ、案Aフォールバックとして重心近傍（fallback_dist_deg以内）の
    最近傍建物を返す。
    """
    # 案B: bbox containment（OSM重心がPLATEAU footprint bbox内）
    for b in buildings:
        bb = b.get("bbox")
        if not bb:
            continue
        if bb["xmin"] <= target_lon <= bb["xmax"] and bb["ymin"] <= target_lat <= bb["ymax"]:
            return b

    # 案A: 重心近傍フォールバック
    best = None
    best_dist = float("inf")
    for b in buildings:
        fp = b.get("footprint", [])
        if not fp:
            continue
        clat = sum(p[0] for p in fp) / len(fp)
        clon = sum(p[1] for p in fp) / len(fp)
        dist = (clat - target_lat) ** 2 + (clon - target_lon) ** 2
        if dist < best_dist:
            best_dist = dist
            best = b
    if best and best_dist <= fallback_dist_deg ** 2:
        return best
    return None
