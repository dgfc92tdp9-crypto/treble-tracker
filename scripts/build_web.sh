#!/usr/bin/env bash
# Compile the shared TypeScript renderer.
#
# The conformance suite drives this build's output, so it must happen
# before pytest — otherwise "web" would fail on a clean checkout and the
# obvious fix would be to make it skip, which is how a renderer ends up
# untested. Building it here means the suite is never tempted.
set -euo pipefail

cd "$(dirname "$0")/../apps/desktop"

if ! command -v npm > /dev/null 2>&1; then
  echo "npm is required to build the web renderer (it is a renderer under conformance)" >&2
  exit 1
fi

if [ ! -d node_modules ]; then
  npm install --no-audit --no-fund
fi

npx tsc -p tsconfig.renderer.json
node --test ../../treble/render/web/tests/*.test.js
