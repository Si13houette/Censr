# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec для Censr.

Собирает one-folder сборку с двумя exe в одной папке:
  Censr.exe      — GUI, без консоли (запускается двойным кликом)
  Censr-cli.exe  — CLI, с консолью (для скриптов и пакетной обработки)

Модель (models/) и ffmpeg/ кладутся рядом с .exe не здесь, а в build.bat
после сборки — так spec остаётся лёгким, а большие бинарники не гоняются
через анализ PyInstaller.

Запуск:  pyinstaller --clean --noconfirm packaging\censr.spec
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_dynamic_libs

PKG_DIR = os.path.abspath(SPECPATH)               # ...\packaging
ROOT = os.path.dirname(PKG_DIR)                    # корень репозитория
ICON = os.path.join(ROOT, "censr.ico")
VERSION = os.path.join(PKG_DIR, "version_info.txt")

# --- сбор зависимостей с данными/бинарниками ---
datas, binaries, hiddenimports = [], [], []

for pkg in ("onnx_asr", "pymorphy3_dicts_ru"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

hiddenimports += collect_submodules("censr")
hiddenimports += collect_submodules("pymorphy3")
binaries += collect_dynamic_libs("onnxruntime")    # провайдеры onnxruntime (.dll)
hiddenimports += ["onnxruntime"]

_common = dict(
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)

a_gui = Analysis([os.path.join(PKG_DIR, "launch_gui.py")], **_common)
a_cli = Analysis([os.path.join(PKG_DIR, "launch_cli.py")], **_common)

pyz_gui = PYZ(a_gui.pure)
pyz_cli = PYZ(a_cli.pure)

exe_gui = EXE(
    pyz_gui, a_gui.scripts, [],
    exclude_binaries=True,
    name="Censr",
    console=False,                 # GUI — без чёрного окна консоли
    icon=ICON,
    version=VERSION,
    disable_windowed_traceback=False,
)

exe_cli = EXE(
    pyz_cli, a_cli.scripts, [],
    exclude_binaries=True,
    name="Censr-cli",
    console=True,                  # CLI — с консолью
    icon=ICON,
    version=VERSION,
)

coll = COLLECT(
    exe_gui, a_gui.binaries, a_gui.datas,
    exe_cli, a_cli.binaries, a_cli.datas,
    strip=False,
    upx=False,                     # UPX часто триггерит антивирусы — выключено
    name="Censr",
)
