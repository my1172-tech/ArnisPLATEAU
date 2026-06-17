# -*- mode: python ; coding: utf-8 -*-
import shutil
shutil.copy('tools\\_build_config_free.py', 'tools\\_build_config.py')

block_cipher = None

a = Analysis(
    ['tools\\arnis_colorize_gui.py'],
    pathex=['.', 'tools'],
    binaries=[],
    datas=[
        ('tools\\_build_config.py', '.'),
        ('tools\\arnis_launcher.py', '.'),
        ('tools\\block_color_map.py', '.'),
        ('tools\\road_analyzer.py', '.'),
        ('tools\\osm_to_json.py', '.'),
        ('tools\\apply_colors.py', '.'),
    ],
    hiddenimports=[
        'tkinter', 'tkinter.filedialog', 'tkinter.messagebox',
        'json', 'zipfile', 'threading', 'queue',
        'math', 'webbrowser', 'subprocess',
    ],
    excludes=['requests', 'sklearn', 'PIL', 'numpy'],
    hookspath=[], runtime_hooks=[],
    win_no_prefer_redirects=False, win_private_assemblies=False,
    cipher=block_cipher, noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='ArnisPLATEAU',
    debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, upx_exclude=[],
    runtime_tmpdir=None, console=False,
    disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=None,
)
