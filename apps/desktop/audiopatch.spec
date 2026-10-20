# PyInstaller build for AudioPatch.exe. Build with:  python build_exe.py
# One-folder (not one-file): starts instantly and trips antivirus less often.
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

hidden = (
    collect_submodules("uvicorn")          # uvicorn picks its loop/protocol modules at runtime
    + collect_submodules("websockets")
    + collect_submodules("winrt")          # Bluetooth (phones): WinRT projections are imported lazily
    + ["pyaudiowpatch", "pycaw.pycaw", "comtypes.stream", "pyogg"]
)

a = Analysis(
    ["run.py"],
    pathex=["."],
    datas=[("frontend/dist", "frontend/dist")],
    binaries=collect_dynamic_libs("pyogg"),  # opus.dll for streaming to phones
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AudioPatch",
    icon="assets/icon.ico",
    console=False,          # no terminal window; logs go to %APPDATA%\AudioPatch\audiopatch.log
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AudioPatch", upx=False)
