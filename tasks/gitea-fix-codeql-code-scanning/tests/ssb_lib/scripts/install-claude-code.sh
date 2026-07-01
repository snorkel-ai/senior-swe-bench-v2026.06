#!/bin/bash
# Minimal Claude Code binary installer for Senior SWE-Bench containers.
# Downloads a pre-built binary — no Node.js dependency.
# Usage: bash install_claude_code.sh [VERSION]
#   VERSION: optional pin (e.g., "1.0.5"). Default: pinned known-good version.
set -euo pipefail

# Pin to known-good version. 2.1.98+ sends an invalid beta flag that Bedrock
# rejects via Portkey routing. Update this when the issue is fixed upstream.
PINNED_VERSION="2.1.97"
TARGET="${1:-$PINNED_VERSION}"
GCS_BUCKET="https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases"
INSTALL_DIR="/usr/local/bin"

# Retry wrapper for curl — handles 429 rate limits from CDN
_curl_retry() {
    local attempt=0 max=5
    while [ $attempt -lt $max ]; do
        if curl -fsSL "$@"; then return 0; fi
        attempt=$((attempt + 1))
        echo "curl failed (attempt $attempt/$max), retrying in $((attempt * 5))s..." >&2
        sleep $((attempt * 5))
    done
    echo "curl failed after $max attempts" >&2
    return 1
}

# Detect platform
case "$(uname -m)" in
    x86_64|amd64) arch="x64" ;;
    arm64|aarch64) arch="arm64" ;;
    *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

if [ -f /lib/libc.musl-x86_64.so.1 ] || [ -f /lib/libc.musl-aarch64.so.1 ] || ldd /bin/ls 2>&1 | grep -q musl; then
    platform="linux-${arch}-musl"
else
    platform="linux-${arch}"
fi

# Resolve version
if [ -n "$TARGET" ]; then
    version="$TARGET"
else
    version=$(_curl_retry "$GCS_BUCKET/latest")
fi

# Download manifest and extract checksum
manifest=$(_curl_retry "$GCS_BUCKET/$version/manifest.json")
checksum=$(echo "$manifest" | python3 -c "
import json, sys
m = json.load(sys.stdin)
print(m['platforms']['$platform']['checksum'])
" 2>/dev/null) || {
    # Fallback: parse without python3
    checksum=$(echo "$manifest" | tr -d '\n\r\t ' | grep -oP "\"$platform\"[^}]*\"checksum\"\\s*:\\s*\"([a-f0-9]{64})\"" | grep -oP '[a-f0-9]{64}')
}

if [ -z "$checksum" ] || [[ ! "$checksum" =~ ^[a-f0-9]{64}$ ]]; then
    echo "Could not extract checksum for platform $platform" >&2
    exit 1
fi

# Download binary
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
_curl_retry -o "$tmp" "$GCS_BUCKET/$version/$platform/claude"

# Verify checksum
actual=$(sha256sum "$tmp" | cut -d' ' -f1)
if [ "$actual" != "$checksum" ]; then
    echo "Checksum mismatch: expected $checksum, got $actual" >&2
    exit 1
fi

# Install
mv "$tmp" "$INSTALL_DIR/claude"
chmod 755 "$INSTALL_DIR/claude"
trap - EXIT
echo "Claude Code $version installed to $INSTALL_DIR/claude"
