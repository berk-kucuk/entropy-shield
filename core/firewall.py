from __future__ import annotations
import subprocess
import shutil
from typing import Callable

from .config import cfg
from .platform import firewall_backend

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

_NFT_TABLE = "entropy-shield"


def _tor_uid() -> str | None:
    for name in ("debian-tor", "tor", "_tor", "toranon"):
        r = subprocess.run(["id", "-u", name], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    return None


# ── nftables backend ──────────────────────────────────────────

def _nft(script: str) -> None:
    r = subprocess.run(["nft", "-f", "-"], input=script, text=True,
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"nft: {r.stderr.strip()}")


def _nft_build(use_tor: bool, use_dnscrypt: bool,
               tor_trans: int, tor_dns: int, dns_port: int,
               tor_uid: str | None,
               use_lokinet: bool = False) -> str:
    lines = [
        f"table ip {_NFT_TABLE} {{",
        "    chain output {",
        "        type nat hook output priority 100 ;",
    ]

    if use_tor:
        if tor_uid:
            lines.append(f"        meta skuid {tor_uid} return")
        for net in _LOCAL_NETS:
            lines.append(f"        ip daddr {net} return")

        dns_target = dns_port if use_dnscrypt else tor_dns
        lines.append(f"        udp dport 53 redirect to :{dns_target}")
        lines.append(f"        tcp dport 53 redirect to :{dns_target}")
        lines.append(
            f"        tcp flags & (fin|syn|rst|ack) == syn "
            f"redirect to :{tor_trans}"
        )

    elif use_dnscrypt:
        lines.append(f"        udp dport 53 redirect to :{dns_port}")
        lines.append(f"        tcp dport 53 redirect to :{dns_port}")

    elif use_lokinet:
        # Lokinet'in dahili DNS resolver'ı 127.3.2.1:53
        lines.append("        ip daddr 127.3.2.1 return")
        lines.append("        udp dport 53 dnat to 127.3.2.1:53")
        lines.append("        tcp dport 53 dnat to 127.3.2.1:53")

    lines += ["    }", "}"]
    return "\n".join(lines)


# ── iptables backend ──────────────────────────────────────────

def _ipt_run(*args: str) -> None:
    r = subprocess.run(["iptables"] + list(args), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"iptables {' '.join(args)}: {r.stderr.strip()}")


# ── FirewallManager ───────────────────────────────────────────

class FirewallManager:
    def __init__(self, log: Callable[[str], None]):
        self._log = log
        self._ipt_rules: list[list[str]] = []
        self._i2p_http: int = 4444
        self._backend: str = ""

    def apply(self, use_tor: bool, use_dnscrypt: bool, use_i2p: bool,
              use_lokinet: bool = False) -> None:
        self._log("[FW] Applying firewall rules...")
        self._backend = firewall_backend()

        tor_trans = cfg().get("tor", "trans_port")
        tor_dns   = cfg().get("tor", "dns_port")
        dns_port  = cfg().get("dnscrypt", "port")
        i2p_http  = cfg().get("i2p", "http_port")
        self._i2p_http = i2p_http

        if self._backend == "nftables":
            self._apply_nft(use_tor, use_dnscrypt, tor_trans, tor_dns, dns_port,
                            use_lokinet)
        else:
            self._apply_ipt(use_tor, use_dnscrypt, tor_trans, tor_dns, dns_port,
                            use_lokinet)

        if use_i2p:
            self._set_proxy(True)

        self._log(f"[FW] Rules applied via {self._backend}.")

    def remove(self) -> None:
        if not self._backend:
            return

        self._log("[FW] Removing firewall rules...")

        if self._backend == "nftables":
            subprocess.run(
                ["nft", "delete", "table", "ip", _NFT_TABLE],
                capture_output=True,
            )
        else:
            for rule in reversed(self._ipt_rules):
                del_rule = ["-D" if a == "-A" else a for a in rule]
                subprocess.run(["iptables"] + del_rule, capture_output=True)
            self._ipt_rules.clear()

        self._set_proxy(False)
        self._log("[FW] Rules removed.")

    # ── nft ───────────────────────────────────────────────────

    def _apply_nft(self, use_tor: bool, use_dnscrypt: bool,
                   tor_trans: int, tor_dns: int, dns_port: int,
                   use_lokinet: bool = False) -> None:
        # Remove any leftover table first
        subprocess.run(
            ["nft", "delete", "table", "ip", _NFT_TABLE],
            capture_output=True,
        )
        if not (use_tor or use_dnscrypt or use_lokinet):
            return
        uid = _tor_uid() if use_tor else None
        script = _nft_build(use_tor, use_dnscrypt, tor_trans, tor_dns,
                             dns_port, uid, use_lokinet)
        _nft(script)

    # ── iptables ──────────────────────────────────────────────

    def _ipt_add(self, *args: str) -> None:
        _ipt_run(*args)
        if "-A" in args:
            self._ipt_rules.append(list(args))

    def _apply_ipt(self, use_tor: bool, use_dnscrypt: bool,
                   tor_trans: int, tor_dns: int, dns_port: int,
                   use_lokinet: bool = False) -> None:
        if use_tor:
            uid = _tor_uid()
            if uid:
                self._ipt_add("-t", "nat", "-A", "OUTPUT",
                               "-m", "owner", "--uid-owner", uid,
                               "-j", "RETURN")
            for net in _LOCAL_NETS:
                self._ipt_add("-t", "nat", "-A", "OUTPUT",
                               "-d", net, "-j", "RETURN")

            dns_target = str(dns_port) if use_dnscrypt else str(tor_dns)
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "udp", "--dport", "53",
                           "-j", "REDIRECT", "--to-ports", dns_target)
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "tcp", "--dport", "53",
                           "-j", "REDIRECT", "--to-ports", dns_target)
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "tcp", "--syn",
                           "-j", "REDIRECT", "--to-ports", str(tor_trans))

        elif use_dnscrypt:
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "udp", "--dport", "53",
                           "-j", "REDIRECT", "--to-ports", str(dns_port))
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "tcp", "--dport", "53",
                           "-j", "REDIRECT", "--to-ports", str(dns_port))

        elif use_lokinet:
            # Lokinet'in dahili DNS resolver'ı: 127.3.2.1:53
            # Tüm DNS sorgularını bu adrese yönlendir
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "udp", "--dport", "53",
                           "!", "-d", "127.3.2.1",
                           "-j", "DNAT", "--to-destination", "127.3.2.1:53")
            self._ipt_add("-t", "nat", "-A", "OUTPUT",
                           "-p", "tcp", "--dport", "53",
                           "!", "-d", "127.3.2.1",
                           "-j", "DNAT", "--to-destination", "127.3.2.1:53")

    # ── gsettings proxy ───────────────────────────────────────

    def _set_proxy(self, enable: bool) -> None:
        if not shutil.which("gsettings"):
            return
        try:
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
                ]:
                    subprocess.run(cmd, capture_output=True)
            else:
                subprocess.run(
                    ["gsettings", "set", "org.gnome.system.proxy",
                     "mode", "none"],
                    capture_output=True,
                )
        except Exception:
            pass
