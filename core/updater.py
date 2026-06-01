"""Auto-update checker — queries GitHub Releases for a newer version."""
from __future__ import annotations

VERSION = "2.3.0"
_GITHUB_REPO = "berk-kucuk/entropy-shield"


def _version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.lstrip("v").split(".")[:3])
    except Exception:
        return (0,)


def check_for_update() -> tuple[bool, str]:
    """Return (update_available, latest_version_str).

    Makes a single HTTPS request to GitHub API.  Returns (False, "")
    on any network or parse error so callers never raise.
    """
    import urllib.request
    import json
    import ssl

    url = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"entropy-shield/{VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            data = json.loads(r.read().decode())
        latest = data.get("tag_name", "").lstrip("v").strip()
        if not latest:
            return False, ""
        return _version_tuple(latest) > _version_tuple(VERSION), latest
    except Exception:
        return False, ""
