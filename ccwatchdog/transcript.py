import json
import re

CUTOFF_TEXT = re.compile(
    r"mid-response|response stopped arriving|never arrived|stream was malformed"
    r"|before a response was produced"
)


def find_transcript(session_id, claude_dir):
    hits = list((claude_dir / "projects").glob(f"*/{session_id}.jsonl"))
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def last_turn_record(path, tail_bytes=256 * 1024):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        lines = f.read().split(b"\n")
    if size > tail_bytes:
        lines = lines[1:]  # first line is probably cut in half
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if r.get("type") in ("user", "assistant") and not r.get("isMeta") and not r.get("isSidechain"):
            return r
    return None


def is_cutoff(record):
    if not record or not record.get("isApiErrorMessage"):
        return False
    if record.get("truncatedAfterOutput"):
        return True
    content = (record.get("message") or {}).get("content") or []
    text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return bool(CUTOFF_TEXT.search(text))
