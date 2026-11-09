# Resident process: tray icon + watchdog loop + a localhost page for the UI window.
import ctypes
import json
import secrets
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pystray

from . import autostart
from .config import APP_DIR, Config
from .icons import PNG, tray_icon
from .watchdog import Watchdog

UI_HTML = Path(__file__).with_name("ui.html")
LOG = APP_DIR / "events.log"
URL_FILE = APP_DIR / "url"
TITLE = "CC Watchdog"
TICK = 2
ORDER = ["waiting", "paused", "blocked", "sent", "running", "idle", "unsupported"]
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def open_window(url, proc=None):
    hwnd = user32.FindWindowW(None, TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return proc
    if proc and proc.poll() is None:  # still starting up
        return proc
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return subprocess.Popen([str(pythonw), "-m", "ccwatchdog.window", url], cwd=autostart.ROOT)


class App:
    def __init__(self):
        self.config = Config.load()
        self.events = deque(self._load_events(), maxlen=200)
        self.dog = Watchdog(self.config, self.event)
        self.token = secrets.token_urlsafe(16)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}/?t={self.token}"
        self.window = None
        self.running = True
        self._wake = threading.Event()
        self.icon = pystray.Icon("cc-watchdog", tray_icon(self.config.enabled), TITLE, menu=pystray.Menu(
            pystray.MenuItem("打开", self.show, default=True),
            pystray.MenuItem("自动继续", lambda: self.set_enabled(not self.config.enabled),
                             checked=lambda _: self.config.enabled),
            pystray.MenuItem("开机自启", lambda: self.set_autostart(not autostart.enabled()),
                             checked=lambda _: autostart.enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self.quit),
        ))

    def _load_events(self):
        try:
            lines = LOG.read_text(encoding="utf-8").splitlines()[-200:]
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out

    def event(self, text, warn=False):
        e = {"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "text": text, "warn": warn}
        self.events.append(e)
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        if warn:
            self.icon.notify(text, TITLE)

    def state(self):
        rows = sorted(self.dog.snapshot(), key=lambda r: (ORDER.index(r.kind), r.session.name))
        today = datetime.now().strftime("%Y-%m-%d")
        cfg = self.config
        return {
            "enabled": cfg.enabled,
            "autostart": autostart.enabled(),
            "delay": cfg.delay, "max_tries": cfg.max_tries, "window": cfg.window,
            "sessions": [{"id": r.session.session_id, "name": r.session.name, "cwd": r.session.cwd,
                          "state": r.state, "kind": r.kind, "cutoff": r.cutoff, "count": r.count,
                          "last": r.last_continue, "protected": r.session.session_id not in cfg.disabled}
                         for r in rows],
            "events": list(self.events)[::-1][:80],
            "today": sum(1 for e in self.events if e["t"].startswith(today) and "发送“" in e["text"]),
        }

    def act(self, a):
        action, sid, value = a.get("action"), a.get("id"), bool(a.get("value"))
        if action == "enabled":
            self.set_enabled(value)
        elif action == "autostart":
            self.set_autostart(value)
        elif action == "protect":
            disabled = set(self.config.disabled)
            (disabled.discard if value else disabled.add)(sid)
            live = {r.session.session_id for r in self.dog.snapshot()}
            self.config.disabled = sorted(disabled & live)
            self.config.save()
            self._wake.set()
        elif action == "continue":
            self.dog.continue_now(sid)
            self._wake.set()

    def set_enabled(self, on):
        self.config.enabled = on
        self.config.save()
        self.icon.icon = tray_icon(on)
        self.icon.update_menu()
        self._wake.set()

    def set_autostart(self, on):
        autostart.set_enabled(on)
        self.icon.update_menu()

    def _handler(app):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _authorized(self):
                if parse_qs(urlparse(self.path).query).get("t", [""])[0] == app.token:
                    return True
                self.send_error(403)
                return False

            def _send(self, body, ctype="application/json; charset=utf-8"):
                data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/icon.png":
                    return self._send(PNG.read_bytes(), "image/png")
                if not self._authorized():
                    return
                if path == "/":
                    self._send(UI_HTML.read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/state":
                    self._send(app.state())
                else:
                    self.send_error(404)

            def do_POST(self):
                if not self._authorized():
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                app.act(json.loads(body or b"{}"))
                self._send(app.state())

        return Handler

    def loop(self):
        last_error = None
        while self.running:
            try:
                self.dog.tick()
                rows = self.dog.snapshot()
                self.icon.title = f"{TITLE} · {len(rows)} 个会话" + ("" if self.config.enabled else "（已暂停）")
                last_error = None
            except Exception as e:
                if repr(e) != last_error:
                    last_error = repr(e)
                    self.event(f"巡检出错：{e!r}", warn=True)
            self._wake.wait(TICK)
            self._wake.clear()

    def show(self):
        self.window = open_window(self.url, self.window)

    def quit(self):
        self.running = False
        self._wake.set()
        if self.window and self.window.poll() is None:
            self.window.terminate()
        URL_FILE.unlink(missing_ok=True)
        self.server.shutdown()
        self.icon.stop()

    def run(self, show):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        URL_FILE.write_text(self.url, encoding="utf-8")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self.loop, daemon=True).start()
        if show:
            self.show()
        self.icon.run()


def main():
    if "--install" in sys.argv:  # Start Menu entry + start with Windows
        autostart.make_shortcut(autostart.MENU_LINK, "-m ccwatchdog")
        autostart.set_enabled(True)
    kernel32.CreateMutexW(None, False, "Local\\cc-watchdog")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: just open the running instance's window
        try:
            open_window(URL_FILE.read_text(encoding="utf-8"))
        except OSError:
            pass
        return
    App().run(show="--hidden" not in sys.argv)
