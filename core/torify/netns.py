"""Set up an app's network namespace, run the app in it, tear down after.

Started by the launcher (launcher.py) as

    unshare --user --map-root-user --net --mount --pid --fork --mount-proc \\
        python3 -m core.torify.netns <config.json>

so this runs as PID 1 of a fresh PID namespace, as "root" of a fresh user
namespace that maps to the real user — it can configure the namespace's
network and mounts, and nothing outside it. No real privilege is involved.

The namespace gets no real network interface at all. Its only routes lead to
a dummy device that discards everything, and nftables:

  * redirects every DNS query (any address, port 53) to the fake-IP DNS,
  * redirects every other TCP connection to the proxy,
  * drops every packet whose destination is not this namespace's own,
  * drops all IPv6 (which is also switched off).

So UDP, ICMP and anything the proxy does not carry simply go nowhere. The
proxy reaches Tor over a Unix socket; if it or Tor dies, the app is cut off.

Name lookups that would bypass the namespace's network are closed too: the
systemd-resolved and Avahi sockets are hidden (glibc's nss modules talk to
them over Unix sockets, which would resolve names on the host network), and
so is the system D-Bus, through which resolved and NetworkManager answer
lookups as well.

The app itself runs one user namespace deeper, mapped back to the real uid,
so it is the user — not "root" — as far as it can tell. Chromium and Electron
refuse to run as root, and nothing should run as root that does not need to.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

from core.torify.proxy import DNS_PORT, TRANS_PORT  # noqa: E402

DUMMY_ADDR = "10.255.255.1"

NFT_RULES = f"""\
table ip torify {{
  chain nat_out {{
    type nat hook output priority -100;
    udp dport 53 redirect to :{DNS_PORT}
    tcp dport 53 redirect to :{DNS_PORT}
    ip daddr 127.0.0.0/8 return
    meta l4proto tcp redirect to :{TRANS_PORT}
  }}
  chain filter_out {{
    type filter hook output priority 0; policy drop;
    fib daddr type local accept
  }}
}}
table ip6 torify6 {{
  chain filter_out {{
    type filter hook output priority 0; policy drop;
  }}
}}
"""

# Unix sockets through which a name could be resolved on the host network.
HIDDEN_DIRS = ("/run/systemd/resolve", "/run/avahi-daemon", "/var/run/avahi-daemon")
SYSTEM_BUS = "/run/dbus/system_bus_socket"

# A process tree that is gone this quickly, having exited cleanly, almost
# always handed its work to a copy of the app already running outside —
# Firefox, Electron and most single-instance apps do — and that copy is not
# going through Tor.
HANDOFF_SECONDS = 4.0


def _run(*cmd: str, stdin: str | None = None) -> None:
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])}: {(r.stderr or r.stdout).strip()}")


def _sysctl(key: str, value: str) -> None:
    with open("/proc/sys/" + key.replace(".", "/"), "w") as f:
        f.write(value)


def setup_network() -> None:
    _run("ip", "link", "set", "lo", "up")
    _run("ip", "link", "add", "torify0", "type", "dummy")
    _run("ip", "link", "set", "torify0", "up")
    _run("ip", "addr", "add", f"{DUMMY_ADDR}/32", "dev", "torify0")
    # A default route, so connect() to an outside address gets as far as
    # nftables (and the redirect) instead of failing with "unreachable".
    _run("ip", "route", "add", "default", "dev", "torify0")
    try:
        _sysctl("net.ipv6.conf.all.disable_ipv6", "1")
        _sysctl("net.ipv6.conf.default.disable_ipv6", "1")
    except OSError:
        pass     # the ip6 table drops everything anyway
    _run("nft", "-f", "-", stdin=NFT_RULES)


def hide_host_resolvers(hide_system_bus: bool) -> list[str]:
    hidden = []
    for d in HIDDEN_DIRS:
        if os.path.isdir(d) and not os.path.islink(d):
            _run("mount", "-t", "tmpfs", "-o", "mode=755,size=4k", "tmpfs", d)
            hidden.append(d)
    if hide_system_bus and os.path.exists(SYSTEM_BUS):
        _run("mount", "--bind", "/dev/null", SYSTEM_BUS)
        hidden.append(SYSTEM_BUS)
    return hidden


def start_proxy(socks: str, token: str, app_root: str) -> subprocess.Popen:
    r, w = os.pipe()
    env = dict(os.environ, PYTHONPATH=app_root)
    proc = subprocess.Popen(
        [sys.executable, "-P", "-m", "core.torify.proxy", "--socks", socks,
         "--token", token, "--ready-fd", str(w)],
        pass_fds=(w,), env=env, cwd=app_root)
    os.close(w)
    deadline = time.time() + 10
    buf = b""
    import select
    while time.time() < deadline and b"ok" not in buf:
        ready, _, _ = select.select([r], [], [], 0.2)
        if ready:
            chunk = os.read(r, 16)
            if not chunk:
                break
            buf += chunk
        if proc.poll() is not None:
            break
    os.close(r)
    if b"ok" not in buf:
        proc.kill()
        raise RuntimeError("the Tor proxy did not start")
    return proc


def _others(exclude: set[int]) -> list[int]:
    """PIDs in this namespace other than us, the proxy and zombies."""
    out = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        if pid in exclude:
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                state = f.read().rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            continue
        if state != "Z":
            out.append(pid)
    return out


def report(fd: int | None, **msg) -> None:
    if fd is None:
        return
    try:
        os.write(fd, (json.dumps(msg) + "\n").encode())
    except OSError:
        pass


def run(cfg: dict) -> int:
    status_fd = cfg.get("status_fd")
    try:
        setup_network()
        hidden = hide_host_resolvers(bool(cfg.get("hide_system_bus", True)))
        proxy = start_proxy(cfg["socks"], cfg["token"], cfg["app_root"])
    except (RuntimeError, OSError) as e:
        report(status_fd, event="error", reason="setup", detail=str(e))
        return 125
    report(status_fd, event="ready", hidden=hidden)

    env = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        env.pop(k, None)           # a proxy setting would only add a dead hop
    env.update(cfg.get("env") or {})
    argv = ["unshare", "--user", f"--map-user={cfg['uid']}",
            f"--map-group={cfg['gid']}", "--", *cfg["argv"]]
    started = time.time()
    try:
        app = subprocess.Popen(argv, env=env, cwd=cfg.get("cwd") or None)
    except OSError as e:
        proxy.terminate()
        report(status_fd, event="error", reason="exec", detail=str(e))
        return 127
    report(status_fd, event="started", pid=app.pid)

    def forward(sig, _frame) -> None:
        for pid in _others({1, proxy.pid}):
            try:
                os.kill(pid, sig)
            except OSError:
                pass
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, forward)

    code = None
    # PID 1 reaps everything orphaned in the namespace; the session lasts
    # until the last of the app's processes is gone, not just the first one.
    while True:
        try:
            pid, status = os.waitpid(-1, 0)
        except ChildProcessError:
            break
        except InterruptedError:
            continue
        if pid == app.pid:
            code = os.waitstatus_to_exitcode(status)
        if pid == proxy.pid:
            # Without the proxy nothing can leave; end the session loudly
            # rather than leave the app running with a dead network.
            report(status_fd, event="error", reason="proxy_died")
            forward(signal.SIGTERM, None)
        if code is not None and not _others({1, proxy.pid}):
            break

    lifetime = time.time() - started
    proxy.terminate()
    try:
        proxy.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proxy.kill()
    report(status_fd, event="exited", code=code,
           handoff=bool(code == 0 and lifetime < HANDOFF_SECONDS))
    return code if code is not None else 0


def main() -> int:
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
    return run(cfg)


if __name__ == "__main__":
    sys.exit(main())
