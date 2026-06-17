"""
デスクトップパスの一元管理モジュール。
このプロジェクトでは C:\\Users\\my117\\Desktop を使用しない方針のため、
常にOneDriveデスクトップを優先して返す。
"""
import os


def get_desktop_path() -> str:
    """
    OneDriveデスクトップのパスを返す。
    OneDriveデスクトップが存在しない環境（他のPC等）では、
    フォールバックとして通常のDesktopパスを返す。
    """
    onedrive_desktop = os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive", "デスクトップ")
    if os.path.isdir(onedrive_desktop):
        return onedrive_desktop

    # 英語環境のOneDrive構成（Desktop表記）も確認
    onedrive_desktop_en = os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive", "Desktop")
    if os.path.isdir(onedrive_desktop_en):
        return onedrive_desktop_en

    # フォールバック（OneDrive未使用環境向け）
    return os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
