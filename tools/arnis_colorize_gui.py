"""
arnis_colorize_gui.py
ArnisPLATEAU カラー適用GUI — v2.9.0 Mosaic対応
"""
import os
import subprocess
import threading
import zipfile
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from datetime import datetime


# v2.9.0以降のログパターン（既存パターンに追加）
COMPLETION_PATTERNS = [
    "Generation complete",      # 旧パターン（互換）
    "World generation finished",
    "Finished writing",         # v2.9.0ストリーミング書き込み完了
    "chunks written",           # v2.9.0チャンク並列化完了ログ
]


def is_generation_complete(line: str) -> bool:
    return any(p.lower() in line.lower() for p in COMPLETION_PATTERNS)


def find_arnis_exe(base_dir: str) -> str:
    """
    arnis本体exeを優先順位付きで検索する。
    v2.9.0以降は arnis-windows.exe が正式名称。
    ローカルビルド・旧版との互換性のため複数名を探索する。
    """
    candidates = [
        "arnis-windows.exe",   # v2.9.0+ 公式
        "arnis-jp.exe",        # ArnisPLATEAU fork旧名
        "arnis.exe",           # 汎用フォールバック
    ]
    for name in candidates:
        path = os.path.join(base_dir, name)
        if os.path.exists(path):
            return path
    return os.path.join(base_dir, candidates[0])  # 見つからない場合は最新名を返す


class ArnisColorizeGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ArnisPLATEAU カラー適用ツール v2.9.0")
        self.root.resizable(True, True)

        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.arnis_exe = find_arnis_exe(self.base_dir)

        self.world_folder = tk.StringVar(value="")
        self.custom_output_enabled = tk.BooleanVar(value=False)
        self.custom_output_path = tk.StringVar(value="")
        self.mcworld_enabled = tk.BooleanVar(value=False)

        self._build_ui()

    def _build_ui(self):
        # ワールドフォルダ選択
        frame_world = tk.LabelFrame(self.root, text="ワールドフォルダ", padx=8, pady=8)
        frame_world.pack(fill="x", padx=10, pady=5)

        tk.Entry(frame_world, textvariable=self.world_folder, width=50).grid(row=0, column=0, padx=5)
        tk.Button(
            frame_world, text="選択...", command=self._browse_world
        ).grid(row=0, column=1, padx=5)

        # 出力設定
        self._build_output_section(self.root)

        # 実行ボタン
        tk.Button(
            self.root,
            text="カラー適用を実行",
            command=self._run_colorize,
            bg="#4a90d9",
            fg="white",
            font=("", 11, "bold"),
            padx=16,
            pady=6
        ).pack(pady=10)

        # ログ
        frame_log = tk.LabelFrame(self.root, text="ログ", padx=8, pady=8)
        frame_log.pack(fill="both", expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(
            frame_log, height=12, state="disabled", font=("Courier New", 9)
        )
        self.log_text.pack(fill="both", expand=True)

    def _build_output_section(self, parent):
        frame = tk.LabelFrame(parent, text="出力設定", padx=8, pady=8)
        frame.pack(fill="x", padx=10, pady=5)

        # 保存先指定チェックボックス
        cb_custom = tk.Checkbutton(
            frame,
            text="保存先を指定する",
            variable=self.custom_output_enabled,
            command=self._on_custom_output_toggle
        )
        cb_custom.grid(row=0, column=0, sticky="w")

        self.btn_browse_output = tk.Button(
            frame,
            text="フォルダ選択...",
            command=self._browse_output_dir,
            state="disabled"
        )
        self.btn_browse_output.grid(row=0, column=1, padx=5)

        self.lbl_output_path = tk.Label(
            frame,
            textvariable=self.custom_output_path,
            fg="gray",
            width=40,
            anchor="w"
        )
        self.lbl_output_path.grid(row=0, column=2, sticky="w")

        # Bedrock mcworld保存チェックボックス
        self.mcworld_enabled = tk.BooleanVar(value=False)
        cb_mcworld = tk.Checkbutton(
            frame,
            text="統合版（Bedrock）.mcworld として保存",
            variable=self.mcworld_enabled,
            command=self._on_mcworld_toggle
        )
        cb_mcworld.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.lbl_mcworld_note = tk.Label(
            frame,
            text="※ Bedrock世界フォルダをzip圧縮して .mcworld に変換します",
            fg="gray",
            font=("", 8)
        )
        self.lbl_mcworld_note.grid(row=2, column=0, columnspan=3, sticky="w")

    def _on_custom_output_toggle(self):
        if self.custom_output_enabled.get():
            self.btn_browse_output.config(state="normal")
        else:
            self.btn_browse_output.config(state="disabled")
            self.custom_output_path.set("")

    def _browse_output_dir(self):
        path = filedialog.askdirectory(title="保存先フォルダを選択")
        if path:
            self.custom_output_path.set(path)

    def _on_mcworld_toggle(self):
        # mcworldが有効なら保存先指定を自動ONにする
        if self.mcworld_enabled.get():
            self.custom_output_enabled.set(True)
            self.btn_browse_output.config(state="normal")
            if not self.custom_output_path.get():
                # デフォルトをデスクトップに設定
                desktop = os.path.join(os.path.expanduser("~"), "Desktop")
                if not os.path.exists(desktop):
                    desktop = os.path.join(os.path.expanduser("~"), "OneDrive", "デスクトップ")
                self.custom_output_path.set(desktop)

    def _browse_world(self):
        path = filedialog.askdirectory(title="ワールドフォルダを選択")
        if path:
            self.world_folder.set(path)

    def _log(self, message: str):
        self.log_text.config(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")
        self.root.update_idletasks()

    def _run_colorize(self):
        world = self.world_folder.get()
        if not world or not os.path.isdir(world):
            messagebox.showerror("エラー", "有効なワールドフォルダを選択してください。")
            return

        apply_script = os.path.join(self.base_dir, "apply_colors.py")
        if not os.path.exists(apply_script):
            self._log(f"[WARN] apply_colors.py が見つかりません: {apply_script}")
            self._log("apply_colors.py なしでカラー適用をシミュレートします。")
            self._on_colorize_complete(world)
            return

        self._log(f"カラー適用開始: {world}")

        def worker():
            try:
                proc = subprocess.Popen(
                    ["python", apply_script, world],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace"
                )
                for line in proc.stdout:
                    self.root.after(0, self._log, line.rstrip())
                proc.wait()
                if proc.returncode == 0:
                    self.root.after(0, self._log, "カラー適用完了")
                    self.root.after(0, self._on_colorize_complete, world)
                else:
                    self.root.after(0, self._log, f"[ERROR] returncode={proc.returncode}")
            except Exception as e:
                self.root.after(0, self._log, f"[ERROR] {e}")

        threading.Thread(target=worker, daemon=True).start()

    def save_as_mcworld(self, world_folder: str, output_dir: str) -> str:
        """
        BedrockワールドフォルダをMCWorldファイルとして保存する。

        Args:
            world_folder: Bedrockワールドのフォルダパス
            output_dir: 保存先ディレクトリ
        Returns:
            保存したmcworldファイルのパス
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        world_name = os.path.basename(world_folder.rstrip("/\\"))
        mcworld_name = f"{world_name}_{timestamp}.mcworld"
        mcworld_path = os.path.join(output_dir, mcworld_name)

        self._log(f"mcworld作成中: {mcworld_name}")

        with zipfile.ZipFile(mcworld_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(world_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, world_folder)
                    zf.write(file_path, arcname)

        size_mb = os.path.getsize(mcworld_path) / (1024 * 1024)
        self._log(f"mcworld保存完了: {mcworld_path} ({size_mb:.1f} MB)")
        return mcworld_path

    def _on_colorize_complete(self, world_folder: str):
        """色付け完了後の処理"""

        output_dir = self.custom_output_path.get() if self.custom_output_enabled.get() else None

        if self.mcworld_enabled.get():
            if not output_dir:
                output_dir = os.path.join(os.path.expanduser("~"), "Desktop")
            try:
                mcworld_path = self.save_as_mcworld(world_folder, output_dir)
                # 保存先フォルダをエクスプローラーで開く
                subprocess.Popen(["explorer", f'/select,"{mcworld_path}"'])
            except Exception as e:
                self._log(f"[ERROR] mcworld作成失敗: {e}")

        elif output_dir and os.path.exists(output_dir):
            # mcworldでない場合はワールドフォルダごとコピー
            dst = os.path.join(output_dir, os.path.basename(world_folder))
            try:
                shutil.copytree(world_folder, dst, dirs_exist_ok=True)
                self._log(f"ワールドコピー完了: {dst}")
            except Exception as e:
                self._log(f"[ERROR] コピー失敗: {e}")


def main():
    root = tk.Tk()
    app = ArnisColorizeGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
