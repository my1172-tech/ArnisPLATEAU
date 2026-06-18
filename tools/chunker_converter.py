"""
chunker_converter.py
Chunker CLI（HiveGamesOSS, MIT）を使った Java Edition → Bedrock Edition 変換。

同梱の chunker-cli/chunker-cli.exe（JRE バンドル版）を優先して使用するため、
ユーザー側に Java のインストールは不要。
バックアップとして shutil.which("java") + chunker-cli.jar を使用。

arnis-rs が生成する Java ワールドは全チャンクの zPos が実際の位置 + 32 となる
既知のバグがある（NBT の xPos/zPos と region ファイルのテーブル位置が不一致）。
Chunker はこれを "Mislocated chunk" として全チャンクをスキップし、変換後の
Bedrock ワールドが空になる。convert_java_to_bedrock() では Chunker 実行前に
fix_arnis_chunk_coords() でこのズレを自動修正する。
"""
import io
import os
import re
import sys
import shutil
import struct
import zlib
import subprocess
from typing import Callable, Optional

_CREATE_NO_WINDOW = 0x08000000  # Windows: コンソールウィンドウを非表示にする


def _chunker_base() -> str:
    """chunker-cli/ ディレクトリの親ディレクトリを返す。"""
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _find_chunker_exe() -> Optional[str]:
    """chunker-cli.exe（JRE バンドル版）のパスを返す。存在しなければ None。"""
    path = os.path.join(_chunker_base(), "chunker-cli", "chunker-cli.exe")
    return path if os.path.isfile(path) else None


def _find_chunker_jar() -> Optional[str]:
    """chunker-cli-*.jar のパスを返す（システム Java 経由のバックアップ用）。"""
    app_dir = os.path.join(_chunker_base(), "chunker-cli", "app")
    if os.path.isdir(app_dir):
        jars = sorted(f for f in os.listdir(app_dir) if f.endswith(".jar"))
        if jars:
            return os.path.join(app_dir, jars[-1])
    return None


def fix_arnis_chunk_coords(java_world_path: str) -> dict:
    """
    arnis-rs が生成した Java Edition ワールドの region/*.mca を in-place で修正する。

    arnis のバグ: 全チャンクの zPos = 実際の cz_local + 32 （+32 のズレ）。
    Chunker はこれを "Mislocated chunk" と判断し変換をスキップするため、
    Chunker 実行前に正しい xPos/zPos（= rx*32+cx_local, rz*32+cz_local）に書き戻す。

    Returns: {"fixed": int, "skipped": int, "errors": int}
    """
    import nbt.nbt as nbt_lib

    region_dir = os.path.join(java_world_path, "region")
    if not os.path.isdir(region_dir):
        return {"fixed": 0, "skipped": 0, "errors": 0}

    total_fixed = total_skipped = total_errors = 0

    for fname in os.listdir(region_dir):
        m = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", fname)
        if not m:
            continue
        rx, rz = int(m.group(1)), int(m.group(2))
        mca_path = os.path.join(region_dir, fname)

        with open(mca_path, "rb") as f:
            loc_table = bytearray(f.read(4096))
            _ts_table = f.read(4096)
            file_data  = bytearray(f.read())

        file_size = 8192 + len(file_data)
        changed = False

        for idx in range(1024):
            entry = struct.unpack_from(">I", loc_table, idx * 4)[0]
            sector_offset = (entry >> 8) & 0xFFFFFF
            if sector_offset == 0:
                continue

            cx_local = idx % 32
            cz_local = idx // 32
            expected_x = rx * 32 + cx_local
            expected_z = rz * 32 + cz_local

            data_start = sector_offset * 4096 - 8192  # file_data 内オフセット
            if data_start < 0 or data_start + 5 > len(file_data):
                total_errors += 1
                continue

            data_len  = struct.unpack_from(">I", file_data, data_start)[0]
            comp_byte = file_data[data_start + 4]
            raw_comp  = bytes(file_data[data_start + 5: data_start + 4 + data_len])

            try:
                if comp_byte == 2:
                    raw = zlib.decompress(raw_comp)
                elif comp_byte == 1:
                    import gzip
                    raw = gzip.decompress(raw_comp)
                else:
                    total_skipped += 1
                    continue

                buf = io.BytesIO(raw)
                nbt_data = nbt_lib.NBTFile(buffer=buf)

                # xPos/zPos が正しければスキップ
                need_fix = False
                try:
                    cur_x = int(nbt_data["xPos"].value)
                    cur_z = int(nbt_data["zPos"].value)
                    if cur_x != expected_x or cur_z != expected_z:
                        nbt_data["xPos"].value = expected_x
                        nbt_data["zPos"].value = expected_z
                        need_fix = True
                except KeyError:
                    total_skipped += 1
                    continue

                if not need_fix:
                    total_skipped += 1
                    continue

                # 修正済み NBT を plain bytes で書き戻す
                out_buf = io.BytesIO()
                nbt_data.write_file(buffer=out_buf)
                new_raw = out_buf.getvalue()

                if comp_byte == 2:
                    new_comp = zlib.compress(new_raw, 6)
                else:
                    import gzip
                    new_comp = gzip.compress(new_raw)

                new_payload = struct.pack(">IB", len(new_comp) + 1, comp_byte) + new_comp
                pad = (4096 - len(new_payload) % 4096) % 4096
                new_sector_data = new_payload + b"\x00" * pad

                # 末尾に追記し、ロケーションテーブルを更新
                new_sector_offset = file_size // 4096
                file_data += new_sector_data
                file_size += len(new_sector_data)

                new_sectors = len(new_sector_data) // 4096
                struct.pack_into(">I", loc_table, idx * 4,
                                 (new_sector_offset << 8) | min(new_sectors, 255))
                changed = True
                total_fixed += 1

            except Exception:
                total_errors += 1
                continue

        if changed:
            with open(mca_path, "wb") as f:
                f.write(bytes(loc_table))
                f.write(_ts_table)
                f.write(bytes(file_data))

    return {"fixed": total_fixed, "skipped": total_skipped, "errors": total_errors}


