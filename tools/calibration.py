"""
calibration.py — 位置ズレキャリブレーション

基準点の「期待MC座標（arnis式）」と「実際の最近傍補正ポリゴン重心」を比較し、
ズレ量とパターンをログ出力する。
GUI（arnis_colorize_gui.py）とCLI（test_e2e.py）から共通して呼び出せる。
"""
import math
from typing import Callable, Dict, List, Optional


def run_calibration(
    corrections: List[Dict],
    metadata: Dict,
    calibration_points: List[Dict],
    log_fn: Callable[[str], None] = print,
) -> None:
    """
    基準点のMC座標期待値と最近傍補正ポリゴン重心を比較してズレを分析する。

    calibration_points: [{"name": str, "lat": float, "lon": float}, ...]
    log_fn: 出力関数（GUIでは self._log、CLIでは print）
    """
    log_fn("")
    log_fn("=" * 55)
    log_fn("  検証用基準点 キャリブレーション")
    log_fn("=" * 55)

    min_lat = metadata["minGeoLat"]; max_lat = metadata["maxGeoLat"]
    min_lon = metadata["minGeoLon"]; max_lon = metadata["maxGeoLon"]
    min_x   = metadata["minMcX"];   max_x   = metadata["maxMcX"]
    min_z   = metadata["minMcZ"];   max_z   = metadata["maxMcZ"]

    def latlon_to_mc(lat: float, lon: float):
        """arnis 本家と同式 (truncation, Z反転)"""
        lr  = (lat - min_lat) / (max_lat - min_lat)
        or_ = (lon - min_lon) / (max_lon - min_lon)
        return (int(min_x + or_ * (max_x - min_x)),
                int(min_z + (1.0 - lr) * (max_z - min_z)))

    # 補正ポリゴン重心リスト（MC座標）
    corr_centroids = []
    for c in corrections:
        poly = c.get("polygon_mc_xz", [])
        if poly:
            corr_centroids.append((
                sum(p[0] for p in poly) / len(poly),
                sum(p[1] for p in poly) / len(poly),
            ))

    diffs: List[tuple] = []
    valid_pts: List[Dict] = []

    for cp in calibration_points:
        name = cp.get("name", "基準点")
        lat  = cp.get("lat")
        lon  = cp.get("lon")
        if lat is None or lon is None:
            continue

        # bbox範囲外チェック
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            log_fn(f"[{name}]  bbox範囲外のためスキップ ({lat:.6f}, {lon:.6f})")
            continue

        ex, ez = latlon_to_mc(lat, lon)

        if corr_centroids:
            best = min(corr_centroids,
                       key=lambda cc: (cc[0] - ex) ** 2 + (cc[1] - ez) ** 2)
            ax = int(best[0]); az = int(best[1])
            nearest_d = math.sqrt((best[0] - ex) ** 2 + (best[1] - ez) ** 2)
        else:
            ax, az, nearest_d = ex, ez, 0.0

        dx = ax - ex; dz = az - ez
        dist_m = math.sqrt(dx ** 2 + dz ** 2)
        diffs.append((dx, dz))
        valid_pts.append(cp)

        log_fn(f"[{name}]")
        log_fn(f"  座標      : ({lat:.6f}, {lon:.6f})")
        log_fn(f"  期待MC    : x={ex:5d}, z={ez:5d}")
        log_fn(f"  最近補正  : x={ax:5d}, z={az:5d}  (距離:{nearest_d:.1f}blk)")
        log_fn(f"  差分      : dx={dx:+d}, dz={dz:+d}  ({dist_m:.1f}m相当)")
        if abs(dx) > 5 or abs(dz) > 5:
            log_fn(f"  [WARNING] 5ブロック超のズレ — 座標変換の精度確認が必要です")
        else:
            log_fn(f"  [OK] 許容範囲内")

    # ── パターン分析（2点以上）─────────────────────────────────────────────────
    if len(diffs) < 2:
        log_fn("=" * 55)
        return

    log_fn("")
    log_fn("--- ズレパターン分析 ---")
    dx_v = [d[0] for d in diffs]; dz_v = [d[1] for d in diffs]
    dx_m = sum(dx_v) / len(dx_v); dz_m = sum(dz_v) / len(dz_v)
    dx_s = max(abs(d - dx_m) for d in dx_v)
    dz_s = max(abs(d - dz_m) for d in dz_v)

    if dx_s < 5 and dz_s < 5:
        log_fn(f"  固定オフセット (dx≈{dx_m:+.0f}, dz≈{dz_m:+.0f}): "
               "全基準点でほぼ同じズレ → 座標変換の系統誤差の可能性")

    if abs(dx_m) > 10 and abs(dz_m) < abs(dx_m) * 0.3:
        log_fn(f"  X軸ズレ支配的 (dx≈{dx_m:+.0f}): 経度変換の誤差")
    elif abs(dz_m) > 10 and abs(dx_m) < abs(dz_m) * 0.3:
        log_fn(f"  Z軸ズレ支配的 (dz≈{dz_m:+.0f}): 緯度/Z軸変換の誤差")

    # スケール誤差（3点以上）
    if len(diffs) >= 3:
        cx_m = (max_lon + min_lon) / 2
        cz_m = (max_lat + min_lat) / 2
        pairs = sorted([
            (math.sqrt((p["lat"] - cz_m) ** 2 + (p["lon"] - cx_m) ** 2),
             math.sqrt(diffs[i][0] ** 2 + diffs[i][1] ** 2))
            for i, p in enumerate(valid_pts) if i < len(diffs)
        ])
        if (len(pairs) >= 3 and
                all(pairs[j][1] <= pairs[j + 1][1] + 2 for j in range(len(pairs) - 1)) and
                pairs[-1][1] > pairs[0][1] + 5):
            log_fn("  スケール誤差の可能性: 中心から遠いほどズレが拡大")
            log_fn("  → 座標変換のスケールファクターに誤差がある可能性があります")

    if dx_s >= 5 or dz_s >= 5:
        log_fn("  不規則なズレ: 各点でズレの大きさ・方向が異なる")
        log_fn("  → マッチング精度の低下、または個別誤差の可能性")

    log_fn("=" * 55)
