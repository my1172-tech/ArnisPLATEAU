"""
Java Edition (.mca) リージョンファイル ブロック書き込みモジュール

nbt ライブラリで直接 NBT を操作し、リージョンファイルに書き戻す。
対応: Minecraft Java Edition 1.18+ (Y=-64〜319, セクション Y=-4〜19)
"""
import io
import math
import os
import struct
import time
import zlib
from typing import Callable, Dict, List, Optional, Tuple

import nbt.nbt as nbt_lib

STONE = "minecraft:stone"
AIR   = "minecraft:air"
_AIR_NAMES = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}

WORLD_MIN_Y = -64
WORLD_MAX_Y = 319


# ── ビットパック ヘルパー ──────────────────────────────────────────────────

def _bpb(palette_size: int) -> int:
    return max(4, math.ceil(math.log2(max(2, palette_size))))


def _to_u64(longs: list) -> list:
    return [v + (1 << 64) if v < 0 else v for v in longs]


def _to_signed(vals: list) -> list:
    return [v - (1 << 64) if v >= (1 << 63) else v for v in vals]


def _read_idx(data_u64: list, block_idx: int, bits: int) -> int:
    bpl = 64 // bits
    li = block_idx // bpl
    bo = (block_idx % bpl) * bits
    return (data_u64[li] >> bo) & ((1 << bits) - 1)


def _write_idx(data_u64: list, block_idx: int, pal_idx: int, bits: int) -> None:
    bpl = 64 // bits
    li = block_idx // bpl
    bo = (block_idx % bpl) * bits
    mask = (1 << bits) - 1
    data_u64[li] = (data_u64[li] & ~(mask << bo)) | ((pal_idx & mask) << bo)


def _repack(data_u64: list, old_bits: int, new_bits: int, total: int = 4096) -> list:
    old_bpl = 64 // old_bits
    new_bpl = 64 // new_bits
    new_data = [0] * math.ceil(total / new_bpl)
    for i in range(total):
        _write_idx(new_data, i, _read_idx(data_u64, i, old_bits), new_bits)
    return new_data


def _blk_idx(lx: int, ly: int, lz: int) -> int:
    return (ly & 15) * 256 + (lz & 15) * 16 + (lx & 15)


def _longs_from_tag(tag) -> list:
    """TAG_Long_Array または TAG_List[TAG_Long] どちらからでも int リストを返す。"""
    if isinstance(tag, nbt_lib.TAG_Long_Array):
        return [int(v) for v in tag.value]
    return [t.value for t in tag]


# ── セクション操作 ────────────────────────────────────────────────────────

def _find_section(sections_tag, section_y: int):
    for s in sections_tag:
        if s["Y"].value == section_y:
            return s
    return None


def _new_section(section_y: int):
    s = nbt_lib.TAG_Compound()
    s.tags.append(nbt_lib.TAG_Byte(name="Y", value=section_y))
    return s


def _get_block(section, lx: int, ly: int, lz: int) -> str:
    if "block_states" not in section:
        return AIR
    bs = section["block_states"]
    if "palette" not in bs:
        return AIR
    pal = bs["palette"]
    if len(pal) == 1:
        return pal[0]["Name"].value
    if "data" not in bs or len(bs["data"]) == 0:
        return AIR
    data_u64 = _to_u64(_longs_from_tag(bs["data"]))
    bits = _bpb(len(pal))
    idx = _read_idx(data_u64, _blk_idx(lx, ly, lz), bits)
    return pal[idx]["Name"].value


