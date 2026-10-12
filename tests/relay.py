#!/usr/bin/env python3
# Test stand-in for an upstream proxy such as Burp. It accepts CONNECT, tunnels the bytes,
# and appends one line per tunnel to a log, so a test can prove traffic went through it.
# Usage: python3 -I tests/relay.py <port> <logfile>
import asyncio
import sys

PORT = int(sys.argv[1])
LOG = sys.argv[2]


def log(line):
    with open(LOG, "a") as f:
        f.write(line + "\n")


async def pipe(src, dst):
    try:
        while data := await src.read(65536):
            dst.write(data)
            await dst.drain()
    except OSError:
        pass
    finally:
        dst.close()


async def reply(writer, status):
    writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\n\r\n".encode())
    await writer.drain()
    writer.close()


async def handle(reader, writer):
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        writer.close()
        return
    method, target, _ = head.split(b"\r\n", 1)[0].decode(errors="replace").split(" ", 2)
    if method != "CONNECT":
        await reply(writer, "405 Method Not Allowed")
        return
    host, _, port = target.rpartition(":")
    try:
        up_reader, up_writer = await asyncio.open_connection(host, int(port))
    except (OSError, ValueError):
        await reply(writer, "502 Bad Gateway")
        return
    log(f"CONNECT {target}")
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", PORT)
    async with server:
        await server.serve_forever()


asyncio.run(main())
