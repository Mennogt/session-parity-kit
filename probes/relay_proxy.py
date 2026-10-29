"""Локальный прокси-релей с подстановкой авторизации.

Нужен, чтобы запускать обычный Chrome напрямую: Chrome не умеет передавать
логин/пароль прокси через флаги, а диалог авторизации ломает автоматизацию.
Релей принимает соединение без авторизации и ходит наверх с заголовком
Proxy-Authorization.

    python -m probes.relay_proxy --listen 7890 --port 10100

Затем: chrome.exe --proxy-server=127.0.0.1:7890
"""

from __future__ import annotations

import argparse
import asyncio
import base64

from probes.proxy_check import HOST, PASSWORD, USER

UPSTREAM = f"{HOST}:10000"


def _auth_header() -> str:
    raw = f"{USER}:{PASSWORD}".encode()
    return "Proxy-Authorization: Basic " + base64.b64encode(raw).decode()


class Relay:
    def __init__(self, upstream: str) -> None:
        self.upstream_host, self.upstream_port = self._split(upstream)

    @staticmethod
    def _split(value: str) -> tuple[str, int]:
        host, _, port = value.rpartition(":")
        return host, int(port)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            return

        text = head.decode("latin-1")
        first = text.split("\r\n", 1)[0]
        parts = first.split()
        if len(parts) < 2:
            writer.close()
            return
        method, target = parts[0], parts[1]

        if method != "CONNECT":
            # Обычный HTTP прокси-запрос: отвечаем отказом, Chrome для HTTPS
            # всегда использует CONNECT.
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        try:
            up_reader, up_writer = await asyncio.open_connection(
                self.upstream_host, self.upstream_port
            )
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        up_writer.write(
            f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{_auth_header()}\r\n\r\n".encode()
        )
        await up_writer.drain()

        reply = await up_reader.readuntil(b"\r\n\r\n")
        status = reply.split(b"\r\n", 1)[0]
        writer.write(reply)
        await writer.drain()
        if b" 200 " not in status:
            up_writer.close()
            writer.close()
            return

        async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    chunk = await src.read(65536)
                    if not chunk:
                        break
                    dst.write(chunk)
                    await dst.drain()
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        await asyncio.gather(pump(reader, up_writer), pump(up_reader, writer))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=7890)
    ap.add_argument("--port", type=int, default=10100, help="порт в пуле провайдера")
    ap.add_argument("--upstream", default=None, help="полный upstream user:pass@host:port")
    ap.add_argument("--upstream-line", default=None,
                    help="строка вида user:pass@host:port из файла списка")
    args = ap.parse_args()

    if args.upstream_line:
        from probes.pool_score import normalize

        url = normalize(args.upstream_line)
        if not url:
            print(f"не разобрал строку: {args.upstream_line!r}")
            return 2
        rest = url.split("://", 1)[1]
        creds, hostport = rest.rsplit("@", 1)
        user, _, password = creds.partition(":")
        relay = Relay.__new__(Relay)
        relay.upstream_host, relay.upstream_port = Relay._split(hostport)
        globals()["USER"], globals()["PASSWORD"] = user, password
        upstream = f"{hostport}"
    elif args.upstream:
        creds, hostport = args.upstream.rsplit("@", 1)
        user, _, password = creds.partition(":")
        globals()["USER"], globals()["PASSWORD"] = user, password
        relay = Relay(hostport)
        upstream = hostport
    else:
        upstream = args.upstream or f"{HOST}:{args.port}"
        relay = Relay(upstream)

    server = await asyncio.start_server(relay.handle, "127.0.0.1", args.listen)
    print(f"релей слушает 127.0.0.1:{args.listen} -> {upstream}", flush=True)
    async with server:
        await server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
