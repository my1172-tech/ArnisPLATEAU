"""
pywebviewを使ったLeaflet地図の範囲選択・スポーン地点指定ウィンドウ。
GUIから呼び出され、選択結果を辞書として返す。
"""
import os
import json
import threading
import webview


class MapPickerAPI:
    def __init__(self):
        self.result = None
        self._event = threading.Event()

    def on_area_confirmed(self, json_str: str):
        """JavaScript側から呼ばれるコールバック"""
        try:
            self.result = json.loads(json_str)
        except Exception as e:
            print(f"[map_picker] JSON解析エラー: {e}")
            self.result = None
        self._event.set()
        # ウィンドウを閉じる
        for window in webview.windows:
            window.destroy()


def open_map_picker(timeout: int = 600) -> dict:
    """
    地図選択ウィンドウを開き、ユーザーが確定するまでブロックする。
    戻り値: {"bbox": {...}, "spawn": {"lat":..., "lon":...} or None} または None（キャンセル時）
    """
    api = MapPickerAPI()
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map_picker.html")

    window = webview.create_window(
        "ArnisPLATEAU - 範囲選択",
        html_path,
        js_api=api,
        width=1000,
        height=750,
    )

    def run():
        webview.start()

    t = threading.Thread(target=run, daemon=True)
    t.start()

    api._event.wait(timeout=timeout)
    return api.result
