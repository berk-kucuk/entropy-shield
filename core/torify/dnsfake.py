"""Fake-IP DNS for an app running through Tor.

The app's DNS never leaves the namespace and is never really resolved here.
Every name gets a made-up address from 10.192.0.0/10 (the same range Tor uses
for its own VirtualAddrNetwork), and the proxy turns that address back into
the name when the app connects — so the name travels to Tor inside the SOCKS
request and is resolved by the exit relay. Two things follow:

* nothing about the lookup is visible to the local network, the ISP or the
  system resolver, and
* .onion addresses work, because Tor sees the name, not an IP.

Only what is needed is parsed: one question per query, A answered with a fake
address, AAAA and every other type answered empty (NOERROR, no records), so
the app falls back to IPv4 without treating the name as missing.
"""
from __future__ import annotations

import ipaddress
import struct
import threading

POOL = ipaddress.ip_network("10.192.0.0/10")
TTL = 60

QTYPE_A = 1
QTYPE_PTR = 12


class DnsError(ValueError):
    pass


class FakeIPPool:
    """name ↔ fake IPv4 address, stable for the life of one app session."""

    def __init__(self, network: ipaddress.IPv4Network = POOL):
        self._net = network
        self._next = int(network.network_address) + 1
        self._last = int(network.broadcast_address) - 1
        self._by_name: dict[str, str] = {}
        self._by_ip: dict[str, str] = {}
        self._lock = threading.Lock()

    def ip_for(self, name: str) -> str:
        name = name.rstrip(".").lower()
        with self._lock:
            ip = self._by_name.get(name)
            if ip is None:
                if self._next > self._last:
                    # Four million names in one session: start over rather than
                    # fail. Old mappings may now point at new names, which can
                    # only misroute a connection held open that long.
                    self._next = int(self._net.network_address) + 1
                ip = str(ipaddress.IPv4Address(self._next))
                self._next += 1
                old = self._by_ip.pop(ip, None)
                if old is not None:
                    self._by_name.pop(old, None)
                self._by_name[name] = ip
                self._by_ip[ip] = name
            return ip

    def name_for(self, ip: str) -> str | None:
        with self._lock:
            return self._by_ip.get(ip)

    def owns(self, ip: str) -> bool:
        try:
            return ipaddress.IPv4Address(ip) in self._net
        except ValueError:
            return False


def _read_name(packet: bytes, offset: int) -> tuple[str, int]:
    """A question name. Queries never use compression pointers in the
    question, so one is refused rather than followed."""
    labels = []
    while True:
        if offset >= len(packet):
            raise DnsError("truncated name")
        length = packet[offset]
        offset += 1
        if length == 0:
            break
        if length & 0xC0:
            raise DnsError("compressed name in question")
        label = packet[offset:offset + length]
        if len(label) != length:
            raise DnsError("truncated label")
        labels.append(label.decode("ascii", "replace"))
        offset += length
        if sum(len(x) + 1 for x in labels) > 255:
            raise DnsError("name too long")
    return ".".join(labels), offset


def parse_query(packet: bytes) -> tuple[int, int, str, int, bytes]:
    """(id, flags, name, qtype, raw question section)."""
    if len(packet) < 12:
        raise DnsError("short packet")
    qid, flags, qdcount = struct.unpack("!HHH", packet[:6])
    if flags & 0x8000:
        raise DnsError("not a query")
    if qdcount != 1:
        raise DnsError("expected one question")
    name, end = _read_name(packet, 12)
    if end + 4 > len(packet):
        raise DnsError("truncated question")
    qtype, _qclass = struct.unpack("!HH", packet[end:end + 4])
    return qid, flags, name, qtype, packet[12:end + 4]


def _reverse_ip(name: str) -> str | None:
    parts = name.lower().split(".")
    if len(parts) == 6 and parts[4:] == ["in-addr", "arpa"]:
        return ".".join(reversed(parts[:4]))
    return None


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("ascii", "replace")[:63]
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def answer(packet: bytes, pool: FakeIPPool) -> bytes:
    """The reply to one query. Raises DnsError for anything that is not a
    well-formed single-question query (the caller drops it)."""
    qid, flags, name, qtype, question = parse_query(packet)
    rd = flags & 0x0100
    reply_flags = 0x8000 | 0x0400 | rd | 0x0080   # QR, AA, RD copied, RA
    records = b""
    count = 0
    if qtype == QTYPE_A and name:
        ip = pool.ip_for(name)
        records = (b"\xc0\x0c" + struct.pack("!HHIH", QTYPE_A, 1, TTL, 4)
                   + ipaddress.IPv4Address(ip).packed)
        count = 1
    elif qtype == QTYPE_PTR:
        target = pool.name_for(_reverse_ip(name) or "")
        if target:
            rdata = _encode_name(target)
            records = (b"\xc0\x0c" + struct.pack("!HHIH", QTYPE_PTR, 1, TTL, len(rdata))
                       + rdata)
            count = 1
    header = struct.pack("!HHHHHH", qid, reply_flags, 1, count, 0, 0)
    return header + question + records
