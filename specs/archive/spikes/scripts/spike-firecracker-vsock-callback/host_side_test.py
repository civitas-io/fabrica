"""Real end-to-end validation: boots a Firecracker guest running the ACTUAL
_firecracker_guest_shim.py (baked into a real rootfs via a proper mount, not
debugfs -w), speaks the real length-prefixed JSON protocol over vsock, and
proves a full code+tool-call+result round trip -- not just a raw byte
exchange like the first vsock spike.

Run ON THE HOST (kodiak@darkenergy), with a real firecracker process
already configured for vsock (see boot_and_test.sh in this same directory).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import struct


async def _send(writer: asyncio.StreamWriter, message: dict) -> None:
    body = json.dumps(message).encode()
    writer.write(struct.pack(">I", len(body)) + body)
    await writer.drain()


async def _recv(reader: asyncio.StreamReader) -> dict:
    length_bytes = await reader.readexactly(4)
    (length,) = struct.unpack(">I", length_bytes)
    body = await reader.readexactly(length)
    return json.loads(body)


async def _on_tool_call(tool: str, params: dict) -> dict:
    print(f"HOST: real on_tool_call invoked: {tool}({params})")
    if tool == "add":
        return {"success": True, "value": params["a"] + params["b"], "error_message": None}
    return {"success": False, "value": None, "error_message": f"unknown tool: {tool}"}


async def main() -> None:
    uds_path = "/tmp/fc-vsock-full.sock_5555"
    with contextlib.suppress(FileNotFoundError):
        os.remove(uds_path)

    code_to_run = (
        "result = namespace.call('add', {'a': 2, 'b': 3})\n"
        "print(f'2 + 3 = {result[\"value\"]}')\n"
    )

    server = await asyncio.start_unix_server(
        lambda r, w: _serve_one_connection(r, w, code_to_run), path=uds_path
    )
    print("HOST: listening on", uds_path, flush=True)
    async with server:
        await asyncio.wait_for(server.serve_forever(), timeout=20)


async def _serve_one_connection(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, code_to_run: str
) -> None:
    message = await _recv(reader)
    assert message["type"] == "ready"
    print("HOST: guest is ready, sending code", flush=True)
    await _send(writer, {"type": "code", "code": code_to_run})

    while True:
        message = await _recv(reader)
        if message["type"] == "tool_call":
            result = await _on_tool_call(message["tool"], message["params"])
            await _send(writer, {"type": "tool_result", **result})
        elif message["type"] == "result":
            print("HOST: FINAL RESULT:", json.dumps(message, indent=2), flush=True)
            writer.close()
            raise SystemExit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        pass
