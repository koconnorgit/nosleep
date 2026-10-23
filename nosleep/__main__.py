"""Command line entry point.

    nosleep              run the tray app (or raise settings if already running)
    nosleep --settings   open the settings dialog of the running instance (starts it if needed)
    nosleep --status     print the current state
    nosleep --awake      force awake until the next scheduled transition
    nosleep --sleep      allow sleep until the next scheduled transition
    nosleep --auto       follow the schedule again
    nosleep --quit       stop the running instance
"""

from __future__ import annotations

import argparse
import logging
import sys


def _setup_logging(verbose: bool) -> None:
    fmt = "%(levelname)s %(name)s: %(message)s"
    if sys.stderr.isatty():
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt, stream=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nosleep", description="Scheduled sleep inhibitor for KDE Plasma.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--settings", action="store_true", help="open the settings dialog")
    g.add_argument("--status", action="store_true", help="print current state and exit")
    g.add_argument("--awake", action="store_true", help="force awake until the next scheduled transition")
    g.add_argument("--sleep", action="store_true", help="allow sleep until the next scheduled transition")
    g.add_argument("--auto", action="store_true", help="follow the schedule again")
    g.add_argument("--quit", action="store_true", help="stop the running instance")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = p.parse_args(argv)
    _setup_logging(args.verbose)

    from PySide6.QtCore import QCoreApplication

    if args.status or args.awake or args.sleep or args.auto or args.quit:
        _ = QCoreApplication(sys.argv)  # QtDBus needs an application object
        from .app import call_running

        if args.status:
            reply = call_running("Status")
            if reply is None:
                print("nosleep is not running")
                return 1
            print(reply.arguments()[0] if reply.arguments() else reply.errorMessage())
            return 0
        if args.quit:
            return 0 if call_running("Quit") is not None else 1
        mode = "awake" if args.awake else "sleep" if args.sleep else "auto"
        reply = call_running("SetMode", mode)
        if reply is None:
            print("nosleep is not running", file=sys.stderr)
            return 1
        reply = call_running("Status")
        if reply is not None and reply.arguments():
            print(reply.arguments()[0])
        return 0

    from .app import run_tray

    return run_tray(open_settings=args.settings)


if __name__ == "__main__":
    sys.exit(main())
