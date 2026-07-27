# Tests

Layout mirrors `treble/`. See CLAUDE.md §7 for the testing contract.

- `golden/`      published reference values. An analytic is not done until it passes these.
- `fixtures/`    recorded source payloads. **No network access in CI** - every ingest test runs offline.
- `conformance/` renderer conformance cases (I6). Each case is a screen definition, a fixed
                 context, a frozen TAPI response, and two golden artefacts: an abstract layout
                 tree (JSON) and a text snapshot. Every renderer must reproduce both.
