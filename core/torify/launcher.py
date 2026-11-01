"""entropy-shield-run — start one app with all of its traffic through Tor.

    entropy-shield-run firefox
    entropy-shield-run --desktop /usr/share/applications/org.kde.konversation.desktop
    entropy-shield-run --keep-timezone --allow-system-bus -- some-app --flag

Runs as the user; needs no password. Prints progress on stderr and, when not
started from a terminal (Dolphin, the app menu, the Entropy Shield window),
shows it as desktop notifications instead.

Browsers get a fresh, empty profile for every run. Without it, a second
`firefox` hands its window to the Firefox that is already open — outside
Tor — and exits; a separate profile makes it a separate browser. Any other
app that does the same is caught afterwards: a run that ends cleanly within a
few seconds is reported as a likely hand-off rather than as success.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

from core.torify import paths  # noqa: E402

EXIT_SETUP = 125


class LaunchError(Exception):
    pass


# ── i18n (the window and the menu entries carry the rest) ────────────────────

_MSG = {
    "en": {
        "connecting": "Connecting to Tor… {pct}%",
        "starting": "Starting {app} through Tor",
        "started": "{app} is running through Tor{via}",
        "via_system": " (Entropy Shield's Tor)",
        "handoff": "{app} closed right away. It probably handed over to a copy "
                   "that was already open — and that copy is NOT using Tor. "
                   "Close {app} everywhere, then try again.",
        "failed": "Could not start {app} through Tor: {err}",
        "no_userns": "this system does not allow unprivileged user namespaces",
        "no_unshare": "util-linux 2.38 or newer is needed (unshare --map-user)",
    },
    "tr": {
        "connecting": "Tor'a bağlanılıyor… %{pct}",
        "starting": "{app} Tor üzerinden başlatılıyor",
        "started": "{app} Tor üzerinden çalışıyor{via}",
        "via_system": " (Entropy Shield'in Tor'u)",
        "handoff": "{app} hemen kapandı. Büyük olasılıkla zaten açık olan bir "
                   "kopyasına devretti — o kopya Tor KULLANMIYOR. {app} uygulamasını "
                   "her yerde kapatıp yeniden deneyin.",
        "failed": "{app} Tor üzerinden başlatılamadı: {err}",
        "no_userns": "bu sistem yetkisiz kullanıcı ad alanlarına izin vermiyor",
        "no_unshare": "util-linux 2.38 veya üstü gerekiyor (unshare --map-user)",
    },
}


def _lang() -> str:
    try:
        from core.config import cfg
        lang = str(cfg().get("language") or "")
    except Exception:  # noqa: BLE001
        lang = ""
    if not lang:
        lang = (os.environ.get("LANG") or "en")[:2]
    return lang if lang in _MSG else "en"


def msg(key: str, **kw) -> str:
    return _MSG[_lang()].get(key, _MSG["en"][key]).format(**kw)


# ── .desktop files ───────────────────────────────────────────────────────────

_FIELD_CODES = {"%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%i", "%c", "%k", "%v", "%m"}


def parse_desktop(path: str) -> tuple[str, list[str]]:
    """(Name, argv) from a .desktop file's [Desktop Entry]."""
    name, exec_line, in_entry = "", "", False
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("["):
                in_entry = line == "[Desktop Entry]"
                continue
            if not in_entry or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key == "Name" and not name:
                name = value
            elif key == "Exec" and not exec_line:
                exec_line = value
    if not exec_line:
        raise LaunchError(f"no Exec= line in {path}")
    argv = [a for a in shlex.split(exec_line) if a not in _FIELD_CODES]
    argv = [a.replace("%%", "%") for a in argv]
    if not argv:
        raise LaunchError(f"empty Exec= line in {path}")
    return name or Path(path).stem, argv


# ── browsers ─────────────────────────────────────────────────────────────────

_FIREFOX = ("firefox", "firefox-esr", "firefox-developer-edition", "librewolf", "waterfox")
_CHROMIUM = ("chromium", "google-chrome", "google-chrome-stable", "brave", "brave-browser",
             "vivaldi", "vivaldi-stable", "microsoft-edge", "microsoft-edge-stable")

# Firefox: no WebRTC (it can reveal addresses even through a proxy), no
# connectivity pings, no DoH — DNS already goes through Tor here.
_FIREFOX_PREFS = """\
user_pref("media.peerconnection.enabled", false);
user_pref("network.dns.disablePrefetch", true);
user_pref("network.prefetch-next", false);
user_pref("network.trr.mode", 5);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
"""


