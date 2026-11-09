# Talk to another process's console: read its screen, type into its input box.
# Attaching is process-wide, so every call holds _lock.
import ctypes
import ctypes.wintypes as w
import threading
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = w.HANDLE
INVALID_HANDLE = w.HANDLE(-1).value
_lock = threading.Lock()


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", w.BOOL), ("wRepeatCount", w.WORD), ("wVirtualKeyCode", w.WORD),
                ("wVirtualScanCode", w.WORD), ("uChar", w.WCHAR), ("dwControlKeyState", w.DWORD)]


class _Event(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", w.WORD), ("Event", _Event)]


class COORD(ctypes.Structure):
    _fields_ = [("X", w.SHORT), ("Y", w.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", w.SHORT), ("Top", w.SHORT), ("Right", w.SHORT), ("Bottom", w.SHORT)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", w.WORD),
                ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]


class _Attached:
    def __init__(self, pid, name):
        self.pid, self.name = pid, name

    def __enter__(self):
        _lock.acquire()
        k32.FreeConsole()
        if not k32.AttachConsole(self.pid):
            _lock.release()
            raise OSError(f"AttachConsole({self.pid}) failed: {ctypes.get_last_error()}")
        self.h = k32.CreateFileW(self.name, 0xC0000000, 3, None, 3, 0, None)
        if self.h in (None, INVALID_HANDLE):
            self.__exit__()
            raise OSError(f"open {self.name} failed: {ctypes.get_last_error()}")
        return self.h

    def __exit__(self, *exc):
        if getattr(self, "h", None) not in (None, INVALID_HANDLE):
            k32.CloseHandle(self.h)
        k32.FreeConsole()
        _lock.release()


def _key_records(text):
    recs = []
    for ch in text:
        vk, scan = (0x0D, 0x1C) if ch == "\r" else (0, 0)
        for down in (1, 0):
            r = INPUT_RECORD()
            r.EventType = 1  # KEY_EVENT
            e = r.Event.KeyEvent
            e.bKeyDown, e.wRepeatCount, e.wVirtualKeyCode, e.wVirtualScanCode, e.uChar = down, 1, vk, scan, ch
            recs.append(r)
    return (INPUT_RECORD * len(recs))(*recs), len(recs)


def type_line(pid, text):
    with _Attached(pid, "CONIN$") as h:
        n = w.DWORD()
        # Enter goes separately, otherwise the burst can be taken as a paste and Enter becomes a newline
        for part in (text, "\r"):
            arr, count = _key_records(part)
            if not k32.WriteConsoleInputW(h, arr, count, ctypes.byref(n)):
                raise OSError(f"WriteConsoleInputW failed: {ctypes.get_last_error()}")
            time.sleep(0.3)


def read_screen(pid):
    with _Attached(pid, "CONOUT$") as h:
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not k32.GetConsoleScreenBufferInfo(h, ctypes.byref(info)):
            raise OSError(f"GetConsoleScreenBufferInfo failed: {ctypes.get_last_error()}")
        width, lines = info.dwSize.X, []
        for y in range(info.srWindow.Top, info.srWindow.Bottom + 1):
            buf = ctypes.create_unicode_buffer(width)
            n = w.DWORD()
            k32.ReadConsoleOutputCharacterW(h, buf, width, COORD(0, y), ctypes.byref(n))
            lines.append(buf.value[:n.value].rstrip())
        return lines


def prompt_is_empty(lines):
    # Claude Code draws the input box between two full-width "─" rules (the top one may carry the
    # session name). Returns True/False, or None if the box isn't found.
    rules = [i for i, line in enumerate(lines) if line.count("─") >= 20]
    if len(rules) < 2:
        return None
    body = "\n".join(lines[rules[-2] + 1:rules[-1]]).strip()
    return body.lstrip("❯").strip() == ""
