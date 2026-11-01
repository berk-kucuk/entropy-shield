"""Where "run through Tor" keeps its sockets and state.

Everything per-user lives in the user's runtime directory (tmpfs, 0700, gone
at logout), except Tor's guard state, which is meant to persist. Unix socket
paths must stay under 108 bytes, which $XDG_RUNTIME_DIR always is.
"""
from __future__ import annotations

import os
from pathlib import Path

# Entropy Shield's own Tor, while a system-wide session runs. The directory is
# root:entropy-shield 0750 (tmpfiles.d), so only members of the group that may
# use Entropy Shield at all can reach the socket inside it.
SYSTEM_SOCKS = Path("/run/entropy-shield/tor-socks/socks.sock")


def _private_dir(path: Path, create: bool) -> Path:
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    return path


def run_dir(create: bool = False) -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return _private_dir(Path(base) / "entropy-shield-torify", create)


def sessions_dir(create: bool = False) -> Path:
    return _private_dir(run_dir(create) / "sessions", create)


def service_socket() -> Path:
    return run_dir() / "service.sock"


def tor_data_dir(create: bool = False) -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return _private_dir(Path(base) / "entropy-shield" / "torify-tor", create)


def app_root() -> str:
    """The directory that holds the `core` package (/opt/entropy-shield)."""
    return str(Path(__file__).resolve().parents[2])