def browser_profile(argv: list[str], session: str) -> tuple[list[str], Path | None]:
    """argv with a fresh profile added for a known browser, and that profile."""
    exe = Path(argv[0]).name
    if exe not in _FIREFOX and exe not in _CHROMIUM:
        return argv, None
    prof = Path(tempfile.mkdtemp(prefix=f"profile-{session}-",
                                 dir=paths.run_dir(create=True)))
    if exe in _FIREFOX:
        (prof / "user.js").write_text(_FIREFOX_PREFS)
        return [argv[0], "--no-remote", "--profile", str(prof), *argv[1:]], prof
    return [argv[0], f"--user-data-dir={prof}", "--no-first-run",
            "--no-default-browser-check", "--disable-sync",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", *argv[1:]], prof


# ── preconditions ────────────────────────────────────────────────────────────

def check_system() -> None:
    if not shutil.which("unshare"):
        raise LaunchError(msg("no_unshare"))
    help_text = subprocess.run(["unshare", "--help"], capture_output=True, text=True).stdout
    if "--map-user" not in help_text:
        raise LaunchError(msg("no_unshare"))
    r = subprocess.run(["unshare", "--user", "--map-root-user", "--net", "true"],
                       capture_output=True)
    if r.returncode != 0:
        raise LaunchError(msg("no_userns"))


# ── the service ──────────────────────────────────────────────────────────────

def _connect_service() -> socket.socket | None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(paths.service_socket()))
        return s
    except OSError:
        s.close()
        return None


def service_connection() -> socket.socket:
    s = _connect_service()
    if s is not None:
        return s
    run = paths.run_dir(create=True)
    log = open(run / "service.log", "ab")
    subprocess.Popen([sys.executable, "-P", "-m", "core.torify.service"],
                     cwd=paths.app_root(), env=dict(os.environ, PYTHONPATH=paths.app_root()),
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     start_new_session=True)
    log.close()
    deadline = time.time() + 10
    while time.time() < deadline:
        s = _connect_service()
        if s is not None:
            return s
        time.sleep(0.1)
    raise LaunchError("the Tor service did not start")


def request(op: dict) -> dict:
    """One request/reply to the service (list, new_identity, status, end)."""
    s = service_connection() if op.get("op") != "list" else _connect_service()
    if s is None:
        return {"ok": True, "sessions": []}
    with s, s.makefile("rwb") as f:
        f.write((json.dumps(op) + "\n").encode())
        f.flush()
        line = f.readline()
    return json.loads(line) if line else {"ok": False, "error": "no reply"}


# ── notifications ────────────────────────────────────────────────────────────

