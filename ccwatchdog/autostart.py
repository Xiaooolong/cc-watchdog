import os
import subprocess
import sys
from pathlib import Path

from .icons import ICO

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
LINK = PROGRAMS / "Startup/CC Watchdog.lnk"
MENU_LINK = PROGRAMS / "CC Watchdog.lnk"


def make_shortcut(link, args):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
          f"$s.TargetPath='{pythonw}';$s.Arguments='{args}';$s.IconLocation='{ICO},0';"
          f"$s.Description='Auto-continue Claude Code after a dropped connection';$s.WorkingDirectory='{ROOT}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def enabled():
    return LINK.exists()


def set_enabled(on):
    if on:
        make_shortcut(LINK, "-m ccwatchdog --hidden")
    else:
        LINK.unlink(missing_ok=True)
