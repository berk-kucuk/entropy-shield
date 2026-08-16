from __future__ import annotations
import os
import shutil
import stat
import subprocess
import pwd
from typing import Callable

# Browser profile directories.
#
# SECURITY: these used to be fixed paths in /tmp ("/tmp/entropy-shield-ff-tor",
# …).  On a shared machine /tmp is world-writable, so any other local account
# could create those names first — as a directory it owns, or as a symlink to
# somewhere else — and then own the profile that "Open Tor Browser" launches.
# Because Firefox reads prefs.js from the profile, that is enough to turn the
# proxy back off and route the user's supposedly-anonymous session straight to
# the clearnet, as well as to read the resulting history and cookies afterwards.
# The profiles now live under the invoking user's own 0700 runtime directory,
# which no other unprivileged account can create or traverse.
_PROFILE_NAMES = {
    "ff-tor": "firefox-tor",
    "ff-i2p": "firefox-i2p",
    "cr-tor": "chromium-tor",
    "cr-i2p": "chromium-i2p",
}

# The old world-writable locations, removed on first use so an attacker-planted
# directory left over from a previous version cannot linger.
_LEGACY_PROFILES = [
    "/tmp/entropy-shield-ff-tor",
    "/tmp/entropy-shield-ff-i2p",
    "/tmp/entropy-shield-cr-tor",
    "/tmp/entropy-shield-cr-i2p",
]

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

# Terminal emulators in priority order.
# Most inherit env from the Popen call; gnome-terminal routes through a D-Bus
# daemon so proxy vars must be passed explicitly via env(1).
_TERMINAL_BINS    = [
    "alacritty", "kitty", "foot", "wezterm",
    "gnome-terminal", "konsole", "xfce4-terminal", "mate-terminal",
    "lxterminal", "xterm",
]
_DBUS_TERMINALS = {"gnome-terminal"}


def _real_user() -> tuple[int, pwd.struct_passwd] | None:
    # When running as root via pkexec/sudo, find the original user.
    for var in ("PKEXEC_UID", "SUDO_UID"):
        val = os.environ.get(var)
        if val and val.isdigit():
            uid = int(val)
            try:
                return uid, pwd.getpwuid(uid)
            except KeyError:
                pass
    # When the GUI runs as a normal user (runner subprocess handles root ops),
    # use the current process's own UID directly.
    uid = os.getuid()
    if uid != 0:
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
                with open(f"/proc/{pid_s}/status") as _sf:
                    if f"\nUid:\t{uid}\t" not in _sf.read():
                        continue
                with open(f"/proc/{pid_s}/environ", "rb") as _ef:
                    raw = _ef.read()
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


def _drop_legacy_profiles() -> None:
    """Delete the old /tmp profile directories, but only if we own them.

    If another account planted one of these names we must not follow it or
    delete its contents — leaving it alone is safe now that nothing reads it.
    """
    for path in _LEGACY_PROFILES:
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode):
            # A symlink at a world-writable path: only unlink the link itself.
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        if st.st_uid != os.getuid():
            continue
        shutil.rmtree(path, ignore_errors=True)


def _profile_dir(key: str, uid: int, pw: pwd.struct_passwd) -> str:
    """Return a private, user-owned directory for browser profile *key*.

    Prefers the per-user runtime directory (/run/user/<uid>, mode 0700, wiped
    on logout — ideal for a privacy tool); falls back to ~/.cache when the
    runtime directory is unavailable.
    """
    runtime_dir = f"/run/user/{uid}"
    if os.path.isdir(runtime_dir):
        base = os.path.join(runtime_dir, "entropy-shield", "profiles")
    else:
        base = os.path.join(pw.pw_dir, ".cache", "entropy-shield", "profiles")

    path = os.path.join(base, _PROFILE_NAMES[key])
    os.makedirs(path, mode=0o700, exist_ok=True)
    # makedirs() does not change the mode of directories that already exist,
    # and the parents are created with the process umask applied — tighten the
    # whole chain explicitly so the profile is never group/world readable.
    walk = path
    for _ in range(3):
        try:
            os.chmod(walk, 0o700)
            if os.geteuid() == 0:
                os.chown(walk, uid, pw.pw_gid)
        except OSError:
            pass
        walk = os.path.dirname(walk)
    return path


