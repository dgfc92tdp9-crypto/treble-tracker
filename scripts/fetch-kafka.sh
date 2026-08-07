#!/usr/bin/env bash
# Fetch Apache Kafka (Apache-2.0) for the Kafka/Redpanda transport tests.
#
# Kafka rather than Redpanda because Redpanda publishes no broker binary for
# either platform -- its releases carry only `rpk`, the CLI -- so running it
# needs Docker, which this machine does not have and CI should not require.
# The two speak the same wire protocol, so the adapter under test is the same
# adapter either way; what is *not* claimed is that Redpanda itself has been
# run here. It has not.
#
# Pinned to a 3.x line on purpose: Kafka 4.x requires Java 17+, and this
# repository's dev machine has 16. A version chosen by "latest" would make
# the suite depend on whichever JDK a contributor happens to have.
set -euo pipefail

VERSION="3.9.1"
SCALA="2.13"
DEST=".tools"
HOME_DIR="${DEST}/kafka"

[ -x "${HOME_DIR}/bin/kafka-server-start.sh" ] && exit 0

if ! command -v java >/dev/null 2>&1; then
  echo "java is required to run the Kafka broker the transport tests use." >&2
  echo "Install a JDK (11 or later; 4.x-only features are not used)." >&2
  exit 1
fi

URL="https://archive.apache.org/dist/kafka/${VERSION}/kafka_${SCALA}-${VERSION}.tgz"
mkdir -p "${DEST}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "fetching kafka ${VERSION} (~116MB)"
curl -fsSL -o "${TMP}/kafka.tgz" "${URL}"
tar xzf "${TMP}/kafka.tgz" -C "${TMP}"
mv "${TMP}/kafka_${SCALA}-${VERSION}" "${HOME_DIR}"
echo "kafka ${VERSION} at ${HOME_DIR}"
