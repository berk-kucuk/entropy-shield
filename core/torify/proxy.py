"""The proxy inside an app's network namespace.

Runs in the namespace, next to the app, and is the only way anything gets
out: nftables sends every TCP connection to 127.0.0.1:9040 and every DNS
query to 127.0.0.1:5353, and this process carries the connections to Tor
over a Unix socket — a filesystem path, which is the one thing the namespace
shares with the host.

* DNS: answered from a fake-IP pool (see dnsfake.py); nothing is resolved here.
* TCP: the original destination comes from SO_ORIGINAL_DST; a fake address is
  turned back into its name, and Tor is asked to CONNECT to that name and port.
* Isolation: the SOCKS username is the session's token, read from a file on
  every new connection. Tor keeps streams with different usernames on
  different circuits, so each app has its own, and "New identity" is just a
  new token.

If this process dies, the app is left with no route anywhere: there is no
fallback to a direct connection, by construction.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import struct
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

from core.torify.dnsfake import DnsError, FakeIPPool, answer  # noqa: E402

TRANS_PORT = 9040
DNS_PORT = 5353
SO_ORIGINAL_DST = 80
_BUF = 64 * 1024
_SOCKS_TIMEOUT = 120      # Tor may take a while to build a fresh circuit

# SOCKS5 reply codes, for the log line when Tor refuses a connection.
_SOCKS_ERRORS = {
    1: "general failure", 2: "not allowed", 3: "network unreachable",
    4: "host unreachable", 5: "connection refused", 6: "TTL expired",
    7: "command not supported", 8: "address type not supported",
}


class Proxy:
    def __init__(self, socks_path: str, token_file: str, pool: FakeIPPool | None = None,
                 trans_port: int = TRANS_PORT, dns_port: int = DNS_PORT,
                 log=None):
        self.socks_path = socks_path
        self.token_file = token_file
        self.pool = pool or FakeIPPool()
        self.trans_port = trans_port
        self.dns_port = dns_port
        self._log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self._servers: list = []

    # ── isolation token ───────────────────────────────────────────────────
    def token(self) -> bytes:
        try:
            with open(self.token_file, "rb") as f:
                tok = f.read(64).strip()
        except OSError:
            tok = b""
        return tok or b"entropy-shield"

    # ── DNS ───────────────────────────────────────────────────────────────
    class _Udp(asyncio.DatagramProtocol):
        def __init__(self, proxy: "Proxy"):
            self.proxy = proxy
            self.transport = None

        def connection_made(self, transport) -> None:
            self.transport = transport

        def datagram_received(self, data: bytes, addr) -> None:
            try:
                self.transport.sendto(answer(data, self.proxy.pool), addr)
            except DnsError:
                pass

    async def _dns_tcp(self, reader: asyncio.StreamReader,
                       writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await reader.readexactly(2)
                (n,) = struct.unpack("!H", head)
                query = await reader.readexactly(n)
                try:
                    reply = answer(query, self.pool)
                except DnsError:
                    break
                writer.write(struct.pack("!H", len(reply)) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    # ── TCP ───────────────────────────────────────────────────────────────
    @staticmethod
    def original_dst(sock: socket.socket) -> tuple[str, int]:
        raw = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
        port, ip = struct.unpack("!2xH4s8x", raw)
        return socket.inet_ntoa(ip), port

    def target_for(self, ip: str) -> tuple[str, bool]:
        """(host, is_name): the name behind a fake address, else the IP."""
        if self.pool.owns(ip):
            name = self.pool.name_for(ip)
            if name:
                return name, True
        return ip, False

    async def socks_connect(self, host: str, is_name: bool, port: int
                            ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_unix_connection(self.socks_path)
        try:
            tok = self.token()[:255]
            writer.write(b"\x05\x01\x02")                      # offer user/pass only
            await writer.drain()
            ver, method = await reader.readexactly(2)
            if ver != 5 or method != 2:
                raise ConnectionError("Tor refused SOCKS authentication")
            writer.write(b"\x01" + bytes([len(tok)]) + tok + b"\x01x")
            await writer.drain()
            _v, status = await reader.readexactly(2)
            if status != 0:
                raise ConnectionError("SOCKS authentication failed")
            if is_name:
                raw = host.encode("idna")[:255]
                addr = b"\x03" + bytes([len(raw)]) + raw
            else:
                addr = b"\x01" + socket.inet_aton(host)
            writer.write(b"\x05\x01\x00" + addr + struct.pack("!H", port))
            await writer.drain()
            ver, rep, _rsv, atyp = await reader.readexactly(4)
            if rep != 0:
                raise ConnectionError(_SOCKS_ERRORS.get(rep, f"error {rep}"))
            skip = {1: 4, 4: 16}.get(atyp)
            if skip is None:
                (skip,) = await reader.readexactly(1)
            await reader.readexactly(skip + 2)
            return reader, writer
        except BaseException:
            writer.close()
            raise

    @staticmethod
    async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await src.read(_BUF)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            try:
                if dst.can_write_eof():
                    dst.write_eof()
            except (OSError, RuntimeError):
                pass

    async def _trans(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        try:
            ip, port = self.original_dst(sock)
            host, is_name = self.target_for(ip)
            t_reader, t_writer = await asyncio.wait_for(
                self.socks_connect(host, is_name, port), _SOCKS_TIMEOUT)
        except (OSError, ConnectionError, asyncio.TimeoutError,
                asyncio.IncompleteReadError, UnicodeError) as e:
            self._log(f"[TORIFY] connection refused: {e}")
            writer.transport.abort()
            return
        try:
            await asyncio.gather(self._pipe(reader, t_writer),
                                 self._pipe(t_reader, writer))
        finally:
            t_writer.close()
            writer.close()

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def start(self, host: str = "127.0.0.1") -> None:
        loop = asyncio.get_running_loop()
        udp, _ = await loop.create_datagram_endpoint(
            lambda: Proxy._Udp(self), local_addr=(host, self.dns_port))
        self._servers.append(udp)
        self._servers.append(await asyncio.start_server(
            self._dns_tcp, host, self.dns_port, reuse_address=True))
        self._servers.append(await asyncio.start_server(
            self._trans, host, self.trans_port, reuse_address=True))

    def close(self) -> None:
        for s in self._servers:
            s.close()


async def _main(args) -> None:
    proxy = Proxy(args.socks, args.token)
    await proxy.start()
    if args.ready_fd is not None:
        os.write(args.ready_fd, b"ok\n")
        os.close(args.ready_fd)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    proxy.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--socks", required=True, help="Tor SocksPort Unix socket")
    ap.add_argument("--token", required=True, help="file holding the isolation token")
    ap.add_argument("--ready-fd", type=int, default=None)
    args = ap.parse_args()
    asyncio.run(_main(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