def _prepare_firefox_profile(profile_dir: str, user_js: str,
                              uid: int, gid: int) -> None:
    user_js_path = os.path.join(profile_dir, "user.js")
    # O_NOFOLLOW: never write through a symlink someone else may have left in
    # the profile directory.
    fd = os.open(user_js_path,
                 os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(user_js)
    try:
        os.chown(profile_dir, uid, gid)
        os.chown(user_js_path, uid, gid)
    except Exception:
        pass


def _spawn_as_user(cmd: list[str], pw: pwd.struct_passwd,
                   env: dict[str, str]) -> None:
    # GUI runs as a normal user — launch directly without privilege switching.
    if os.getuid() == pw.pw_uid:
        subprocess.Popen(cmd, env=env,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return
    # Running as root (legacy path) — switch to the real user first.
    for prefix in (
        ["runuser", "-u", pw.pw_name, "--"],
        ["su", "-s", "/bin/sh", pw.pw_name, "-c",
         " ".join(f'"{a}"' if " " in a else a for a in cmd)],
    ):
        try:
            full = prefix + cmd if prefix[0] == "runuser" else prefix
            subprocess.Popen(full, env=env,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue
    raise RuntimeError("runuser/su not found — cannot launch browser as user.")


def _find_terminal() -> tuple[str, str] | None:
    """Return (path, binary_name) of the first available terminal emulator."""
    for b in _TERMINAL_BINS:
        p = shutil.which(b)
        if p:
            return p, b
    return None


# ── public API ────────────────────────────────────────────────

def launch_tor(socks_port: int, log: Callable[[str], None]) -> None:
    """Open an isolated browser window routed through Tor's SocksPort."""
    info = _real_user()
    if info is None:
        raise RuntimeError(
            "Cannot determine real user UID (run via sudo or pkexec).")
    uid, pw = info
    env = _session_env(uid, pw)
    _drop_legacy_profiles()

    ff = _find_firefox()
    if ff:
        profile = _profile_dir("ff-tor", uid, pw)
        user_js = _TOR_USER_JS.format(socks_port=socks_port)
        _prepare_firefox_profile(profile, user_js, uid, pw.pw_gid)
        cmd = [ff, "--no-remote", "--profile", profile]
        _spawn_as_user(cmd, pw, env)
        log(f"[BROWSER] Firefox launched via Tor (profile: {profile}).")
        return

    cr = _find_chromium()
    if cr:
        profile = _profile_dir("cr-tor", uid, pw)
        cmd = [
            cr,
            f"--proxy-server=socks5://127.0.0.1:{socks_port}",
            "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost",
            f"--user-data-dir={profile}",
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
    _drop_legacy_profiles()

    ff = _find_firefox()
    if ff:
        profile = _profile_dir("ff-i2p", uid, pw)
        user_js = _I2P_USER_JS.format(http_port=http_port, socks_port=socks_port)
        _prepare_firefox_profile(profile, user_js, uid, pw.pw_gid)
        cmd = [ff, "--no-remote", "--profile", profile]
        _spawn_as_user(cmd, pw, env)
        log(f"[BROWSER] Firefox launched via I2P (profile: {profile}).")
        return

    cr = _find_chromium()
    if cr:
        profile = _profile_dir("cr-i2p", uid, pw)
        cmd = [
            cr,
            f"--proxy-server=http://127.0.0.1:{http_port}",
            f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-sync",
        ]
        _spawn_as_user(cmd, pw, env)
        log(f"[BROWSER] {os.path.basename(cr)} launched via I2P.")
        return

    raise RuntimeError(
        "No supported browser found. Install firefox, chromium, or brave.")


def launch_proxy_terminal(socks_port: int, log: Callable[[str], None]) -> None:
    """Open a terminal with Tor proxy env vars pre-set.

    The terminal inherits the proxy environment directly — only that terminal
    session is affected; no system-wide files are written.
    """
    info = _real_user()
    if info is None:
        raise RuntimeError(
            "Cannot determine real user UID (run via sudo or pkexec).")
    uid, pw = info
    env = _session_env(uid, pw)

    socks5h_url = f"socks5h://127.0.0.1:{socks_port}"
    no_proxy    = "localhost,127.0.0.1,127.0.0.0/8,::1"
    proxy_pairs = [
        ("ALL_PROXY",    socks5h_url), ("all_proxy",    socks5h_url),
        ("SOCKS_PROXY",  socks5h_url), ("socks_proxy",  socks5h_url),
        ("HTTP_PROXY",   socks5h_url), ("http_proxy",   socks5h_url),
        ("HTTPS_PROXY",  socks5h_url), ("https_proxy",  socks5h_url),
        ("NO_PROXY",     no_proxy),    ("no_proxy",     no_proxy),
    ]
    for k, v in proxy_pairs:
        env[k] = v

    result = _find_terminal()
    if result is None:
        raise RuntimeError(
            "No supported terminal emulator found. "
            "Install alacritty, kitty, foot, gnome-terminal, konsole, or xterm.")

    term_path, term_name = result
    user_shell = pw.pw_shell or "/usr/bin/bash"

    # Set SHELL so terminal emulators (konsole, alacritty, …) know which
    # shell to start.  Without this they may receive an empty string from
    # the stripped environment and fall back with a warning.
    env["SHELL"] = user_shell

    if term_name in _DBUS_TERMINALS:
        # gnome-terminal talks to a D-Bus daemon that spawns the shell, so
        # the Popen env dict won't reach the shell — pass vars via env(1).
        env_args = [f"{k}={v}" for k, v in proxy_pairs]
        cmd = [term_path, "--", "env"] + env_args + [user_shell]
    else:
        cmd = [term_path]

    _spawn_as_user(cmd, pw, env)
    log(f"[TERMINAL] {term_name} launched with Tor proxy environment.")
    log("[TERMINAL] Note: curl/wget use socks5h:// — curl works natively; "
        "wget does not support SOCKS (use curl or torsocks wget).")
