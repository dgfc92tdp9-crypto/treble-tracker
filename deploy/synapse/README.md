# Local Synapse for `IM`

**Status: shipped, not verified.** Docker was not installed on the machine
this was written on, so `compose.yaml` has never been run. It is written
from Synapse's published deployment docs. Treat it as a starting point that
has been reviewed, not as a tested deployment.

What *is* verified is the client that talks to it. `treble/im/matrix.py` is
exercised end to end against `treble/im/simulator.py` — an in-memory
homeserver that speaks the same client-server API and can be made hostile on
demand (reject a token, replay a sync batch). That is how the login,
idempotent send, and sync-token handling are known to work without a
container.

## Why the client was built against a simulator anyway

The same argument as the FIX work. A client this author wrote, driven by a
server this author wrote, agrees with itself — so the simulator is
deliberately able to misbehave, and the client's refusals are exercised
rather than asserted. Running real Synapse would add confidence about
*Synapse*; it would not replace tests that can produce a replayed sync batch
on demand.

## What to check first when it is run

1. `docker compose run --rm synapse generate` writes `data/homeserver.yaml`.
   Confirm `server_name` matches what `PEOP` will show as the employer
   domain — the domain half of an MXID is the identity claim.
2. Registration is closed by default in generated configs. Open it only
   deliberately; an open homeserver is one anybody can take an account on,
   and `identity.py` treats the domain as evidence of employer.
3. The port binding above is `127.0.0.1:8008:8008`. Do not shorten it to
   `8008:8008`, which binds every interface.
