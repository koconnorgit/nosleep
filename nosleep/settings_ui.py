"""Settings dialog. Apply takes effect immediately."""

from __future__ import annotations

import logging
import subprocess
from datetime import time

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .config import Config
from .engine import Engine

log = logging.getLogger(__name__)

SERVICE = "nosleep.service"


def _systemctl(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("systemctl %s failed: %s", " ".join(args), e)
        return None


def service_installed() -> bool:
    r = _systemctl("cat", SERVICE)
    return bool(r and r.returncode == 0)


def service_enabled() -> bool:
    r = _systemctl("is-enabled", SERVICE)
    return bool(r and r.stdout.strip() == "enabled")


def set_service_enabled(enabled: bool) -> bool:
    r = _systemctl("enable" if enabled else "disable", SERVICE)
    ok = bool(r and r.returncode == 0)
    if not ok:
        log.warning("could not %s %s: %s", "enable" if enabled else "disable", SERVICE,
                    (r.stderr.strip() if r else "systemctl unavailable"))
    return ok


def _qtime(t: time) -> QTime:
    return QTime(t.hour, t.minute)


def _pytime(q: QTime) -> time:
    return time(q.hour(), q.minute())


class SettingsDialog(QDialog):
    def __init__(self, engine: Engine, parent: QWidget | None = None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("nosleep settings")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        form = QFormLayout()
        self.start = QTimeEdit()
        self.start.setDisplayFormat("HH:mm")
        self.end = QTimeEdit()
        self.end.setDisplayFormat("HH:mm")
        self.enabled = QCheckBox("Keep awake on this schedule")
        self.inhibit_screen = QCheckBox("Also keep the display on")
        self.inhibit_screen.setToolTip("Adds a screensaver inhibition while awake, so the "
                                       "screen never blanks inside the window either.")
        self.notify = QCheckBox("Notify when the state changes")
        self.autostart = QCheckBox("Start at login")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(mid);")

        form.addRow(self.enabled)
        form.addRow("Stay awake from", self.start)
        form.addRow("until", self.end)
        form.addRow(self.hint)
        form.addRow(self.inhibit_screen)
        form.addRow(self.notify)
        form.addRow(self.autostart)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.start.timeChanged.connect(self._update_hint)
        self.end.timeChanged.connect(self._update_hint)
        self.enabled.toggled.connect(self._update_hint)
        self.load()

    # ----- model <-> widgets --------------------------------------------------

    def load(self) -> None:
        cfg = self.engine.cfg
        self.start.setTime(_qtime(cfg.start))
        self.end.setTime(_qtime(cfg.end))
        self.enabled.setChecked(cfg.enabled)
        self.inhibit_screen.setChecked(cfg.inhibit_screen)
        self.notify.setChecked(cfg.notify)
        if service_installed():
            self.autostart.setEnabled(True)
            self.autostart.setChecked(service_enabled())
            self.autostart.setToolTip("Enables the nosleep systemd user service for the graphical session.")
        else:
            self.autostart.setEnabled(False)
            self.autostart.setChecked(False)
            self.autostart.setToolTip("Run install.sh first — the systemd user unit is not installed.")
        self._update_hint()

    def _update_hint(self) -> None:
        s, e = _pytime(self.start.time()), _pytime(self.end.time())
        if not self.enabled.isChecked():
            txt = "Schedule off: the tray toggle and menu still work as a manual switch."
        elif s == e:
            txt = "Start and end are equal: always awake."
        elif s > e:
            end_txt = "midnight" if e == time(0) else e.strftime("%H:%M")
            txt = f"Window crosses midnight: awake from {s:%H:%M} until {end_txt} the next day."
        else:
            txt = f"Awake from {s:%H:%M} until {e:%H:%M}; the PC may sleep the rest of the day."
        self.hint.setText(txt)

    def current_config(self) -> Config:
        return Config(
            start=_pytime(self.start.time()),
            end=_pytime(self.end.time()),
            enabled=self.enabled.isChecked(),
            inhibit_screen=self.inhibit_screen.isChecked(),
            notify=self.notify.isChecked(),
        )

    # ----- actions ----------------------------------------------------------------

    def apply(self) -> None:
        cfg = self.current_config()
        if cfg != self.engine.cfg:
            self.engine.apply_config(cfg)
        if self.autostart.isEnabled() and self.autostart.isChecked() != service_enabled():
            if not set_service_enabled(self.autostart.isChecked()):
                self.autostart.setChecked(service_enabled())

    def _ok(self) -> None:
        self.apply()
        self.accept()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.load()
        super().showEvent(event)
