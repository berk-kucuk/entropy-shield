from __future__ import annotations
import os
import pwd
import subprocess
import shutil
from typing import Callable

from .config import cfg
from .platform import firewall_backend
from .i2p import REDSOCKS_PORT

_LOCAL_NETS = [
    "0.0.0.0/8",
    "10.0.0.0/8",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
]

# When Tor is active, Tor maps .onion addresses to virtual IPs in
# 10.192.0.0/10. That range must NOT be excluded from TransPort
# redirection, so we split 10.0.0.0/8 into the two halves that
# cover only real private LAN space (10.0–10.191).
_TOR_LOCAL_NETS = [
    "0.0.0.0/8",
    "10.0.0.0/9",     # 10.0.0.0   – 10.127.255.255  (real LAN)
    "10.128.0.0/10",  # 10.128.0.0 – 10.191.255.255  (real LAN)
    # 10.192.0.0/10 omitted — Tor virtual .onion address space
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
]

_NFT_TABLE = "entropy-shield"


def _tor_uid() -> str | None:
    for name in ("debian-tor", "tor", "_tor", "toranon"):
        r = subprocess.run(["id", "-u", name], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    return None


def _i2pd_uid() -> str | None:
    for name in ("i2pd", "i2p"):
        r = subprocess.run(["id", "-u", name], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    return None


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


# ── nftables ──────────────────────────────────────────────────

def _nft(script: str) -> None:
    r = subprocess.run(["nft", "-f", "-"], input=script, text=True,
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"nft: {r.stderr.strip()}")


def _nft_build(use_tor: bool, use_dnscrypt: bool,
               tor_trans: int, tor_dns: int, dns_port: int,
               tor_uid: str | None,
               use_i2p: bool = False,
               i2p_transparent: bool = False) -> str:

    i2p_only = use_i2p and not use_tor and not use_dnscrypt
    lines = [f"table ip {_NFT_TABLE} {{"]

    # ── nat output chain ──────────────────────────────────────
    if use_tor or use_dnscrypt or (i2p_only and i2p_transparent):
        lines += ["    chain output {",
                  "        type nat hook output priority 100 ;"]

        if use_tor:
            if tor_uid:
                lines.append(f"        meta skuid {tor_uid} return")
            # DNS redirect before local-net exclusion so queries to 127.0.0.1:53
            # (resolv.conf nameserver) are caught and sent to Tor's DNSPort.
            dns_target = dns_port if use_dnscrypt else tor_dns
            lines.append(f"        udp dport 53 redirect to :{dns_target}")
            lines.append(f"        tcp dport 53 redirect to :{dns_target}")
            # Use _TOR_LOCAL_NETS (not _LOCAL_NETS) to leave 10.192.0.0/10
            # (Tor's AutomapHostsOnResolve virtual space) out of the exclusion
            # so .onion connections are forwarded to TransPort correctly.
            for net in _TOR_LOCAL_NETS:
                lines.append(f"        ip daddr {net} return")
            lines.append(
                f"        tcp flags & (fin|syn|rst|ack) == syn redirect to :{tor_trans}"
            )

        elif use_dnscrypt:
            lines.append(f"        udp dport 53 redirect to :{dns_port}")
            lines.append(f"        tcp dport 53 redirect to :{dns_port}")

        elif i2p_only and i2p_transparent:
            # Transparent proxy: redirect all TCP through redsocks → i2pd SOCKS.
            # i2pd's own traffic must be excluded (it needs direct internet access
            # to reach other I2P routers for network participation).
            # Local net exclusion also prevents the redsocks → 127.0.0.1:4447 loop.
            i2pd_uid = _i2pd_uid()
            if i2pd_uid:
                lines.append(f"        meta skuid {i2pd_uid} return")
            for net in _LOCAL_NETS:
                lines.append(f"        ip daddr {net} return")
            lines.append(
                f"        tcp flags & (fin|syn|rst|ack) == syn redirect to :{REDSOCKS_PORT}"
            )

        lines.append("    }")

    # ── filter output chain ───────────────────────────────────
    if use_tor:
        # Block UDP leaks: QUIC/HTTP3 (port 443/UDP), WebRTC, and any other UDP
        # bypass Tor because Tor's TransPort only accepts TCP.
        # DNS (port 53) is allowed here so the nat chain can redirect it.
        lines += ["    chain filter_output {",
                  "        type filter hook output priority 0 ;"]
        if tor_uid:
            lines.append(f"        meta skuid {tor_uid} return")
        for net in _TOR_LOCAL_NETS:
            lines.append(f"        ip daddr {net} return")
        lines += [
            "        udp dport 53 return",    # DNS → redirected by nat chain above
            "        meta l4proto udp drop",  # block QUIC/HTTP3, WebRTC, all other UDP
            "    }",
        ]
    elif i2p_only:
        lines += ["    chain filter_output {",
                  "        type filter hook output priority 0 ;"]
        i2pd_uid = _i2pd_uid()
        if i2pd_uid:
            # i2pd needs UDP for its SSU2 transport (peer-to-peer I2P traffic)
            lines.append(f"        meta skuid {i2pd_uid} return")
        for net in _LOCAL_NETS:
            lines.append(f"        ip daddr {net} return")
        lines += [
            "        udp dport 53 drop",
            "        tcp dport 53 drop",
            "        meta l4proto udp drop",
            "    }",
        ]

    lines.append("}")

    # ── ip6 table: block all IPv6 when Tor is active ──────────
    # Tor's TransPort only listens on IPv4 (127.0.0.1). IPv6 connections
    # go directly to the internet, leaking the real IPv6 address.
    if use_tor:
        lines += [
            f"\ntable ip6 {_NFT_TABLE} {{",
            "    chain output {",
            "        type filter hook output priority 0 ;",
            "        ip6 daddr ::1 return",       # loopback
            "        ip6 daddr fe80::/10 return",  # link-local (LAN)
            "        drop",
            "    }",
            "}",
        ]
    elif use_dnscrypt:
        # DNSCrypt-only: redirect IPv6 DNS to dnscrypt-proxy ([::1]:dns_port).
        # Without this, IPv6 DNS queries bypass encryption entirely.
        lines += [
            f"\ntable ip6 {_NFT_TABLE} {{",
            "    chain output {",
            "        type nat hook output priority 100 ;",
            f"        udp dport 53 redirect to :{dns_port}",
            f"        tcp dport 53 redirect to :{dns_port}",
            "    }",
            "}",
        ]

    return "\n".join(lines)


# ── FirewallManager ───────────────────────────────────────────

class FirewallManager:
    def __init__(self, log: Callable[[str], None]):
        self._log            = log
        self._ipt_rules:     list[list[str]] = []
        self._ip6t_rules:    list[list[str]] = []
        self._i2p_http:      int             = 4444
        self._i2p_socks:     int             = 4447
        self._tor_socks:     int             = 9050
        self._backend:       str             = ""
        self._use_tor:       bool            = False

    def apply(self, use_tor: bool, use_dnscrypt: bool, use_i2p: bool,
              i2p_transparent: bool = False) -> None:
        self._log("[FW] Applying firewall rules...")
        self._backend = firewall_backend()

        tor_trans = cfg().get("tor", "trans_port")
        tor_dns   = cfg().get("tor", "dns_port")
        dns_port  = cfg().get("dnscrypt", "port")
        self._i2p_http  = cfg().get("i2p", "http_port")
        self._i2p_socks = cfg().get("i2p", "socks_port")

        if self._backend == "nftables":
            self._apply_nft(use_tor, use_dnscrypt, tor_trans, tor_dns,
                            dns_port, use_i2p, i2p_transparent)
        else:
            self._apply_ipt(use_tor, use_dnscrypt, tor_trans, tor_dns,
                            dns_port, use_i2p, i2p_transparent)

        # ── system proxy ──────────────────────────────────────────
        # When Tor is active, point the system SOCKS proxy to Tor's
        # SocksPort (9050).  Browsers then send .onion hostnames
        # directly to Tor via SOCKS — no local DNS needed, which
        # bypasses the RFC 7686 block modern browsers enforce on
        # .onion DNS lookups.
        if use_tor:
            self._use_tor    = True
            self._tor_socks  = cfg().get("tor", "socks_port")
            self._set_tor_proxy(True)
            self._log(
                f"[FW] System SOCKS proxy → Tor (127.0.0.1:{self._tor_socks})."
            )
            self._log(
                "[FW] .onion access: restart Firefox/browser to pick up proxy. "
                "For the current terminal run: "
                "source /etc/profile.d/entropy-shield-proxy.sh"
            )
        elif use_i2p:
            # I2P-only: set HTTP + SOCKS proxy to I2P's ports.
            self._set_proxy(True)
            i2p_only = not use_dnscrypt
            if i2p_only:
                if i2p_transparent:
                    self._log(
                        "[FW] I2P transparent proxy active. "
                        "All TCP → redsocks → i2pd SOCKS. "
                        "UDP and DNS blocked."
                    )
                else:
                    self._log(
                        "[FW] I2P proxy-only mode. DNS blocked. "
                        f"Set apps to use HTTP proxy 127.0.0.1:{self._i2p_http} "
                        f"or SOCKS 127.0.0.1:{self._i2p_socks}."
                    )

        self._log(f"[FW] Rules applied via {self._backend}.")

    def remove(self) -> None:
        if not self._backend:
            return
        self._log("[FW] Removing firewall rules...")

        if self._backend == "nftables":
            subprocess.run(["nft", "delete", "table", "ip",  _NFT_TABLE], capture_output=True)
            subprocess.run(["nft", "delete", "table", "ip6", _NFT_TABLE], capture_output=True)
        else:
            for rule in reversed(self._ipt_rules):
                del_rule = ["-D" if a == "-A" else a for a in rule]
                subprocess.run(["iptables"] + del_rule, capture_output=True)
            self._ipt_rules.clear()
            for rule in reversed(self._ip6t_rules):
                del_rule = ["-D" if a == "-A" else a for a in rule]
                subprocess.run(["ip6tables"] + del_rule, capture_output=True)
            self._ip6t_rules.clear()

        if self._use_tor:
            self._set_tor_proxy(False)
            self._use_tor = False
        else:
            self._set_proxy(False)
        self._log("[FW] Rules removed.")

    # ── nft ───────────────────────────────────────────────────

    def _apply_nft(self, use_tor: bool, use_dnscrypt: bool,
                   tor_trans: int, tor_dns: int, dns_port: int,
                   use_i2p: bool = False,
                   i2p_transparent: bool = False) -> None:
        subprocess.run(["nft", "delete", "table", "ip",  _NFT_TABLE], capture_output=True)
        subprocess.run(["nft", "delete", "table", "ip6", _NFT_TABLE], capture_output=True)
        i2p_only = use_i2p and not use_tor and not use_dnscrypt
        has_rules = use_tor or use_dnscrypt or i2p_only
        if not has_rules:
            return

        uid = _tor_uid() if use_tor else None
        script = _nft_build(
            use_tor, use_dnscrypt, tor_trans, tor_dns, dns_port, uid,
            use_i2p, i2p_transparent,
        )
        _nft(script)

    # ── iptables ──────────────────────────────────────────────

    def _ipt_add(self, *args: str) -> None:
        r = subprocess.run(["iptables"] + list(args),
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"iptables {' '.join(args)}: {r.stderr.strip()}")
        if "-A" in args:
            self._ipt_rules.append(list(args))

    def _ip6t_add(self, *args: str) -> None:
        r = subprocess.run(["ip6tables"] + list(args),
                           capture_output=True, text=True)
        if r.returncode != 0:
            return  # ip6tables may not be available; non-fatal
        if "-A" in args:
            self._ip6t_rules.append(list(args))

    def _apply_ipt(self, use_tor: bool, use_dnscrypt: bool,
                   tor_trans: int, tor_dns: int, dns_port: int,
                   use_i2p: bool = False,
                   i2p_transparent: bool = False) -> None:
        i2p_only = use_i2p and not use_tor and not use_dnscrypt

        if use_tor:
            uid = _tor_uid()
            if uid:
                self._ipt_add("-t", "nat", "-A", "OUTPUT",
                               "-m", "owner", "--uid-owner", uid, "-j", "RETURN")
            # DNS redirect before local-net exclusion so queries to 127.0.0.1:53
            # are caught and sent to Tor's DNSPort.
            dns_target = str(dns_port) if use_dnscrypt else str(tor_dns)
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "udp", "--dport", "53",
                           "-j", "REDIRECT", "--to-ports", dns_target)
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "tcp", "--dport", "53",
                           "-j", "REDIRECT", "--to-ports", dns_target)
            # Exclude real local/private nets but NOT 10.192.0.0/10 so .onion
            # virtual addresses reach TransPort.
            for net in _TOR_LOCAL_NETS:
                self._ipt_add("-t", "nat", "-A", "OUTPUT", "-d", net, "-j", "RETURN")
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "tcp", "--syn",
                           "-j", "REDIRECT", "--to-ports", str(tor_trans))

            # Block UDP leaks: QUIC/HTTP3 bypasses Tor's TCP-only TransPort.
            # Allow tor UID and real local nets first (but keep 10.192.0.0/10
            # out of the exclusion so UDP to .onion virtual IPs is also dropped).
            if uid:
                self._ipt_add("-t", "filter", "-A", "OUTPUT",
                               "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT")
            for net in _TOR_LOCAL_NETS:
                self._ipt_add("-t", "filter", "-A", "OUTPUT", "-d", net, "-j", "ACCEPT")
            self._ipt_add("-t", "filter", "-A", "OUTPUT",
                           "-p", "udp", "--dport", "53", "-j", "ACCEPT")  # DNS via nat
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-p", "udp", "-j", "DROP")

            # Block IPv6 leaks: Tor's TransPort is IPv4-only.
            self._ip6t_add("-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")
            self._ip6t_add("-A", "OUTPUT", "-d", "fe80::/10", "-j", "ACCEPT")
            self._ip6t_add("-A", "OUTPUT", "-j", "DROP")

        elif use_dnscrypt:
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "udp", "--dport", "53",
                           "-j", "REDIRECT", "--to-ports", str(dns_port))
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "tcp", "--dport", "53",
                           "-j", "REDIRECT", "--to-ports", str(dns_port))
            # IPv6 DNS redirect — dnscrypt-proxy also listens on [::1]:dns_port
            self._ip6t_add("-t", "nat", "-A", "OUTPUT",
                            "-p", "udp", "--dport", "53",
                            "-j", "REDIRECT", "--to-ports", str(dns_port))
            self._ip6t_add("-t", "nat", "-A", "OUTPUT",
                            "-p", "tcp", "--dport", "53",
                            "-j", "REDIRECT", "--to-ports", str(dns_port))

        if i2p_only:
            i2pd_uid = _i2pd_uid()

            if i2p_transparent:
                # Exclude i2pd (needs direct internet for peer connections)
                if i2pd_uid:
                    self._ipt_add("-t", "nat", "-A", "OUTPUT",
                                   "-m", "owner", "--uid-owner", i2pd_uid, "-j", "RETURN")
                for net in _LOCAL_NETS:
                    self._ipt_add("-t", "nat", "-A", "OUTPUT", "-d", net, "-j", "RETURN")
                self._ipt_add("-t", "nat", "-A", "OUTPUT",
                               "-p", "tcp", "--syn",
                               "-j", "REDIRECT", "--to-ports", str(REDSOCKS_PORT))

            # Block DNS and non-local UDP regardless of transparent mode
            if i2pd_uid:
                self._ipt_add("-t", "filter", "-A", "OUTPUT",
                               "-m", "owner", "--uid-owner", i2pd_uid, "-j", "ACCEPT")
            for net in _LOCAL_NETS:
                self._ipt_add("-t", "filter", "-A", "OUTPUT", "-d", net, "-j", "ACCEPT")
            self._ipt_add("-t", "filter", "-A", "OUTPUT",
                           "-p", "udp", "--dport", "53", "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT",
                           "-p", "tcp", "--dport", "53", "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-p", "udp", "-j", "DROP")

    # ── system proxy (GNOME + KDE) ────────────────────────────

    def _set_proxy(self, enable: bool) -> None:
        info = _real_user()
        if info is None:
            return
        uid, pw = info
        self._set_gnome_proxy(pw, enable)
        self._set_kde_proxy(pw, enable)

    def _user_run(self, pw: pwd.struct_passwd, cmd: list[str]) -> None:
        runtime_dir = f"/run/user/{pw.pw_uid}"
        env = {
            "HOME":                     pw.pw_dir,
            "USER":                     pw.pw_name,
            "LOGNAME":                  pw.pw_name,
            "XDG_RUNTIME_DIR":          runtime_dir,
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_dir}/bus",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        }
        try:
            subprocess.run(
                ["runuser", "-u", pw.pw_name, "--"] + cmd,
                env=env, capture_output=True,
            )
        except Exception:
            pass

    def _set_gnome_proxy(self, pw: pwd.struct_passwd, enable: bool) -> None:
        if not shutil.which("gsettings"):
            return
        if enable:
            for cmd in [
                ["gsettings", "set", "org.gnome.system.proxy", "mode", "manual"],
                ["gsettings", "set", "org.gnome.system.proxy.http",
                 "host", "127.0.0.1"],
                ["gsettings", "set", "org.gnome.system.proxy.http",
                 "port", str(self._i2p_http)],
                ["gsettings", "set", "org.gnome.system.proxy.https",
                 "host", "127.0.0.1"],
                ["gsettings", "set", "org.gnome.system.proxy.https",
                 "port", str(self._i2p_http)],
                ["gsettings", "set", "org.gnome.system.proxy.socks",
                 "host", "127.0.0.1"],
                ["gsettings", "set", "org.gnome.system.proxy.socks",
                 "port", str(self._i2p_socks)],
            ]:
                self._user_run(pw, cmd)
        else:
            self._user_run(pw, [
                "gsettings", "set", "org.gnome.system.proxy", "mode", "none"
            ])

    def _set_kde_proxy(self, pw: pwd.struct_passwd, enable: bool) -> None:
        kwrite = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
        if not kwrite:
            return
        if enable:
            for cmd in [
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "ProxyType", "1"],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "httpProxy",
                 f"http://127.0.0.1 {self._i2p_http}"],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "httpsProxy",
                 f"http://127.0.0.1 {self._i2p_http}"],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "socksProxy",
                 f"socks://127.0.0.1 {self._i2p_socks}"],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "ReversedException", "false"],
            ]:
                self._user_run(pw, cmd)
        else:
            self._user_run(pw, [
                kwrite, "--file", "kioslaverc",
                "--group", "Proxy Settings", "--key", "ProxyType", "0",
            ])

        self._user_run(pw, [
            "dbus-send", "--session", "--type=signal",
            "/KIO/Scheduler",
            "org.kde.KIO.Scheduler.reparseSlaveConfiguration",
            "string:",
        ])

    # ── Tor SOCKS proxy — universal approach ──────────────────────
    #
    # Modern browsers (Firefox, Chromium) follow RFC 7686: they refuse
    # to resolve .onion hostnames via DNS at the application layer,
    # so the transparent-proxy DNS redirect cannot help for .onion.
    #
    # The only reliable cross-browser, cross-DE fix is to point the
    # system SOCKS5 proxy at Tor's SocksPort.  The browser then sends
    # the .onion hostname directly in the SOCKS CONNECT request (no
    # local DNS needed) and Tor routes it through the onion network.
    #
    # Strategy (tried in order, each is a no-op if tool is absent):
    #   1. GNOME  – gsettings        (GNOME, elementaryOS, …)
    #   2. KDE    – kwriteconfig     (KDE Plasma)
    #   3. XFCE   – xfconf-query     (Xfce)
    #   4. Universal env vars written to
    #              ~/.config/environment.d/entropy-shield.conf  AND
    #              propagated via  systemctl --user set-environment
    #      This covers Sway / Hyprland / i3 / any other WM and is the
    #      fallback for CLI tools regardless of DE.
    #
    # Note: env-var changes only affect processes started AFTER the
    # variable is set.  DE-specific APIs (1-3) take effect immediately
    # for already-running browsers.

    def _set_tor_proxy(self, enable: bool) -> None:
        info = _real_user()
        if info is None:
            return
        _uid, pw = info
        # socks5:// for DE proxy APIs (they handle remote-DNS themselves)
        socks_url   = f"socks5://127.0.0.1:{self._tor_socks}"
        # socks5h:// for env-var / CLI consumers: hostname goes straight to Tor,
        # no local DNS lookup — required for .onion (RFC 7686 blocks local lookup).
        socks5h_url = f"socks5h://127.0.0.1:{self._tor_socks}"
        self._set_gnome_tor_proxy(pw, enable, socks_url)
        self._set_kde_tor_proxy(pw, enable, socks_url)
        self._set_xfce_tor_proxy(pw, enable, socks_url)
        self._set_env_tor_proxy(pw, enable, socks5h_url)
        self._set_profile_d_proxy(enable, socks5h_url)

    def _set_gnome_tor_proxy(self, pw: pwd.struct_passwd,
                             enable: bool, socks_url: str) -> None:
        if not shutil.which("gsettings"):
            return
        if enable:
            host, port = "127.0.0.1", str(self._tor_socks)
            for cmd in [
                ["gsettings", "set", "org.gnome.system.proxy", "mode", "manual"],
                ["gsettings", "set", "org.gnome.system.proxy.http",  "host", ""],
                ["gsettings", "set", "org.gnome.system.proxy.http",  "port", "0"],
                ["gsettings", "set", "org.gnome.system.proxy.https", "host", ""],
                ["gsettings", "set", "org.gnome.system.proxy.https", "port", "0"],
                ["gsettings", "set", "org.gnome.system.proxy.socks", "host", host],
                ["gsettings", "set", "org.gnome.system.proxy.socks", "port", port],
            ]:
                self._user_run(pw, cmd)
        else:
            self._user_run(pw, [
                "gsettings", "set", "org.gnome.system.proxy", "mode", "none"
            ])

    def _set_kde_tor_proxy(self, pw: pwd.struct_passwd,
                           enable: bool, socks_url: str) -> None:
        kwrite = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
        if not kwrite:
            return
        if enable:
            for cmd in [
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "ProxyType", "1"],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "httpProxy",  ""],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "httpsProxy", ""],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "socksProxy", socks_url],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "ReversedException", "false"],
            ]:
                self._user_run(pw, cmd)
        else:
            self._user_run(pw, [
                kwrite, "--file", "kioslaverc",
                "--group", "Proxy Settings", "--key", "ProxyType", "0",
            ])
        self._user_run(pw, [
            "dbus-send", "--session", "--type=signal",
            "/KIO/Scheduler",
            "org.kde.KIO.Scheduler.reparseSlaveConfiguration",
            "string:",
        ])

    def _set_xfce_tor_proxy(self, pw: pwd.struct_passwd,
                            enable: bool, socks_url: str) -> None:
        if not shutil.which("xfconf-query"):
            return
        # XFCE stores network proxy in the xsettings channel (GTK proxy settings)
        ch = "xsettings"
        if enable:
            for cmd in [
                ["xfconf-query", "-c", ch, "-p", "/Net/ProxyMode",    "-s", "1"],
                ["xfconf-query", "-c", ch, "-p", "/Net/SocksProxy",   "-s", socks_url],
                ["xfconf-query", "-c", ch, "-p", "/Net/NoProxyFor",   "-s",
                 "localhost,127.0.0.0/8,::1"],
            ]:
                self._user_run(pw, cmd)
        else:
            self._user_run(pw, [
                "xfconf-query", "-c", ch, "-p", "/Net/ProxyMode", "-s", "0"
            ])

    def _set_env_tor_proxy(self, pw: pwd.struct_passwd,
                           enable: bool, socks_url: str) -> None:
        """Universal fallback: systemd user-session env vars + persistent env file.

        socks_url is expected to use the socks5h:// scheme so that hostname
        resolution is delegated to Tor (required for .onion addresses).
        """
        _VAR_NAMES = [
            "ALL_PROXY", "all_proxy",
            "SOCKS_PROXY", "socks_proxy",
            # Some tools (Python requests, httpx) only honour http(s)_proxy.
            "HTTP_PROXY", "http_proxy",
            "HTTPS_PROXY", "https_proxy",
        ]
        env_dir  = os.path.join(pw.pw_dir, ".config", "environment.d")
        env_file = os.path.join(env_dir, "entropy-shield-proxy.conf")

        if enable:
            # 1. Propagate to the live systemd user session so any process
            #    spawned from now on inherits the variable.
            for var in _VAR_NAMES:
                self._user_run(pw, [
                    "systemctl", "--user", "set-environment",
                    f"{var}={socks_url}",
                ])

            # 2. Persist across reboots / new logins.
            try:
                os.makedirs(env_dir, exist_ok=True)
                with open(env_file, "w") as f:
                    for var in _VAR_NAMES:
                        f.write(f"{var}={socks_url}\n")
                os.chown(env_file, pw.pw_uid, pw.pw_gid)
            except Exception:
                pass
        else:
            for var in _VAR_NAMES:
                self._user_run(pw, [
                    "systemctl", "--user", "unset-environment", var,
                ])
            try:
                os.unlink(env_file)
            except FileNotFoundError:
                pass

    # ── /etc/profile.d — system-wide shell env ────────────────────

    _PROFILE_D_SH   = "/etc/profile.d/entropy-shield-proxy.sh"
    _PROFILE_D_FISH = "/etc/fish/conf.d/entropy-shield-proxy.fish"

    def _set_profile_d_proxy(self, enable: bool, socks5h_url: str) -> None:
        """Write/remove proxy env to /etc/profile.d so every new shell session
        inherits it without needing to restart the login session.  Covers bash,
        zsh (both source /etc/profile.d/*.sh) and fish."""
        if enable:
            sh = (
                "# Written by entropy-shield — removed on disconnect\n"
                f'export ALL_PROXY="{socks5h_url}"\n'
                f'export all_proxy="{socks5h_url}"\n'
                f'export SOCKS_PROXY="{socks5h_url}"\n'
                f'export socks_proxy="{socks5h_url}"\n'
                f'export HTTP_PROXY="{socks5h_url}"\n'
                f'export http_proxy="{socks5h_url}"\n'
                f'export HTTPS_PROXY="{socks5h_url}"\n'
                f'export https_proxy="{socks5h_url}"\n'
                'export NO_PROXY="localhost,127.0.0.1,127.0.0.0/8,::1"\n'
                'export no_proxy="localhost,127.0.0.1,127.0.0.0/8,::1"\n'
            )
            fish = (
                "# Written by entropy-shield — removed on disconnect\n"
                f'set -gx ALL_PROXY "{socks5h_url}"\n'
                f'set -gx all_proxy "{socks5h_url}"\n'
                f'set -gx SOCKS_PROXY "{socks5h_url}"\n'
                f'set -gx socks_proxy "{socks5h_url}"\n'
                f'set -gx HTTP_PROXY "{socks5h_url}"\n'
                f'set -gx http_proxy "{socks5h_url}"\n'
                f'set -gx HTTPS_PROXY "{socks5h_url}"\n'
                f'set -gx https_proxy "{socks5h_url}"\n'
                'set -gx NO_PROXY "localhost,127.0.0.1,127.0.0.0/8,::1"\n'
                'set -gx no_proxy "localhost,127.0.0.1,127.0.0.0/8,::1"\n'
            )
            try:
                with open(self._PROFILE_D_SH, "w") as f:
                    f.write(sh)
            except Exception:
                pass
            try:
                fish_dir = os.path.dirname(self._PROFILE_D_FISH)
                os.makedirs(fish_dir, exist_ok=True)
                with open(self._PROFILE_D_FISH, "w") as f:
                    f.write(fish)
            except Exception:
                pass
        else:
            for path in (self._PROFILE_D_SH, self._PROFILE_D_FISH):
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

