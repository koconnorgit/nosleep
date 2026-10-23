"""Tray icon (StatusNotifierItem via QSystemTrayIcon), menu, tooltip."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .engine import MODE_AUTO, MODE_AWAKE, MODE_SLEEP, Engine

log = logging.getLogger(__name__)

ICON_DIR = Path(__file__).parent / "icons"

# We render the SVGs ourselves in the palette's text colour and hand Plasma
# pixmaps (Qt ships them via the SNI IconThemePath mechanism). Passing theme
# names instead would let Plasma recolour the icons, but plasmashell only
# learns about icon-theme directories at startup, so a freshly installed icon
# shows up as a blank tray slot until Plasma is restarted. Pixmaps always work.
# The installed theme icons (nosleep-awake/-asleep) remain for the launcher.
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


def load_icon(theme_name: str, svg: Path) -> QIcon:
    if svg.exists():
        log.debug("icon %s: rendered from %s", theme_name, svg)
        return _render_svg(svg)
    if QIcon.hasThemeIcon(theme_name):
        log.debug("icon %s: from theme", theme_name)
        return QIcon.fromTheme(theme_name)
    log.warning("icon %s: %s missing and not in theme — using Breeze fallback", theme_name, svg)
    # Last resort: Breeze's own suspend status icons.
    breeze = "system-suspend-inhibited" if "awake" in theme_name else "system-suspend-uninhibited"
    return QIcon.fromTheme(breeze)


class Tray(QObject):
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, engine: Engine, parent: QObject | None = None):
        super().__init__(parent)
        self.engine = engine
        self._load_icons()

        self.tray = QSystemTrayIcon(self.icon_asleep, self)
        QApplication.instance().installEventFilter(self)  # re-render on colour-scheme change
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

    # ----- icons ----------------------------------------------------------------

    def _load_icons(self) -> None:
        self.icon_awake = load_icon(THEME_AWAKE, FILE_AWAKE)
        self.icon_asleep = load_icon(THEME_ASLEEP, FILE_ASLEEP)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            log.debug("palette changed — re-rendering tray icons")
            self._load_icons()
            self.refresh()
        return False

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
