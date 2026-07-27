#!/usr/bin/env bash
# Build the desktop client and install it into ~/Applications.
#
# `set -euo pipefail` is not decoration here: a failed build that still
# copied a stale bundle would look exactly like a successful install.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE="$REPO/apps/desktop/src-tauri/target/release/bundle/macos/Treble Tracker.app"
DESTINATION="$HOME/Applications"

export PATH="$HOME/.cargo/bin:$PATH"

cd "$REPO/apps/desktop"
npx tauri build

[ -d "$BUNDLE" ] || { echo "build reported success but produced no bundle" >&2; exit 1; }

mkdir -p "$DESTINATION"
rm -rf "$DESTINATION/Treble Tracker.app"
cp -R "$BUNDLE" "$DESTINATION/"

echo
echo "Installed: $DESTINATION/Treble Tracker.app"
echo "Open it from Launchpad or Spotlight, then keep it in the Dock."
