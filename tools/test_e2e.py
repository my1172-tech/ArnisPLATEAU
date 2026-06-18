"""
end-to-end テスト:
  arnis(Java) → GSI統合 → PLATEAU補正 → Chunker変換 → mcworld化

各ステップの所要時間・結果を計測・報告する。
"""
import sys, os, json, time, zipfile, shutil, struct, datetime, io

# stdout を UTF-8 に切り替え（Windows CP932 環境での arnis Unicode 出力対策）
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# tools/ パスを追加
TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT  = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

# ── 設定 ──────────────────────────────────────────────────────────────────
ARNIS_EXE = r"C:\Users\my117\OneDrive\デスクトップ\フリー\arnis-windows.exe"
BBOX = {
    "min_lat": 35.665560, "max_lat": 35.672585,
    "min_lon": 139.743453, "max_lon": 139.756280,
}
OUT_DIR = os.path.join(ROOT, "test_e2e_out")
os.makedirs(OUT_DIR, exist_ok=True)

OSM_RAW    = os.path.join(OUT_DIR, "osm_raw.json")
OSM_MERGED = os.path.join(OUT_DIR, "osm_merged.json")
MCWORLD_OUT = OUT_DIR

# ── ユーティリティ ─────────────────────────────────────────────────────────
def step(name):
    print(f"\n{'='*60}")
    print(f"[STEP] {name}")
    print(f"{'='*60}")
    return time.time()

def elapsed(t0):
    return f"{time.time() - t0:.1f}s"

def fail(msg):
    print(f"\n[FAIL] {msg}")
    sys.exit(1)

# ── STEP 1: arnis 生成 ─────────────────────────────────────────────────────
t0 = step("1. arnis (bedrock=False) でJava版ワールドを生成")

# 既存の出力ワールドがあればスキップ（再生成コスト節約）
existing_worlds = sorted(
    [d for d in os.scandir(OUT_DIR)
     if d.is_dir() and d.name not in ("__pycache__",)
     and os.path.isdir(os.path.join(d.path, "region"))],
    key=lambda d: d.stat().st_mtime, reverse=True,
) if os.path.isdir(OUT_DIR) else []

if existing_worlds:
    world_path = existing_worlds[0].path
    gen_time = "0.0s (既存ワールドを再利用)"
    print(f"  [SKIP] 既存ワールドを再利用: {world_path}")
else:
    from arnis_launcher import ArnisLauncher
    launcher = ArnisLauncher()
    launcher.launch(
        ARNIS_EXE,
        bbox=BBOX,
        output_dir=OUT_DIR,
        bedrock=False,
        save_json_path=OSM_RAW,
    )
    ok = launcher.wait_for_complete(timeout=3600)
    # "Saving world..." 検出後もプロセスが書き込みを終えるまで待つ
    launcher.wait_until_exit(timeout=120)
    gen_time = elapsed(t0)
    if not ok and launcher.process.returncode != 0:
        fail(f"arnis タイムアウトまたは失敗 ({gen_time})")

    # ログ表示（Unicode 安全）
    logs = launcher.get_logs()
    for line in logs:
        try:
            print(f"  arnis> {line}")
        except Exception:
            print(f"  arnis> [non-printable line]")

    world_path = launcher.world_path
    if not world_path or not os.path.isdir(world_path):
        subdirs = sorted(
            [d for d in os.scandir(OUT_DIR)
             if d.is_dir() and d.name not in ("__pycache__",)
             and os.path.isdir(os.path.join(d.path, "region"))],
            key=lambda d: d.stat().st_mtime, reverse=True
        )
        world_path = subdirs[0].path if subdirs else None

if not world_path or not os.path.isdir(world_path):
    fail(f"Java版ワールドフォルダが見つかりません")

print(f"\n  Java world: {world_path}")
print(f"  所要時間: {gen_time}")

# ワールド内容確認
region_dir = os.path.join(world_path, "region")
has_region = os.path.isdir(region_dir)
mca_files = list(f for f in os.listdir(region_dir) if f.endswith(".mca")) if has_region else []
has_leveldat = os.path.isfile(os.path.join(world_path, "level.dat"))
print(f"  region/ : {'あり' if has_region else 'なし'}  ({len(mca_files)} .mca ファイル)")
print(f"  level.dat: {'あり' if has_leveldat else 'なし'}")
if not has_region or not mca_files:
    fail("Java Edition ワールドに region/*.mca がありません")

# ── STEP 2: GSI 統合 ────────────────────────────────────────────────────────
t0 = step("2. GSI建物データ統合")

from gsi_merge import merge_gsi_into_osm_json
if os.path.exists(OSM_RAW):
    gsi_result = merge_gsi_into_osm_json(OSM_RAW, BBOX, OSM_MERGED)
    print(f"  OSM建物: {gsi_result['osm_buildings']}棟")
    print(f"  GSI建物: {gsi_result['gsi_buildings']}棟")
    print(f"  合計:    {gsi_result['total']}棟")
    print(f"  所要時間: {elapsed(t0)}")
else:
    print(f"  [SKIP] osm_raw.json が見つかりません: {OSM_RAW}")

# ── STEP 3: PLATEAU 補正 ────────────────────────────────────────────────────
t0 = step("3. PLATEAU高さ補正")

from plateau_height_merge import build_height_corrections
from world_height_writer import apply_height_corrections

plateau_source = OSM_MERGED if os.path.exists(OSM_MERGED) else OSM_RAW
metadata_path  = os.path.join(world_path, "metadata.json")

metadata = {}
if os.path.isfile(metadata_path):
    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    print(f"  metadata.json: あり（{len(metadata)}キー）")
else:
    print(f"  metadata.json: なし → PLATEAU補正はスキップ")

