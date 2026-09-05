# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

block_cipher = None
root = Path.cwd()

# Flask 页面资源和外部工具。ffmpeg.exe / yt-dlp.exe 是被 subprocess 调用的
# 外部程序，按 data files 打包即可，运行时会保留原文件名和目录结构。
datas = [
    (str(root / "fb_collector" / "templates"), "fb_collector/templates"),
    (str(root / "fb_collector" / "static"), "fb_collector/static"),
    (str(root / "README.md"), "."),
    (str(root / "用户使用说明.md"), "."),
]
if (root / "google_credentials.json").exists():
    datas.append((str(root / "google_credentials.json"), "."))
if (root / "tools").exists():
    datas.append((str(root / "tools"), "tools"))

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["pystray", "pystray._win32", "PIL", "PIL.Image", "PIL.ImageDraw"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 当前 Python 环境安装了大量科学计算/AI 包。pytesseract 的可选依赖探测
    # 会让 PyInstaller 把它们全部带入，但本程序只使用 image_to_string，完全不需要。
    excludes=[
        "cv2",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "tensorboard",
        "torch",
        "torchaudio",
        "torchvision",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
icon_file = root / "fb_collector" / "static" / "app.ico"
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FBPostCollector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if icon_file.exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FBPostCollector",
)
