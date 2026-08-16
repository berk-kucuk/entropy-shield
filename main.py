#!/usr/bin/env python3
from __future__ import annotations
import sys
import os

# Ensure NixOS system paths are always available
for _p in ("/run/current-system/sw/bin", "/run/wrappers/bin",
           "/nix/var/nix/profiles/default/bin"):
    if _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _p + ":" + os.environ.get("PATH", "")

# Keep a module-level reference so the lock is held for the entire lifetime
# of the process.  Without this the file handle is GC'd and the lock drops.
_instance_lock = None


def _lock_dir() -> str:
    """Return a private, user-owned directory to hold the single-instance lock.

    Never /tmp: it is world-writable, so on a shared machine another account
    could hold "entropy-shield.lock" open to keep the app from ever starting,
    or point it at a symlink and have us truncate a file of theirs choosing.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return runtime
    fallback = os.path.join(os.path.expanduser("~"), ".cache", "entropy-shield")
    os.makedirs(fallback, mode=0o700, exist_ok=True)
    return fallback


def _acquire_lock() -> bool:
    """Return True if this is the first instance, False if one is already running."""
    global _instance_lock
    import fcntl
    lock_path = os.path.join(_lock_dir(), "entropy-shield.lock")
    try:
        fd = os.open(lock_path,
                     os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        _instance_lock = os.fdopen(fd, "w")
        fcntl.flock(_instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main() -> None:
    if not _acquire_lock():
        print("Entropy Shield is already running.")
        sys.exit(0)

    # Headless / systemd service mode: run the privileged daemon directly.
    # This must be invoked as root (systemd does this); it no longer shells out
    # to sudo/pkexec.  The desktop GUI talks to this daemon over its socket.
    if "--service" in sys.argv or "--headless" in sys.argv:
        if os.geteuid() != 0:
            print("ERROR: headless/service mode must run as root.\n"
                  "Use the systemd service instead:\n"
                  "    sudo systemctl enable --now entropy-shield")
            sys.exit(1)
        from core.daemon import main as daemon_main
        daemon_main()
        return

    # Only force QT_QPA_PLATFORM when neither Wayland nor X11 is set in env.
    if not os.environ.get("QT_QPA_PLATFORM"):
        if os.environ.get("WAYLAND_DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "wayland"
        elif os.environ.get("DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "xcb"

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFontDatabase
    from gui.main_window import MainWindow

    # Start hidden in the system tray only (no visible window). Used by the
    # autostart entry so the app comes up in the tray on login without popping
    # the GUI open. The user can restore the window from the tray menu.
    start_hidden = "--tray" in sys.argv or "--minimized" in sys.argv

    app = QApplication(sys.argv)
    app.setApplicationName("Entropy Shield")
    # Keep running in the tray even when the main window is hidden/closed.
    app.setQuitOnLastWindowClosed(False)

    _font_path = os.path.join(os.path.dirname(__file__), "Fonts", "Pixeled.ttf")
    if os.path.exists(_font_path):
        QFontDatabase.addApplicationFont(_font_path)

    w = MainWindow()
    if not start_hidden:
        w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
