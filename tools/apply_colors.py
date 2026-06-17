"""apply_colors.py — Bedrockワールドにブロック色を適用するスクリプト"""
import sys
import os


def apply_colors(world_folder: str):
    print(f"カラー適用開始: {world_folder}")
    if not os.path.isdir(world_folder):
        print(f"[ERROR] フォルダが見つかりません: {world_folder}")
        sys.exit(1)
    # TODO: amulet-core等でブロック置換処理を実装
    print("Finished writing")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python apply_colors.py <world_folder>")
        sys.exit(1)
    apply_colors(sys.argv[1])
