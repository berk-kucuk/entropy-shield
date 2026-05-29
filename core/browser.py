from __future__ import annotations
import os
import shutil
import subprocess
import pwd
from typing import Callable

_PROFILE_TOR      = "/tmp/entropy-shield-ff-tor"
_PROFILE_I2P      = "/tmp/entropy-shield-ff-i2p"
_PROFILE_CR_TOR   = "/tmp/entropy-shield-cr-tor"
_PROFILE_CR_I2P   = "/tmp/entropy-shield-cr-i2p"

_TOR_USER_JS = """\
user_pref("network.proxy.type", 1);
user_pref("network.proxy.socks", "127.0.0.1");
user_pref("network.proxy.socks_port", {socks_port});
user_pref("network.proxy.socks_version", 5);
user_pref("network.proxy.socks_remote_dns", true);
user_pref("network.proxy.no_proxies_on", "127.0.0.1,localhost,::1");
user_pref("network.dns.disablePrefetch", true);
user_pref("network.dns.disablePrefetchFromHTTPS", true);
user_pref("network.prefetch-next", false);
user_pref("media.peerconnection.enabled", false);
user_pref("browser.startup.homepage", "about:blank");
"""

_I2P_USER_JS = """\
user_pref("network.proxy.type", 1);
user_pref("network.proxy.http", "127.0.0.1");
user_pref("network.proxy.http_port", {http_port});
user_pref("network.proxy.ssl", "127.0.0.1");
user_pref("network.proxy.ssl_port", {http_port});
user_pref("network.proxy.socks", "127.0.0.1");
user_pref("network.proxy.socks_port", {socks_port});
user_pref("network.proxy.socks_version", 5);
user_pref("network.proxy.socks_remote_dns", true);
user_pref("network.proxy.no_proxies_on", "127.0.0.1,localhost,::1");
user_pref("network.dns.disablePrefetch", true);
user_pref("network.dns.disablePrefetchFromHTTPS", true);
user_pref("network.prefetch-next", false);
user_pref("media.peerconnection.enabled", false);
user_pref("browser.startup.homepage", "http://127.0.0.1:{http_port}");
"""

# Chromium/Brave candidates in priority order
_CHROMIUM_BINS = [
    "brave", "brave-browser",
    "chromium", "chromium-browser", "chromium-stable",
    "google-chrome", "google-chrome-stable",
]

_FIREFOX_BINS = ["firefox", "firefox-esr"]


def _real_user() -> tuple[int, pwd.struct_passwd] | None:
    for var in ("PKEXEC_UID", "SUDO_UID"):
        val = os.environ.get(var)
        if val and val.isdigit():
            uid = int(val)
            try:
                return uid, pwd.getpwuid(uid)
            except KeyError:
                pass
    return None


def _session_env(uid: int, pw: pwd.struct_passwd) -> dict[str, str]:
    runtime_dir = f"/run/user/{uid}"
    env: dict[str, str] = {
        "HOME":                     pw.pw_dir,
        "USER":                     pw.pw_name,
        "LOGNAME":                  pw.pw_name,
        "XDG_RUNTIME_DIR":          runtime_dir,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_dir}/bus",
        "PATH":                     "/usr/bin:/bin:/usr/local/bin",
    }
    _WANT = {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
             "XDG_RUNTIME_DIR", "QT_QPA_PLATFORM"}
    try:
        for pid_s in os.listdir("/proc"):
            if not pid_s.isdigit():
                continue
            try:
                if f"\nUid:\t{uid}\t" not in open(f"/proc/{pid_s}/status").read():
                    continue
                raw = open(f"/proc/{pid_s}/environ", "rb").read()
                for item in raw.split(b"\x00"):
                    if b"=" not in item:
                        continue
                    k, _, v = item.partition(b"=")
                    key = k.decode(errors="replace")
                    if key in _WANT:
                        env.setdefault(key, v.decode(errors="replace"))
                if "DISPLAY" in env or "WAYLAND_DISPLAY" in env:
                    break
            except (PermissionError, FileNotFoundError, ProcessLookupError):
                continue
    except Exception:
        pass

    if "WAYLAND_DISPLAY" not in env and "DISPLAY" not in env:
        try:
            for f in os.listdir(runtime_dir):
                if f.startswith("wayland-") and not f.endswith(".lock"):
                    env["WAYLAND_DISPLAY"] = f
                    break
        except Exception:
            pass

    return env


