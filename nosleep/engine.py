"""Schedule engine: ties the pure schedule maths to the inhibitor and a timer.

Re-evaluates on a single-shot timer armed for the next transition but capped
at 60 s — QTimer doesn't survive suspend and the wall clock can jump (NTP,
resume after midnight), so a cheap once-a-minute re-check is the robust
answer. Also re-evaluates on settings change, when PowerDevil reappears, and
on resume from sleep.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import SLOT, QObject, QTimer, Signal, Slot
from PySide6.QtDBus import QDBusConnection

from . import config as cfgmod
from .config import Config
from .inhibitor import Inhibitor
from .schedule import Override, State, desired_state, fmt_time, fmt_when, fmt_window, next_transition

log = logging.getLogger(__name__)

TICK_CAP_S = 60

MODE_AUTO = "auto"
MODE_AWAKE = "awake"
MODE_SLEEP = "sleep"


class Engine(QObject):
    #: emitted after every evaluation (tooltip/menu refresh)
    updated = Signal()
    #: emitted only when the effective awake state flips (bool new_awake)
    flipped = Signal(bool)

    def __init__(self, cfg: Config, inhibitor: Inhibitor, parent: QObject | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.inhibitor = inhibitor
        self.override: Override | None = None
        self.state: State = State(False, "disabled", None)
        self._last_awake: bool | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.evaluate)

        self.inhibitor.changed.connect(lambda *_: self.updated.emit())
        self._watch_resume()

    # ----- time -------------------------------------------------------------

    @staticmethod
    def now() -> datetime:
        # Naive local wall clock on purpose: the window is defined in wall-clock
        # terms, so DST shifts fall out naturally and no tz bookkeeping is needed.
        return datetime.now()

    # ----- evaluation -------------------------------------------------------

    @Slot()
    def evaluate(self) -> None:
        now = self.now()
        if self.override is not None and not self.override.is_active(now):
            log.info("manual override expired — back to schedule")
            self.override = None

        st = desired_state(now, self.cfg.start, self.cfg.end, self.cfg.enabled, self.override)
        self.state = st
        self.inhibitor.set_desired(st.awake, self.cfg.inhibit_screen, self.inhibit_reason())

        if self._last_awake is None:
            log.info("initial state: %s (%s)", "awake" if st.awake else "may sleep", self.describe())
        elif st.awake != self._last_awake:
            log.info("state flipped: %s (%s)", "awake" if st.awake else "may sleep", self.describe())
            self.flipped.emit(st.awake)
        self._last_awake = st.awake

        self._arm(now)
        self.updated.emit()

    def _arm(self, now: datetime) -> None:
        delay = TICK_CAP_S
        if self.state.next_change is not None:
            until = (self.state.next_change - now).total_seconds() + 0.5
            delay = max(1.0, min(TICK_CAP_S, until))
        self._timer.start(int(delay * 1000))

    # ----- manual override --------------------------------------------------

    def mode(self) -> str:
        if self.override is None:
            return MODE_AUTO
        return MODE_AWAKE if self.override.awake else MODE_SLEEP

    def _override_expiry(self, now: datetime) -> datetime | None:
        if not self.cfg.enabled:
            return None  # schedule off: overrides last until changed
        return next_transition(now, self.cfg.start, self.cfg.end)  # None when "always"

    def set_mode(self, mode: str) -> None:
        now = self.now()
        if mode == MODE_AUTO:
            self.override = None
        elif mode in (MODE_AWAKE, MODE_SLEEP):
            self.override = Override(mode == MODE_AWAKE, self._override_expiry(now))
        else:
            raise ValueError(f"unknown mode {mode!r}")
        log.info("mode set to %s%s", mode,
                 f" (until {fmt_time(self.override.expires_at.time())})"
                 if self.override and self.override.expires_at else "")
        self.evaluate()

    def toggle(self) -> None:
        """Left-click: flip the effective state.

        If flipping lands on what the schedule would do anyway, just drop the
        override instead of keeping a redundant one around.
        """
        now = self.now()
        want_awake = not self.state.awake
        scheduled = desired_state(now, self.cfg.start, self.cfg.end, self.cfg.enabled, None)
        if scheduled.awake == want_awake:
            self.set_mode(MODE_AUTO)
        else:
            self.set_mode(MODE_AWAKE if want_awake else MODE_SLEEP)

    # ----- config -----------------------------------------------------------

    def apply_config(self, cfg: Config, persist: bool = True) -> None:
        self.cfg = cfg
        if self.override is not None:
            # The window moved under an active override: recompute its expiry.
            self.override = Override(self.override.awake, self._override_expiry(self.now()))
        if persist:
            cfgmod.save(cfg)
        self.evaluate()

    # ----- text ---------------------------------------------------------------

    def inhibit_reason(self) -> str:
        st = self.state
        if st.source == "override":
            if st.next_change is None:
                return "Manual override"
            return f"Manual override until {fmt_time(st.next_change.time())}"
        if st.source == "always":
            return "Always awake (schedule covers the whole day)"
        return f"Scheduled awake window {fmt_window(self.cfg.start, self.cfg.end)}"

    def describe(self) -> str:
        """One line of 'why', for the tooltip."""
        st = self.state
        win = fmt_window(self.cfg.start, self.cfg.end)
        if st.source == "override":
            if st.next_change is None:
                return "manual override"
            return f"manual override until {fmt_when(st.next_change, self.now())}"
        if st.source == "disabled":
            return "schedule disabled"
        if st.source == "always":
            return "always awake (window covers the whole day)"
        return f"scheduled {win}" if st.awake else f"outside schedule {win}"

    def next_text(self) -> str:
        st = self.state
        if st.next_change is None:
            return ""
        what = "allows sleep" if st.awake else "wakes"
        return f"Next: {what} at {fmt_when(st.next_change, self.now())}"

    def status_lines(self) -> list[str]:
        head = "Awake — sleep is blocked" if self.state.awake else "May sleep"
        if self.state.awake and not self.inhibitor.is_power_inhibited:
            head = "Awake (inhibit NOT active — PowerDevil missing?)"
        lines = [head, self.describe().capitalize()]
        nxt = self.next_text()
        if nxt:
            lines.append(nxt)
        return lines

    # ----- resume hook --------------------------------------------------------

    def _watch_resume(self) -> None:
        bus = QDBusConnection.systemBus()
        if not bus.isConnected():
            return
        ok = bus.connect("org.freedesktop.login1", "/org/freedesktop/login1",
                         "org.freedesktop.login1.Manager", "PrepareForSleep",
                         self, SLOT("_on_prepare_for_sleep(bool)"))
        log.debug("PrepareForSleep hook: %s", "connected" if ok else "unavailable")

    @Slot(bool)
    def _on_prepare_for_sleep(self, going_to_sleep: bool) -> None:
        if not going_to_sleep:
            log.info("resumed from sleep — re-evaluating")
            self.evaluate()

    # ----- shutdown -----------------------------------------------------------

    def shutdown(self) -> None:
        self._timer.stop()
        self.inhibitor.shutdown()
