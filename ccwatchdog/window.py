# The UI window, run as its own process so the WebView2 engine only lives while the window is open.
import ctypes
import sys

import webview

from .icons import ICO

TITLE = "CC Watchdog"

if __name__ == "__main__":
    # own taskbar identity, so Windows shows our icon instead of grouping it with Python
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("cc-watchdog")
    webview.create_window(TITLE, sys.argv[1], width=1040, height=720, min_size=(780, 540),
                          background_color="#F0EEE6")
    webview.start(icon=str(ICO))