class Notifier:
    def __init__(self, gui: bool):
        self.gui = gui and bool(shutil.which("notify-send"))
        self._id = ""

    def __call__(self, text: str, urgent: bool = False) -> None:
        print(text, file=sys.stderr, flush=True)
        if not self.gui:
            return
        cmd = ["notify-send", "--app-name=Entropy Shield", "--icon=entropy-shield",
               "--print-id"]
        if self._id:
            cmd.append(f"--replace-id={self._id}")
        if urgent:
            cmd.append("--urgency=critical")
        try:
            out = subprocess.run(cmd + ["Entropy Shield", text], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
            self._id = out or self._id
        except (OSError, subprocess.SubprocessError):
            pass


# ── run ──────────────────────────────────────────────────────────────────────

def run(name: str, argv: list[str], keep_timezone: bool = False,
        allow_system_bus: bool = False, notify: Notifier | None = None,
        warn_handoff: bool = True) -> int:
    notify = notify or Notifier(gui=False)
    check_system()
    notify(msg("starting", app=name))

    svc = service_connection()
    f = svc.makefile("rwb")
    f.write((json.dumps({"op": "prepare", "app": name}) + "\n").encode())
    f.flush()
    reply: dict = {}
    last_pct = -1
    while True:
        line = f.readline()
        if not line:
            raise LaunchError("the Tor service closed the connection")
        reply = json.loads(line)
        if "progress" in reply:
            pct = int(reply["progress"])
            if pct // 25 != last_pct // 25 or pct == 100:
                notify(msg("connecting", pct=pct))
            last_pct = pct
            continue
        break
    if not reply.get("ok"):
        raise LaunchError(reply.get("error") or "Tor is not available")

    session = reply["session"]
    argv, profile = browser_profile(argv, session)
    env = {"ENTROPY_SHIELD_TOR": "1"}
    if not keep_timezone:
        env["TZ"] = "UTC"
    r_fd, w_fd = os.pipe()
    cfg = {"socks": reply["socks"], "token": reply["token"], "app_root": paths.app_root(),
           "uid": os.getuid(), "gid": os.getgid(), "argv": argv, "env": env,
           "cwd": os.getcwd(), "hide_system_bus": not allow_system_bus, "status_fd": w_fd}
    cfg_path = paths.sessions_dir(create=True) / f"{session}.json"
    fd = os.open(cfg_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as cf:
        json.dump(cfg, cf)

    proc = subprocess.Popen(
        ["unshare", "--user", "--map-root-user", "--net", "--mount", "--pid", "--fork",
         "--mount-proc", "--kill-child", sys.executable, "-P", "-m", "core.torify.netns",
         str(cfg_path)],
        pass_fds=(w_fd,), cwd=paths.app_root(),
        env=dict(os.environ, PYTHONPATH=paths.app_root()))
    os.close(w_fd)

    events = os.fdopen(r_fd, "rb")
    result: dict = {}
    try:
        for raw in events:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if ev.get("event") == "started":
                notify(msg("started", app=name,
                           via=msg("via_system") if reply.get("via") == "system" else ""))
            elif ev.get("event") == "error":
                result = ev
            elif ev.get("event") == "exited":
                result = ev
        code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        code = proc.wait()
    finally:
        events.close()
        f.close()
        svc.close()            # ends the session in the service
        cfg_path.unlink(missing_ok=True)
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)

    if result.get("event") == "error":
        raise LaunchError(f"{result.get('reason')}: {result.get('detail', '')}".strip(": "))
    if result.get("handoff") and warn_handoff:
        notify(msg("handoff", app=name), urgent=True)
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="entropy-shield-run",
                                 description="Run one app with all of its traffic through Tor.")
    ap.add_argument("--desktop", help="a .desktop file to run")
    ap.add_argument("--name", help="name shown in notifications and the app list")
    ap.add_argument("--keep-timezone", action="store_true",
                    help="keep the real time zone (default: UTC, so it is not revealed)")
    ap.add_argument("--allow-system-bus", action="store_true",
                    help="let the app use the system D-Bus (closed by default: it can "
                         "resolve names outside Tor)")
    ap.add_argument("--browser", action="store_true",
                    help="a fresh browser (Firefox, LibreWolf, Chromium, Brave)")
    ap.add_argument("--terminal", action="store_true",
                    help="a terminal whose commands go through Tor")
    ap.add_argument("--list", action="store_true", help="list apps running through Tor")
    ap.add_argument("--new-identity", metavar="SESSION", help="new Tor circuits for an app")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)

    if args.list:
        print(json.dumps(request({"op": "list"}), indent=2))
        return 0
    if args.new_identity:
        return 0 if request({"op": "new_identity", "session": args.new_identity}).get("ok") else 1

    notify = Notifier(gui=not sys.stderr.isatty())
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    # Dolphin's "Run through Tor" passes the file it was used on.
    if not args.desktop and len(command) == 1 and command[0].endswith(".desktop") \
            and os.path.isfile(command[0]):
        args.desktop, command = command[0], []
    try:
        if args.browser or args.terminal:
            from core.torify import apps
            cmd = apps.browser_command() if args.browser else apps.terminal_command()
            if cmd is None:
                raise LaunchError("no supported browser found" if args.browser
                                  else "no suitable terminal found")
            name = "Tor Browser" if args.browser else "Tor Terminal"
            args.desktop = args.desktop or "menu"   # started from a menu: warn on hand-off
        elif args.desktop:
            name, cmd = parse_desktop(args.desktop)
        elif command:
            name, cmd = Path(command[0]).name, command
        else:
            ap.error("give a command or --desktop FILE")
        name = args.name or name
        # A terminal command that finishes in a second is just a short
        # command; only an app started from a menu "handing off" is suspect.
        warn = bool(args.desktop) or not sys.stdin.isatty()
        return run(name, cmd, args.keep_timezone, args.allow_system_bus, notify, warn)
    except LaunchError as e:
        notify(msg("failed", app=args.name or (command[0] if command else "app"), err=e),
               urgent=True)
        return EXIT_SETUP


if __name__ == "__main__":
    sys.exit(main())
