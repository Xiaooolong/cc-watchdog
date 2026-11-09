import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "cc-watchdog"


@dataclass
class Config:
    enabled: bool = True
    message: str = "继续"    # what gets typed into a cut-off session
    disabled: list = field(default_factory=list)  # session ids with protection turned off
    delay: int = 10          # seconds to wait after a cut-off before the first message
    max_tries: int = 3       # attempts per session within `window` before pausing
    window: int = 900
    max_age: int = 3600      # cut-offs older than this (e.g. found at startup) are left alone

    @classmethod
    def load(cls):
        try:
            data = json.loads((APP_DIR / "config.json").read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        (APP_DIR / "config.json").write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
