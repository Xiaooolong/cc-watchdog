import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from . import console
from .sessions import CLAUDE_DIR, live_sessions
from .transcript import find_transcript, is_cutoff, last_turn_record

IDLE = ("idle", "shell")  # "shell": turn ended, a background shell is still running
MAX_WAIT = 180  # backoff cap per attempt, so all tries fit in the window
SENT_GRACE = 15  # seconds to wait for the sent message to show up in the transcript


@dataclass
class Row:
    session: object
    protected: bool
    state: str
    kind: str = "idle"
    cutoff: bool = False
    last_continue: float = 0
    count: int = 0


class Watchdog:
    def __init__(self, config, on_event, claude_dir=CLAUDE_DIR,
                 list_sessions=live_sessions, term=console, clock=time.time):
        self.config = config
        self.on_event = on_event  # on_event(text, warn=False)
        self.claude_dir = claude_dir
        self.list_sessions = list_sessions
        self.term = term
        self.clock = clock
        self.rows = {}
        self._lock = threading.Lock()
        self._manual = set()
        self._paths = {}
        self._tails = {}
        self._attempts = {}   # session id -> deque of attempt times
        self._last = {}       # session id -> last attempt time
        self._count = {}      # session id -> total messages sent
        self._warned = set()  # (session id, cut-off uuid, tag) already reported

    def snapshot(self):
        with self._lock:
            return list(self.rows.values())

    def continue_now(self, session_id):
        with self._lock:
            self._manual.add(session_id)

    def tick(self):
        rows = {}
        for s in self.list_sessions(self.claude_dir):
            rows[s.session_id] = self._evaluate(s, self._last_record(s.session_id))
        with self._lock:
            self.rows = rows
            self._manual &= rows.keys()

    def _last_record(self, sid):
        path = self._paths.get(sid)
        if path is None or not path.exists():
            path = self._paths[sid] = find_transcript(sid, self.claude_dir)
        if path is None:
            return None
        try:
            st = path.stat()
            key = (st.st_size, st.st_mtime_ns)
            cached = self._tails.get(sid)
            if cached and cached[0] == key:
                return cached[1]
            rec = last_turn_record(path)
        except OSError:
            return None
        self._tails[sid] = (key, rec)
        return rec

    def _evaluate(self, s, rec):
        cfg, now, sid = self.config, self.clock(), s.session_id
        protected = cfg.enabled and sid not in cfg.disabled
        row = Row(s, protected, "", last_continue=self._last.get(sid, 0), count=self._count.get(sid, 0))

        def mark(state, kind):
            row.state, row.kind = state, kind
            return row

        if not is_cutoff(rec):
            return mark(*{"busy": ("运行中", "running"), "idle": ("空闲", "idle"),
                          "shell": ("空闲（后台命令在跑）", "idle")}.get(s.status, (s.status or "未知", "idle")))
        row.cutoff = True
        if s.entrypoint != "cli":
            return mark("断线（非终端会话，无法自动继续）", "unsupported")

        manual = sid in self._manual
        attempts = self._attempts.setdefault(sid, deque())
        while attempts and now - attempts[0] > cfg.window:
            attempts.popleft()
        uuid = rec.get("uuid")
        cut_at = datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00")).timestamp()
        last = self._last.get(sid, 0)

        if last > cut_at and now - last < SENT_GRACE:
            return mark(f"已发送“{cfg.message}”", "sent")
        if not manual:
            if not protected:
                return mark("断线（未保护）", "blocked")
            if now - cut_at > cfg.max_age:
                return mark("断线（较早，未自动继续）", "blocked")
            if len(attempts) >= cfg.max_tries:
                self._report(sid, uuid, "paused", f"{s.name}：{cfg.window // 60} 分钟内已断 {len(attempts)} 次，先暂停自动继续", warn=True)
                return mark("连续断线，暂停自动继续", "paused")
            wait = min(cfg.delay * 3 ** len(attempts), MAX_WAIT) - (now - max(cut_at, last))
            if wait > 0:
                return mark(f"断线，{int(wait) + 1} 秒后继续", "waiting")

        try:
            empty = self.term.prompt_is_empty(self.term.read_screen(s.pid))
            if empty is False:
                return mark("断线，输入框里有内容，暂不发送", "blocked")
            # not idle (e.g. background agents running): only type when the input box is clearly there,
            # so Enter can't land on some dialog
            if empty is None and s.status not in IDLE:
                return mark("断线，没认出输入框，暂不发送", "blocked")
            self.term.type_line(s.pid, cfg.message)
        except OSError as e:
            self._report(sid, uuid, "oserror", f"{s.name}：发送失败（{e}）")
            return mark("断线，连不上它的终端", "blocked")

        attempts.append(now)
        self._last[sid] = row.last_continue = now
        self._count[sid] = row.count = row.count + 1
        with self._lock:
            self._manual.discard(sid)
        self.on_event(f"{s.name}：{'手动' if manual else '自动'}发送“{cfg.message}”")
        return mark(f"已发送“{cfg.message}”", "sent")

    def _report(self, sid, uuid, tag, text, warn=False):
        if (sid, uuid, tag) not in self._warned:
            self._warned.add((sid, uuid, tag))
            self.on_event(text, warn=warn)