def _find_firefox() -> str | None:
    for b in _FIREFOX_BINS:
        p = shutil.which(b)
        if p:
            return p
    return None


def _find_chromium() -> str | None:
    for b in _CHROMIUM_BINS:
        p = shutil.which(b)
        if p:
            return p
    return None


def _prepare_firefox_profile(profile_dir: str, user_js: str,
                              uid: int, gid: int) -> None:
    os.makedirs(profile_dir, exist_ok=True)
    user_js_path = os.path.join(profile_dir, "user.js")
    with open(user_js_path, "w") as f:
        f.write(user_js)
    try:
        os.chown(profile_dir, uid, gid)
        os.chown(user_js_path, uid, gid)
    except Exception:
        pass


def _spawn_as_user(cmd: list[str], pw: pwd.struct_passwd,
                   env: dict[str, str]) -> None:
    launched = False
    for prefix in (
        ["runuser", "-u", pw.pw_name, "--"],
        ["su", "-s", "/bin/sh", pw.pw_name, "-c",
         " ".join(f'"{a}"' if " " in a else a for a in cmd)],
    ):
        try:
            if prefix[0] in ("runuser",):
                full = prefix + cmd
            else:
                full = prefix  # su variant already embeds cmd
            subprocess.Popen(full, env=env,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            launched = True
            break
        except FileNotFoundError:
            continue
    if not launched:
        raise RuntimeError("runuser/su not found — cannot launch browser as user.")


# ── public API ────────────────────────────────────────────────

def launch_tor(socks_port: int, log: Callable[[str], None]) -> None:
    """Open an isolated browser window routed through Tor's SocksPort."""
    info = _real_user()
    if info is None:
        raise RuntimeError(
            "Cannot determine real user UID (run via sudo or pkexec).")
    uid, pw = info
    env = _session_env(uid, pw)

    ff = _find_firefox()
    if ff:
        user_js = _TOR_USER_JS.format(socks_port=socks_port)
        _prepare_firefox_profile(_PROFILE_TOR, user_js, uid, pw.pw_gid)
        cmd = [ff, "--no-remote", "--profile", _PROFILE_TOR]
        _spawn_as_user(cmd, pw, env)
        log(f"[BROWSER] Firefox launched via Tor (profile: {_PROFILE_TOR}).")
        return

    cr = _find_chromium()
    if cr:
        os.makedirs(_PROFILE_CR_TOR, exist_ok=True)
        try:
            os.chown(_PROFILE_CR_TOR, uid, pw.pw_gid)
        except Exception:
            pass
        cmd = [
            cr,
            f"--proxy-server=socks5://127.0.0.1:{socks_port}",
            "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost",
            f"--user-data-dir={_PROFILE_CR_TOR}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-sync", "--incognito",
        ]
        _spawn_as_user(cmd, pw, env)
        log(f"[BROWSER] {os.path.basename(cr)} launched via Tor.")
        return

    raise RuntimeError(
        "No supported browser found. Install firefox, chromium, or brave.")


def launch_i2p(http_port: int, socks_port: int,
               log: Callable[[str], None]) -> None:
    """Open an isolated browser window routed through I2P's HTTP proxy."""
    info = _real_user()
    if info is None:
        raise RuntimeError(
            "Cannot determine real user UID (run via sudo or pkexec).")
    uid, pw = info
    env = _session_env(uid, pw)

    ff = _find_firefox()
    if ff:
        user_js = _I2P_USER_JS.format(http_port=http_port, socks_port=socks_port)
        _prepare_firefox_profile(_PROFILE_I2P, user_js, uid, pw.pw_gid)
        cmd = [ff, "--no-remote", "--profile", _PROFILE_I2P]
        _spawn_as_user(cmd, pw, env)
        log(f"[BROWSER] Firefox launched via I2P (profile: {_PROFILE_I2P}).")
        return

    cr = _find_chromium()
    if cr:
        os.makedirs(_PROFILE_CR_I2P, exist_ok=True)
        try:
            os.chown(_PROFILE_CR_I2P, uid, pw.pw_gid)
        except Exception:
            pass
        cmd = [
            cr,
            f"--proxy-server=http://127.0.0.1:{http_port}",
            f"--user-data-dir={_PROFILE_CR_I2P}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-sync",
        ]
        _spawn_as_user(cmd, pw, env)
        log(f"[BROWSER] {os.path.basename(cr)} launched via I2P.")
        return

    raise RuntimeError(
        "No supported browser found. Install firefox, chromium, or brave.")
