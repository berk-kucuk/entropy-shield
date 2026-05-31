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


def _acquire_lock() -> bool:
    """Return True if this is the first instance, False if one is already running."""
    global _instance_lock
    import fcntl
    lock_path = os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
        "entropy-shield.lock",
    )
    try:
        _instance_lock = open(lock_path, "w")
        fcntl.flock(_instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main() -> None:
    if not _acquire_lock():
        print("Entropy Shield is already running.")
        sys.exit(0)

    # Headless / systemd service mode: hand off to the privileged runner.
    if "--service" in sys.argv or "--headless" in sys.argv:
        import subprocess, shutil
        runner = os.path.join(os.path.dirname(__file__), "core", "privileged_runner.py")
        cmd: list[str]
        if shutil.which("sudo"):
            cmd = ["sudo", sys.executable, runner, "--headless"]
        elif shutil.which("pkexec"):
            cmd = ["pkexec", sys.executable, runner, "--headless"]
        else:
            print("ERROR: sudo or pkexec required for headless mode.")
            sys.exit(1)
        os.execvp(cmd[0], cmd)  # replace current process

    # Only force QT_QPA_PLATFORM when neither Wayland nor X11 is set in env.
    if not os.environ.get("QT_QPA_PLATFORM"):
        if os.environ.get("WAYLAND_DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "wayland"
        elif os.environ.get("DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "xcb"

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFontDatabase
    from gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Entropy Shield")

    _font_path = os.path.join(os.path.dirname(__file__), "Fonts", "Pixeled.ttf")
    if os.path.exists(_font_path):
        QFontDatabase.addApplicationFont(_font_path)

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
