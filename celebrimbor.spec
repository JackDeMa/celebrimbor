# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

import os

# Both models go inside the build: the executable must start offline.
MODELS = ['models/hand_landmarker.task', 'models/face_landmarker.task']
missing = [m for m in MODELS if not os.path.isfile(m) or os.path.getsize(m) == 0]
if missing:
    raise SystemExit(
        'Missing model files: ' + ', '.join(missing) + '\n'
        'Start the app once (python main.py) to download them, '
        'or fetch them as in .github/workflows/build.yml.'
    )

datas = [(m, 'models') for m in MODELS]
datas += collect_data_files('mediapipe')
# The window's themes and fonts are JSON / font files PyInstaller cannot see.
datas += collect_data_files('customtkinter')

# matplotlib and sounddevice cannot be excluded: mediapipe.tasks imports both
# unconditionally (vision.drawing_utils, audio.core.audio_record).
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# console=True: --list-gestures, --selftest and the --no-preview log print there.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='celebrimbor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='celebrimbor',
)
