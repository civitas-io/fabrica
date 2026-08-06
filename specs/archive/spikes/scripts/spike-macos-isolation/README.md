Run locally on macOS (this dev machine). Held per instruction — commands run
inline in the session, reconstructed here for reproducibility.

## srt (Tier 1) setup + tests
    npm install -g @anthropic-ai/sandbox-runtime
    srt echo hello world                                    # sanity
    srt bash -c 'echo test > /tmp/x.txt && cat /tmp/x.txt'   # write denial test
    srt -c 'curl -sS --max-time 5 https://example.com'       # network denial test
    srt cat ~/.gitconfig                                     # read-allowed-by-default check
    # timing: see timing.py

## libkrun/krunvm (Tier 2) setup + packaging fixes
    brew tap libkrun/krun
    brew trust libkrun/krun          # newer Homebrew requires explicit trust for non-official taps
    brew install krunvm

    # Fix 1 + 2: buildah's hardcoded /etc/containers/* paths don't match
    # Homebrew's /opt/homebrew/etc/containers/* install location.
    # User-level XDG override avoids needing sudo:
    mkdir -p ~/.config/containers
    cp /opt/homebrew/etc/containers/policy.json ~/.config/containers/policy.json
    cp /opt/homebrew/etc/containers/registries.conf ~/.config/containers/registries.conf

    # Fix 3: buildah defaults to matching the HOST os (darwin) instead of
    # linux when pulling a container image -- always wrong for this use case.
    buildah --platform linux/arm64 from docker.io/library/alpine:latest

    # NOT resolved within the timebox: krunvm create/start themselves don't
    # expose an equivalent --platform override, so whether krunvm's own
    # wrapper hits the same OS-mismatch problem is unconfirmed.
