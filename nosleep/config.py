"""Config load/save: ~/.config/nosleep/config.toml.

Read with stdlib ``tomllib``; written with a tiny hand-rolled dumper so we
don't pull in a dependency for five flat keys.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import asdict, dataclass, replace
from datetime import time
from pathlib import Path

log = logging.getLogger(__name__)


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "nosleep"


def config_path() -> Path:
    return config_dir() / "config.toml"


def parse_time(s: str) -> time:
    """Parse 'HH:MM' (24h). '24:00' is accepted and normalised to 00:00."""
    s = s.strip()
    if s == "24:00":
        return time(0, 0)
    parts = s.split(":")
    if len(parts) != 2:
        raise ValueError(f"bad time {s!r}, expected HH:MM")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"bad time {s!r}, expected HH:MM")
    return time(h, m)


@dataclass(frozen=True)
class Config:
    start: time = time(9, 0)
    end: time = time(0, 0)
    enabled: bool = True
    inhibit_screen: bool = False
    notify: bool = False

    def with_(self, **kw) -> "Config":
        return replace(self, **kw)

    def to_toml(self) -> str:
        lines = [
            "# nosleep configuration — edited by the Settings dialog, hand-edits are fine too.",
            "# Times are local 24h wall clock. end == start means 'always awake'.",
            f'start = "{self.start.strftime("%H:%M")}"',
            f'end = "{self.end.strftime("%H:%M")}"',
            f"enabled = {_b(self.enabled)}",
            f"inhibit_screen = {_b(self.inhibit_screen)}",
            f"notify = {_b(self.notify)}",
        ]
        return "\n".join(lines) + "\n"

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        defaults = cls()
        kw: dict = {}
        for key in ("start", "end"):
            if key in d:
                try:
                    kw[key] = parse_time(str(d[key]))
                except ValueError as e:
                    log.warning("config: %s — using default", e)
        for key in ("enabled", "inhibit_screen", "notify"):
            if key in d:
                kw[key] = bool(d[key])
        return replace(defaults, **kw)


def _b(v: bool) -> str:
    return "true" if v else "false"


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return Config()
    except (OSError, tomllib.TOMLDecodeError) as e:
        log.error("config: cannot read %s (%s) — using defaults", path, e)
        return Config()
    return Config.from_dict(data)


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(cfg.to_toml(), encoding="utf-8")
    os.replace(tmp, path)
    log.info("config saved to %s: %s", path, asdict(cfg))