corrections_count = 0
plateau_result = None
if os.path.exists(plateau_source) and metadata:
    with open(plateau_source, "r", encoding="utf-8") as f:
        osm_data = json.load(f)
    corrections = build_height_corrections(BBOX, osm_data, metadata)
    corrections_count = len(corrections)
    print(f"  PLATEAU対応建物: {corrections_count}棟")

    if corrections:
        plateau_result = apply_height_corrections(world_path, corrections)
        print(f"  補正完了: {plateau_result['corrected']}棟 / エラー: {plateau_result.get('errors',0)}棟")
    else:
        print(f"  補正対象なし")
else:
    print(f"  [SKIP] source={os.path.exists(plateau_source)}, metadata={bool(metadata)}")
print(f"  所要時間: {elapsed(t0)}")

# ── STEP 4: Chunker 変換 ────────────────────────────────────────────────────
t0 = step("4. Chunker CLI で Java → Bedrock 変換")

from chunker_converter import convert_java_to_bedrock

def progress(msg):
    print(f"  chunker> {msg}")

chunker_start = time.time()
conv = convert_java_to_bedrock(
    world_path,
    OUT_DIR,
    bedrock_version="BEDROCK_1_21_0",
    progress_callback=progress,
)
chunker_time = time.time() - chunker_start

print(f"\n  変換結果: success={conv['success']}")
print(f"  所要時間: {chunker_time:.1f}s")

if not conv["success"]:
    print(f"  [WARN] 変換失敗: {conv['error']}")
    bedrock_dir = None
else:
    bedrock_dir = conv["output_path"]
    # db/ 内容確認
    db_dir = os.path.join(bedrock_dir, "db")
    if os.path.isdir(db_dir):
        ldb = [f for f in os.listdir(db_dir) if f.endswith(".ldb")]
        log = [f for f in os.listdir(db_dir) if f.endswith(".log")]
        print(f"  db/ .ldb: {len(ldb)}件  .log: {len(log)}件")
    total_bytes = sum(
        os.path.getsize(os.path.join(r, fn))
        for r, _, fs in os.walk(bedrock_dir)
        for fn in fs
    )
    print(f"  Bedrockフォルダ合計: {total_bytes//1024//1024} MB")
    print(f"  Bedrock構造:")
    for item in sorted(os.listdir(bedrock_dir)):
        p = os.path.join(bedrock_dir, item)
        if os.path.isdir(p):
            sub = len(os.listdir(p))
            print(f"    {item}/  ({sub} items)")
        else:
            print(f"    {item}  ({os.path.getsize(p)//1024} KB)")

# ── STEP 5: mcworld 化 ──────────────────────────────────────────────────────
t0 = step("5. .mcworld ZIP化")

if bedrock_dir:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    world_name = os.path.basename(bedrock_dir.rstrip("/\\"))
    mcworld_path = os.path.join(MCWORLD_OUT, f"{world_name}_{ts}.mcworld")

    _STORED_EXTS = {".ldb", ".log"}
    stored_count = deflated_count = 0
    with zipfile.ZipFile(mcworld_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(bedrock_dir):
            for fn in files:
                fp = os.path.join(root, fn)
                arcname = os.path.relpath(fp, bedrock_dir)
                ext = os.path.splitext(fn)[1].lower()
                compress = zipfile.ZIP_STORED if ext in _STORED_EXTS else zipfile.ZIP_DEFLATED
                if compress == zipfile.ZIP_STORED:
                    stored_count += 1
                else:
                    deflated_count += 1
                zf.write(fp, arcname, compress_type=compress)

    mcworld_size = os.path.getsize(mcworld_path)
    print(f"  .mcworld: {mcworld_path}")
    print(f"  ファイルサイズ: {mcworld_size//1024//1024} MB")
    print(f"  ZIP_STORED (.ldb/.log): {stored_count}件")
    print(f"  ZIP_DEFLATED (その他): {deflated_count}件")
    print(f"  所要時間: {elapsed(t0)}")

    # ZIP 内容検査
    print("\n  [ZIP内部構造]")
    with zipfile.ZipFile(mcworld_path, "r") as zf:
        infos = zf.infolist()
        print(f"  総エントリ数: {len(infos)}")
        db_entries = [i for i in infos if "db/" in i.filename or i.filename.startswith("db")]
        other_top = [i.filename for i in infos if "/" not in i.filename.rstrip("/")]
        print(f"  db/ エントリ数: {len(db_entries)}")
        print(f"  ルートファイル: {other_top[:10]}")
        # .ldb が STORED になっているか確認
        ldb_entries = [i for i in infos if i.filename.endswith(".ldb")]
        if ldb_entries:
            sample = ldb_entries[0]
            compress_type = "STORED" if sample.compress_type == zipfile.ZIP_STORED else "DEFLATED"
            print(f"  .ldb 圧縮方式サンプル ({sample.filename}): {compress_type}")
else:
    print("  Bedrock変換が失敗したためmcworld化をスキップ")
    mcworld_path = None

# ── サマリー ────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("[SUMMARY]")
print(f"{'='*60}")
print(f"  Java world:    {world_path}")
print(f"  region/*.mca:  {len(mca_files)} ファイル")
print(f"  PLATEAU補正:   {corrections_count}棟対象 / {plateau_result['corrected'] if plateau_result else 0}棟完了")
print(f"  Chunker変換:   {'成功' if conv['success'] else '失敗'} ({chunker_time:.1f}s)")
print(f"  .mcworld:      {mcworld_path if mcworld_path else 'なし'}")
print(f"\n  arnis生成所要時間:   {gen_time}")
print(f"  Chunker変換所要時間: {chunker_time:.1f}s")
