# PyInstaller build spec — produces a single-file GUI executable.
#
#     pyinstaller elaris-importer.spec
#
# No browser is bundled: the map renderer shells out to whatever Chrome, Edge
# or Chromium is already installed, and Edge ships with Windows 10 and 11.

import sys

block_cipher = None

a = Analysis(
    ["gui.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=["elaris_import.pipeline"],
    hookspath=[],
    runtime_hooks=[],
    # Trimmed to keep the binary small; none of these are imported at runtime.
    excludes=[
        "matplotlib", "numpy", "pandas", "PIL", "scipy",
        "pytest", "playwright", "cairosvg", "IPython",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ElarisImporter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # A GUI app: no console window on Windows, but keep one on Linux/macOS
    # where the terminal is where you launched it from anyway.
    console=sys.platform not in ("win32", "darwin"),
    disable_windowed_traceback=False,
    icon=None,
)
