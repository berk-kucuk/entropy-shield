from __future__ import annotations
import os
import pwd

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


def _real_user_home() -> str | None:
    """Return the home directory of the user who launched the app (pre-sudo)."""
    for var in ("PKEXEC_UID", "SUDO_UID"):
        val = os.environ.get(var)
        if val and val.isdigit():
            try:
                return pwd.getpwuid(int(val)).pw_dir
            except KeyError:
                pass
    return None


def _autostart_dir(home: str) -> str:
    return os.path.join(home, ".config", "autostart")


def _desktop_path(home: str) -> str:
    return os.path.join(_autostart_dir(home), _DESKTOP_NAME)


def enable() -> bool:
    """Create the XDG autostart .desktop file. Returns True on success."""
    home = _real_user_home()
    if not home:
        return False
    try:
        adir = _autostart_dir(home)
        os.makedirs(adir, exist_ok=True)
        path = _desktop_path(home)
        with open(path, "w") as f:
            f.write(_DESKTOP_CONTENT)
        uid_s = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID")
        if uid_s and uid_s.isdigit():
            uid = int(uid_s)
            pw  = pwd.getpwuid(uid)
            os.chown(adir,  uid, pw.pw_gid)
            os.chown(path,  uid, pw.pw_gid)
        return True
    except Exception:
        return False


def disable() -> bool:
    """Remove the XDG autostart .desktop file. Returns True on success."""
    home = _real_user_home()
    if not home:
        return False
    path = _desktop_path(home)
    try:
        if os.path.exists(path):
            os.unlink(path)
        return True
    except Exception:
        return False


def is_enabled() -> bool:
    home = _real_user_home()
    if not home:
        return False
    return os.path.exists(_desktop_path(home))
