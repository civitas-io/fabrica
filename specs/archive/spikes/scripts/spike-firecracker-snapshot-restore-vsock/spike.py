"""Real spike: does a live vsock connection survive Firecracker
snapshot/restore into a FRESH process? Answers PLAN.md item 20's exact
open question empirically, not by inference from docs.

Protocol matches _firecracker_guest_shim.py exactly: length-prefixed
(4-byte big-endian + JSON) messages, ready/code/tool_call/result.

Run against BASE_ROOTFS pointing at the unmodified golden-rootfs.ext4
first (proves the crash); then again after baking patched_guest_shim.py
in via build_firecracker_rootfs.sh (proves the fix). See
SPIKE-firecracker-snapshot-restore-vsock.md for the full result.

Usage (on the homelab, from a fabrica checkout):
    uv run python -u spike.py
"""

import asyncio
import json
import shutil
import struct
import subprocess
import time
from pathlib import Path

FC_BINARY = "/home/kodiak/bin/firecracker"
KERNEL = "/home/kodiak/fc-spike/vmlinux-6.1.177"
BASE_ROOTFS = "/home/kodiak/fc-spike/golden-rootfs.ext4"  # or a patched-shim rootfs, see above
WORKDIR = Path("/tmp/spike-snap-vsock")
HOST_VSOCK_PORT = 5555


