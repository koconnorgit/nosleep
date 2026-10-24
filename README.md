# nosleep

Scheduled sleep inhibitor for KDE Plasma, with a tray icon and a settings dialog.

Between a daily window you choose (default **09:00 → 00:00**) the PC never
auto-suspends from idle. Outside the window Plasma's own idle rules apply
unchanged. nosleep doesn't edit any power settings: it simply holds a
power-management inhibitor while inside the window and releases it outside,
the same way `kde-inhibit --power` or a media player does.

| Tray icon | Meaning |
|-----------|---------|
| open eye  | awake: sleep is blocked (inside the window, or forced on) |
| closed eye | may sleep (outside the window, or forced off) |

- **Left click** the icon to flip the state manually. The override lasts until
  the next scheduled transition, then the schedule takes over again.
- **Right click** for *Awake now* / *Allow sleep now* / *Follow schedule*,
  *Settings…* and *Quit*.
- **Tooltip** shows the current state, why, and the next transition time. If
  another app is holding its own sleep inhibitor (Firefox with a tab that
  claims to be playing audio is the usual one), the tooltip says so, because
  the PC won't sleep until that app lets go, whatever nosleep's state is.
  `nosleep --status` prints the same lines, and the journal logs when such a
  blocker appears or goes away.

## Requirements

- KDE Plasma 6 (Wayland or X11). PowerDevil provides the
  `org.freedesktop.PowerManagement.Inhibit` interface nosleep talks to.
- Python 3.11+ and PySide6 with the QtDBus and QtSvg modules. On Arch /
  CachyOS: `sudo pacman -S --needed pyside6`. No pip packages, no venv.
- systemd user session (for autostart). Without Plasma the app falls back to
  `systemd-inhibit`, but the tray icon needs a StatusNotifier host.

## Install

```sh
git clone https://github.com/koconnorgit/nosleep.git
cd nosleep
./install.sh
```

This copies the code to `~/.local/lib/nosleep`, puts a launcher at
`~/.local/bin/nosleep`, installs the icons and a desktop entry, and enables
and starts the `nosleep.service` systemd user unit so it runs in every
graphical session. Look for the eye icon in the system tray.

To remove everything except your config: `./install.sh --uninstall`.

## Usage

```
nosleep              run the tray app (if it's already running, opens Settings)
nosleep --settings   open the settings dialog
nosleep --status     print the current state
nosleep --awake      force awake until the next scheduled transition
nosleep --sleep      allow sleep until the next scheduled transition
nosleep --auto       follow the schedule again
nosleep --quit       stop the running instance
```

Settings (all apply immediately, no restart):

- **Stay awake from / until**: 24-hour times. The window may cross midnight,
  e.g. 21:00 → 02:00. Midnight is `00:00`. Equal start and end means
  "always awake".
- **Keep awake on this schedule**: untick to disable the schedule; the tray
  toggle and menu then work as a plain manual switch.
- **Also keep the display on**: adds a screensaver inhibition inside the
  window so the screen doesn't blank either. Off by default.
- **Notify when the state changes**: a Plasma notification on each flip.
- **Start at login**: enables or disables the systemd user service.

Config lives in `~/.config/nosleep/config.toml` and is safe to hand-edit:

```toml
start = "09:00"
end = "00:00"
enabled = true
inhibit_screen = false
notify = false
```

Logs: `journalctl --user -u nosleep -f`.

## How it works

- The desired state is `schedule_enabled and in_window(now)`, unless a manual
  override is active. `in_window` compares local wall-clock times, so DST
  changes don't move the window.
- A single-shot timer is armed for the next transition but capped at 60 s, and
  every tick re-evaluates. That keeps things correct across suspend/resume and
  clock jumps without any extra hooks. It also re-evaluates on settings
  changes, on resume (`PrepareForSleep` from logind), and when PowerDevil
  reappears on the bus.
- Exactly one inhibit cookie is held at a time. If PowerDevil restarts the
  cookie is void; nosleep watches `NameOwnerChanged` and re-inhibits. The
  cookie is released on quit and on SIGTERM.
- The tray icon is rendered from the bundled SVGs in the current palette's
  text colour and handed to Plasma as pixmaps. Theme-name lookups would let
  Plasma recolour it, but plasmashell only scans icon directories at startup,
  so a freshly installed icon shows as an empty tray slot until Plasma restarts.
- The inhibition shows up in Plasma's battery/power widget as "nosleep is
  blocking sleep", next to any other app's. nosleep never *causes* a suspend;
  it only ever blocks one.

## Development

```sh
python -m unittest discover -s tests     # pure-logic tests, no Qt needed
python -m nosleep -v                     # run from the source tree
```

Layout:

```
nosleep/
  __main__.py     CLI
  app.py          QApplication, single-instance guard, D-Bus control object
  engine.py       schedule <-> inhibitor glue, timer, overrides
  schedule.py     pure window maths (in_window, next_transition, desired_state)
  inhibitor.py    PowerDevil / ScreenSaver D-Bus inhibit with restart recovery
  tray.py         QSystemTrayIcon (StatusNotifierItem on Plasma), menu, tooltip
  settings_ui.py  settings dialog
  config.py       TOML load/save
  icons/          eye-open.svg, eye-closed.svg (Breeze-style, recolourable)
tests/            unittest suite for schedule and config
nosleep.service   systemd user unit
nosleep.desktop   launcher entry
install.sh        install / --uninstall
PLANNING.md       original design notes
```

The running instance exports `org.kevin.nosleep` on the session bus with
`ShowSettings()`, `SetMode(s)`, `Status() -> s` and `Quit()`, which is what
the CLI flags call.

## License

MIT, see [LICENSE](LICENSE).