def _set_block(section, lx: int, ly: int, lz: int, block_name: str) -> None:
    # block_states が無ければ作成
    if "block_states" not in section:
        bs = nbt_lib.TAG_Compound(name="block_states")
        pal = nbt_lib.TAG_List(type=nbt_lib.TAG_Compound, name="palette")
        air = nbt_lib.TAG_Compound()
        air.tags.append(nbt_lib.TAG_String(name="Name", value=AIR))
        pal.tags.append(air)
        bs.tags.append(pal)
        section.tags.append(bs)

    bs = section["block_states"]
    if "palette" not in bs:
        pal = nbt_lib.TAG_List(type=nbt_lib.TAG_Compound, name="palette")
        air = nbt_lib.TAG_Compound()
        air.tags.append(nbt_lib.TAG_String(name="Name", value=AIR))
        pal.tags.append(air)
        bs.tags.append(pal)

    pal = bs["palette"]
    pal_names = [p["Name"].value for p in pal]
    old_size = len(pal_names)

    if block_name not in pal_names:
        entry = nbt_lib.TAG_Compound()
        entry.tags.append(nbt_lib.TAG_String(name="Name", value=block_name))
        pal.tags.append(entry)
        pal_names.append(block_name)

    pal_idx = pal_names.index(block_name)
    new_size = len(pal_names)
    new_bits = _bpb(new_size)

    bpl = 64 // new_bits
    total = 4096

    if "data" not in bs or len(bs["data"]) == 0:
        # 全エアとして初期化
        data_u64 = [0] * math.ceil(total / bpl)
    else:
        data_u64 = _to_u64(_longs_from_tag(bs["data"]))
        old_bits = _bpb(old_size)
        if old_bits != new_bits:
            data_u64 = _repack(data_u64, old_bits, new_bits, total)

    _write_idx(data_u64, _blk_idx(lx, ly, lz), pal_idx, new_bits)

    # data を書き戻し
    bs.tags = [t for t in bs.tags if t.name != "data"]
    la = nbt_lib.TAG_Long_Array(name="data")
    la.value = _to_signed(data_u64)
    bs.tags.append(la)


# ── チャンク I/O ────────────────────────────────────────────────────────────

def _read_chunk(region_path: str, cx_local: int, cz_local: int):
    with open(region_path, "rb") as f:
        header = f.read(8192)

    idx = cz_local * 32 + cx_local
    raw_loc = int.from_bytes(header[idx * 4: idx * 4 + 4], "big")
    if raw_loc == 0:
        return None, None

    with open(region_path, "rb") as f:
        f.seek((raw_loc >> 8) * 4096)
        length = int.from_bytes(f.read(4), "big")
        comp = f.read(1)[0]
        data = f.read(length - 1)

    if comp == 2:
        raw = zlib.decompress(data)
    elif comp == 1:
        import gzip
        raw = gzip.decompress(data)
    else:
        raise ValueError(f"未知の圧縮形式: {comp}")

    # arnis は zlib(plain_NBT) を書く。nbt_lib の fileobj= は内部で gzip を試みて失敗するため
    # buffer= を使って直接 plain NBT として読む。gzip 二重ラップの場合は fallback。
    buf = io.BytesIO(raw)
    try:
        nbt_data = nbt_lib.NBTFile(buffer=buf)
    except Exception:
        buf.seek(0)
        nbt_data = nbt_lib.NBTFile(fileobj=buf)
    return nbt_data, comp


def _write_chunk(region_path: str, cx_local: int, cz_local: int,
                 nbt_data, comp: int = 2) -> None:
    buf = io.BytesIO()
    # buffer= を使って plain NBT（gzip ラップなし）で書く。arnis/Chunker 互換形式。
    nbt_data.write_file(buffer=buf)
    raw = buf.getvalue()

    if comp == 2:
        compressed = zlib.compress(raw, level=6)
    else:
        import gzip
        compressed = gzip.compress(raw)

    payload = struct.pack(">IB", len(compressed) + 1, comp) + compressed
    pad = (4096 - len(payload) % 4096) % 4096
    sector_data = payload + b"\x00" * pad
    sectors_needed = len(sector_data) // 4096

    with open(region_path, "r+b") as f:
        f.seek(0, 2)
        eof = f.tell()
        if eof % 4096 != 0:
            align = 4096 - eof % 4096
            f.write(b"\x00" * align)
            eof += align
        new_sector = eof // 4096
        f.write(sector_data)

        idx = cz_local * 32 + cx_local
        f.seek(idx * 4)
        f.write(struct.pack(">I", (new_sector << 8) | min(sectors_needed, 255)))
        f.seek(4096 + idx * 4)
        f.write(struct.pack(">I", int(time.time())))


# ── NBT 形式ヘルパー（1.18+ / pre-1.18 両対応） ──────────────────────────

def _get_sections_tag(nbt_data):
    """
    1.18+  形式: nbt["sections"]
    pre-1.18 形式: nbt["Level"]["Sections"]
    どちらかを返す。見つからなければ None。
    """
    if "sections" in nbt_data:
        return nbt_data["sections"]
    if "Level" in nbt_data:
        level = nbt_data["Level"]
        if "Sections" in level:
            return level["Sections"]
    return None


