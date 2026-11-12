# HubSpot behaviour checks

Run with `uv run python -m monitor hubspot probe` on 2026-11-12 against the developer test account, API version 2026-09. Each probe created records tagged `northwind_id = PROBE-xx`, read them back and archived them.

| Probe | Question | Affects | HTTP | Result | As planned |
|---|---|---|---|---|---|
| P1 | Is a closed-won deal with a close date 30 days ago accepted and kept? | every closed deal; D08 | 201 | accepted; close date sent 2026-10-13, read back 2026-10-13T00:00:00Z | yes |
| P2 | Does a contact created without a lifecycle stage stay blank after a deal is associated? | D16; lifecycle stage settings | 201 | still blank | yes |
| P3 | Does a new contact create a company for its email domain? | D04; company auto-creation setting | 201 | no company created | yes |
| P4 | Is the contact property hs_analytics_source (Original source) read-only? | northwind_source custom property | 200 | readOnlyValue = False; the seed uses northwind_source either way | yes |
| P5 | Do all 12 generator industries match an option of the company industry property? | industry map in monitor/hubspot/seed.py | 200 | 148 options; all 12 industries match | yes |
