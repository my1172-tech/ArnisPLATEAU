"""
建物の高さ（measuredHeight）をMinecraftワールドに書き込むモジュール。

対応フォーマット:
  - Java Edition (region/*.mca): java_world_editor モジュールで直接 NBT 編集
  - Bedrock (.mcworld 展開フォルダ): 現時点では plateau_height_plan.json に保存
    （Phase C で対応予定）

修正履歴:
  - base_y=0 バグ修正: java_world_editor._find_base_y() で地盤面を動的に検出
  - amulet 偽成功修正: amulet 2.0.9a0 は world editing API 未搭載のため使用しない
  - 修正A: _save_correction_plan の偽成功 (corrected=N) を廃止し正直に 0 を返す
  - 修正B: base_y を java_world_editor 側で y=55〜90 スキャンにより動的取得

重要: ポリゴン内部のセルのみ高さ補正する（輪郭外は無変更）。
屋根形状は対象外（上面を平らに揃えるのみ）。
"""
import json
import os
from typing import Dict, List, Tuple


def point_in_polygon(x: float, z: float, polygon_xz: List[Tuple[float, float]]) -> bool:
    n = len(polygon_xz)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, zi = polygon_xz[i]
        xj, zj = polygon_xz[j]
        if ((zi > z) != (zj > z)) and (x < (xj - xi) * (z - zi) / (zj - zi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def get_cells_in_polygon(polygon_xz: List[Tuple[float, float]]) -> List[Tuple[int, int]]:
    if len(polygon_xz) < 3:
        return []
    xs = [p[0] for p in polygon_xz]
    zs = [p[1] for p in polygon_xz]
    min_x, max_x = int(min(xs)), int(max(xs))
    min_z, max_z = int(min(zs)), int(max(zs))
    cells = []
    for x in range(min_x, max_x + 1):
        for z in range(min_z, max_z + 1):
            if point_in_polygon(x + 0.5, z + 0.5, polygon_xz):
                cells.append((x, z))
    return cells


def _is_java_edition(world_folder: str) -> bool:
    """region/ ディレクトリの存在で Java Edition を判定する。"""
    return os.path.isdir(os.path.join(world_folder, "region"))


def _is_bedrock_edition(world_folder: str) -> bool:
    """db/ ディレクトリの存在で Bedrock Edition を判定する。"""
    return os.path.isdir(os.path.join(world_folder, "db"))


def apply_height_corrections(
    world_folder: str,
    building_corrections: List[Dict],
    block_height_m: float = 1.0,
) -> dict:
    """
    building_corrections: [
        {"polygon_mc_xz": [(x,z), ...], "target_height_m": float, ...}
    ]
    ポリゴン内部のセルのみ高さを補正する。
    Java Edition → java_world_editor で直接 .mca 編集。
    Bedrock Edition → plateau_height_plan.json に保存（Phase C 対応待ち）。
    """
    if not building_corrections:
        return {"corrected": 0, "skipped": 0, "errors": 0}

    if _is_java_edition(world_folder):
        print(f"[world_height_writer] Java Edition ワールドを検出: {world_folder}")
        return _apply_java(world_folder, building_corrections, block_height_m)

    if _is_bedrock_edition(world_folder):
        print(f"[world_height_writer] Bedrock Edition ワールドを検出（現時点はJSONプラン保存のみ）")
        return _save_correction_plan(world_folder, building_corrections, block_height_m)

    print(f"[world_height_writer] ワールド形式を判定できません: {world_folder}")
    return _save_correction_plan(world_folder, building_corrections, block_height_m)


def _apply_java(
    world_folder: str,
    building_corrections: List[Dict],
    block_height_m: float,
) -> dict:
    """Java Edition 向け実装。java_world_editor モジュールに委譲する。"""
    try:
        from java_world_editor import apply_corrections_java
    except ImportError as e:
        msg = f"java_world_editor インポート失敗: {e}"
        print(f"[world_height_writer] {msg}")
        return {"corrected": 0, "skipped": 0, "errors": len(building_corrections), "detail": msg}

    result = apply_corrections_java(
        world_folder,
        building_corrections,
        get_cells_fn=get_cells_in_polygon,
        block_height_m=block_height_m,
    )
    if result is None:
        # region/ が見つからなかった（想定外）
        return _save_correction_plan(world_folder, building_corrections, block_height_m)

    print(
        f"[world_height_writer] Java補正完了: "
        f"チャンク{result['corrected']}件 / スキップ{result.get('skipped', 0)}件 / "
        f"エラー{result['errors']}件"
    )
    # GUI が「○棟」と表示する corrected フィールドを建物数ベースに変換
    # (java_editor はチャンク単位で返すため、建物数は building_corrections の長さで近似)
    buildings_done = len(building_corrections) - result["errors"]
    return {
        "corrected": max(0, buildings_done),
        "skipped": result.get("skipped", 0),
        "errors": result["errors"],
    }


def _save_correction_plan(
    world_folder: str,
    building_corrections: List[Dict],
    block_height_m: float,
) -> dict:
    """
    ブロック書き込みができない場合のフォールバック。
    plateau_height_plan.json に補正計画を保存するが、
    corrected=0 を正直に返す（偽成功を報告しない）。
    """
    plan_path = os.path.join(world_folder, "plateau_height_plan.json")
    count = len(building_corrections)
    try:
        os.makedirs(world_folder, exist_ok=True)
        plan = {"block_height_m": block_height_m, "corrections": building_corrections}
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print(
            f"[world_height_writer] 補正計画を保存しました（{count}棟）: {plan_path}\n"
            f"  → ブロックは書き込まれていません。Bedrock対応実装後に再適用予定。"
        )
    except Exception as e:
        print(f"[world_height_writer] 補正計画の保存に失敗: {e}")
        return {"corrected": 0, "skipped": 0, "errors": count}

    return {"corrected": 0, "skipped": count, "errors": 0, "plan_saved": plan_path}
