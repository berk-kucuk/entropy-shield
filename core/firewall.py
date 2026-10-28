from __future__ import annotations
import ipaddress
import os
import pwd
import re
import subprocess
import shutil
import threading
from typing import Callable, NamedTuple

from .config import cfg, cfg_port
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

# iptables fallback: our rules live in these chains, jumped to from the top of
# the matching built-in chain (see FirewallManager._ipt_hook).
_IPT_CHAINS = {
    "OUTPUT":  "ENTROPY_SHIELD_OUT",
    "FORWARD": "ENTROPY_SHIELD_FWD",
}
_IPT_TABLES = {"OUTPUT": ("filter", "nat"), "FORWARD": ("filter",)}


def purge_iptables_chains() -> None:
    """Unhook and delete every iptables chain Entropy Shield may have created.

    Works without any FirewallManager state, so the daemon's startup heal and
    the panic path can clean up after a crashed session — and it touches only
    our own chains, never the rest of the host's ruleset.
    """
    for binary in ("iptables", "ip6tables"):
        if not shutil.which(binary):
            continue
        for builtin, chain in _IPT_CHAINS.items():
            for table in _IPT_TABLES[builtin]:
                # A crash between two hooks can leave more than one jump.
                for _ in range(8):
                    r = subprocess.run(
                        [binary, "-t", table, "-D", builtin, "-j", chain],
                        capture_output=True)
                    if r.returncode != 0:
                        break
                subprocess.run([binary, "-t", table, "-F", chain], capture_output=True)
                subprocess.run([binary, "-t", table, "-X", chain], capture_output=True)

# Known DNS-over-HTTPS resolver IPs. Blocked in DNSCrypt-only mode so apps
# cannot bypass DNSCrypt by hardcoding their own DoH endpoint.
# (In Tor mode, all TCP is already redirected through TransPort — no need
# to block individually.)
_DOH_IPS = [
    # Google
    "8.8.8.8", "8.8.4.4",
    # Cloudflare
    "1.1.1.1", "1.0.0.1",
    # Quad9
    "9.9.9.9", "149.112.112.112",
    # AdGuard
    "94.140.14.14", "94.140.15.15",
    # OpenDNS
    "208.67.222.222", "208.67.220.220",
    # CleanBrowsing
    "185.228.168.168", "185.228.169.168",
]


_DECIMAL = re.compile(r"^[0-9]{1,10}$")


def _resolve_uid(uid_or_user: str) -> str | None:
    """Resolve a username or numeric UID string to a numeric UID string.

    SECURITY: the return value is interpolated into an nftables ``meta skuid``
    expression, so it must be a plain decimal number and nothing else.  Note
    that ``str.isdigit()`` — used here previously — is True for non-ASCII digits
    such as "٥", which would be emitted verbatim and break the whole ruleset;
    the explicit ASCII pattern is what makes this safe.
    """
    if not uid_or_user:
        return None
    if _DECIMAL.match(uid_or_user):
        return uid_or_user
    r = subprocess.run(["id", "-u", uid_or_user], capture_output=True, text=True)
    if r.returncode == 0:
        out = r.stdout.strip()
        if _DECIMAL.match(out):
            return out
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
        uid = _resolve_uid(name)
        if uid is not None:
            return uid
    return "0"


def _i2pd_uid() -> str | None:
    for name in ("i2pd", "i2p"):
        uid = _resolve_uid(name)
        if uid is not None:
            return uid
    return None


# ── VPN coexistence ───────────────────────────────────────────
#
# The kill switch drops every packet that is not redirected into Tor/I2P.  A
# VPN's OUTER packets (WireGuard/OpenVPN UDP to the VPN server, Tailscale's
# peer traffic, …) are among them, so with a VPN up the tunnel died, Tor's own
# traffic — routed into that tunnel — went nowhere, and Connect hung in
# bootstrap.  We therefore exempt exactly the tunnel's transport: its server
# endpoints and the firewall marks VPN software puts on its own packets.
# Traffic INSIDE the tunnel is unaffected and still goes through Tor.

