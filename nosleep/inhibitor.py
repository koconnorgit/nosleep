"""Hold / release a PowerDevil power-management inhibitor over D-Bus.

Uses the freedesktop interface that ``kde-inhibit --power`` uses:

    org.freedesktop.PowerManagement.Inhibit  /org/freedesktop/PowerManagement/Inhibit
        Inhibit(s app, s reason) -> u cookie
        UnInhibit(u cookie)

and, optionally, ``org.freedesktop.ScreenSaver`` for "keep the display on".

The cookie is tied to our bus connection; if PowerDevil restarts the cookie is
void, so we watch ``NameOwnerChanged`` and re-inhibit when the name comes back.
``cookie`` is the single source of truth for "are we actually inhibiting".
``want`` is the desired state; ``reconcile()`` makes actual == desired.

Fallback for non-KDE sessions without the freedesktop service: spawn
``systemd-inhibit --what=idle --mode=block sleep infinity`` and kill it to
release.
"""

from __future__ import annotations

import logging
import subprocess

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage, QDBusServiceWatcher

log = logging.getLogger(__name__)

PM_SERVICE = "org.freedesktop.PowerManagement.Inhibit"
PM_PATH = "/org/freedesktop/PowerManagement/Inhibit"
PM_IFACE = "org.freedesktop.PowerManagement.Inhibit"

SS_SERVICE = "org.freedesktop.ScreenSaver"
SS_PATH = "/ScreenSaver"
SS_IFACE = "org.freedesktop.ScreenSaver"

APP_NAME = "nosleep"


class _Cookie:
    """One inhibitor on one D-Bus service."""

    def __init__(self, bus: QDBusConnection, service: str, path: str, iface: str, label: str):
        self.bus = bus
        self.service = service
        self.path = path
        self.iface = iface
        self.label = label
        self.cookie: int | None = None

    @property
    def held(self) -> bool:
        return self.cookie is not None

    def _iface(self) -> QDBusInterface:
        return QDBusInterface(self.service, self.path, self.iface, self.bus)

    def acquire(self, reason: str) -> bool:
        if self.cookie is not None:
            return True
        reply = self._iface().call("Inhibit", APP_NAME, reason)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            log.warning("%s: Inhibit failed: %s", self.label, reply.errorMessage() or reply.type())
            return False
        self.cookie = int(reply.arguments()[0])
        log.info("%s: inhibited (cookie %d): %s", self.label, self.cookie, reason)
        return True

    def release(self) -> None:
        if self.cookie is None:
            return
        cookie, self.cookie = self.cookie, None
        # UnInhibit takes a D-Bus 'u' (uint32). A plain Python int marshals as
        # 'i' and PowerDevil rejects the call, so go through the introspected
        # meta-object, which knows the real signature and converts for us.
        iface = self._iface()
        ok = iface.isValid() and QMetaObject.invokeMethod(
            iface, "UnInhibit", Qt.ConnectionType.DirectConnection, Q_ARG("uint", cookie)
        )
        if not ok or iface.lastError().isValid():
            # Service may be gone; the cookie died with it. Not an error for us.
            log.info("%s: UnInhibit(%d) failed (%s) — service probably restarted",
                     self.label, cookie, iface.lastError().message() or "interface invalid")
        else:
            log.info("%s: released cookie %d", self.label, cookie)

    def forget(self) -> None:
        """The service went away: our cookie is void. Drop it without calling out."""
        if self.cookie is not None:
            log.info("%s: service vanished, cookie %d is void", self.label, self.cookie)
            self.cookie = None


class _SystemdFallback:
    """systemd-inhibit child process, for sessions without PowerDevil."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None

    @property
    def held(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def acquire(self, reason: str) -> bool:
        if self.held:
            return True
        try:
            self.proc = subprocess.Popen(
                ["systemd-inhibit", "--what=idle:sleep", "--mode=block",
                 f"--who={APP_NAME}", f"--why={reason}", "sleep", "infinity"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            log.error("systemd-inhibit fallback failed: %s", e)
            self.proc = None
            return False
        log.info("systemd-inhibit fallback active (pid %d)", self.proc.pid)
        return True

    def release(self) -> None:
        if self.proc is None:
            return
        proc, self.proc = self.proc, None
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.info("systemd-inhibit fallback released")


class Inhibitor(QObject):
    """Desired-state driven inhibitor with PowerDevil restart recovery."""

    #: emitted after every reconcile with (power_inhibited, screen_inhibited)
    changed = Signal(bool, bool)

    def __init__(self, parent: QObject | None = None, bus: QDBusConnection | None = None):
        super().__init__(parent)
        self.bus = bus or QDBusConnection.sessionBus()
        self.power = _Cookie(self.bus, PM_SERVICE, PM_PATH, PM_IFACE, "power")
        self.screen = _Cookie(self.bus, SS_SERVICE, SS_PATH, SS_IFACE, "screen")
        self.fallback = _SystemdFallback()

        self.want_power = False
        self.want_screen = False
        self.reason = "nosleep"

        # NB: construct with the connection up front. setConnection() followed
        # by addWatchedService() deadlocks under PySide6 6.12.
        self._watcher = QDBusServiceWatcher(
            PM_SERVICE, self.bus, QDBusServiceWatcher.WatchModeFlag.WatchForOwnerChange, self
        )
        self._watcher.addWatchedService(SS_SERVICE)
        self._watcher.serviceRegistered.connect(self._on_service_registered)
        self._watcher.serviceUnregistered.connect(self._on_service_unregistered)

    # ----- public -----------------------------------------------------------

    def pm_available(self) -> bool:
        iface = self.bus.interface()
        return bool(iface and iface.isServiceRegistered(PM_SERVICE).value())

    def set_desired(self, power: bool, screen: bool, reason: str) -> None:
        self.want_power = power
        self.want_screen = screen and power  # screen inhibit only makes sense while awake
        self.reason = reason
        self.reconcile()

    def reconcile(self) -> None:
        if self.want_power:
            if self.pm_available():
                if self.fallback.held:
                    self.fallback.release()
                self.power.acquire(self.reason)
            elif not self.power.held:
                log.warning("%s not on the bus — using systemd-inhibit fallback", PM_SERVICE)
                self.fallback.acquire(self.reason)
        else:
            self.power.release()
            self.fallback.release()

        if self.want_screen:
            self.screen.acquire(self.reason)
        else:
            self.screen.release()

        self.changed.emit(self.is_power_inhibited, self.screen.held)

    @property
    def is_power_inhibited(self) -> bool:
        return self.power.held or self.fallback.held

    def shutdown(self) -> None:
        self.want_power = self.want_screen = False
        self.power.release()
        self.screen.release()
        self.fallback.release()

    # ----- service watcher --------------------------------------------------

    @Slot(str)
    def _on_service_registered(self, name: str) -> None:
        log.info("%s appeared on the bus — reconciling", name)
        self.reconcile()

    @Slot(str)
    def _on_service_unregistered(self, name: str) -> None:
        if name == PM_SERVICE:
            self.power.forget()
        elif name == SS_SERVICE:
            self.screen.forget()
        self.changed.emit(self.is_power_inhibited, self.screen.held)