# ── 列ごとのベースY検出 ──────────────────────────────────────────────────

def _find_base_y(nbt_data, bx: int, bz: int) -> int:
    """建物の地盤面Yを推定する（y=55〜90 をスキャンして最初の非エアを返す）。"""
    secs = _get_sections_tag(nbt_data)
    if secs is None:
        return 64
    for y in range(55, 90):
        s = _find_section(secs, y >> 4)
        if s is None:
            continue
        if _get_block(s, bx, y & 15, bz) not in _AIR_NAMES:
            return y
    return 64


# ── 列の高さ再構築 ────────────────────────────────────────────────────────

def _rebuild_column(nbt_data, bx: int, bz: int, base_y: int, target_blocks: int) -> None:
    """
    (bx, bz) 列を base_y 起点で target_blocks 高さに再構築する。
    base_y 〜 base_y+target_blocks-1: STONE で埋める（エアのみ）
    base_y+target_blocks 〜 base_y+target_blocks+35: AIR に削る（非エアのみ）
    """
    secs = _get_sections_tag(nbt_data)
    if secs is None:
        return

    def ensure_section(sy):
        s = _find_section(secs, sy)
        if s is None:
            s = _new_section(sy)
            secs.tags.append(s)
        return s

    for y in range(base_y, base_y + target_blocks):
        if not (WORLD_MIN_Y <= y <= WORLD_MAX_Y):
            continue
        s = ensure_section(y >> 4)
        if _get_block(s, bx, y & 15, bz) in _AIR_NAMES:
            _set_block(s, bx, y & 15, bz, STONE)

    for y in range(base_y + target_blocks, base_y + target_blocks + 35):
        if not (WORLD_MIN_Y <= y <= WORLD_MAX_Y):
            break
        s = _find_section(secs, y >> 4)
        if s is None:
            break
        if _get_block(s, bx, y & 15, bz) in _AIR_NAMES:
            break
        _set_block(s, bx, y & 15, bz, AIR)


# ── メインエントリ ────────────────────────────────────────────────────────

def apply_corrections_java(
    world_folder: str,
    corrections: List[dict],
    get_cells_fn: Callable,
    block_height_m: float = 1.0,
) -> Optional[dict]:
    """
    Java Edition ワールドに高さ補正を適用する。
    world_folder/region/*.mca を直接編集する。
    Java Edition でなければ None を返す。
    """
    region_dir = os.path.join(world_folder, "region")
    if not os.path.isdir(region_dir):
        return None

    # チャンク単位でタスクをグループ化（I/O 最適化）
    chunk_tasks: Dict[Tuple, List[Tuple[int, int, int]]] = {}

    for correction in corrections:
        polygon_xz = correction.get("polygon_mc_xz", [])
        t_blocks = max(1, round(correction.get("target_height_m", 1) / block_height_m))
        cells = get_cells_fn(polygon_xz)
        for (x, z) in cells:
            cx, bx = divmod(x, 16)
            cz, bz = divmod(z, 16)
            rx, cx_l = divmod(cx, 32)
            rz, cz_l = divmod(cz, 32)
            chunk_tasks.setdefault((rx, rz, cx_l, cz_l), []).append((bx, bz, t_blocks))

    print(f"[java_editor] 対象チャンク数: {len(chunk_tasks)}")
    corrected = errors = skipped = 0

    for (rx, rz, cx_l, cz_l), tasks in chunk_tasks.items():
        region_path = os.path.join(region_dir, f"r.{rx}.{rz}.mca")
        if not os.path.exists(region_path):
            skipped += 1
            continue
        try:
            nbt_data, comp = _read_chunk(region_path, cx_l, cz_l)
            if nbt_data is None:
                skipped += 1
                continue

            for (bx, bz, t_blocks) in tasks:
                base_y = _find_base_y(nbt_data, bx, bz)
                _rebuild_column(nbt_data, bx, bz, base_y, t_blocks)

            _write_chunk(region_path, cx_l, cz_l, nbt_data, comp or 2)
            corrected += 1
        except Exception as e:
            print(f"[java_editor] チャンク({rx},{rz},{cx_l},{cz_l})エラー: {e}")
            import traceback; traceback.print_exc()
            errors += 1

    return {"corrected": corrected, "errors": errors, "skipped": skipped}