_TUNNEL_PREFIXES = ("wg", "tun", "tap", "ppp", "tailscale", "nordlynx",
                    "proton", "mullvad", "zt", "vti", "ipsec", "nebula", "utun")


class VpnExempt(NamedTuple):
    v4:    tuple[str, ...]                      # endpoint addresses / CIDRs
    v6:    tuple[str, ...]
    marks: tuple[tuple[int, int | None], ...]   # (fwmark, mask)
    ifaces: tuple[str, ...]                     # tunnel interfaces seen (for logs)


_NO_VPN = VpnExempt((), (), (), ())


def _ip_or_net(text: str):
    """Parse *text* as an address or network; None if it is neither.

    Everything that reaches a ruleset passes through ipaddress, so nothing
    from command output or the user's config is interpolated verbatim.
    """
    try:
        return ipaddress.ip_network(text.strip().strip("[]"), strict=False)
    except ValueError:
        return None


def _tunnel_ifaces() -> list[str]:
    try:
        r = subprocess.run(["ip", "-o", "-d", "link", "show", "up"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        name = parts[1].strip().split("@")[0]
        detail = parts[2]
        if (name.startswith(_TUNNEL_PREFIXES) or " wireguard " in detail
                or " tun " in detail):
            out.append(name)
    return out


def detect_vpn() -> VpnExempt:
    """Find the transport of every active VPN so the kill switch spares it."""
    from .config import cfg as _cfg
    v4: set[str] = set()
    v6: set[str] = set()
    marks: set[tuple[int, int | None]] = set()

    def _add(text: str) -> None:
        net = _ip_or_net(text)
        if net is None or net.is_loopback or net.is_unspecified:
            return
        (v4 if net.version == 4 else v6).add(str(net))

    # Addresses the user listed explicitly (always honoured).
    for item in _cfg().get("vpn", "allow"):
        _add(item)

    ifaces = _tunnel_ifaces()
    if not _cfg().get("vpn", "auto_detect") or not ifaces:
        return VpnExempt(tuple(sorted(v4)), tuple(sorted(v6)), (), tuple(ifaces))

    # WireGuard (kernel module, wg-quick, NetworkManager, Mullvad, Proton, …):
    # peer endpoints plus the fwmark wg-quick sets on encapsulated packets.
    if shutil.which("wg"):
        try:
            r = subprocess.run(["wg", "show", "all", "endpoints"],
                               capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                cols = line.split()
                if len(cols) >= 3 and cols[2] != "(none)":
                    _add(cols[2].rsplit(":", 1)[0])
            r = subprocess.run(["wg", "show", "all", "fwmark"],
                               capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                cols = line.split()
                if len(cols) >= 2 and cols[1] != "off":
                    try:
                        marks.add((int(cols[1], 0) & 0xFFFFFFFF, None))
                    except ValueError:
                        pass
        except (OSError, subprocess.TimeoutExpired):
            pass

    # OpenVPN, NetworkManager VPNs, strongSwan, …: while a tunnel is up they
    # pin the server with a host route through the physical uplink.
    for fam, flag in ((4, "-4"), (6, "-6")):
        try:
            r = subprocess.run(["ip", flag, "-o", "route", "show", "table", "all"],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in r.stdout.splitlines():
            cols = line.split()
            if not cols or "via" not in cols or "dev" not in cols:
                continue
            dev = cols[cols.index("dev") + 1] if cols.index("dev") + 1 < len(cols) else ""
            if dev in ifaces or dev.startswith(_TUNNEL_PREFIXES):
                continue
            dst = cols[0]
            host_len = 32 if fam == 4 else 128
            net = _ip_or_net(dst)
            if net is not None and net.prefixlen == host_len and net.is_global:
                _add(dst)

    # Tailscale marks its own packets with 0x80000 in the 0xff0000 range.
    if any(i.startswith("tailscale") for i in ifaces):
        marks.add((0x80000, 0xFF0000))

    return VpnExempt(tuple(sorted(v4)), tuple(sorted(v6)),
                     tuple(sorted(marks, key=lambda m: (m[0], m[1] or 0))),
                     tuple(ifaces))


def _nft_vpn_lines(vpn: VpnExempt, fam: int) -> list[str]:
    out = []
    for mark, mask in vpn.marks:
        if mask is None:
            out.append(f"        meta mark 0x{mark:x} return")
        else:
            out.append(f"        meta mark & 0x{mask:x} == 0x{mark:x} return")
    key = "ip" if fam == 4 else "ip6"
    for net in (vpn.v4 if fam == 4 else vpn.v6):
        if _ip_or_net(net) is not None:
            out.append(f"        {key} daddr {net} return")
    return out


def ipv6_enabled() -> bool:
    """False when the kernel runs without IPv6 (ipv6.disable=1, no module).

    nft refuses to load an ip6 table there, and the whole script — kill switch
    included — would fail; without IPv6 there is also nothing to leak.
    """
    return os.path.exists("/proc/net/if_inet6")


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

def _nft_port(value, fallback: int) -> int:
    """Coerce *value* to an in-range port, falling back to a safe default."""
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
        return value
    return fallback


def _nft(script: str) -> None:
    r = subprocess.run(["nft", "-f", "-"], input=script, text=True,
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"nft: {r.stderr.strip()}")


def _nft_build(use_tor: bool, use_dnscrypt: bool,
               tor_trans: int, tor_dns: int, dns_port: int,
               tor_uid: str | None,
               use_i2p: bool = False,
               i2p_transparent: bool = False,
               vpn: VpnExempt = _NO_VPN,
               ipv6: bool = True) -> str:

    # SECURITY: everything below is interpolated into a script handed to
    # `nft -f -` as root.  The port numbers originate in the desktop user's
    # config file, so they are forced to integers here as well as in
    # core.config — a string carrying a newline would otherwise append
    # attacker-chosen rules to the ruleset that is supposed to be preventing
    # leaks, with the GUI still reporting "PROTECTED".
    tor_trans = _nft_port(tor_trans, 9040)
    tor_dns   = _nft_port(tor_dns,   5300)
    dns_port  = _nft_port(dns_port,  5380)
    if tor_uid is not None and not _DECIMAL.match(str(tor_uid)):
        tor_uid = None

    i2p_only = use_i2p and not use_tor and not use_dnscrypt
    lines = [f"table ip {_NFT_TABLE} {{"]

    # ── nat output chain ──────────────────────────────────────
    if use_tor or use_dnscrypt or (i2p_only and i2p_transparent):
        # -100 (dstnat) — the slot iptables' nat OUTPUT uses.  NAT must run
        # BEFORE the filter chain below (priority 0) so the filter sees the
        # post-redirect destination (127.0.0.1:TransPort) and can drop every
        # packet that was *not* redirected.  At priority 100 the filter ran
        # first, saw the original destination and had to let all TCP through,
        # so whatever the SYN-only redirect missed — e.g. a mid-stream packet
        # of a connection opened before connect(), which conntrack picks up
        # as NEW after the flush — went straight out with the real IP.
        lines += ["    chain output {",
                  "        type nat hook output priority -100 ;"]

        if use_tor:
            if tor_uid:
                lines.append(f"        meta skuid {tor_uid} return")
            # A TCP-based VPN (OpenVPN over TCP) must reach its server directly,
            # not be redirected into Tor's TransPort.
            lines += _nft_vpn_lines(vpn, 4)

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
            lines += _nft_vpn_lines(vpn, 4)
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
        # The VPN's own transport (outer packets to its server) — see
        # detect_vpn().  Without it an active VPN dies and Tor cannot start.
        lines += _nft_vpn_lines(vpn, 4)
        # Per-app: bypass UIDs go direct (allow all protocols incl. UDP).
        # Block UIDs are fully isolated here before any other rule.
        bypass_f, block_f = _per_app_rules()
        for uid in bypass_f:
            lines.append(f"        meta skuid {uid} return")
        for uid in block_f:
            lines.append(f"        meta skuid {uid} drop")
        # Packets conntrack cannot place (stray FIN/RST of a flushed
        # connection, …) are never redirected — the transparent-proxy leak the
        # Tor project's TransparentProxy guide warns about.
        lines.append("        ct state invalid drop")
        # Anything routed via loopback never leaves the machine.  This covers
        # the redirected traffic AND its replies: TransPort answers to the
        # host's own address (198.51.x, CGNAT 100.64/10, a public IP …), which
        # is not in the local-net list below.
        lines.append('        oifname "lo" return')
        # The nat chain has already run (priority -100): redirected TCP and
        # DNS now carry daddr 127.0.0.1 and pass the returns above/below.
        # Everything else — UDP, ICMP, SCTP and any TCP the redirect did not
        # catch — is dropped, so the ruleset fails closed.
        for net in _TOR_LOCAL_NETS:
            lines.append(f"        ip daddr {net} return")
        lines += [
            "        drop",
            "    }",
        ]
    elif i2p_only:
        lines += ["    chain filter_output {",
                  "        type filter hook output priority 0 ;"]
        i2pd_uid = _i2pd_uid()
        if i2pd_uid:
            # i2pd needs UDP for SSU2 (peer-to-peer I2P transport)
            lines.append(f"        meta skuid {i2pd_uid} return")
        lines += _nft_vpn_lines(vpn, 4)
        lines.append('        oifname "lo" return')   # see the Tor chain
        for net in _LOCAL_NETS:
            lines.append(f"        ip daddr {net} return")
        lines += [
            "        udp dport 53 drop",
            "        tcp dport 53 drop",
        ]
        # Transparent mode: redirected TCP now points at 127.0.0.1:redsocks
        # (returned above as local).  Proxy-only mode: apps reach the I2P
        # proxies on 127.0.0.1.  Either way nothing else may leave with the
        # real IP — the old "meta l4proto != tcp drop" let every direct TCP
        # connection out, unlike the iptables backend's catch-all DROP.
        lines += [
            "        ct state invalid drop",
            "        drop",
            "    }",
        ]

    # ── DNSCrypt-only: DoH blocking filter chain ─────────────
    # When only DNSCrypt is active (no Tor), apps can bypass DNSCrypt by
    # hardcoding a DoH endpoint (port 443, known resolver IPs).  Block
    # those IPs so all DNS resolving goes through dnscrypt-proxy.
    # (In Tor mode, all TCP already goes through TransPort — no need here.)
    dns_only = use_dnscrypt and not use_tor and not i2p_only
    if dns_only and cfg().get("doh_block"):
        doh_set = ", ".join(_DOH_IPS)
        lines += [
            "    chain filter_output {",
            "        type filter hook output priority 0 ;",
            f"        ip daddr {{ {doh_set} }} tcp dport 443 drop",
            # DNS-over-TLS (TCP) and DNS-over-QUIC (UDP) to any resolver carry
            # lookups around dnscrypt-proxy just like DoH does.
            "        tcp dport 853 drop",
            "        udp dport 853 drop",
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
    v6_block = [
        "    chain output {",
        "        type filter hook output priority 0 ;",
        '        oifname "lo" return',
        "        ip6 daddr ::1 return",        # loopback
        "        ip6 daddr fe80::/10 return",  # link-local (LAN)
        *_nft_vpn_lines(vpn, 6),
        "        drop",
        "    }",
    ]
    if not ipv6:
        pass  # kernel without IPv6: no ip6 family to load, nothing to leak
    elif use_tor:
        # Block all IPv6: Tor's TransPort only listens on IPv4 (127.0.0.1).
        # Any IPv6 connection would reach the internet directly, leaking the
        # real IPv6 address.
        lines += [
            f"\ntable ip6 {_NFT_TABLE} {{",
            *v6_block,
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
            "        type nat hook output priority -100 ;",
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
            *v6_block,
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
        self._ipt_hooks:     list[tuple[str, str, str]] = []
        self._i2p_http:      int             = 4444
        self._i2p_socks:     int             = 4447
        self._tor_socks:     int             = 9050
        self._backend:       str             = ""
        self._use_tor:       bool            = False
        self._rules:         tuple | None    = None   # last apply() args
        self._vpn:           VpnExempt       = _NO_VPN
        # Serialises apply/remove with the watchdog's re-apply.
        self._lock = threading.RLock()

    def apply(self, use_tor: bool, use_dnscrypt: bool, use_i2p: bool,
              i2p_transparent: bool = False) -> None:
        self._log("[FW] Applying firewall rules...")

        self._vpn = detect_vpn()
        if self._vpn.ifaces:
            self._log(f"[FW] VPN interface(s) detected: {', '.join(self._vpn.ifaces)}.")
        if self._vpn.v4 or self._vpn.v6 or self._vpn.marks:
            n = len(self._vpn.v4) + len(self._vpn.v6) + len(self._vpn.marks)
            self._log(f"[FW] Exempting the VPN tunnel's own transport "
                      f"({n} endpoint/mark rule(s)); traffic inside it still "
                      f"goes through the privacy layers.")
        elif self._vpn.ifaces and (use_tor or use_i2p):
            self._log("[FW] Warning: could not determine this VPN's server "
                      "endpoint — if the tunnel drops, add its server address "
                      "to \"vpn\": {\"allow\": [...]} in config.json.")

        with self._lock:
            self._rules = (use_tor, use_dnscrypt, use_i2p, i2p_transparent)
            self._apply_rules()

        # ── system proxy ──────────────────────────────────────────
        # When Tor is active, point the system SOCKS proxy to Tor's
        # SocksPort (9050).  Browsers then send .onion hostnames
        # directly to Tor via SOCKS — no local DNS needed, which
        # bypasses the RFC 7686 block modern browsers enforce on
        # .onion DNS lookups.
        if use_tor:
            self._use_tor    = True
            self._tor_socks  = cfg_port("tor", "socks_port")
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
                        "[FW] I2P proxy-only mode. DNS and direct connections blocked. "
                        f"Set apps to use HTTP proxy 127.0.0.1:{self._i2p_http} "
                        f"or SOCKS 127.0.0.1:{self._i2p_socks}."
                    )

        self._log(f"[FW] Rules applied via {self._backend}.")

        # Flush connection tracking so pre-existing connections are re-evaluated
        # against the new rules.  Without this, established TCP sessions that
        # existed before connect() are NOT subject to the TransPort redirect and
        # continue flowing to their original destinations — a classic bypass.
        self._flush_conntrack()

    def _apply_rules(self) -> None:
        """Load the ruleset for self._rules with the best working backend."""
        use_tor, use_dnscrypt, use_i2p, i2p_transparent = self._rules
        tor_trans = cfg_port("tor", "trans_port")
        tor_dns   = cfg_port("tor", "dns_port")
        dns_port  = cfg_port("dnscrypt", "port")
        self._i2p_http  = cfg_port("i2p", "http_port")
        self._i2p_socks = cfg_port("i2p", "socks_port")

        self._backend = firewall_backend()
        if self._backend == "nftables":
            try:
                self._apply_nft(use_tor, use_dnscrypt, tor_trans, tor_dns,
                                dns_port, use_i2p, i2p_transparent)
                return
            except RuntimeError as exc:
                # e.g. a kernel built without some nf_tables expression.  Fall
                # back rather than leave the user without a kill switch.
                if not shutil.which("iptables"):
                    raise
                self._log(f"[FW] nftables failed ({exc}) — falling back to iptables.")
                for fam in ("ip", "ip6"):
                    subprocess.run(["nft", "delete", "table", fam, _NFT_TABLE],
                                   capture_output=True)
                self._backend = "iptables"
        self._apply_ipt(use_tor, use_dnscrypt, tor_trans, tor_dns,
                        dns_port, use_i2p, i2p_transparent)

    def rules_intact(self) -> bool:
        """True while our ruleset is still loaded in the kernel."""
        with self._lock:
            if not self._backend or not self._rules:
                return True
            if self._backend == "nftables":
                r = subprocess.run(["nft", "list", "table", "ip", _NFT_TABLE],
                                   capture_output=True)
                return r.returncode == 0
            for binary, table, builtin in self._ipt_hooks:
                r = subprocess.run(
                    [binary, "-t", table, "-C", builtin, "-j", _IPT_CHAINS[builtin]],
                    capture_output=True)
                if r.returncode != 0:
                    return False
            return True

    def reapply_if_missing(self) -> bool:
        """Re-load the ruleset if another tool removed it.  Returns True if so.

        `ufw reload`, `firewall-cmd --reload`, `iptables -F`, `nft flush
        ruleset`, a NetworkManager dispatcher script … can all wipe our rules
        while the GUI keeps saying PROTECTED.  The session watchdog calls this.
        """
        with self._lock:
            if self.rules_intact():
                return False
            self._log("[FW] WARNING: firewall rules were removed by another "
                      "program — re-applying the kill switch.")
            self._ipt_hooks.clear()
            self._apply_rules()
            self._flush_conntrack()
            return True

    def remove(self) -> None:
        with self._lock:
            self._rules = None
            self._remove()

    def _remove(self) -> None:
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
            for binary, table, builtin in reversed(self._ipt_hooks):
                chain = _IPT_CHAINS[builtin]
                r = subprocess.run(
                    [binary, "-t", table, "-D", builtin, "-j", chain],
                    capture_output=True, text=True)
                if r.returncode != 0 and binary == "iptables":
                    self._log(
                        f"[FW] Warning: could not unhook {table}/{chain}: "
                        f"{r.stderr.strip()}"
                    )
                subprocess.run([binary, "-t", table, "-F", chain], capture_output=True)
                subprocess.run([binary, "-t", table, "-X", chain], capture_output=True)
            self._ipt_hooks.clear()
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
            use_i2p, i2p_transparent, vpn=self._vpn, ipv6=ipv6_enabled(),
        )
        _nft(script)

    # ── iptables ──────────────────────────────────────────────

    def _ipt_hook(self, binary: str, table: str, builtin: str) -> str:
        """Return our own chain for *builtin*, creating and hooking it once.

        Rules used to be appended (-A) straight to the built-in chains, i.e.
        BEHIND whatever was already there: one pre-existing ACCEPT (ufw,
        firewalld, docker, a VPN client) and the kill switch never ran.  Our
        rules now live in a dedicated chain that is jumped to from position 1
        of the built-in chain, so they are evaluated first, and removal is a
        matter of unhooking and deleting that chain — no rule-by-rule undo,
        no host-wide flush.
        """
        chain = _IPT_CHAINS[builtin]
        key = (binary, table, builtin)
        if key in self._ipt_hooks:
            return chain
        subprocess.run([binary, "-t", table, "-N", chain], capture_output=True)
        subprocess.run([binary, "-t", table, "-F", chain], capture_output=True)
        r = subprocess.run([binary, "-t", table, "-I", builtin, "1", "-j", chain],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"{binary} -t {table} -I {builtin}: {r.stderr.strip()}")
        self._ipt_hooks.append(key)
        return chain

    def _ipt_run(self, binary: str, args: tuple[str, ...]) -> subprocess.CompletedProcess:
        """Run an ``-A <BUILTIN> …`` rule, retargeted at our own chain."""
        argv = list(args)
        table = "filter"
        if "-t" in argv:
            table = argv[argv.index("-t") + 1]
        i = argv.index("-A")
        argv[i + 1] = self._ipt_hook(binary, table, argv[i + 1])
        return subprocess.run([binary] + argv, capture_output=True, text=True)

    def _ipt_add(self, *args: str) -> None:
        r = self._ipt_run("iptables", args)
        if r.returncode != 0:
            raise RuntimeError(
                f"iptables {' '.join(args)}: {r.stderr.strip()}")

    def _ip6t_add(self, *args: str, required: bool = False) -> None:
        """Add an ip6tables rule.

        Optional rules may fail silently.  *required* ones are the IPv6 kill
        switch: if IPv6 is up and they cannot be loaded, real IPv6 traffic
        would bypass Tor entirely, so connect() must fail instead.
        """
        try:
            r = self._ipt_run("ip6tables", args)
            ok = r.returncode == 0
            err = r.stderr.strip()
        except (RuntimeError, OSError) as exc:
            ok, err = False, str(exc)
        if not ok and required and ipv6_enabled():
            raise RuntimeError(
                "IPv6 is enabled but ip6tables rules could not be loaded "
                f"({err or 'ip6tables missing'}) — refusing to connect with "
                "an IPv6 leak. Install ip6tables or disable IPv6.")

    def _ipt_vpn(self, table: str, verdict: str) -> None:
        """Exempt the VPN transport (see detect_vpn) in *table*."""
        for mark, mask in self._vpn.marks:
            m = f"0x{mark:x}" + (f"/0x{mask:x}" if mask is not None else "")
            self._ipt_add("-t", table, "-A", "OUTPUT", "-m", "mark", "--mark", m,
                          "-j", verdict)
            if table == "filter":
                self._ip6t_add("-A", "OUTPUT", "-m", "mark", "--mark", m,
                               "-j", verdict)
        for net in self._vpn.v4:
            self._ipt_add("-t", table, "-A", "OUTPUT", "-d", net, "-j", verdict)
        if table == "filter":
            for net in self._vpn.v6:
                self._ip6t_add("-A", "OUTPUT", "-d", net, "-j", verdict)

    def _apply_ipt(self, use_tor: bool, use_dnscrypt: bool,
                   tor_trans: int, tor_dns: int, dns_port: int,
                   use_i2p: bool = False,
                   i2p_transparent: bool = False) -> None:
        # Same reasoning as _nft_build(): these come from the user's config and
        # are about to become command arguments run as root.
        tor_trans = _nft_port(tor_trans, 9040)
        tor_dns   = _nft_port(tor_dns,   5300)
        dns_port  = _nft_port(dns_port,  5380)
        i2p_only = use_i2p and not use_tor and not use_dnscrypt
        # Start from a clean slate, like _apply_nft() deleting its tables.
        purge_iptables_chains()

        if use_tor:
            uid = _tor_uid()
            if uid:
                self._ipt_add("-t", "nat", "-A", "OUTPUT",
                               "-m", "owner", "--uid-owner", uid, "-j", "RETURN")
            self._ipt_vpn("nat", "RETURN")
            # Per-app routing: bypass UIDs skip TransPort (direct internet).
            bypass_uids, block_uids = _per_app_rules()
            for b_uid in bypass_uids:
                self._ipt_add("-t", "nat", "-A", "OUTPUT",
                               "-m", "owner", "--uid-owner", b_uid, "-j", "RETURN")
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
            self._ipt_vpn("filter", "ACCEPT")
            # Per-app: bypass UIDs allowed all protocols (direct internet).
            # Block UIDs fully isolated before local-net accepts.
            for b_uid in bypass_uids:
                self._ipt_add("-t", "filter", "-A", "OUTPUT",
                               "-m", "owner", "--uid-owner", b_uid, "-j", "ACCEPT")
            for b_uid in block_uids:
                self._ipt_add("-t", "filter", "-A", "OUTPUT",
                               "-m", "owner", "--uid-owner", b_uid, "-j", "DROP")
            # Same invalid-state leak guard and loopback accept as the
            # nftables ruleset.  Without -o lo the replies of TransPort to a
            # non-RFC1918 host address hit the catch-all DROP below.
            self._ipt_add("-t", "filter", "-A", "OUTPUT",
                           "-m", "conntrack", "--ctstate", "INVALID", "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")
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
            self._ip6t_block()

            # Block forwarded packets (prevents clearnet bypass via IP forwarding).
            self._ipt_add("-t", "filter", "-A", "FORWARD", "-j", "DROP")

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
            # Block DoH in DNSCrypt-only mode (no Tor) so apps can't bypass
            # dnscrypt-proxy by hardcoding a DoH resolver on port 443.
            if not use_tor and cfg().get("doh_block"):
                for doh_ip in _DOH_IPS:
                    self._ipt_add("-t", "filter", "-A", "OUTPUT",
                                   "-p", "tcp", "--dport", "443",
                                   "-d", doh_ip, "-j", "DROP")
                # DNS-over-TLS / DNS-over-QUIC, as in the nftables ruleset.
                for proto in ("tcp", "udp"):
                    self._ipt_add("-t", "filter", "-A", "OUTPUT",
                                   "-p", proto, "--dport", "853", "-j", "DROP")

        if i2p_only:
            i2pd_uid = _i2pd_uid()

            if i2p_transparent:
                # Exclude i2pd (needs direct internet for peer connections)
                if i2pd_uid:
                    self._ipt_add("-t", "nat", "-A", "OUTPUT",
                                   "-m", "owner", "--uid-owner", i2pd_uid, "-j", "RETURN")
                self._ipt_vpn("nat", "RETURN")
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
            self._ipt_vpn("filter", "ACCEPT")
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")
            for net in _LOCAL_NETS:
                self._ipt_add("-t", "filter", "-A", "OUTPUT", "-d", net, "-j", "ACCEPT")
            self._ipt_add("-t", "filter", "-A", "OUTPUT",
                           "-p", "udp", "--dport", "53", "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT",
                           "-p", "tcp", "--dport", "53", "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT",
                           "-m", "conntrack", "--ctstate", "INVALID", "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-p", "udp",  "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-p", "icmp", "-j", "DROP")
            self._ipt_add("-t", "filter", "-A", "OUTPUT", "-j", "DROP")  # catch-all

            # IPv6 was left wide open here, unlike the nftables backend.
            self._ip6t_block()

            # Block forwarded packets for I2P-only mode too.
            self._ipt_add("-t", "filter", "-A", "FORWARD", "-j", "DROP")

    def _ip6t_block(self) -> None:
        """IPv6 kill switch for the iptables backend (mirrors the nft ip6 table)."""
        if not ipv6_enabled():
            return
        self._ip6t_add("-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT", required=True)
        self._ip6t_add("-A", "OUTPUT", "-d", "fe80::/10", "-j", "ACCEPT", required=True)
        self._ip6t_add("-A", "OUTPUT", "-j", "DROP", required=True)
        self._ip6t_add("-A", "FORWARD", "-j", "DROP", required=True)

    # ── system proxy (GNOME + KDE) ────────────────────────────

    def _set_proxy(self, enable: bool) -> None:
        info = _real_user()
        if info is None:
            return
        uid, pw = info
        self._set_gnome_proxy(pw, enable)
        self._set_kde_proxy(pw, enable)

    def _user_systemctl(self, pw: pwd.struct_passwd, *args: str) -> None:
        """Run 'systemctl --user' in the desktop user's session.

        Always as that user (runuser).  Talking to their session bus as root —
        the old first attempt — meant a root process speaking D-Bus to a
        socket the user fully controls, for no benefit.
        """
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
        self._set_profile_d_proxy(enable, socks5h_url)
        # Clean up any environment.d file written by older versions.
        # We no longer use systemctl --user set-environment because it injects
        # proxy vars into every running GUI process (Spotify, Electron apps, etc.).
        env_file = os.path.join(pw.pw_dir, ".config", "environment.d",
                                "entropy-shield-proxy.conf")
        if not enable:
            self._user_systemctl(
                pw, "unset-environment",
                "ALL_PROXY", "all_proxy", "SOCKS_PROXY", "socks_proxy",
                "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "NO_PROXY", "no_proxy")
        # Removed AS THE USER: the path is inside their home, so they control
        # every directory on it.  A root unlink() would follow a symlinked
        # ~/.config/environment.d into any directory on the system.
        if os.path.lexists(env_file):
            self._user_run(pw, ["rm", "-f", "--", env_file])

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

