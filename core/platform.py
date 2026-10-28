from __future__ import annotations
import os
import shutil
import subprocess


def is_nixos() -> bool:
    try:
        with open("/etc/os-release") as f:
            return "ID=nixos" in f.read()
    except Exception:
        return os.path.exists("/run/current-system")


def _nft_usable() -> bool:
    """True if the nft binary exists AND the kernel answers it.

    The binary alone proves nothing: minimal kernels, some containers/VPS
    hypervisors and old distributions ship nft without a working nf_tables,
    and every connect would then fail instead of using iptables.
    """
    if not shutil.which("nft"):
        return False
    try:
        r = subprocess.run(["nft", "list", "tables"], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def firewall_backend() -> str:
    # Prefer nftables: on NixOS nftables is native; iptables is often a
    # nft shim that can behave unexpectedly with NixOS's own ruleset.
    # (Run as root by the daemon — the probe needs CAP_NET_ADMIN.)
    if _nft_usable():
        return "nftables"
    if shutil.which("iptables"):
        return "iptables"
    raise RuntimeError(
        "Neither nft nor iptables found.\n"
        "Install nftables or iptables and try again."
    )
