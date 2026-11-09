import json
from datetime import datetime, timezone

import pytest

from ccwatchdog.config import Config
from ccwatchdog.console import prompt_is_empty
from ccwatchdog.sessions import Session
from ccwatchdog.transcript import is_cutoff, last_turn_record
from ccwatchdog.watchdog import Watchdog

SID = "7a06e13f-0fd8-486b-a453-3d223612b197"
T0 = 1_800_000_000.0
RULE = "─" * 40


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def cutoff_record(t, uuid="err-1"):
    return {"type": "assistant", "uuid": uuid, "timestamp": iso(t), "isApiErrorMessage": True,
            "truncatedAfterOutput": True, "error": "server_error",
            "message": {"content": [{"type": "text", "text": "API Error: Connection lost mid-response. "
                                     "The response above may be incomplete."}]}}


def user_record(t, text="继续"):
    return {"type": "user", "uuid": "u-" + text, "timestamp": iso(t), "message": {"content": text}}


def write_transcript(claude_dir, records):
    d = claude_dir / "projects" / "D--x"
    d.mkdir(parents=True, exist_ok=True)
    tail = [{"type": "system", "subtype": "turn_duration"}, {"type": "file-history-snapshot"}]
    (d / f"{SID}.jsonl").write_text("\n".join(json.dumps(r) for r in records + tail) + "\n", encoding="utf-8")


# --- transcript ---

def test_last_turn_record_skips_bookkeeping(tmp_path):
    write_transcript(tmp_path, [user_record(T0, "hi"), cutoff_record(T0 + 1)])
    rec = last_turn_record(tmp_path / "projects" / "D--x" / f"{SID}.jsonl")
    assert rec["uuid"] == "err-1" and is_cutoff(rec)


def test_last_turn_record_with_partial_first_line(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(user_record(T0, "x" * 500)) + "\n" + json.dumps(cutoff_record(T0)) + "\n")
    assert last_turn_record(p, tail_bytes=300)["uuid"] == "err-1"


@pytest.mark.parametrize("text,expected", [
    ("API Error: The response stopped arriving. The response above may be incomplete.", True),
    ("API Error: Connection lost before a response was produced", True),
    ("API Error: 402 可用余额或额度不足", False),
    ("API Error: Request rejected (429) · Rate limit exceeded", False),
])
def test_is_cutoff_by_text(text, expected):
    rec = {"isApiErrorMessage": True, "message": {"content": [{"type": "text", "text": text}]}}
    assert is_cutoff(rec) is expected


def test_normal_assistant_message_is_not_cutoff():
    assert not is_cutoff({"type": "assistant", "message": {"content": [{"type": "text", "text": "mid-response"}]}})


# --- console screen parsing ---

def test_prompt_empty_and_filled():
    screen = ["● 好", RULE, "❯", RULE, "  ⏵⏵ auto mode on"]
    assert prompt_is_empty(screen) is True
    screen[2] = "❯ 我在打字"
    assert prompt_is_empty(screen) is False
    assert prompt_is_empty(["no box here"]) is None


def test_prompt_rule_with_session_name():
    screen = ["─" * 30 + " exp-画面词表-yeti ─", "❯", "─" * 40, "  ⏵⏵ bypass permissions on · 1 shell"]
    assert prompt_is_empty(screen) is True


# --- watchdog policy ---

class FakeTerm:
    def __init__(self):
        self.sent, self.screen = [], ["", RULE, "❯", RULE]

    def read_screen(self, pid):
        return self.screen

    def type_line(self, pid, text):
        self.sent.append((pid, text))

    prompt_is_empty = staticmethod(prompt_is_empty)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    session = Session(pid=42, session_id=SID, name="demo", cwd="D:\\x", status="idle",
                      status_since=T0, entrypoint="cli")
    term, clock, events = FakeTerm(), Clock(T0), []
    dog = Watchdog(Config(), lambda text, warn=False: events.append((text, warn)), claude_dir=tmp_path,
                   list_sessions=lambda _: [session], term=term, clock=clock)
    return dog, session, term, clock, events, tmp_path


def state(dog):
    return dog.snapshot()[0].state


