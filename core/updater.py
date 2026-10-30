"""Auto-update checker — queries GitHub Releases for a newer version."""
from __future__ import annotations
import json
import subprocess

VERSION = "4.2.1"
_GITHUB_REPO = "berk-kucuk/entropy-shield"


def _version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.lstrip("v").split(".")[:3])
    except Exception:
        return (0,)


def check_for_update(socks_port: int) -> tuple[bool, str]:
    """Return (update_available, latest_version_str).

    Makes a single HTTPS request to the GitHub API **through Tor's SocksPort**
    (socks5h: the hostname is resolved by Tor too), with a generic
    User-Agent.  A privacy tool must not announce itself from the user's real
    address: the old clearnet request at startup sent
    "User-Agent: entropy-shield/<ver>" from the real IP before any layer was up.
    Returns (False, "") on any network or parse error so callers never raise.
    """
    url = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
    try:
        r = subprocess.run(
            ["curl", "-sS", "--fail", "--max-time", "20", "--proto", "=https",
             "--socks5-hostname", f"127.0.0.1:{int(socks_port)}",
             "-H", "Accept: application/vnd.github+json",
             "-A", "curl",
             url],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False, ""
        data = json.loads(r.stdout)
        latest = str(data.get("tag_name", "")).lstrip("v").strip()
        if not latest:
            return False, ""
        return _version_tuple(latest) > _version_tuple(VERSION), latest
    except Exception:
        return False, ""
