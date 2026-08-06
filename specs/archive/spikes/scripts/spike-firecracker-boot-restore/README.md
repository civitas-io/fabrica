Run remotely on kodiak@darkenergy (Ubuntu 24.04, bare-metal AMD-V, KVM group
membership required — the only privileged setup step, done via
`sudo usermod -aG kvm kodiak`).

Setup performed (not scripted here, done inline over SSH):
- Firecracker v1.16.1 + jailer binaries downloaded to ~/bin (no sudo — static binary)
- Kernel (vmlinux-6.1.177) + Ubuntu 24.04 squashfs fetched from the official
  Firecracker CI S3 bucket (firecracker-ci/20260805-f2f43b669a02-0/x86_64/)
- squashfs converted to a 1G ext4 rootfs via `fakeroot unsquashfs` + `fakeroot mkfs.ext4`
  (avoids the `sudo chown`/`sudo mkfs.ext4` steps in Firecracker's own getting-started guide)

Scripts:
- 01-boot-snapshot-restore.sh — full run: boot timing, pause, snapshot create, restore
- 02-restore-repeat-trials.sh — 4 additional restore-only trials for a latency range
