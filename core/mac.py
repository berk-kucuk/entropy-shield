"""MAC address randomization — applied on connect, restored on disconnect.

Uses `ip link` (iproute2) — no external dependency.
The interface is brought down briefly during the MAC change; DHCP
will negotiate a new lease when it comes back up, which further
reduces local-network traceability.
"""
from __future__ import annotations
import random
import re
import subprocess
from typing import Callable

# Interface name prefixes that should never be touched
_SKIP = ("lo", "docker", "veth", "br-", "virbr", "tun", "tap",
         "wg", "dummy", "bond", "team", "vmnet", "vboxnet", "xenbr",
         "ovs-", "flannel", "cni", "calico", "cilium")


def _interfaces() -> list[str]:
    """Return UP physical/wifi interfaces, excluding virtual ones."""
    r = subprocess.run(["ip", "-o", "link", "show", "up"],
                       capture_output=True, text=True)
    ifaces: list[str] = []
    for line in r.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        iface = parts[1].strip().split("@")[0].strip()
        if any(iface.startswith(p) for p in _SKIP):
            continue
        ifaces.append(iface)
    return ifaces


def _get_mac(iface: str) -> str | None:
    r = subprocess.run(["ip", "link", "show", iface],
                       capture_output=True, text=True)
    m = re.search(r"link/ether\s+([0-9a-f:]{17})", r.stdout)
    return m.group(1) if m else None


def _random_mac() -> str:
    b = [random.randint(0, 255) for _ in range(6)]
    b[0] = (b[0] & 0xFE) | 0x02  # unicast + locally administered
    return ":".join(f"{x:02x}" for x in b)


class MacRandomizer:
    def __init__(self, log: Callable[[str], None]):
        self._log  = log
        self._saved: dict[str, str] = {}

    def randomize(self) -> None:
        """Randomize MAC on all suitable interfaces. Best-effort — never raises."""
        for iface in _interfaces():
            orig = _get_mac(iface)
            if orig is None:
                continue
            new_mac = _random_mac()

            # Try without down/up first — avoids NetworkManager race condition
            # where NM detects the link-down and restores the original MAC.
            r = subprocess.run(
                ["ip", "link", "set", iface, "address", new_mac],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                # Driver requires interface to be down; try with down/up.
                subprocess.run(["ip", "link", "set", iface, "down"],
                               capture_output=True)
                r = subprocess.run(
                    ["ip", "link", "set", iface, "address", new_mac],
                    capture_output=True, text=True,
                )
                subprocess.run(["ip", "link", "set", iface, "up"],
                               capture_output=True)

            actual = _get_mac(iface)
            if actual == new_mac:
                self._saved[iface] = orig
                self._log(f"[MAC] {iface}: {orig} → {new_mac}")
            else:
                err = r.stderr.strip() or "driver or NetworkManager rejected change"
                self._log(f"[MAC] {iface}: randomization failed ({err})")

    def restore(self) -> None:
        """Restore original MACs. Best-effort — never raises."""
        for iface, orig in self._saved.items():
            r = subprocess.run(
                ["ip", "link", "set", iface, "address", orig],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                subprocess.run(["ip", "link", "set", iface, "down"],
                               capture_output=True)
                r = subprocess.run(
                    ["ip", "link", "set", iface, "address", orig],
                    capture_output=True, text=True,
                )
                subprocess.run(["ip", "link", "set", iface, "up"],
                               capture_output=True)

            actual = _get_mac(iface)
            if actual == orig:
                self._log(f"[MAC] {iface}: restored → {orig}")
            else:
                self._log(f"[MAC] {iface}: restore failed ({r.stderr.strip()})")
        self._saved.clear()
