"""QApplication wiring, single-instance guard, D-Bus control interface."""

from __future__ import annotations

import logging
import signal
import socket
import sys

from PySide6.QtCore import ClassInfo, QObject, QSocketNotifier, QTimer, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH, __version__
from . import config as cfgmod
from .engine import MODE_AUTO, MODE_AWAKE, MODE_SLEEP, Engine
from .inhibitor import Inhibitor
from .settings_ui import SettingsDialog
from .tray import Tray

log = logging.getLogger(__name__)

TRAY_WAIT_S = 120


@ClassInfo({"D-Bus Interface": DBUS_INTERFACE})
class Control(QObject):
    """Exported on the session bus so a second `nosleep` invocation can talk to us."""

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app

    @Slot()
    def ShowSettings(self) -> None:  # noqa: N802 (D-Bus style)
        self.app.show_settings()

    @Slot(str)
    def SetMode(self, mode: str) -> None:  # noqa: N802
        if mode in (MODE_AUTO, MODE_AWAKE, MODE_SLEEP):
            self.app.engine.set_mode(mode)
        else:
            log.warning("SetMode: unknown mode %r", mode)

    @Slot(result=str)
    def Status(self) -> str:  # noqa: N802
        e = self.app.engine
        lines = e.status_lines() + [f"Mode: {e.mode()}", f"Inhibiting: {e.inhibitor.is_power_inhibited}"]
        return "\n".join(lines)

    @Slot()
    def Quit(self) -> None:  # noqa: N802
        QTimer.singleShot(0, self.app.qapp.quit)


class App(QObject):
    def __init__(self, qapp: QApplication, open_settings: bool = False):
        super().__init__()
        self.qapp = qapp
        self.cfg = cfgmod.load()
        self.inhibitor = Inhibitor(self)
        self.engine = Engine(self.cfg, self.inhibitor, self)
        self.tray: Tray | None = None
        self.settings: SettingsDialog | None = None

        bus = QDBusConnection.sessionBus()
        self.control = Control(self)
        if not bus.registerObject(DBUS_PATH, self.control, QDBusConnection.RegisterOption.ExportAllSlots):
            log.warning("could not export control object: %s", bus.lastError().message())

        qapp.aboutToQuit.connect(self.shutdown)
        self.engine.evaluate()
        self._wait_for_tray(deadline_s=TRAY_WAIT_S)
        if open_settings:
            QTimer.singleShot(0, self.show_settings)

    # ----- tray -------------------------------------------------------------

    def _wait_for_tray(self, deadline_s: float) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._create_tray()
            return
        log.info("no system tray yet — waiting up to %ds for the StatusNotifierWatcher", int(deadline_s))
        attempts = {"left": int(deadline_s / 2)}

        def poll():
            if QSystemTrayIcon.isSystemTrayAvailable():
                self._create_tray()
                return
            attempts["left"] -= 1
            if attempts["left"] <= 0:
                log.warning("system tray never became available — running headless (inhibiting still works)")
                return
            QTimer.singleShot(2000, poll)

        QTimer.singleShot(2000, poll)

    def _create_tray(self) -> None:
        self.tray = Tray(self.engine, self)
        self.tray.settings_requested.connect(self.show_settings)
        self.tray.quit_requested.connect(self.qapp.quit)
        log.info("tray icon shown")

    # ----- settings ---------------------------------------------------------

    @Slot()
    def show_settings(self) -> None:
        if self.settings is None:
            self.settings = SettingsDialog(self.engine)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    # ----- lifecycle --------------------------------------------------------

    @Slot()
    def shutdown(self) -> None:
        log.info("shutting down — releasing inhibitors")
        self.engine.shutdown()
        if self.tray is not None:
            self.tray.tray.hide()


def _install_signal_handlers(qapp: QApplication) -> tuple:
    """Make SIGTERM/SIGINT quit the Qt loop cleanly (so we release the cookie)."""
    rsock, wsock = socket.socketpair()
    wsock.setblocking(False)
    signal.set_wakeup_fd(wsock.fileno())
    notifier = QSocketNotifier(rsock.fileno(), QSocketNotifier.Type.Read)

    def on_ready(*_):
        try:
            rsock.recv(64)
        except BlockingIOError:
            pass
        log.info("signal received — quitting")
        qapp.quit()

    notifier.activated.connect(on_ready)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: None)
    return notifier, rsock, wsock


def _hand_off_to_service() -> bool:
    """`nosleep --settings` from the launcher: prefer the systemd service instance.

    If the unit is installed, (re)start it and ask it to show settings, so the
    launcher never ends up running a second, unmanaged tray instance.
    """
    import time

    from .settings_ui import service_installed, start_service

    if client_interface() is None:
        if not service_installed():
            return False
        if not start_service():
            return False
        for _ in range(50):  # up to ~10 s for the service to own the name
            time.sleep(0.2)
            if client_interface() is not None:
                break
        else:
            log.warning("service started but never claimed %s — running in-process", DBUS_NAME)
            return False
    log.info("nosleep service is running — asking it to show settings")
    call_running("ShowSettings")
    return True


def client_interface() -> QDBusInterface | None:
    """Interface to a running instance, or None if nothing owns the name."""
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    reg = bus.interface().isServiceRegistered(DBUS_NAME)
    if not reg.value():
        return None
    iface = QDBusInterface(DBUS_NAME, DBUS_PATH, DBUS_INTERFACE, bus)
    return iface if iface.isValid() else None


def run_tray(open_settings: bool = False) -> int:
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("nosleep")
    qapp.setApplicationDisplayName("nosleep")
    qapp.setApplicationVersion(__version__)
    qapp.setDesktopFileName("nosleep")
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(QIcon.fromTheme("nosleep-awake"))

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        log.error("no session bus — cannot run")
        return 1
    if open_settings and _hand_off_to_service():
        return 0
    if not bus.registerService(DBUS_NAME):
        log.info("nosleep is already running — asking it to show settings")
        iface = client_interface()
        if iface is not None:
            iface.call("ShowSettings")
        return 0

    keep = _install_signal_handlers(qapp)  # noqa: F841 (must stay alive)
    app = App(qapp, open_settings=open_settings)  # noqa: F841
    log.info("nosleep %s running", __version__)
    rc = qapp.exec()
    bus.unregisterService(DBUS_NAME)
    return rc


def call_running(method: str, *args) -> QDBusMessage | None:
    iface = client_interface()
    if iface is None:
        return None
    return iface.call(method, *args)
