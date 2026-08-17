"""`EMS` — order entry over FIX (spec §23.3, Phase 3).

The session layer and an in-repo simulator to run it against. There is no
venue here and no intention of reaching one: the gate criterion says
"against a simulator", and a simulator this repository owns is one CI can
run offline, which §7 requires.
"""