async def _api_put(sock_path: Path, endpoint: str, body: dict) -> str:
    proc = await asyncio.create_subprocess_exec(
        "curl", "-s", "--unix-socket", str(sock_path), "-X", "PUT",
        "-H", "Content-Type: application/json", "-d", json.dumps(body),
        f"http://localhost{endpoint}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return out.decode() + err.decode()


async def _api_patch(sock_path: Path, endpoint: str, body: dict) -> str:
    proc = await asyncio.create_subprocess_exec(
        "curl", "-s", "--unix-socket", str(sock_path), "-X", "PATCH",
        "-H", "Content-Type: application/json", "-d", json.dumps(body),
        f"http://localhost{endpoint}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return out.decode() + err.decode()


async def _recv(reader: asyncio.StreamReader) -> dict:
    length_bytes = await reader.readexactly(4)
    (length,) = struct.unpack(">I", length_bytes)
    body = await reader.readexactly(length)
    return json.loads(body.decode())


async def _send(writer: asyncio.StreamWriter, message: dict) -> None:
    body = json.dumps(message).encode()
    writer.write(struct.pack(">I", len(body)) + body)
    await writer.drain()


async def main() -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    api_sock = WORKDIR / "api1.sock"
    vsock_uds = WORKDIR / "vsock.sock"
    rootfs_copy = WORKDIR / "rootfs.ext4"
    console_log = WORKDIR / "console1.log"
    snap_state = WORKDIR / "snap.state"
    snap_mem = WORKDIR / "snap.mem"

    for f in (api_sock, vsock_uds, rootfs_copy, console_log, snap_state, snap_mem):
        f.unlink(missing_ok=True)
    (WORKDIR / f"{vsock_uds.name}_{HOST_VSOCK_PORT}").unlink(missing_ok=True)

    print("==> copying rootfs")
    shutil.copyfile(BASE_ROOTFS, rootfs_copy)

    vsock_conn: asyncio.Future = asyncio.get_event_loop().create_future()

    async def _accept(reader, writer):
        if not vsock_conn.done():
            vsock_conn.set_result((reader, writer))

    print("==> starting vsock host listener")
    server1 = await asyncio.start_unix_server(_accept, path=f"{vsock_uds}_{HOST_VSOCK_PORT}")

    print("==> booting firecracker process #1")
    proc1 = await asyncio.create_subprocess_exec(
        FC_BINARY, "--api-sock", str(api_sock),
        stdout=open(console_log, "wb"), stderr=asyncio.subprocess.STDOUT,
    )
    for _ in range(100):
        if api_sock.exists():
            break
        await asyncio.sleep(0.01)

    boot_args = "console=ttyS0 reboot=k panic=1 init=/usr/bin/python3 -- /tmp/guest_shim.py"
    print(await _api_put(api_sock, "/boot-source", {"kernel_image_path": KERNEL, "boot_args": boot_args}))
    print(await _api_put(api_sock, "/drives/rootfs", {
        "drive_id": "rootfs", "path_on_host": str(rootfs_copy),
        "is_root_device": True, "is_read_only": False,
    }))
    print(await _api_put(api_sock, "/machine-config", {"vcpu_count": 2, "mem_size_mib": 512}))
    print(await _api_put(api_sock, "/vsock", {"guest_cid": 3, "uds_path": str(vsock_uds)}))
    print(await _api_put(api_sock, "/actions", {"action_type": "InstanceStart"}))

    print("==> waiting for guest vsock connection + ready message")
    reader, writer = await asyncio.wait_for(vsock_conn, timeout=15.0)
    ready = await asyncio.wait_for(_recv(reader), timeout=15.0)
    print("guest sent:", ready)
    assert ready["type"] == "ready"

    print("==> pausing VM")
    t0 = time.monotonic()
    print(await _api_patch(api_sock, "/vm", {"state": "Paused"}))
    print(f"pause took {(time.monotonic()-t0)*1000:.1f}ms")

    print("==> creating snapshot (guest's vsock connection is LIVE and blocked-on-recv at this point)")
    t0 = time.monotonic()
    print(await _api_put(api_sock, "/snapshot/create", {
        "snapshot_path": str(snap_state),
        "mem_file_path": str(snap_mem),
    }))
    print(f"snapshot create took {(time.monotonic()-t0)*1000:.1f}ms")

    print("==> killing firecracker process #1 (simulates a real pool restoring later, fresh process)")
    print(f"proc1.pid={proc1.pid} proc1.returncode(before kill)={proc1.returncode}")
    try:
        proc1.kill()
        print("proc1.kill() returned")
    except ProcessLookupError:
        print("proc1.kill() raised ProcessLookupError -- process already gone")
    try:
        await asyncio.wait_for(proc1.wait(), timeout=5.0)
        print(f"proc1.wait() returned, returncode={proc1.returncode}")
    except TimeoutError:
        print("proc1.wait() TIMED OUT after 5s -- a real finding, not expected")

    print("closing server1 (not awaiting wait_closed() -- it hangs while the")
    print("OLD accepted connection's writer is still open, a real asyncio")
    print("gotcha found here, not a product bug -- just close() and move on)")
    with __import__("contextlib").suppress(Exception):
        writer.close()
    server1.close()

    # Real finding from the first run of this spike: Firecracker's OWN
    # vsock device binds a Unix socket at the BASE uds_path itself (not
    # just the per-port `{uds_path}_{port}` files) -- SIGKILL gives it no
    # chance to clean that up, so a stale socket FILE is left occupying
    # the path. Restoring a snapshot into a fresh process without
    # removing it first fails immediately: "VsockUnixBackend: Error
    # binding to the host-side Unix socket: Address in use (os error 98)".
    (WORKDIR / f"{vsock_uds.name}_{HOST_VSOCK_PORT}").unlink(missing_ok=True)
    print(f"removing stale base vsock_uds file left by SIGKILL: {vsock_uds}")
    vsock_uds.unlink(missing_ok=True)
    vsock_conn2: asyncio.Future = asyncio.get_event_loop().create_future()

    async def _accept2(reader, writer):
        if not vsock_conn2.done():
            vsock_conn2.set_result((reader, writer))

    print("==> starting a FRESH vsock host listener for the restored process")
    server2 = await asyncio.start_unix_server(_accept2, path=f"{vsock_uds}_{HOST_VSOCK_PORT}")
    print("server2 listening")

    api_sock2 = WORKDIR / "api2.sock"
    console_log2 = WORKDIR / "console2.log"
    api_sock2.unlink(missing_ok=True)

    print("==> starting firecracker process #2 (fresh process, will load the snapshot)")
    proc2 = await asyncio.create_subprocess_exec(
        FC_BINARY, "--api-sock", str(api_sock2),
        stdout=open(console_log2, "wb"), stderr=asyncio.subprocess.STDOUT,
    )
    print(f"proc2 spawned, pid={proc2.pid}")
    for i in range(100):
        if api_sock2.exists():
            break
        await asyncio.sleep(0.01)
    print(f"api_sock2 exists after {i} iterations: {api_sock2.exists()}")

    print("==> loading snapshot into process #2")
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(_api_put(api_sock2, "/snapshot/load", {
            "snapshot_path": str(snap_state),
            "mem_backend": {"backend_type": "File", "backend_path": str(snap_mem)},
            "resume_vm": True,
        }), timeout=15.0)
        print(f"snapshot load took {(time.monotonic()-t0)*1000:.1f}ms")
        print("load result:", result)
    except TimeoutError:
        print("snapshot/load curl call TIMED OUT after 15s -- a real finding")
        result = None

    print("==> checking: does the OLD reader/writer (from process #1) still work?")
    try:
        await asyncio.wait_for(_send(writer, {"type": "code", "code": "print(1)"}), timeout=3.0)
        print("OLD connection: send succeeded (unexpected)")
        try:
            resp = await asyncio.wait_for(_recv(reader), timeout=3.0)
            print("OLD connection: got response:", resp)
        except Exception as exc:
            print(f"OLD connection: send OK but recv failed: {type(exc).__name__}: {exc}")
    except Exception as exc:
        print(f"OLD connection: send FAILED (expected if truly dead): {type(exc).__name__}: {exc}")

    print("==> checking: does a NEW connection arrive on the fresh listener within 10s?")
    try:
        reader2, writer2 = await asyncio.wait_for(vsock_conn2, timeout=10.0)
        print("NEW connection arrived! Guest reconnected after restore.")
        try:
            msg = await asyncio.wait_for(_recv(reader2), timeout=5.0)
            print("guest sent on new connection:", msg)
        except Exception as exc:
            print(f"NEW connection: recv failed: {type(exc).__name__}: {exc}")
    except TimeoutError:
        print("NO new connection arrived within 10s -- guest did NOT reconnect autonomously.")

    print("==> cleanup")
    with __import__("contextlib").suppress(ProcessLookupError):
        proc2.kill()
        await asyncio.wait_for(proc2.wait(), timeout=5.0)
    server2.close()

    print("==> console log #1 tail (last 20 lines):")
    subprocess.run(["tail", "-20", str(console_log)])
    print("==> console log #2 tail (last 20 lines):")
    subprocess.run(["tail", "-20", str(console_log2)])


asyncio.run(main())
