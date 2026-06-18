"""
java_world_editor の単体テスト
1.18+ 形式 NBT を直接作成してテストする
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import io, tempfile, struct, zlib, math
import nbt.nbt as nbt_lib
from java_world_editor import (
    _get_block, _set_block, _find_base_y, _rebuild_column,
    _find_section, _get_sections_tag, _new_section,
    _read_chunk, _write_chunk, apply_corrections_java,
)
from world_height_writer import get_cells_in_polygon

SEP = "=" * 50


def make_1_18_chunk_nbt(cx=0, cz=0):
    """1.18+ 形式の最小チャンク NBTFile を作成する"""
    nbt_file = nbt_lib.NBTFile()
    nbt_file.tags.append(nbt_lib.TAG_Int(name="DataVersion", value=3337))  # 1.20.1
    nbt_file.tags.append(nbt_lib.TAG_Int(name="xPos", value=cx))
    nbt_file.tags.append(nbt_lib.TAG_Int(name="zPos", value=cz))
    nbt_file.tags.append(nbt_lib.TAG_String(name="Status", value="full"))
    secs = nbt_lib.TAG_List(type=nbt_lib.TAG_Compound, name="sections")
    nbt_file.tags.append(secs)
    return nbt_file


def fill_column(nbt_file, bx, bz, y_start, y_end, block_name):
    """1.18+ チャンクの列を指定ブロックで埋める"""
    secs = nbt_file["sections"]
    for y in range(y_start, y_end + 1):
        sy = y >> 4
        s = _find_section(secs, sy)
        if s is None:
            s = _new_section(sy)
            secs.tags.append(s)
        _set_block(s, bx, y & 15, bz, block_name)


# ── テスト1: packed bit array 読み書き ───────────────────────────────────
print(f"\n{SEP}\n[Test1] _set_block / _get_block\n{SEP}")

s = nbt_lib.TAG_Compound()
s.tags.append(nbt_lib.TAG_Byte(name="Y", value=4))

_set_block(s, 5, 0, 7, "minecraft:stone")
assert _get_block(s, 5, 0, 7) == "minecraft:stone", "FAIL: stone set"
assert _get_block(s, 0, 0, 0) == "minecraft:air",   "FAIL: air default"
_set_block(s, 0, 0, 0, "minecraft:stone")
_set_block(s, 5, 0, 7, "minecraft:air")
assert _get_block(s, 5, 0, 7) == "minecraft:air",   "FAIL: clear stone"
assert _get_block(s, 0, 0, 0) == "minecraft:stone", "FAIL: stone at 0,0,0"
print("  PASS")


# ── テスト2: _find_base_y / _rebuild_column (1.18+ NBT) ──────────────────
print(f"\n{SEP}\n[Test2] _find_base_y / _rebuild_column\n{SEP}")

nbt_data = make_1_18_chunk_nbt()
fill_column(nbt_data, 5, 7, 64, 144, "minecraft:stone")  # 81 ブロック

base = _find_base_y(nbt_data, 5, 7)
print(f"  base_y: {base}  (expected: 64)")
assert base == 64, f"FAIL base_y: {base}"

_rebuild_column(nbt_data, 5, 7, base, 120)

secs = _get_sections_tag(nbt_data)
stone_ys = set()
for y in range(50, 220):
    s = _find_section(secs, y >> 4)
    if s is None:
        continue
    if _get_block(s, 5, y & 15, 7) == "minecraft:stone":
        stone_ys.add(y)

print(f"  stone Y range: {min(stone_ys)}~{max(stone_ys)}  count={len(stone_ys)}")
assert min(stone_ys) == 64,  f"FAIL min stone Y: {min(stone_ys)}"
assert max(stone_ys) == 183, f"FAIL max stone Y: {max(stone_ys)}"
assert len(stone_ys) == 120, f"FAIL stone count: {len(stone_ys)}"
print("  PASS: y=64~183 (120 blocks) stone, y=184+ air")


# ── テスト3: region ファイル I/O ─────────────────────────────────────────
print(f"\n{SEP}\n[Test3] region file I/O\n{SEP}")

def make_region_file(path, chunks):
    """chunks = [(cx_local, cz_local, nbt_file), ...]"""
    sectors = [b"\x00" * 4096, b"\x00" * 4096]  # ヘッダ2セクター
    loc_table = bytearray(4096)
    ts_table  = bytearray(4096)

    for (cx_l, cz_l, nbt_file) in chunks:
        buf = io.BytesIO()
        nbt_file.write_file(fileobj=buf)
        compressed = zlib.compress(buf.getvalue(), 6)
        payload = struct.pack(">IB", len(compressed) + 1, 2) + compressed
        pad = (4096 - len(payload) % 4096) % 4096
        sector_data = payload + b"\x00" * pad
        sector_num = len(sectors)
        sectors.append(sector_data)

        idx = cz_l * 32 + cx_l
        struct.pack_into(">I", loc_table, idx * 4, (sector_num << 8) | 1)

    with open(path, "wb") as f:
        f.write(bytes(loc_table))
        f.write(bytes(ts_table))
        for s in sectors[2:]:
            f.write(s)


with tempfile.TemporaryDirectory() as tmpdir:
    region_dir = os.path.join(tmpdir, "region")
    os.makedirs(region_dir)
    region_path = os.path.join(region_dir, "r.0.0.mca")

    # 81 ブロックの建物を含むチャンクを作成
    base_chunk = make_1_18_chunk_nbt(0, 0)
    fill_column(base_chunk, 3, 3, 64, 144, "minecraft:stone")
    make_region_file(region_path, [(0, 0, base_chunk)])
    print(f"  region file: {os.path.getsize(region_path)} bytes")

    # apply_corrections_java を実行
    corrections = [{"polygon_mc_xz": [(0, 0), (10, 0), (10, 10), (0, 10)], "target_height_m": 120.0}]
    result = apply_corrections_java(tmpdir, corrections, get_cells_in_polygon)
    print(f"  result: {result}")

    # 読み直して確認
    nbt_reread, _ = _read_chunk(region_path, 0, 0)
    secs2 = _get_sections_tag(nbt_reread)
    stone2 = set()
    for y in range(50, 220):
        s = _find_section(secs2, y >> 4)
        if s is None: continue
        if _get_block(s, 3, y & 15, 3) == "minecraft:stone":
            stone2.add(y)

    print(f"  after: stone Y {min(stone2)}~{max(stone2)} count={len(stone2)}")
    assert len(stone2) == 120, f"FAIL stone count after correction: {len(stone2)}"
    print("  PASS: region I/O + height correction OK")

print(f"\n{SEP}\nAll tests PASS\n{SEP}")
