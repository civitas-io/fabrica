#!/bin/bash
# SPIKE: real Firecracker guest<->host vsock round trip, validated on real
# bare-metal hardware (kodiak@darkenergy, same homelab as
# SPIKE-firecracker-boot-restore-latency.md). Throwaway, timeboxed script --
# not production code. Boots the guest straight into /bin/sh over the
# console (no custom init/guest-shim baked into the rootfs -- that's a
# separate, real follow-on problem, see the findings doc), and drives it
# interactively via a FIFO wired to firecracker's stdin.
#
# Proves: a real AF_VSOCK connection from inside a Firecracker guest,
# through the host-side UDS proxy Firecracker creates, to a real Python
# socket listening on the host, with real bidirectional data exchange.
set -e
export PATH=$HOME/bin:$PATH
cd ~/fc-spike
cp ubuntu-24.04.ext4 /tmp/vsock-clean-rootfs.ext4

cat > /tmp/vsock_host_listener.py << 'PYEOF'
import socket, os
path = "/tmp/vsock-rt-uds.sock_5555"
try: os.remove(path)
except FileNotFoundError: pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(path)
s.listen(1)
print("HOST: listening", flush=True)
s.settimeout(20)
conn, _ = s.accept()
data = conn.recv(4096)
print("HOST GOT:", data, flush=True)
conn.sendall(b"hello from host\n")
conn.close()
PYEOF

SOCK=/tmp/vsock-rt.sock
VSOCK_UDS=/tmp/vsock-rt-uds.sock
CONSOLE=/tmp/vsock-rt-console.log
FIFO=/tmp/vsock-rt-stdin.fifo
rm -f "$SOCK" "$VSOCK_UDS" "${VSOCK_UDS}_5555" "$CONSOLE" "$FIFO"
mkfifo "$FIFO"

python3 /tmp/vsock_host_listener.py > /tmp/vsock-host-out.log 2>&1 &
LISTENER_PID=$!
sleep 0.3

# vsock's GUEST-initiated direction requires the host to be listening on
# {uds_path}_{port} BEFORE the guest connects -- Firecracker's vhost-vsock
# device proxies a guest connect(cid=2, port=N) to exactly this path.
exec 3<>"$FIFO"
firecracker --api-sock "$SOCK" < "$FIFO" > "$CONSOLE" 2>&1 &
FC_PID=$!
sleep 0.3

curl -s -X PUT --unix-socket "$SOCK" \
  -d "{\"kernel_image_path\": \"$PWD/vmlinux-6.1.177\", \"boot_args\": \"console=ttyS0 reboot=k panic=1 init=/bin/sh\"}" \
  http://localhost/boot-source -o /dev/null

curl -s -X PUT --unix-socket "$SOCK" \
  -d "{\"drive_id\": \"rootfs\", \"path_on_host\": \"/tmp/vsock-clean-rootfs.ext4\", \"is_root_device\": true, \"is_read_only\": false}" \
  http://localhost/drives/rootfs -o /dev/null

curl -s -X PUT --unix-socket "$SOCK" \
  -d '{"vcpu_count": 2, "mem_size_mib": 512}' \
  http://localhost/machine-config -o /dev/null

curl -s -X PUT --unix-socket "$SOCK" \
  -d "{\"guest_cid\": 3, \"uds_path\": \"$VSOCK_UDS\"}" \
  http://localhost/vsock -o /dev/null

curl -s -X PUT --unix-socket "$SOCK" \
  -d '{"action_type": "InstanceStart"}' \
  http://localhost/actions -o /dev/null

sleep 2
{
  echo 'python3 -c "import socket; s=socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM); s.connect((2,5555)); s.sendall(b\"hello from guest\n\"); print(\"GUEST_GOT:\", s.recv(1024))"'
  sleep 4
  echo 'echo VSOCK_ROUNDTRIP_MARKER_DONE'
} >&3

sleep 6
echo "=== HOST LISTENER OUTPUT ==="
cat /tmp/vsock-host-out.log
echo "=== GUEST CONSOLE OUTPUT (tail) ==="
tail -25 "$CONSOLE"

exec 3>&-
kill $FC_PID $LISTENER_PID 2>/dev/null
wait 2>/dev/null || true
