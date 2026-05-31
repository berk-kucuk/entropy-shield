"""Panic / Emergency wipe helpers (user-space, no root required)."""
from __future__ import annotations
import subprocess
import os


def flush_dns_cache() -> None:
    """Try every known DNS cache flush method."""
    for cmd in (
        ["resolvectl", "flush-caches"],
        ["systemd-resolve", "--flush-caches"],
        ["nscd", "-i", "hosts"],
    ):
        try:
            subprocess.run(cmd, capture_output=True, timeout=3)
        except Exception:
            pass


def clear_proxy_env() -> None:
    """Remove proxy env vars from the current process environment."""
    for var in (
        "ALL_PROXY", "all_proxy", "SOCKS_PROXY", "socks_proxy",
        "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
        "NO_PROXY", "no_proxy",
    ):
        os.environ.pop(var, None)


def clear_temp_traces() -> None:
    """Remove entropy-shield proxy env files written to disk."""
    _env_file = os.path.expanduser(
        "~/.config/environment.d/entropy-shield-proxy.conf"
    )
    for path in (
        _env_file,
        "/etc/profile.d/entropy-shield-proxy.sh",
        "/etc/fish/conf.d/entropy-shield-proxy.fish",
    ):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except Exception:
            pass


def user_space_panic(log_fn=None) -> None:
    """Everything we can do without root: flush DNS, clear env, remove files."""
    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    _log("[PANIC] Flushing DNS cache…")
    flush_dns_cache()

    _log("[PANIC] Clearing proxy environment variables…")
    clear_proxy_env()

    _log("[PANIC] Removing proxy env files…")
    clear_temp_traces()

    _log("[PANIC] User-space cleanup complete.")
