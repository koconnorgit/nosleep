# nosleep — planning notes

Scheduled sleep inhibitor for KDE Plasma with a tray icon and a settings GUI.
Written 2026-10-23 for Kev-PC (CachyOS, Plasma 6.8.1, Wayland). These notes are the
brief for the coding session; everything under "Verified on this machine" was checked
live, the rest is design.

## Goal

Between a configurable daily window (default **09:00 → 00:00** local time) the PC must
never auto-suspend from idle. Outside the window Plasma's normal idle rules apply
(currently: display off after 15 min, suspend after 45 min on AC). Nothing about
Plasma's own timeouts is edited; the app simply holds a power-management inhibitor
while inside the window and releases it outside.

## User-facing behaviour

- **Tray icon** (StatusNotifierItem, lives next to arch-update-tray etc.)
  - Inhibiting (inside window, or manually forced on): **open eye** — "awake".
  - Not inhibiting (outside window, or manually forced off): **closed eye** — "may sleep".
  - Tooltip: current state, why ("scheduled 09:00–00:00", "manual override until 00:00",
    "paused"), and the next transition time.
- **Left click** on the icon: toggle a manual override (force awake / force sleep-allowed).
  Override lasts until the next scheduled transition, then the schedule takes back over.
  This mirrors Plasma's own "Block sleep" toggle but with a timeout.
- **Right click** menu: Awake now / Allow sleep now / Follow schedule, Settings…, Quit.
- **Settings dialog**
  - Start time and end time (QTimeEdit, 24h). Window may cross midnight (e.g. 21:00 → 02:00);
    treat "end == start" as "always inhibit".
  - Enable/disable schedule checkbox (disabled = app only does manual overrides).
  - "Also keep the display on" checkbox (adds screensaver inhibition; default OFF —
    user only asked for sleep).
  - "Start at login" checkbox (writes/removes the autostart entry).
  - Apply takes effect immediately, no restart.
- **Notifications** (optional, default off): a Plasma notification when the state flips.

## Mechanism — how inhibition works

Plasma's PowerDevil exposes the freedesktop inhibit interface on the session bus:

```
service:   org.freedesktop.PowerManagement.Inhibit
path:      /org/freedesktop/PowerManagement/Inhibit
interface: org.freedesktop.PowerManagement.Inhibit
  Inhibit(s application, s reason) -> u cookie
  UnInhibit(u cookie)
  HasInhibit() -> b
```

This is the exact call `kde-inhibit --power` makes, and PowerDevil honours it for
auto-suspend (it shows up in the battery widget as "an application is blocking sleep").
For the optional "keep display on" option use `org.freedesktop.ScreenSaver`
(`/ScreenSaver`, `Inhibit(s,s)->u` / `UnInhibit(u)`) the same way.

Rules:
- The inhibitor is tied to our bus connection. If PowerDevil restarts (Plasma crash,
  `systemctl --user restart plasma-powerdevil`) the cookie is void. Watch
  `NameOwnerChanged` for `org.freedesktop.PowerManagement.Inhibit` and re-inhibit when
  the name reappears while we're in the awake state.
- Only ever hold one cookie. Keep `cookie: int | None` as the single source of truth for
  "are we actually inhibiting"; the *desired* state is computed separately. A reconcile
  step makes actual == desired.
- Release the cookie on quit (and on SIGTERM) — otherwise PowerDevil keeps the block
  until the bus connection drops, which is fine but untidy.

Fallback if the freedesktop interface is ever unavailable (non-KDE session): spawn
`systemd-inhibit --what=idle --mode=block sleep infinity` and kill it to release.
Not needed on this machine; note it and move on.

## Schedule engine

- Desired state = `schedule_enabled and in_window(now)` XOR'd with any active manual
  override. Override stores `(forced_state, expires_at)` where `expires_at` is the next
  scheduled transition.
- `in_window(now)`: if `start < end`: `start <= now.time() < end`; if `start > end`
  (crosses midnight): `now.time() >= start or now.time() < end`; if equal: always true.
  Midnight end is stored as `00:00` and handled by the "crosses midnight" branch
  (09:00 → 00:00 means start > end). Do the comparison on local wall-clock `time`, not
  on seconds since epoch, so DST shifts just work.
- Timer strategy: compute `next_transition()` and arm a single-shot QTimer for it, but
  **cap it at 60 s** and re-evaluate on every tick. Reason: QTimer doesn't survive
  suspend correctly, and the clock can jump (NTP, resume from sleep after midnight).
  Re-evaluating every minute costs nothing and makes resume-from-suspend correct
  without extra logind hooks.
- Also re-evaluate immediately on: settings change, PowerDevil name reappearing,
  `PrepareForSleep(false)` from `org.freedesktop.login1` (nice-to-have, the 60 s tick
  already covers it).

## Stack

- **Python 3.14 + PySide6 6.12** (both installed system-wide via pacman; no venv needed,
  no pip). Use PySide6's `QtDBus` for the inhibit calls so there's one event loop;
  `python-dbus` is also installed as a fallback but mixing two bus libraries is a
  known source of pain.
