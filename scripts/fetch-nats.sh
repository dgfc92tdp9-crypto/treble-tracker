#!/usr/bin/env bash
# Fetch nats-server (Apache-2.0) for the transport tests.
#
# Pinned, not "latest": a test suite whose broker version changes underneath
# it reports a different thing on every run, and the first thing that breaks
# would look like a defect in this repository.
set -euo pipefail

VERSION="v2.14.4"
DEST=".tools"
BIN="${DEST}/nats-server"

[ -x "${BIN}" ] && exit 0

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  PLATFORM="darwin-arm64" ;;
  Darwin-x86_64) PLATFORM="darwin-amd64" ;;
  Linux-x86_64)  PLATFORM="linux-amd64" ;;
  Linux-aarch64) PLATFORM="linux-arm64" ;;
  *) echo "no pinned nats-server build for $(uname -sm)" >&2; exit 1 ;;
esac

URL="https://github.com/nats-io/nats-server/releases/download/${VERSION}/nats-server-${VERSION}-${PLATFORM}.tar.gz"
mkdir -p "${DEST}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "fetching nats-server ${VERSION} (${PLATFORM})"
curl -fsSL -o "${TMP}/ns.tar.gz" "${URL}"
tar xzf "${TMP}/ns.tar.gz" -C "${TMP}"
find "${TMP}" -name nats-server -type f -exec cp {} "${BIN}" \;
chmod +x "${BIN}"
"${BIN}" --version
