#!/bin/bash
# Sets up an isolated, throwaway Tessera store for the credential-boundary
# validation spike. Not for real use -- a real deployment would never put
# a passphrase in a shell script.
set -euo pipefail

TSR="${TSR_BINARY:-$HOME/workspace/projects/tessera/target/release/tsr}"
export TSR_STORE_DIR="${TSR_STORE_DIR:-/tmp/tsr-fabrica-demo}"
export TSR_PASSPHRASE="${TSR_PASSPHRASE:-demo-passphrase-for-integration-test}"

printf '%s' "demo-secret-value-123" | "$TSR" add demo-api --format json
"$TSR" policy demo-api \
  --http-upstream https://httpbin.org \
  --http-header Authorization \
  --http-scheme "Bearer {}" \
  --format json
"$TSR" ls --format json
