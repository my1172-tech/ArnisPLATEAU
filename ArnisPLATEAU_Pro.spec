# -*- mode: python ; coding: utf-8 -*-
import shutil
shutil.copy('tools\\_build_config_pro.py', 'tools\\_build_config.py')

block_cipher = None

a = Analysis(
    ['tools\\arnis_colorize_gui.py'],
    pathex=['.', 'tools'],
    binaries=[],
    datas=[
        ('tools\\_build_config.py', '.'),
        ('tools\\desktop_path.py', '.'),
        ('tools\\license_client.py', '.'),
        ('tools\\arnis_launcher.py', '.'),
        ('tools\\block_color_map.py', '.'),
        ('tools\\road_analyzer.py', '.'),
        ('tools\\streetview_fetcher.py', '.'),
        ('tools\\color_extractor.py', '.'),
        ('tools\\colorize.py', '.'),
        ('tools\\apply_colors.py', '.'),
        ('tools\\osm_to_json.py', '.'),
        ('tools\\gsi_fetcher.py', '.'),
        ('tools\\gsi_merge.py', '.'),
        ('tools\\map_picker.py', '.'),
        ('tools\\map_picker.html', '.'),
        ('tools\\plateau_fetcher.py', '.'),
        ('tools\\plateau_height_merge.py', '.'),
        ('tools\\world_height_writer.py', '.'),
    ],
    hiddenimports=[
        'tkinter', 'tkinter.filedialog', 'tkinter.messagebox',
        'requests', 'sklearn', 'sklearn.cluster',
        'PIL', 'PIL.Image', 'numpy',
        'json', 'zipfile', 'hashlib', 'threading', 'queue',
        'math', 'webbrowser', 'subprocess', 'base64',
        'mapbox_vector_tile', 'webview',
        'amulet',
    ],
    hookspath=[], runtime_hooks=[],
    win_no_prefer_redirects=False, win_private_assemblies=False,
    cipher=block_cipher, noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='ArnisPLATEAU_Pro',
    debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, upx_exclude=[],
    runtime_tmpdir=None, console=False,
    disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=None,
)
