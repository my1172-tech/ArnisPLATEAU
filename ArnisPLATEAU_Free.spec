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
        ('tools\\desktop_path.py', '.'),
        ('tools\\arnis_launcher.py', '.'),
        ('tools\\arnis_version_manager.py', '.'),
        ('tools\\block_color_map.py', '.'),
        ('tools\\road_analyzer.py', '.'),
        ('tools\\osm_to_json.py', '.'),
        ('tools\\apply_colors.py', '.'),
        ('tools\\gsi_fetcher.py', '.'),
        ('tools\\gsi_merge.py', '.'),
        ('tools\\map_picker.py', '.'),
        ('tools\\map_picker.html', '.'),
        ('tools\\osm_building_extractor.py', '.'),
        ('tools\\plateau_fetcher.py', '.'),
        ('tools\\plateau_height_merge.py', '.'),
        ('tools\\world_height_writer.py', '.'),
        ('tools\\java_world_editor.py', '.'),
        ('tools\\calibration.py', '.'),
        ('tools\\chunker_converter.py', '.'),
        ('tools\\building_height_editor.py', '.'),
        ('tools\\satellite_roof_color.py', '.'),
        ('tools\\chunker-cli', 'chunker-cli'),
    ],
    hiddenimports=[
        'tkinter', 'tkinter.filedialog', 'tkinter.messagebox',
        'json', 'zipfile', 'threading', 'queue',
        'math', 'webbrowser', 'subprocess',
        'mapbox_vector_tile', 'webview',
        'amulet',
        'requests',
        'shapely', 'shapely.geometry',
        'PIL', 'PIL.Image', 'PIL.ImageDraw',
    ],
    excludes=['sklearn', 'numpy'],
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
