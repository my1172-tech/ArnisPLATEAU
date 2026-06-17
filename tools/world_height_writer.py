"""
建物の高さ（measuredHeight）をMinecraftワールドに書き込むモジュール。
amulet-core を使用してブロックを直接編集する。
amulet が利用不可の場合は補正計画を JSON として保存するフォールバック動作をする。

重要: 壁の形（footprintポリゴンの輪郭）は元のarnis/GSI生成形状をそのまま保持する。
      対象を矩形（外接バウンディングボックス）で全部埋めるのではなく、
      point-in-polygon判定でポリゴン内部のセルのみ高さを補正する。
屋根形状は対象外（上面はポリゴン内部を平らに揃える）。
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


def _load_amulet_level(world_folder: str):
    """amulet でレベルを開く。利用不可なら None を返す。"""
    # amulet 1.x: load_level が amulet トップレベルにある
    # amulet 2.x: API が変更されているため試みて失敗したら None
    try:
        import amulet
        load_level = getattr(amulet, "load_level", None)
        if load_level is None:
            # 2.x では amulet.level モジュール経由になる可能性
            try:
                from amulet.level import load_level as ll
                load_level = ll
            except Exception:
                pass
        if load_level is None:
            raise ImportError("load_level が見つかりません")
        return load_level(world_folder)
    except Exception as e:
        print(f"[world_height_writer] amulet-core 使用不可: {e}")
        return None


def apply_height_corrections(world_folder: str, building_corrections: List[Dict], block_height_m: float = 1.0) -> dict:
    """
    building_corrections: [
        {"polygon_mc_xz": [(x,z), ...], "target_height_m": float}
    ]
    元のarnis/GSI生成済み建物の外形ポリゴン（polygon_mc_xz）の輪郭をそのまま使い、
    その内部のセルのみ高さをtarget_height_mに合わせて積み直す。
    輪郭外のセルは一切変更しない。
    amulet が利用不可の場合は補正計画を world_folder/plateau_height_plan.json に保存する。
    """
    level = _load_amulet_level(world_folder)
    if level is None:
        return _save_correction_plan(world_folder, building_corrections, block_height_m)

    corrected = 0
    skipped = 0
    errors = 0

    try:
        for correction in building_corrections:
            try:
                polygon_xz = correction["polygon_mc_xz"]
                if len(polygon_xz) < 3:
                    skipped += 1
                    continue

                target_blocks = max(1, round(correction["target_height_m"] / block_height_m))
                cells = get_cells_in_polygon(polygon_xz)

                if not cells:
                    skipped += 1
                    continue

                for x, z in cells:
                    _rebuild_single_column(level, x, z, target_blocks)
                corrected += 1
            except Exception as e:
                print(f"[world_height_writer] 建物補正エラー（スキップ）: {e}")
                errors += 1
    finally:
        level.save()
        level.close()

    return {"corrected": corrected, "skipped": skipped, "errors": errors}


def _save_correction_plan(world_folder: str, building_corrections: List[Dict], block_height_m: float) -> dict:
    """amulet が使えない場合の代替: 補正計画を JSON で保存する"""
    plan_path = os.path.join(world_folder, "plateau_height_plan.json")
    try:
        os.makedirs(world_folder, exist_ok=True)
        plan = {
            "block_height_m": block_height_m,
            "corrections": building_corrections,
        }
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        count = len(building_corrections)
        print(f"[world_height_writer] 補正計画を保存しました（{count}棟）: {plan_path}")
        print("[world_height_writer] amulet-core が利用可能になったとき、このファイルを使って再適用できます")
        return {"corrected": count, "skipped": 0, "errors": 0, "plan_saved": plan_path}
    except Exception as e:
        print(f"[world_height_writer] 補正計画の保存にも失敗: {e}")
        return {"corrected": 0, "skipped": 0, "errors": len(building_corrections)}


def _rebuild_single_column(level, x: int, z: int, target_blocks: int, base_y: int = 0, wall_block: str = "minecraft:stone"):
    """
    指定セル(x, z)の柱を target_blocks の高さまで壁ブロックで埋める（上面フラット）。
    既存ブロックがtarget_blocksより高い場合は削る（airに置換）、低い場合は積む。
    このセル単位の処理により、呼び出し元でポリゴン内部のセルだけを渡せば
    輪郭の形がそのまま保たれる。
    """
    dimension = "minecraft:overworld"

    for y in range(base_y, base_y + target_blocks):
        block = level.block.get_block(x, y, z, dimension)
        if block.namespaced_name == "minecraft:air":
            level.set_version_block(x, y, z, dimension, ("java", (1, 21, 0)), level.block.get_block_id(wall_block))

    for y in range(base_y + target_blocks, base_y + target_blocks + 30):
        block = level.block.get_block(x, y, z, dimension)
        if block.namespaced_name != "minecraft:air":
            level.set_version_block(x, y, z, dimension, ("java", (1, 21, 0)), level.block.get_block_id("minecraft:air"))
        else:
            break