from __future__ import annotations
import os
import shutil
import subprocess
import pwd
from typing import Callable

# Temp profile directories — reused across launches so Firefox remembers
# the session, but user.js is always rewritten with fresh proxy settings.
_PROFILE_TOR = "/tmp/entropy-shield-ff-tor"
_PROFILE_I2P = "/tmp/entropy-shield-ff-i2p"

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
    """Collect the minimum display/wayland env needed to spawn a GUI app."""
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

    # Wayland socket fallback
    if "WAYLAND_DISPLAY" not in env and "DISPLAY" not in env:
        try:
            for f in os.listdir(runtime_dir):
                if f.startswith("wayland-") and not f.endswith(".lock"):
                    env["WAYLAND_DISPLAY"] = f
                    break
        except Exception:
            pass

    return env


def _prepare_profile(profile_dir: str, user_js: str,
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


def _spawn_firefox(profile_dir: str, pw: pwd.struct_passwd,
                   env: dict[str, str], log: Callable[[str], None]) -> None:
    ff = shutil.which("firefox") or shutil.which("firefox-esr")
    if not ff:
        raise RuntimeError(
            "Firefox not found. Install firefox or firefox-esr.")

    ff_args = [ff, "--no-remote", "--profile", profile_dir]

    launched = False
    for cmd in (
        ["runuser", "-u", pw.pw_name, "--"] + ff_args,
        ["su", "-s", "/bin/sh", pw.pw_name, "-c",
         " ".join(f'"{a}"' if " " in a else a for a in ff_args)],
    ):
        try:
            subprocess.Popen(cmd, env=env,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            launched = True
            break
        except FileNotFoundError:
            continue

    if not launched:
        raise RuntimeError("runuser/su not found — cannot launch browser as user.")
    log(f"[BROWSER] Firefox launched (profile: {profile_dir}).")


def launch_tor(socks_port: int, log: Callable[[str], None]) -> None:
    """Open a fresh Firefox window routed entirely through Tor's SocksPort."""
    info = _real_user()
    if info is None:
        raise RuntimeError(
            "Cannot determine real user UID (run via sudo or pkexec).")
    uid, pw = info
    env = _session_env(uid, pw)
    user_js = _TOR_USER_JS.format(socks_port=socks_port)
    _prepare_profile(_PROFILE_TOR, user_js, uid, pw.pw_gid)
    _spawn_firefox(_PROFILE_TOR, pw, env, log)


def launch_i2p(http_port: int, socks_port: int,
               log: Callable[[str], None]) -> None:
    """Open a fresh Firefox window routed through I2P's HTTP proxy."""
    info = _real_user()
    if info is None:
        raise RuntimeError(
            "Cannot determine real user UID (run via sudo or pkexec).")
    uid, pw = info
    env = _session_env(uid, pw)
    user_js = _I2P_USER_JS.format(http_port=http_port, socks_port=socks_port)
    _prepare_profile(_PROFILE_I2P, user_js, uid, pw.pw_gid)
    _spawn_firefox(_PROFILE_I2P, pw, env, log)