- `QSystemTrayIcon` → on Plasma this becomes a proper StatusNotifierItem automatically
  (Qt's xdg-platform-theme plugin). No need to hand-roll the SNI D-Bus interface.
- Icons: ship our own two SVGs (`eye-open.svg`, `eye-closed.svg`) as the primary design.
  Breeze also has `system-suspend-inhibited` / `system-suspend-uninhibited` in
  `/usr/share/icons/breeze/status/22/` — good fallback via `QIcon.fromTheme`, and worth
  looking at for stroke weight so ours match the panel. Tray icons on Plasma should be
  monochrome, ~22 px, and use `currentColor`-style fill so they follow light/dark panels;
  Plasma recolours SVGs that use the Breeze stylesheet classes (`ColorScheme-Text`).
- Config: `~/.config/nosleep/config.toml` (stdlib `tomllib` to read; write with a tiny
  hand-rolled dumper or `tomli_w` if present — avoid new deps). Keys: `start`, `end`,
  `enabled`, `inhibit_screen`, `notify`.
- Logging: `logging` to stderr; journald picks it up when run as a service.

## Startup / install

Follow the existing `~/development/hanotifications` convention:
- Code installed to `~/.local/lib/nosleep/`, launcher at `~/.local/bin/nosleep`.
- Run as a **systemd user service** `nosleep.service`, `WantedBy=graphical-session.target`,
  `After=plasma-powerdevil.service` (unit exists in Plasma 6), `Restart=on-failure`.
  Needs `Environment` for `WAYLAND_DISPLAY`/`DISPLAY` — graphical-session.target already
  imports these on Plasma, same as hanotifications.
- A `.desktop` file in `~/.local/share/applications/` so it shows in the launcher
  (`Exec=nosleep --settings` opens the dialog of the running instance via a single-
  instance D-Bus name `org.kevin.nosleep`; second launch just raises settings).
- `install.sh` does the copy + `systemctl --user daemon-reload && enable --now`.
- Settings "Start at login" toggles the systemd enable state (not an XDG autostart
  entry, to avoid two competing mechanisms).

## File layout

```
nosleep/
  PLANNING.md          this file
  README.md            user docs once it works
  nosleep/
    __init__.py
    __main__.py        argparse: (default) run tray app | --settings | --status | --awake | --sleep | --auto
    app.py             QApplication, single-instance guard, wiring
    inhibitor.py       PowerDevil D-Bus inhibit/uninhibit + NameOwnerChanged watcher
    schedule.py        pure functions: in_window, next_transition, desired_state (unit-testable, no Qt)
    tray.py            QSystemTrayIcon, menu, tooltip, icon switching
    settings_ui.py     QDialog
    config.py          load/save toml, defaults, validation
    icons/eye-open.svg, eye-closed.svg
  tests/test_schedule.py   pytest for window math incl. midnight crossing and DST days
  nosleep.service
  nosleep.desktop
  install.sh
```

## Milestones (in order)

1. `schedule.py` + tests. Pure logic first; it's where the bugs would be.
2. `inhibitor.py` — prove with a 5-line script that Inhibit/UnInhibit flips
   `qdbus6 org.freedesktop.PowerManagement.Inhibit /org/freedesktop/PowerManagement/Inhibit HasInhibit`
   and that the Plasma battery widget shows the block.
3. Tray icon with the two SVGs and the right-click menu; manual override only.
4. Hook the schedule engine to the tray; 60 s tick; tooltip shows next transition.
5. Settings dialog + config persistence + live apply.
6. systemd unit, install script, `.desktop`, single-instance.
7. Soak test: leave it over a midnight boundary and confirm the machine suspends
   ~45 min after 00:00 with no input; confirm it does not suspend at e.g. 15:00 idle.

## Edge cases to keep in mind

- Resume from suspend after midnight while window spans it → tick re-evaluates, fine.
- Plasma restart kills PowerDevil → cookie void → NameOwnerChanged re-inhibit.
- User changes the window while an override is active → recompute override expiry.
- `end == 00:00` must display as "midnight" / 00:00, not 24:00, and must parse back.
- Multiple sessions (TTY login + Plasma) — only run inside the graphical session.
- Don't fight Plasma's own manual "Block sleep" toggle; ours is additive. Both can be on.
- Known machine quirk: Kev-PC has an intermittent suspend/resume hang and a nvidia-open
  615 DP/VRR resume regression (see memory notes). Fewer suspends during the day is
  partly the point here, but the app must never *cause* a suspend — it only ever
  blocks one.

## Verified on this machine (2026-10-23)

- `XDG_CURRENT_DESKTOP=KDE`, `XDG_SESSION_TYPE=wayland`, plasmashell 6.8.1.
- `~/.config/powerdevilrc` `[AC][SuspendAndShutdown] AutoSuspendIdleTimeoutSec=2700`,
  `[AC][Display] TurnOffDisplayIdleTimeoutSec=900`.
- Session bus has `org.freedesktop.PowerManagement.Inhibit` and
  `org.kde.Solid.PowerManagement.PolicyAgent` (AddInhibition/ReleaseInhibition also
  available, KDE-specific; prefer the freedesktop one).
- `org.kde.StatusNotifierWatcher` present (kded6) → QSystemTrayIcon will be an SNI.
- Installed: python 3.14.8, pyside6 6.12.0, python-pyqt6 6.11.0, python-dbus 1.5.0,
  `/usr/bin/kde-inhibit`, `/usr/bin/systemd-inhibit`.
- Timezone America/Detroit.
- Breeze status icons: `system-suspend-inhibited.svg`, `system-suspend-uninhibited.svg`.
