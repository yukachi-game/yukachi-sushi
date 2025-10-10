# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules
from PyInstaller.building.build_main import Analysis, PYZ, EXE
from PyInstaller.building.datastruct import Tree

# 安全なベースディレクトリ解決
try:
    SPEC_DIR = Path(__file__).resolve().parent
except NameError:
    SPEC_DIR = Path.cwd().resolve()

PROJECT_DIR = (SPEC_DIR / "yukachi_sushi").resolve()
GAME_PATH   = PROJECT_DIR / "game.py"
ASSETS_DIR  = PROJECT_DIR / "assets"

if not GAME_PATH.exists():
    raise SystemExit(f"[spec error] game.py not found at: {GAME_PATH}")
if not ASSETS_DIR.exists():
    raise SystemExit(f"[spec error] assets not found at: {ASSETS_DIR}")

# OpenCV 依存収集（cv2 の DLL と隠し依存）
cv2_bins   = collect_dynamic_libs('cv2')
cv2_hidden = collect_submodules('cv2')

# Analysis には datas を渡さない（空のまま）
a = Analysis(
    [str(GAME_PATH)],
    pathex=[str(PROJECT_DIR)],
    binaries=cv2_bins,      # pygame はフックに任せる
    datas=[],
    hiddenimports=cv2_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(SPEC_DIR / "runtime_chdir.py")],
    excludes=[],
    noarchive=False
)

# ここで a.datas を 3タプルの TOC で拡張
# assets/ はツリーごと（Tree は 3タプルTOCを返す）
a.datas += Tree(str(ASSETS_DIR), prefix='assets')

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,   # 3要素タプルTOCのみ
    [],
    name='yukachi_sushi_game',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False  # デバッグしたいとき True
)
