"""Launcher for both dev runs (`python run.py`) and the packaged AudioPatch.exe.

Dev: re-launches under .venv, builds the dashboard if needed, opens a browser tab.
Packaged: opens the dashboard in its own app window and quits when that window
closes. Launching a second copy just reopens the window.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
QUIT_AFTER_CLOSE_S = 8  # grace period so a page reload doesn't quit the app


def ensure_venv() -> None:
    """Re-launch under the project's .venv so a plain `python run.py` works."""
    if VENV_PY.exists() and Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        sys.exit(subprocess.call([str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]]))
    try:
        import sounddevice, fastapi, uvicorn  # noqa: F401
    except ImportError as e:
        sys.exit(f"Missing dependency ({e.name}). Set up with:\n"
                 f"  python -m venv .venv\n  .venv\\Scripts\\pip install -r requirements.txt")


def build_frontend(force: bool) -> None:
    dist = FRONTEND / "dist" / "index.html"
    src_mtime = max(p.stat().st_mtime for p in (FRONTEND / "src").rglob("*") if p.is_file())
    if not force and dist.exists() and dist.stat().st_mtime > src_mtime:
        return
    npm = shutil.which("npm")
    if not npm:
        sys.exit("npm not found: install Node.js 18+ to build the dashboard")
    if not (FRONTEND / "node_modules").exists():
        subprocess.check_call([npm, "install"], cwd=FRONTEND)
    subprocess.check_call([npm, "run", "build"], cwd=FRONTEND)


def page_url(base: str) -> str:
    """The dashboard URL tagged with this build, so a window never shows a
    dashboard cached from a previous version of the app."""
    index = (Path(getattr(sys, "_MEIPASS", ROOT)) / "frontend" / "dist" / "index.html")
    try:
        return f"{base}/?v={int(index.stat().st_mtime)}"
    except OSError:
        return base


def already_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/system", timeout=1) as r:
            return "os" in json.load(r)
    except Exception:
        return False


def open_window(url: str) -> None:
    """Open the dashboard as a chromeless app window (Edge/Chrome --app mode),
    falling back to a normal browser tab."""
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    for exe in candidates:
        if exe.exists():
            subprocess.Popen([str(exe), f"--app={url}", "--window-size=1280,900"],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            return
    webbrowser.open(url)


def setup_logging() -> Path | None:
    if not FROZEN:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        return None
    # Windowed exe: there's no console, so stdout/stderr are None. Log to a file.
    log_dir = Path(os.environ.get("APPDATA", Path.home())) / "AudioPatch"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "audiopatch.log"
    stream = open(log_file, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream
    logging.basicConfig(level=logging.INFO, stream=stream,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return log_file


def quit_when_window_closes(server) -> None:
    from backend.api.app import hub
    while not server.should_exit:
        time.sleep(1)
        if hub.ever_connected and not hub.clients and time.monotonic() - hub.idle_since > QUIT_AFTER_CLOSE_S:
            logging.info("dashboard closed; shutting down")
            server.should_exit = True


def main() -> None:
    if not FROZEN:
        ensure_venv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--no-browser", action="store_true", help="don't open any window")
    ap.add_argument("--app", action="store_true", help="app window + quit when it closes (default for the exe)")
    args = ap.parse_args()
    app_mode = args.app or FROZEN

    setup_logging()
    url = f"http://127.0.0.1:{args.port}"
    if already_running(url):
        if not args.no_browser:
            open_window(page_url(url))
        return

    if not FROZEN:
        build_frontend(args.rebuild)

    import uvicorn
    from backend.api.app import app

    # log_config=None: use our logging setup (uvicorn's default needs a console).
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_config=None))

    if not args.no_browser:
        def launch():
            while not server.started and not server.should_exit:
                time.sleep(0.05)
            if server.started:
                (open_window if app_mode else webbrowser.open)(page_url(url))
        threading.Thread(target=launch, daemon=True).start()
    if app_mode:
        threading.Thread(target=quit_when_window_closes, args=(server,), daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
