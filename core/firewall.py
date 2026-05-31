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


def _resolve_uid(uid_or_user: str) -> str | None:
    """Resolve a username or numeric UID string to a numeric UID string."""
    if uid_or_user.isdigit():
        return uid_or_user
    r = subprocess.run(["id", "-u", uid_or_user], capture_output=True, text=True)
    if r.returncode == 0:
        return r.stdout.strip()
    return None


def _per_app_rules() -> tuple[list[str], list[str]]:
    """Return (bypass_uids, block_uids) from config."""
    from .config import cfg as _cfg
    par = _cfg().get("per_app_routing")
    if not par.get("enabled"):
        return [], []
    bypass, block = [], []
    for rule in par.get("rules", []):
        uid = _resolve_uid(str(rule.get("uid_or_user", "")))
        if uid is None:
            continue
        action = rule.get("action", "tor")
        if action == "direct":
            bypass.append(uid)
        elif action == "block":
            block.append(uid)
    return bypass, block


def _tor_uid() -> str:
    """Return the UID of the Tor system user as a string.

    Falls back to "0" (root) when no dedicated Tor user exists.  In the
    runner-based privilege model the only root process is the runner itself
    plus the Tor subprocess it owns, so exempting UID 0 is safe and avoids
    the TransPort redirect loop that would occur if Tor's own traffic were
    redirected back through TransPort.
    """
    for name in ("debian-tor", "tor", "_tor", "toranon"):
        r = subprocess.run(["id", "-u", name], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    return "0"


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

            # Per-app routing: bypass (direct clearnet) and block rules
            bypass_uids, block_uids = _per_app_rules()
            for uid in bypass_uids:
                lines.append(f"        meta skuid {uid} return")  # NAT skip → direct
            for uid in block_uids:
                lines.append(f"        meta skuid {uid} drop")    # drop in nat = refuse

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
        # Tor's TransPort only carries TCP. Everything else must be blocked
        # to prevent leaks: UDP (QUIC/HTTP3, WebRTC…), ICMP (ping/traceroute),
        # SCTP, and any other L4 protocol.
        # DNS/53 is returned first so the nat chain can redirect it to DNSPort.
        lines += ["    chain filter_output {",
                  "        type filter hook output priority 0 ;"]
        if tor_uid:
            lines.append(f"        meta skuid {tor_uid} return")
        for net in _TOR_LOCAL_NETS:
            lines.append(f"        ip daddr {net} return")
        lines += [
            "        udp dport 53 return",        # DNS → nat chain → Tor DNSPort
            "        meta l4proto != tcp drop",   # drop UDP, ICMP, SCTP, etc.
            "    }",
        ]
    elif i2p_only:
        lines += ["    chain filter_output {",
                  "        type filter hook output priority 0 ;"]
        i2pd_uid = _i2pd_uid()
        if i2pd_uid:
            # i2pd needs UDP for SSU2 (peer-to-peer I2P transport)
            lines.append(f"        meta skuid {i2pd_uid} return")
        for net in _LOCAL_NETS:
            lines.append(f"        ip daddr {net} return")
        lines += [
            "        udp dport 53 drop",
            "        tcp dport 53 drop",
            "        meta l4proto != tcp drop",   # drop UDP, ICMP, SCTP, etc.
            "    }",
        ]

    # ── FORWARD chain: block all forwarded packets ───────────
    # Prevents IP-forwarding (e.g. shared internet for VMs, containers)
    # from routing clearnet traffic that bypasses the OUTPUT rules.
    if use_tor or use_dnscrypt or i2p_only:
        lines += [
            "    chain forward {",
            "        type filter hook forward priority 0 ;",
            "        drop",
            "    }",
        ]

    lines.append("}")

    # ── ip6 table ─────────────────────────────────────────────
    if use_tor:
        # Block all IPv6: Tor's TransPort only listens on IPv4 (127.0.0.1).
        # Any IPv6 connection would reach the internet directly, leaking the
        # real IPv6 address.
        lines += [
            f"\ntable ip6 {_NFT_TABLE} {{",
            "    chain output {",
            "        type filter hook output priority 0 ;",
            "        ip6 daddr ::1 return",        # loopback
            "        ip6 daddr fe80::/10 return",  # link-local (LAN)
            "        drop",
            "    }",
            "    chain forward {",
            "        type filter hook forward priority 0 ;",
            "        drop",
            "    }",
            "}",
        ]
    elif use_dnscrypt:
        # DNSCrypt-only: redirect IPv6 DNS so encrypted DNS also covers IPv6.
        # Without this, IPv6 DNS queries reach the clearnet resolver unencrypted.
        lines += [
            f"\ntable ip6 {_NFT_TABLE} {{",
            "    chain output {",
            "        type nat hook output priority 100 ;",
            f"        udp dport 53 redirect to :{dns_port}",
            f"        tcp dport 53 redirect to :{dns_port}",
            "    }",
            "    chain forward {",
            "        type filter hook forward priority 0 ;",
            "        drop",
            "    }",
            "}",
        ]
    elif i2p_only:
        # I2P-only: block all IPv6 (I2P is IPv4-only in most deployments).
        lines += [
            f"\ntable ip6 {_NFT_TABLE} {{",
            "    chain output {",
            "        type filter hook output priority 0 ;",
            "        ip6 daddr ::1 return",
            "        ip6 daddr fe80::/10 return",
            "        drop",
            "    }",
            "    chain forward {",
            "        type filter hook forward priority 0 ;",
            "        drop",
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

        # Flush connection tracking so pre-existing connections are re-evaluated
        # against the new rules.  Without this, established TCP sessions that
        # existed before connect() are NOT subject to the TransPort redirect and
        # continue flowing to their original destinations — a classic bypass.
        self._flush_conntrack()

    def remove(self) -> None:
        if not self._backend:
            return
        self._log("[FW] Removing firewall rules...")

        if self._backend == "nftables":
            for fam in ("ip", "ip6"):
                r = subprocess.run(
                    ["nft", "delete", "table", fam, _NFT_TABLE],
                    capture_output=True, text=True,
                )
                # "not found" / "no such" errors mean the table was already
                # absent — that is fine.  Any other error means rules may
                # still be active, so we log a visible warning.
                if r.returncode != 0:
                    s = r.stderr.lower()
                    if "no such" not in s and "not found" not in s:
                        self._log(
                            f"[FW] Warning: could not delete {fam} table "
                            f"{_NFT_TABLE}: {r.stderr.strip()}"
                        )
        else:
            for rule in reversed(self._ipt_rules):
                del_rule = ["-D" if a == "-A" else a for a in rule]
                r = subprocess.run(["iptables"] + del_rule,
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    self._log(
                        f"[FW] Warning: iptables deletion failed: "
                        f"{r.stderr.strip()}"
                    )
            self._ipt_rules.clear()
            for rule in reversed(self._ip6t_rules):
                del_rule = ["-D" if a == "-A" else a for a in rule]
                subprocess.run(["ip6tables"] + del_rule, capture_output=True)
            self._ip6t_rules.clear()
        # Mark backend cleared so a second remove() call is a safe no-op.
        self._backend = ""

        if self._use_tor:
            self._set_tor_proxy(False)
            self._use_tor = False
            self._log(
                "[FW] Proxy vars cleared from system and new sessions. "
                "To clear in current terminal:\n"
                "  unset ALL_PROXY all_proxy HTTP_PROXY http_proxy "
                "HTTPS_PROXY https_proxy SOCKS_PROXY socks_proxy "
                "NO_PROXY no_proxy"
            )
        else:
            self._set_proxy(False)
        self._flush_conntrack()
        self._log("[FW] Rules removed.")

    # ── conntrack ─────────────────────────────────────────────

    def _flush_conntrack(self) -> None:
        r = subprocess.run(["conntrack", "-F"], capture_output=True)
        if r.returncode == 0:
            self._log("[FW] Connection tracking cleared (pre-existing connections dropped).")
        else:
            self._log(
                "[FW] Warning: conntrack not available — existing connections may bypass rules. "
                "Install conntrack-tools for full protection."
            )

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

            # Block everything non-TCP to prevent leaks: UDP (QUIC/HTTP3,
            # WebRTC…), ICMP (ping/traceroute), and other L4 protocols.
            if uid:
                self._ipt_add("-t", "filter", "-A", "OUTPUT",
                               "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT")
            for net in _TOR_LOCAL_NETS:
                self._ipt_add("-t", "filter", "-A", "OUTPUT", "-d", net, "-j", "ACCEPT")
            self._ipt_add("-t", "filter", "-A", "OUTPUT",
                           "-p", "udp", "--dport", "53", "-j", "ACCEPT")  # DNS → nat chain
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-p", "udp",  "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-p", "icmp", "-j", "DROP")
            # Catch-all: block SCTP, GRE, ESP (IPSec), and any other L4 protocol
            # not explicitly handled above.  All legitimate TCP is already
            # redirected to TransPort and ACCEPT'd via the local-nets rule.
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-j", "DROP")

            # Block IPv6 leaks: Tor's TransPort is IPv4-only.
            self._ip6t_add("-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")
            self._ip6t_add("-A", "OUTPUT", "-d", "fe80::/10", "-j", "ACCEPT")
            self._ip6t_add("-A", "OUTPUT", "-j", "DROP")

            # Block forwarded packets (prevents clearnet bypass via IP forwarding).
            self._ipt_add("-t", "filter", "-A", "FORWARD", "-j", "DROP")
            self._ip6t_add("-A", "FORWARD", "-j", "DROP")

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

            # Block DNS, UDP, and ICMP for non-i2pd traffic.
            # i2pd UID is returned first to allow SSU2 UDP transport.
            if i2pd_uid:
                self._ipt_add("-t", "filter", "-A", "OUTPUT",
                               "-m", "owner", "--uid-owner", i2pd_uid, "-j", "ACCEPT")
            for net in _LOCAL_NETS:
                self._ipt_add("-t", "filter", "-A", "OUTPUT", "-d", net, "-j", "ACCEPT")
            self._ipt_add("-t", "filter", "-A", "OUTPUT",
                           "-p", "udp", "--dport", "53", "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT",
                           "-p", "tcp", "--dport", "53", "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-p", "udp",  "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-p", "icmp", "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-j", "DROP")  # catch-all

            # Block forwarded packets for I2P-only mode too.
            self._ipt_add("-t", "filter", "-A", "FORWARD", "-j", "DROP")
            self._ip6t_add("-A", "FORWARD", "-j", "DROP")

    # ── system proxy (GNOME + KDE) ────────────────────────────

    def _set_proxy(self, enable: bool) -> None:
        info = _real_user()
        if info is None:
            return
        uid, pw = info
        self._set_gnome_proxy(pw, enable)
        self._set_kde_proxy(pw, enable)

    def _user_systemctl(self, pw: pwd.struct_passwd, *args: str) -> None:
        """Run 'systemctl --user' as root by connecting to the user's D-Bus socket.

        Tries two methods:
        1. Direct D-Bus socket  (root → user session socket, no helper needed)
        2. runuser fallback     (switches to user and runs systemctl --user)
        """
        runtime_dir = f"/run/user/{pw.pw_uid}"
        bus_sock = f"{runtime_dir}/bus"

        # Method 1: root connects directly to the user's session D-Bus socket.
        if os.path.exists(bus_sock):
            env = {
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus_sock}",
                "XDG_RUNTIME_DIR":          runtime_dir,
                "HOME":    pw.pw_dir,
                "USER":    pw.pw_name,
                "LOGNAME": pw.pw_name,
                "PATH":    os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
            }
            try:
                r = subprocess.run(
                    ["systemctl", "--user"] + list(args),
                    env=env, capture_output=True,
                )
                if r.returncode == 0:
                    return
            except Exception:
                pass

        # Method 2: runuser fallback
        self._user_run(pw, ["systemctl", "--user"] + list(args))

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
            for cmd in [
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "ProxyType", "0"],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "httpProxy",  ""],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "httpsProxy", ""],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "socksProxy", ""],
            ]:
                self._user_run(pw, cmd)

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
            for cmd in [
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "ProxyType", "0"],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "socksProxy", ""],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "httpProxy",  ""],
                [kwrite, "--file", "kioslaverc",
                 "--group", "Proxy Settings", "--key", "httpsProxy", ""],
            ]:
                self._user_run(pw, cmd)
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
        _PROXY_VARS = [
            "ALL_PROXY", "all_proxy",
            "SOCKS_PROXY", "socks_proxy",
            # Some tools (Python requests, httpx) only honour http(s)_proxy.
            "HTTP_PROXY", "http_proxy",
            "HTTPS_PROXY", "https_proxy",
        ]
        _NO_PROXY_VAL = "localhost,127.0.0.1,127.0.0.0/8,::1"
        env_dir  = os.path.join(pw.pw_dir, ".config", "environment.d")
        env_file = os.path.join(env_dir, "entropy-shield-proxy.conf")

        if enable:
            # 1. Propagate to the live systemd user session so any process
            #    spawned from now on inherits the variable.
            for var in _PROXY_VARS:
                self._user_systemctl(pw, "set-environment", f"{var}={socks_url}")
            # NO_PROXY uses a different value (bypass list, not the proxy URL).
            for var in ("NO_PROXY", "no_proxy"):
                self._user_systemctl(pw, "set-environment", f"{var}={_NO_PROXY_VAL}")

            # 2. Persist across reboots / new logins.
            try:
                os.makedirs(env_dir, exist_ok=True)
                with open(env_file, "w") as f:
                    for var in _PROXY_VARS:
                        f.write(f"{var}={socks_url}\n")
                    f.write(f"NO_PROXY={_NO_PROXY_VAL}\n")
                    f.write(f"no_proxy={_NO_PROXY_VAL}\n")
                os.chown(env_file, pw.pw_uid, pw.pw_gid)
            except Exception:
                pass
        else:
            # Unset all proxy vars and NO_PROXY from the live systemd user session.
            for var in _PROXY_VARS + ["NO_PROXY", "no_proxy"]:
                self._user_systemctl(pw, "unset-environment", var)
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
            # Write an unset script so new shells won't inherit the vars,
            # and existing shells can source it to clear them immediately.
            sh_unset = (
                "# Written by entropy-shield — source to clear proxy vars\n"
                "unset ALL_PROXY all_proxy SOCKS_PROXY socks_proxy\n"
                "unset HTTP_PROXY http_proxy HTTPS_PROXY https_proxy\n"
                "unset NO_PROXY no_proxy\n"
            )
            fish_unset = (
                "# Written by entropy-shield — source to clear proxy vars\n"
                "set -ge ALL_PROXY; set -ge all_proxy\n"
                "set -ge SOCKS_PROXY; set -ge socks_proxy\n"
                "set -ge HTTP_PROXY; set -ge http_proxy\n"
                "set -ge HTTPS_PROXY; set -ge https_proxy\n"
                "set -ge NO_PROXY; set -ge no_proxy\n"
            )
            try:
                with open(self._PROFILE_D_SH, "w") as f:
                    f.write(sh_unset)
            except Exception:
                pass
            try:
                with open(self._PROFILE_D_FISH, "w") as f:
                    f.write(fish_unset)
            except Exception:
                pass