def test_sends_continue_after_delay(env):
    dog, session, term, clock, events, root = env
    write_transcript(root, [user_record(T0 - 5, "hi"), cutoff_record(T0)])
    clock.t = T0 + 5
    dog.tick()
    assert term.sent == [] and "秒后继续" in state(dog)
    clock.t = T0 + 11
    dog.tick()
    assert term.sent == [(42, "继续")]
    clock.t = T0 + 13
    dog.tick()  # transcript not updated yet: must not send twice
    assert len(term.sent) == 1 and state(dog) == "已发送“继续”"


def test_backoff_then_pause(env):
    dog, session, term, clock, events, root = env
    t = T0
    for i, gap in enumerate([10, 30, 90, 180, 180]):
        write_transcript(root, [cutoff_record(t, f"err-{i}")])
        clock.t = t + gap - 1
        dog.tick()
        assert len(term.sent) == i
        clock.t = t + gap + 1
        dog.tick()
        assert len(term.sent) == i + 1
        t = clock.t + 20
    write_transcript(root, [cutoff_record(t, "err-5")])
    clock.t = t + 300
    dog.tick()
    assert len(term.sent) == 5 and "暂停" in state(dog)
    assert events[-1][1] is True
    clock.t = T0 + 1000  # the first attempt aged out of the 15-minute window
    dog.tick()
    assert len(term.sent) == 6


def test_skips_when_unprotected_old_busy_or_typing(env):
    dog, session, term, clock, events, root = env
    write_transcript(root, [cutoff_record(T0)])
    clock.t = T0 + 60

    dog.config.disabled = [SID]
    dog.tick()
    assert state(dog) == "中断（未保护）"
    dog.config.disabled = []

    session.status = "busy"
    term.screen = ["some dialog"]
    dog.tick()
    assert "没认出输入框" in state(dog)
    term.screen = ["", RULE, "❯", RULE]
    session.status = "shell"

    term.screen = ["", RULE, "❯ 我在打字", RULE]
    dog.tick()
    assert "输入框" in state(dog)
    term.screen = ["", RULE, "❯", RULE]

    clock.t = T0 + 7200
    dog.tick()
    assert "较早" in state(dog) and term.sent == []

    clock.t = T0 + 60  # status "shell" counts as idle
    dog.tick()
    assert term.sent == [(42, "继续")]


def test_manual_continue_bypasses_policy(env):
    dog, session, term, clock, events, root = env
    write_transcript(root, [cutoff_record(T0)])
    dog.config.disabled = [SID]
    clock.t = T0 + 1
    dog.continue_now(SID)
    dog.tick()
    assert term.sent == [(42, "继续")] and "手动" in events[-1][0]


def test_no_action_after_user_replied(env):
    dog, session, term, clock, events, root = env
    write_transcript(root, [cutoff_record(T0), user_record(T0 + 3)])
    clock.t = T0 + 60
    dog.tick()
    assert term.sent == [] and state(dog) == "空闲"


def test_continues_while_background_tasks_run(env):
    dog, session, term, clock, events, root = env
    write_transcript(root, [cutoff_record(T0)])
    session.status = "busy"
    clock.t = T0 + 60
    dog.tick()
    assert term.sent == [(42, "继续")]


def test_custom_message(env):
    dog, session, term, clock, events, root = env
    dog.config.message = "continue"
    write_transcript(root, [cutoff_record(T0)])
    clock.t = T0 + 60
    dog.tick()
    assert term.sent == [(42, "continue")] and state(dog) == "已发送“continue”"


def test_gateway_errors_from_config(env):
    dog, session, term, clock, events, root = env
    rec = cutoff_record(T0)
    del rec["truncatedAfterOutput"]
    rec["message"]["content"][0]["text"] = ("API Error: 503 This session's transferred context could not be verified. "
                                            "Start a new session and retry.")
    write_transcript(root, [rec])
    clock.t = T0 + 60
    dog.tick()
    assert term.sent == [(42, "继续")]
    dog.config.extra_errors = []
    rec["uuid"] = "err-2"
    write_transcript(root, [rec])
    clock.t = T0 + 600
    dog.tick()
    assert len(term.sent) == 1 and state(dog) == "空闲"
