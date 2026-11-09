import json
from dataclasses import dataclass
from pathlib import Path

import psutil

CLAUDE_DIR = Path.home() / ".claude"


@dataclass
class Session:
    pid: int
    session_id: str
    name: str
    cwd: str
    status: str
    status_since: float
    entrypoint: str


def _alive(pid, proc_start):
    try:
        created = psutil.Process(pid).create_time()
    except psutil.Error:
        return False
    if not proc_start:
        return True
    # procStart is a Windows FILETIME: 100ns ticks since 1601-01-01
    return abs(int(proc_start) / 1e7 - 11644473600 - created) < 2


def live_sessions(claude_dir=CLAUDE_DIR):
    out = []
    for f in (claude_dir / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("kind") != "interactive" or not _alive(d.get("pid", 0), d.get("procStart")):
            continue
        out.append(Session(
            pid=d["pid"],
            session_id=d["sessionId"],
            name=d.get("name") or d["sessionId"][:8],
            cwd=d.get("cwd", ""),
            status=d.get("status", ""),
            status_since=d.get("statusUpdatedAt", d.get("updatedAt", 0)) / 1000,
            entrypoint=d.get("entrypoint", ""),
        ))
    return sorted(out, key=lambda s: s.name)
