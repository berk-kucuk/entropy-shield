from __future__ import annotations
import os

_DESKTOP_NAME = "entropy-shield.desktop"

_DESKTOP_CONTENT = """\
[Desktop Entry]
Name=Entropy Shield
Comment=Network Privacy Stack — Tor, DNSCrypt, I2P
Exec=entropy-shield
Icon=/usr/share/pixmaps/entropy-shield.png
Type=Application
Categories=Network;Security;
Terminal=false
StartupWMClass=entropy-shield
X-GNOME-Autostart-enabled=true
"""


def _autostart_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".config", "autostart")


def _desktop_path() -> str:
    return os.path.join(_autostart_dir(), _DESKTOP_NAME)


def enable() -> bool:
    try:
        adir = _autostart_dir()
        os.makedirs(adir, exist_ok=True)
        with open(_desktop_path(), "w") as f:
            f.write(_DESKTOP_CONTENT)
        return True
    except Exception:
        return False


def disable() -> bool:
    try:
        path = _desktop_path()
        if os.path.exists(path):
            os.unlink(path)
        return True
    except Exception:
        return False


def is_enabled() -> bool:
    return os.path.exists(_desktop_path())
