#!/usr/bin/env python3
from __future__ import annotations
import sys
import os

# Ensure NixOS system paths are always available (needed when running as root via pkexec)
for _p in ("/run/current-system/sw/bin", "/run/wrappers/bin",
           "/nix/var/nix/profiles/default/bin"):
    if _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _p + ":" + os.environ.get("PATH", "")


def _relaunch_as_root() -> None:
    env_fwd = []
    for var in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY",
                "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
                "QT_QPA_PLATFORM", "HOME",
                # Extra vars needed for KDE Plasma system tray
                "KDE_FULL_SESSION", "KDE_SESSION_VERSION",
                "DESKTOP_SESSION", "XDG_SESSION_TYPE",
                "XDG_CURRENT_DESKTOP", "DBUS_SYSTEM_BUS_ADDRESS"):
        val = os.environ.get(var)
        if val:
            env_fwd.append(f"{var}={val}")

    cmd = ["pkexec"]
    if env_fwd:
        cmd += ["env"] + env_fwd
    cmd += [sys.executable] + sys.argv

    try:
        os.execvp("pkexec", cmd)
    except FileNotFoundError:
        print("pkexec not found. Run as root: sudo python3 main.py")
        sys.exit(1)


def main() -> None:
    if os.geteuid() != 0:
        _relaunch_as_root()

    # Only force QT_QPA_PLATFORM when neither Wayland nor X11 is set in env.
    # Leaving it untouched when already set is required for KDE tray to work.
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

    # Register pixel font for the Pixel theme
    _font_path = os.path.join(os.path.dirname(__file__), "Fonts", "Pixeled.ttf")
    if os.path.exists(_font_path):
        QFontDatabase.addApplicationFont(_font_path)

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
