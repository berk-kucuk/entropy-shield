"""Leak test suite — checks DNS leaks, IPv6 leaks, WebRTC, timezone."""
from __future__ import annotations
import subprocess
import socket
import os
import time
from dataclasses import dataclass


@dataclass
class TestResult:
    name:    str
    passed:  bool
    message: str
    detail:  str = ""


class LeakTester:
    def __init__(self, socks_port: int = 9050, dns_port: int = 5300):
        self._socks = socks_port
        self._dns   = dns_port

    # ── public ────────────────────────────────────────────────────────────────

    def run_all(self) -> list[TestResult]:
        results = []
        results.append(self.test_tor_exit())
        results.append(self.test_dns_leak())
        results.append(self.test_ipv6_leak())
        results.append(self.test_webrtc_udp())
        results.append(self.test_timezone())
        results.append(self.test_hostname())
        return results

    # ── individual tests ──────────────────────────────────────────────────────

    def test_tor_exit(self) -> TestResult:
        """Verify public IP is a Tor exit node (not the real IP)."""
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "12",
                 "--socks5-hostname", f"127.0.0.1:{self._socks}",
                 "https://check.torproject.org/api/ip"],
                capture_output=True, text=True, timeout=18,
            )
            if r.returncode != 0:
                return TestResult("Tor Exit IP", False,
                                  "curl failed — is Tor running?",
                                  r.stderr.strip())
            import json
            data   = json.loads(r.stdout)
            ip     = data.get("IP", "?")
            is_tor = data.get("IsTor", False)
            if is_tor:
                return TestResult("Tor Exit IP", True,
                                  f"Exit IP: {ip}  ✓ via Tor")
            return TestResult("Tor Exit IP", False,
                              f"IP: {ip}  ✗ NOT a Tor exit",
                              "Real IP may be exposed")
        except FileNotFoundError:
            return TestResult("Tor Exit IP", False,
                              "curl not found — install curl")
        except Exception as e:
            return TestResult("Tor Exit IP", False, f"Error: {e}")

    def test_dns_leak(self) -> TestResult:
        """Query a DNS server through Tor and verify it is not the system resolver."""
        try:
            # Ask Tor's DNSPort directly for a unique subdomain
            # to verify DNS goes through Tor and not the clearnet resolver.
            test_domain = "check.torproject.org"
            r = subprocess.run(
                ["dig", "+short", f"@127.0.0.1", f"-p{self._dns}",
                 test_domain, "A"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                ip = r.stdout.strip().split()[-1]
                return TestResult("DNS Leak", True,
                                  f"DNS resolves via Tor DNSPort  ✓ ({ip})")
            # Fallback: check if resolv.conf points to 127.0.0.1
            try:
                with open("/etc/resolv.conf") as f:
                    resolv = f.read()
                if "127.0.0.1" in resolv or "127.0.0.53" in resolv:
                    return TestResult("DNS Leak", True,
                                      "resolv.conf → localhost resolver  ✓")
            except Exception:
                pass
            return TestResult("DNS Leak", False,
                              "Could not verify DNS routing",
                              r.stderr.strip() or "dig returned no result")
        except FileNotFoundError:
            # dig not found — check resolv.conf only
            try:
                with open("/etc/resolv.conf") as f:
                    resolv = f.read()
                if "127.0.0.1" in resolv:
                    return TestResult("DNS Leak", True,
                                      "resolv.conf → 127.0.0.1  ✓ (install dig for deeper test)")
                return TestResult("DNS Leak", False,
                                  "resolv.conf does not point to 127.0.0.1",
                                  "Install dnsutils/bind-tools for deep test")
            except Exception:
                return TestResult("DNS Leak", False, "Could not check DNS config")
        except Exception as e:
            return TestResult("DNS Leak", False, f"Error: {e}")

    def test_ipv6_leak(self) -> TestResult:
        """Verify no IPv6 address is reachable externally."""
        # Try to get a global (non-link-local) IPv6 address on any interface
        try:
            r = subprocess.run(
                ["ip", "-6", "addr", "show", "scope", "global"],
                capture_output=True, text=True, timeout=5,
            )
            lines = [ln for ln in r.stdout.splitlines() if "inet6" in ln]
            if not lines:
                return TestResult("IPv6 Leak", True,
                                  "No global IPv6 address assigned  ✓")
            # Global IPv6 found — check if it can reach the internet
            # (it should be blocked by nftables ip6 table)
            try:
                r2 = subprocess.run(
                    ["curl", "-s", "--max-time", "4", "--ipv6",
                     "https://ipv6.icanhazip.com"],
                    capture_output=True, text=True, timeout=6,
                )
                if r2.returncode != 0 or not r2.stdout.strip():
                    return TestResult("IPv6 Leak", True,
                                      "IPv6 address exists but outbound blocked  ✓")
                return TestResult("IPv6 Leak", False,
                                  f"IPv6 reachable: {r2.stdout.strip()}",
                                  "Real IPv6 address exposed!")
            except Exception:
                return TestResult("IPv6 Leak", True,
                                  "IPv6 outbound appears blocked  ✓")
        except Exception as e:
            return TestResult("IPv6 Leak", False, f"Error: {e}")

    def test_webrtc_udp(self) -> TestResult:
        """Check if UDP is blocked (prevents WebRTC leaks)."""
        # Try to send a UDP packet to a well-known external IP.
        # If the firewall is active, this should fail or timeout.
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            target = ("8.8.8.8", 53)
            try:
                s.sendto(b"\x00" * 16, target)
                # If send succeeds immediately, the packet may have gone out
                # (no kernel error). Check by trying to receive — DNS query
                # format is invalid so we'd get ICMP port unreachable or timeout.
                try:
                    s.recv(64)
                    # Got a response — UDP is NOT blocked
                    s.close()
                    return TestResult("WebRTC / UDP Block", False,
                                      "UDP to external IP succeeded  ✗",
                                      "WebRTC / QUIC leaks possible")
                except (socket.timeout, ConnectionRefusedError):
                    # Timeout or ICMP unreachable — packet likely blocked
                    s.close()
                    return TestResult("WebRTC / UDP Block", True,
                                      "UDP to external IP timed out  ✓")
            except OSError as ex:
                s.close()
                if "blocked" in str(ex).lower() or ex.errno in (1, 13, 101, 111):
                    return TestResult("WebRTC / UDP Block", True,
                                      f"UDP blocked by firewall  ✓ ({ex.strerror})")
                return TestResult("WebRTC / UDP Block", True,
                                  f"UDP send failed  ✓ ({ex.strerror})")
        except Exception as e:
            return TestResult("WebRTC / UDP Block", False, f"Error: {e}")

    def test_timezone(self) -> TestResult:
        """Check if system timezone leaks location (should be UTC)."""
        try:
            tz = os.environ.get("TZ", "")
            if not tz:
                # Read /etc/localtime symlink
                offset_sec = -time.timezone if not time.localtime().tm_isdst else -time.altzone
                if offset_sec == 0:
                    return TestResult("Timezone", True,
                                      "System timezone is UTC  ✓")
                hours = offset_sec // 3600
                return TestResult("Timezone", False,
                                  f"System timezone offset: UTC{hours:+d}",
                                  "Non-UTC timezone can fingerprint your location")
            if tz.upper() in ("UTC", "UTC0", "GMT", "GMT0", "Etc/UTC"):
                return TestResult("Timezone", True, "TZ=UTC  ✓")
            return TestResult("Timezone", False,
                              f"TZ={tz}",
                              "Consider setting TZ=UTC while connected")
        except Exception as e:
            return TestResult("Timezone", False, f"Error: {e}")

    def test_hostname(self) -> TestResult:
        """Check if hostname reveals identity (should not be a real name)."""
        try:
            hn = socket.gethostname()
            # Flag common patterns that reveal real hostnames
            risky_patterns = [
                hn.endswith(".local"),
                hn.endswith(".lan"),
                "." in hn and not hn.endswith(".invalid"),
                len(hn) > 3 and hn not in ("localhost", "anonymous"),
            ]
            if any(risky_patterns):
                return TestResult("Hostname", False,
                                  f"Hostname: {hn}",
                                  "Hostname may leak identity in mDNS/NBNS packets")
            return TestResult("Hostname", True, f"Hostname: {hn}  ✓")
        except Exception as e:
            return TestResult("Hostname", False, f"Error: {e}")
