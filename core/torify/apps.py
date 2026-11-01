"""What the window offers to run through Tor, and how it starts them.

The window never runs an app itself: it starts `entropy-shield-run` detached
(no terminal, so progress and errors arrive as notifications) and forgets
about it. The running apps are read back from the per-user service.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core.torify import paths

# Terminals that run as one process per window. gnome-terminal and friends
# hand every window to a server started on D-Bus — outside the namespace —
# so a "Tor terminal" from them would not be one.
_TERMINALS = (
    ("maze-terminal", []),
    ("konsole", ["--separate"]),
    ("alacritty", []),
    ("kitty", []),
    ("foot", []),
    ("wezterm", ["start", "--always-new-process"]),
    ("xterm", []),
)
_BROWSERS = ("firefox", "librewolf", "firefox-esr", "chromium", "brave", "brave-browser",
             "google-chrome-stable", "vivaldi-stable")


@dataclass
class App:
    name: str
    path: str
    icon: str = ""
    comment: str = ""


def _desktop_dirs() -> list[Path]:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    dirs = [data_home] + (os.environ.get("XDG_DATA_DIRS") or
                          "/usr/local/share:/usr/share").split(":")
    out = [Path(d) / "applications" for d in dirs if d]
    out.append(Path("/var/lib/flatpak/exports/share/applications"))
    out.append(Path.home() / ".local/share/flatpak/exports/share/applications")
    return out


def _entry(path: Path, lang: str) -> App | None:
    fields: dict[str, str] = {}
    in_entry = False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("["):
                    if in_entry:
                        break
                    in_entry = line == "[Desktop Entry]"
                    continue
                if in_entry and "=" in line:
                    k, v = line.split("=", 1)
                    fields.setdefault(k.strip(), v.strip())
    except OSError:
        return None
    if fields.get("Type", "Application") != "Application":
        return None
    if fields.get("NoDisplay") == "true" or fields.get("Hidden") == "true":
        return None
    if not fields.get("Exec") or fields.get("Terminal") == "true":
        return None
    name = fields.get(f"Name[{lang}]") or fields.get("Name") or path.stem
    comment = fields.get(f"Comment[{lang}]") or fields.get("Comment", "")
    return App(name=name, path=str(path), icon=fields.get("Icon", ""), comment=comment)


def installed_apps(lang: str = "en") -> list[App]:
    """Menu apps, de-duplicated by file name (the user's copy wins)."""
    seen: dict[str, App] = {}
    for d in _desktop_dirs():
        try:
            files = sorted(d.glob("*.desktop"))
        except OSError:
            continue
        for f in files:
            if f.name in seen or f.name.startswith("entropy-shield"):
                continue
            app = _entry(f, lang)
            if app is not None:
                seen[f.name] = app
    return sorted(seen.values(), key=lambda a: a.name.lower())


def terminal_command() -> list[str] | None:
    for name, extra in _TERMINALS:
        path = shutil.which(name)
        if path:
            return [path, *extra]
    return None


def browser_command() -> list[str] | None:
    for name in _BROWSERS:
        path = shutil.which(name)
        if path:
            return [path]
    return None


def launcher_argv() -> list[str]:
    installed = shutil.which("entropy-shield-run")
    if installed:
        return [installed]
    return [sys.executable, "-P", "-m", "core.torify.launcher"]


def spawn(args: list[str]) -> None:
    """Start entropy-shield-run detached. Errors reach the user as notifications."""
    # The app starts in the home folder, as it would from the app menu.
    subprocess.Popen([*launcher_argv(), *args], cwd=str(Path.home()),
                     env=dict(os.environ, PYTHONPATH=paths.app_root()),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
