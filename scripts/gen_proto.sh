#!/usr/bin/env bash
# Regenerate the gRPC stubs from proto/tapi.proto (spec §8.3).
#
# Output is gitignored and regenerated on every `make check`, so a clean
# checkout always builds from the proto rather than from a stub somebody
# committed once. tests/tapi/test_grpc.py asserts the generated services
# match the services declared in the proto — that is what proves this ran.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=treble/tapi/_generated
rm -rf "$OUT" && mkdir -p "$OUT"
uv run python -m grpc_tools.protoc -Iproto \
  --python_out="$OUT" --grpc_python_out="$OUT" proto/tapi.proto
# protoc emits `import tapi_pb2`, which is absolute and breaks inside a
# package. Rewrite it to a relative import rather than putting the output
# directory on sys.path, which would make the stubs importable under two
# different module names.
sed -i '' 's/^import tapi_pb2 as/from . import tapi_pb2 as/' "$OUT/tapi_pb2_grpc.py" 2>/dev/null \
  || sed -i 's/^import tapi_pb2 as/from . import tapi_pb2 as/' "$OUT/tapi_pb2_grpc.py"
touch "$OUT/__init__.py"
echo "generated $OUT from proto/tapi.proto"