def convert_java_to_bedrock(
    java_world_path: str,
    output_dir: str,
    bedrock_version: str = "BEDROCK_1_21_0",
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Java Edition ワールドフォルダを Bedrock Edition フォルダに変換する。

    Args:
        java_world_path: Java Edition ワールドフォルダのパス（region/*.mca を含む）
        output_dir:      変換後 Bedrock フォルダの親ディレクトリ
        bedrock_version: Chunker の出力フォーマット文字列（例: "BEDROCK_1_21_0"）
        progress_callback: 進捗メッセージを受け取るコールバック（省略可）

    Returns:
        {
            "success":     bool,
            "output_path": str | None,   # Bedrock フォルダのパス（成功時）
            "error":       str | None,   # エラーメッセージ（失敗時）
        }
    """
    def _log(msg: str):
        if progress_callback:
            progress_callback(msg)
        print(f"[chunker] {msg}")

    # ── 入力検証 ────────────────────────────────────────────────────────────
    if not os.path.isdir(java_world_path):
        return {"success": False, "output_path": None,
                "error": f"Java版ワールドフォルダが見つかりません: {java_world_path}"}

    os.makedirs(output_dir, exist_ok=True)
    world_name = os.path.basename(java_world_path.rstrip("/\\"))
    bedrock_out = os.path.join(output_dir, f"{world_name}_bedrock")

    if os.path.exists(bedrock_out):
        shutil.rmtree(bedrock_out)

    # ── arnis チャンク座標バグ修正（Chunker 実行前） ─────────────────────────
    fix_result = fix_arnis_chunk_coords(java_world_path)
    if fix_result["fixed"] > 0:
        _log(f"チャンク座標修正: {fix_result['fixed']}件修正 / {fix_result['skipped']}件スキップ")

    # ── コマンド構築 ─────────────────────────────────────────────────────────
    chunker_exe = _find_chunker_exe()
    chunker_jar = _find_chunker_jar()

    if chunker_exe:
        cmd = [chunker_exe,
               "-i", java_world_path,
               "-f", bedrock_version,
               "-o", bedrock_out]
        _log(f"Bedrock変換開始（JRE同梱版）: {world_name} → {bedrock_version}")
    elif chunker_jar:
        java_exe = shutil.which("java")
        if not java_exe:
            return {
                "success": False, "output_path": None,
                "error": (
                    "Bedrock変換にはJava 17以上が必要ですが見つかりません。\n"
                    "Minecraft Java版を起動できる環境であれば通常インストール済みです。\n"
                    "https://adoptium.net/ から OpenJDK 17 をインストールしてください。"
                ),
            }
        cmd = [java_exe, "-jar", chunker_jar,
               "-i", java_world_path,
               "-f", bedrock_version,
               "-o", bedrock_out]
        _log(f"Bedrock変換開始（java -jar）: {world_name} → {bedrock_version}")
    else:
        return {"success": False, "output_path": None,
                "error": "chunker-cli が見つかりません。ツールが正しく配置されているか確認してください。"}

    # ── 実行 ─────────────────────────────────────────────────────────────────
    popen_kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = _CREATE_NO_WINDOW

    try:
        _log("変換処理中（数十秒〜数分かかる場合があります）...")
        proc = subprocess.Popen(cmd, **popen_kwargs)

        tail_lines: list[str] = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                tail_lines.append(line)
                if len(tail_lines) > 20:
                    tail_lines.pop(0)
                _log(f"  {line}")

        proc.wait(timeout=600)

        if proc.returncode == 0 and os.path.isdir(bedrock_out):
            _log(f"Bedrock変換完了: {bedrock_out}")
            return {"success": True, "output_path": bedrock_out, "error": None}

        detail = "\n".join(tail_lines[-5:]) if tail_lines else "(出力なし)"
        return {
            "success": False, "output_path": None,
            "error": f"変換失敗（終了コード: {proc.returncode}）\n{detail}",
        }

    except subprocess.TimeoutExpired:
        proc.kill()
        return {"success": False, "output_path": None,
                "error": "変換タイムアウト（10分超過）"}
    except Exception as e:
        return {"success": False, "output_path": None, "error": str(e)}
