"""Build dist/AudioPatch/AudioPatch.exe: rebuilds the dashboard, then runs PyInstaller.

    python build_exe.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"


def main() -> None:
    py = str(VENV_PY if VENV_PY.exists() else sys.executable)
    npm = shutil.which("npm")
    if not npm:
        sys.exit("npm not found: install Node.js to build the dashboard")
    frontend = ROOT / "frontend"
    if not (frontend / "node_modules").exists():
        subprocess.check_call([npm, "install"], cwd=frontend)
    subprocess.check_call([npm, "run", "build"], cwd=frontend)
    subprocess.check_call([py, "-m", "PyInstaller", "--noconfirm", "--clean", "audiopatch.spec"], cwd=ROOT)
    exe = ROOT / "dist" / "AudioPatch" / "AudioPatch.exe"
    size = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file()) / 1e6
    print(f"\nBuilt {exe} ({size:.0f} MB folder)")


if __name__ == "__main__":
    main()
