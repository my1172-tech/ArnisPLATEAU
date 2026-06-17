"""
map_picker.py
pywebviewを使ったLeaflet地図の範囲選択・スポーン地点指定ウィンドウ。

【サブプロセス方式】
webview.start() はメインスレッド限定のため tkinter mainloop と同居不可。
このファイルを subprocess で単独起動し、結果を RESULT_FILE に書いて終了する。
"""
import os
import json
import tempfile
import webview

RESULT_FILE = os.path.join(tempfile.gettempdir(), "arnisplateau_map_result.json")


class MapPickerAPI:
    def __init__(self):
        self.result = None

    def on_area_confirmed(self, json_str: str):
        """JavaScript側から呼ばれるコールバック"""
        try:
            self.result = json.loads(json_str)
        except Exception as e:
            print(f"[map_picker] JSON解析エラー: {e}")
            self.result = None
        for window in webview.windows:
            window.destroy()


def run_picker():
    """メインスレッドで呼ぶこと。ウィンドウを開いてブロックし、結果をJSONファイルに書く。"""
    api = MapPickerAPI()
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map_picker.html")

    webview.create_window(
        "ArnisPLATEAU - 範囲選択",
        html_path,
        js_api=api,
        width=1000,
        height=750,
    )
    webview.start()  # メインスレッドでブロック、全ウィンドウが閉じると戻る

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(api.result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    run_picker()
