# A minimal FirecrackerSandbox base rootfs source -- deliberately NOT the
# official `python:*-slim` Docker image. That image installs Python into
# `/usr/local/bin/python3.12` (built from source at image-build time),
# not `/usr/bin/python3` -- and FirecrackerSandbox's kernel boot args
# (firecracker_backend.py) are `init=/usr/bin/python3`, a fixed path, not
# configurable per rootfs. Booting a guest built from `python:*-slim` was
# tried first and confirmed to fail for exactly this reason: a real
# kernel panic, "Requested init /usr/bin/python3 failed (error -2)"
# (ENOENT), verified on real hardware before this Dockerfile was written,
# not assumed. Ubuntu's own `apt install python3` installs the standard
# way instead -- `/usr/bin/python3` -> `/usr/bin/python3.12`, matching
# what the fixed boot args require, no code change and no custom symlink
# step needed.
#
# Matches the existing golden-rootfs.ext4's own base distro (Ubuntu
# 24.04) deliberately -- switching to a different distro/libc (e.g.
# Alpine's musl) here too would be a second, unvalidated variable on top
# of "does a minimal image work at all," not a simplification.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*
