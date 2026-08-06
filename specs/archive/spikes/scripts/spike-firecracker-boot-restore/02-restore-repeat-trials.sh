#!/bin/bash
cd ~/fc-spike
export PATH=$HOME/bin:$PATH
for i in 1 2 3 4; do
  SOCK="/tmp/fc-restore-$i.sock"
  rm -f $SOCK
  firecracker --api-sock $SOCK > /dev/null 2>&1 &
  FC_PID=$!
  for j in $(seq 1 100); do [ -S $SOCK ] && break; sleep 0.01; done

  T0=$(date +%s.%N)
  curl -s -X PUT --unix-socket $SOCK \
    -d '{"snapshot_path": "/home/kodiak/fc-spike/snap.state", "mem_backend": {"backend_type": "File", "backend_path": "/home/kodiak/fc-spike/snap.mem"}, "resume_vm": true}' \
    http://localhost/snapshot/load -o /tmp/r_$i.json
  T1=$(date +%s.%N)
  echo "restore trial $i: $(echo "$T1 - $T0" | bc)s  ($(cat /tmp/r_$i.json))"

  kill $FC_PID 2>/dev/null
  wait $FC_PID 2>/dev/null
done
