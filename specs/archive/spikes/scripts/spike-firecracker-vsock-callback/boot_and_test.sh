#!/bin/bash
# Real end-to-end validation, using the properly-mounted rootfs access
# (sudo mount/umount/losetup, scoped to /mnt/fcrootfs -- see HANDOFF.md)
# instead of debugfs -w's proven-unreliable approach. Bakes the REAL
# _firecracker_guest_shim.py into a fresh rootfs copy, boots with
# init=/usr/bin/python3 pointed directly at it (no systemd at all -- a
# purpose-built sandbox image doesn't need general-purpose OS services),
# and runs host_side_test.py to prove the full protocol round trip.
set -e
export PATH=$HOME/bin:$PATH
cd ~/fc-spike

ROOTFS=/tmp/fc-full-test-rootfs.ext4
cp ubuntu-24.04.ext4 "$ROOTFS"

echo "=== mounting rootfs via the new scoped sudo access ==="
sudo -n mount -o loop "$ROOTFS" /mnt/fcrootfs
# /tmp inside this rootfs image is plain root:root 755 -- NOT world-writable
# (systemd-tmpfiles normally fixes that at boot; a static, never-booted
# image doesn't have it yet) -- a real finding, not assumed. A second,
# narrowly-scoped sudoers rule (cp, restricted to *.py from /tmp into this
# mount) was added for exactly this.
sudo -n cp /tmp/_firecracker_guest_shim.py /mnt/fcrootfs/tmp/guest_shim.py
sync
sudo -n umount /mnt/fcrootfs
echo "mount/copy/umount succeeded"

SOCK=/tmp/fc-vsock-full-api.sock
# MUST match host_side_test.py's uds_path base exactly (that script listens
# on ${VSOCK_UDS}_5555) -- a real mismatch bug was caught here on the first
# real run: SOCK and VSOCK_UDS previously differed only in an easy-to-miss
# way, causing sock.connect() inside the guest to fail with nothing
# listening at the path Firecracker actually proxies to. Also must NOT
# collide with SOCK (firecracker's own API socket) -- fixed both at once.
VSOCK_UDS=/tmp/fc-vsock-full.sock
CONSOLE=/tmp/fc-vsock-full-console.log
rm -f "$SOCK" "$VSOCK_UDS" "${VSOCK_UDS}_5555" "$CONSOLE"

python3 /tmp/host_side_test.py > /tmp/fc-vsock-full-host.log 2>&1 &
HOST_PID=$!
sleep 0.3

firecracker --api-sock "$SOCK" > "$CONSOLE" 2>&1 &
FC_PID=$!
sleep 0.3

curl -s -X PUT --unix-socket "$SOCK" \
  -d "{\"kernel_image_path\": \"$PWD/vmlinux-6.1.177\", \"boot_args\": \"console=ttyS0 reboot=k panic=1 init=/usr/bin/python3 -- /tmp/guest_shim.py\"}" \
  http://localhost/boot-source -o /dev/null

curl -s -X PUT --unix-socket "$SOCK" \
  -d "{\"drive_id\": \"rootfs\", \"path_on_host\": \"$ROOTFS\", \"is_root_device\": true, \"is_read_only\": false}" \
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

sleep 6
echo "=== HOST-SIDE PROTOCOL SERVER OUTPUT ==="
cat /tmp/fc-vsock-full-host.log
echo "=== GUEST CONSOLE (tail, for any errors) ==="
tail -15 "$CONSOLE"

kill $FC_PID $HOST_PID 2>/dev/null
wait 2>/dev/null || true
