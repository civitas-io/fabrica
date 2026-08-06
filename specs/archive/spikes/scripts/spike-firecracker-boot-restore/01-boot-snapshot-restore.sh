#!/bin/bash
set -e
cd ~/fc-spike
export PATH=$HOME/bin:$PATH
SOCK=/tmp/fc-spike.sock
CONSOLE_LOG=/home/kodiak/fc-spike/console.log
rm -f $SOCK $CONSOLE_LOG

KERNEL=/home/kodiak/fc-spike/vmlinux-6.1.177
ROOTFS=/home/kodiak/fc-spike/ubuntu-24.04.ext4
BOOT_ARGS="console=ttyS0 reboot=k panic=1"

echo "=== Phase 1: boot timing ==="
T_SPAWN=$(date +%s.%N)
firecracker --api-sock $SOCK > $CONSOLE_LOG 2>&1 &
FC_PID=$!

# wait for API socket to appear
for i in $(seq 1 100); do
  [ -S $SOCK ] && break
  sleep 0.01
done
T_SOCK_READY=$(date +%s.%N)

curl -s -X PUT --unix-socket $SOCK \
  -d "{\"kernel_image_path\": \"$KERNEL\", \"boot_args\": \"$BOOT_ARGS\"}" \
  http://localhost/boot-source -o /dev/null

curl -s -X PUT --unix-socket $SOCK \
  -d "{\"drive_id\": \"rootfs\", \"path_on_host\": \"$ROOTFS\", \"is_root_device\": true, \"is_read_only\": false}" \
  http://localhost/drives/rootfs -o /dev/null

curl -s -X PUT --unix-socket $SOCK \
  -d "{\"vcpu_count\": 2, \"mem_size_mib\": 512}" \
  http://localhost/machine-config -o /dev/null

T_CONFIGURED=$(date +%s.%N)

curl -s -X PUT --unix-socket $SOCK \
  -d '{"action_type": "InstanceStart"}' \
  http://localhost/actions -o /dev/null

T_START_ISSUED=$(date +%s.%N)

# Poll 1: VMM reports state=Running (the fast "microVM booted" signal)
T_VMM_RUNNING=""
for i in $(seq 1 500); do
  STATE=$(curl -s --unix-socket $SOCK http://localhost/ 2>/dev/null | grep -o '"state":"[^"]*"' || true)
  if echo "$STATE" | grep -q "Running"; then
    T_VMM_RUNNING=$(date +%s.%N)
    break
  fi
  sleep 0.005
done

# Poll 2: full userspace boot — look for a login prompt or systemd target in console log
T_USERSPACE=""
for i in $(seq 1 1000); do
  if grep -qE "login:|Reached target|Ubuntu 24.04" $CONSOLE_LOG 2>/dev/null; then
    T_USERSPACE=$(date +%s.%N)
    break
  fi
  sleep 0.02
done

echo "spawn->socket-ready:      $(echo "$T_SOCK_READY - $T_SPAWN" | bc)s"
echo "configure API calls:      $(echo "$T_CONFIGURED - $T_SOCK_READY" | bc)s"
echo "InstanceStart->VMM=Running: $(echo "$T_VMM_RUNNING - $T_START_ISSUED" | bc)s"
if [ -n "$T_USERSPACE" ]; then
  echo "InstanceStart->userspace(login/target): $(echo "$T_USERSPACE - $T_START_ISSUED" | bc)s"
else
  echo "userspace marker NOT found within timeout"
fi

echo
echo "=== Phase 2: snapshot / restore timing ==="
sleep 1  # let it settle a bit past minimal boot before snapshotting

T_PAUSE_START=$(date +%s.%N)
curl -s -X PATCH --unix-socket $SOCK -d '{"state": "Paused"}' http://localhost/vm -o /tmp/pause_resp.json
T_PAUSE_DONE=$(date +%s.%N)
cat /tmp/pause_resp.json
echo "pause took: $(echo "$T_PAUSE_DONE - $T_PAUSE_START" | bc)s"

rm -f /home/kodiak/fc-spike/snap.mem /home/kodiak/fc-spike/snap.state
T_SNAP_START=$(date +%s.%N)
curl -s -X PUT --unix-socket $SOCK \
  -d '{"snapshot_type": "Full", "snapshot_path": "/home/kodiak/fc-spike/snap.state", "mem_file_path": "/home/kodiak/fc-spike/snap.mem"}' \
  http://localhost/snapshot/create -o /tmp/snap_resp.json
T_SNAP_DONE=$(date +%s.%N)
cat /tmp/snap_resp.json
echo "snapshot create took: $(echo "$T_SNAP_DONE - $T_SNAP_START" | bc)s"
ls -la /home/kodiak/fc-spike/snap.mem /home/kodiak/fc-spike/snap.state

kill $FC_PID 2>/dev/null || true
wait $FC_PID 2>/dev/null || true

echo
echo "=== Phase 3: restore timing (fresh process) ==="
SOCK2=/tmp/fc-spike-restore.sock
rm -f $SOCK2
firecracker --api-sock $SOCK2 > /home/kodiak/fc-spike/console-restore.log 2>&1 &
FC_PID2=$!
for i in $(seq 1 100); do
  [ -S $SOCK2 ] && break
  sleep 0.01
done

T_RESTORE_START=$(date +%s.%N)
curl -s -X PUT --unix-socket $SOCK2 \
  -d '{"snapshot_path": "/home/kodiak/fc-spike/snap.state", "mem_backend": {"backend_type": "File", "backend_path": "/home/kodiak/fc-spike/snap.mem"}, "resume_vm": true}' \
  http://localhost/snapshot/load -o /tmp/restore_resp.json
T_RESTORE_DONE=$(date +%s.%N)
cat /tmp/restore_resp.json
echo
echo "RESTORE took: $(echo "$T_RESTORE_DONE - $T_RESTORE_START" | bc)s"

kill $FC_PID2 2>/dev/null || true
wait $FC_PID2 2>/dev/null || true
echo "done"
