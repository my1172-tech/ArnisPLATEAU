"""
chunker_converter.py
Chunker CLI（HiveGamesOSS, MIT）を使った Java Edition → Bedrock Edition 変換。

同梱の chunker-cli/chunker-cli.exe（JRE バンドル版）を優先して使用するため、
ユーザー側に Java のインストールは不要。
バックアップとして shutil.which("java") + chunker-cli.jar を使用。
"""
import os
import sys
import shutil
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
