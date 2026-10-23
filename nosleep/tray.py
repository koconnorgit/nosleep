"""Tray icon (StatusNotifierItem via QSystemTrayIcon), menu, tooltip."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .engine import MODE_AUTO, MODE_AWAKE, MODE_SLEEP, Engine

log = logging.getLogger(__name__)

ICON_DIR = Path(__file__).parent / "icons"

# Theme icon names (installed by install.sh into hicolor). Using theme names
# lets Plasma recolour the SVGs to match the panel; the file fallback renders
# them ourselves with the palette text colour.
THEME_AWAKE = "nosleep-awake"
THEME_ASLEEP = "nosleep-asleep"
FILE_AWAKE = ICON_DIR / "eye-open.svg"
FILE_ASLEEP = ICON_DIR / "eye-closed.svg"


def _render_svg(path: Path) -> QIcon:
    """Render our monochrome SVG with the current palette's text colour."""
    color = QApplication.palette().color(QPalette.ColorRole.WindowText).name()
    svg = path.read_text(encoding="utf-8").replace("currentColor", color).replace("#232629", color)
    renderer = QSvgRenderer(svg.encode("utf-8"))
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        pm = QPixmap(QSize(size, size))
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pm)
    return icon


def load_icon(theme_name: str, file_fallback: Path) -> QIcon:
    if QIcon.hasThemeIcon(theme_name):
        log.debug("icon %s: from theme", theme_name)
        return QIcon.fromTheme(theme_name)
    if file_fallback.exists():
        log.debug("icon %s: rendered from %s", theme_name, file_fallback)
        return _render_svg(file_fallback)
    log.warning("icon %s: not installed and %s missing — using Breeze fallback", theme_name, file_fallback)
    # Last resort: Breeze's own suspend status icons.
    breeze = "system-suspend-inhibited" if "awake" in theme_name else "system-suspend-uninhibited"
    return QIcon.fromTheme(breeze)


class Tray(QObject):
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, engine: Engine, parent: QObject | None = None):
        super().__init__(parent)
        self.engine = engine
        self.icon_awake = load_icon(THEME_AWAKE, FILE_AWAKE)
        self.icon_asleep = load_icon(THEME_ASLEEP, FILE_ASLEEP)

        self.tray = QSystemTrayIcon(self.icon_asleep, self)
        self.menu = QMenu()
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)

        engine.updated.connect(self.refresh)
        engine.flipped.connect(self._on_flipped)
        self.refresh()
        self.tray.show()

    # ----- menu ---------------------------------------------------------------

    def _build_menu(self) -> None:
        self.mode_group = QActionGroup(self)
        self.mode_group.setExclusive(True)
        self.act_awake = self._mode_action("Awake now", MODE_AWAKE)
        self.act_sleep = self._mode_action("Allow sleep now", MODE_SLEEP)
        self.act_auto = self._mode_action("Follow schedule", MODE_AUTO)
        self.menu.addSeparator()
        act = self.menu.addAction("Settings…")
        act.triggered.connect(self.settings_requested.emit)
        self.menu.addSeparator()
        act = self.menu.addAction("Quit")
        act.triggered.connect(self.quit_requested.emit)

    def _mode_action(self, text: str, mode: str) -> QAction:
        act = QAction(text, self)
        act.setCheckable(True)
        act.triggered.connect(lambda _checked=False, m=mode: self.engine.set_mode(m))
        self.mode_group.addAction(act)
        self.menu.addAction(act)
        return act

    # ----- state ----------------------------------------------------------------

    def refresh(self) -> None:
        awake = self.engine.state.awake
        self.tray.setIcon(self.icon_awake if awake else self.icon_asleep)
        self.tray.setToolTip("\n".join(["nosleep: " + l if i == 0 else l
                                        for i, l in enumerate(self.engine.status_lines())]))
        mode = self.engine.mode()
        self.act_awake.setChecked(mode == MODE_AWAKE)
        self.act_sleep.setChecked(mode == MODE_SLEEP)
        self.act_auto.setChecked(mode == MODE_AUTO)
        self.act_auto.setEnabled(self.engine.cfg.enabled or mode != MODE_AUTO)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.MiddleClick):
            self.engine.toggle()

    def _on_flipped(self, awake: bool) -> None:
        if not self.engine.cfg.notify:
            return
        title = "Staying awake" if awake else "Sleep allowed"
        body = self.engine.describe().capitalize()
        nxt = self.engine.next_text()
        if nxt:
            body += "\n" + nxt
        self.tray.showMessage(title, body, self.icon_awake if awake else self.icon_asleep, 4000)
