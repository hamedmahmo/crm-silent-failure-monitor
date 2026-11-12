# Planted data-quality defects

`data/generate.py --defects` plants the defects below into the Northwind Analytics dataset. The CRM Silent-Failure Monitor is tested against them: it should flag every planted record and nothing in the clean data.

Each defect touches a fixed number of records, whatever the seed or `--scale`. The generator writes the exact record IDs to `defects_manifest.json`, so tests can compare what a monitor finds with what was planted.

The `hubspot_plantable` column says whether HubSpot accepts the defect when a record is created through the API or an import. HubSpot rejects malformed emails, duplicate emails, invalid pipeline stages, unknown company IDs and backdated create dates, so defects that rely on those can only be tested against the CSV files. Defects marked "yes" are the ones that get into a real portal and then break reports without any error.

Reference values used below: `AS_OF` = 2026-06-30, source cutoff = 2026-01-01, stale-stage limit = 60 days, top list price = 150,000 USD, usage-event window = 2026-05-02 to 2026-06-30.

## Summary

| ID | What's wrong | Record types | Count | hubspot_plantable | Notes |
|---|---|---|---|---|---|
| D01 | Duplicate contact: same email differing only by letter case | contacts | 8 contacts | no | HubSpot rejects duplicate emails |
| D02 | Orphan contact: `company_id` not in companies | contacts | 5 contacts | no | HubSpot rejects unknown company IDs |
| D03 | Malformed email (missing `@`, `@@`, trailing space) | contacts | 6 contacts | no | HubSpot rejects malformed emails |
| D04 | Required company fields blank (`industry`, `country`) | companies | 6 companies | yes | |
| D05 | Churned company still emits usage events after `churned_at` | companies, usage_events | 3 companies (4 events each) | no | Usage events are not HubSpot records |
| D06 | `closedwon` deal with blank or zero `amount` | deals | 5 deals | yes | |
| D07 | Deal `amount` 100x too large (cents written to a dollars field) | deals | 4 deals | yes | |
| D08 | Closed deal with `close_date` before `created_at` | deals | 5 deals | yes | Close date set before the real create time; HubSpot accepts it (probe P1, 2026-11-12) |
| D09 | Deal `stage` outside the picklist (`Closed Won`, `won`, `closedwon ` with trailing space) | deals | 6 deals | no | HubSpot rejects invalid stages |
| D10 | Deal owner is inactive or missing from `owners` | deals | 5 deals | no | HubSpot validates owner IDs; to confirm in a HubSpot sandbox |
| D11 | Duplicate usage event (same `event_id` replayed) | usage_events | 10 events | no | Usage events are not HubSpot records |
| D12 | Usage event timestamp after `AS_OF` | usage_events | 8 events | no | Usage events are not HubSpot records |
| D13 | Ingestion gap: all events dropped for 3 consecutive days | usage_events | 2 companies | no | Usage events are not HubSpot records |
| D14 | Ticket `closed_at` before `opened_at` | support_tickets | 5 tickets | yes | Closed date set before the real create time; to confirm in a HubSpot sandbox |
| D15 | Ticket contact belongs to a different company than the ticket | support_tickets, contacts | 6 tickets | yes | |
| D16 | Contact missing `lifecycle_stage` | contacts | 10 contacts | yes | |
| D17 | Contact or deal with no owner | contacts, deals | 8 records (4 contacts, 4 deals) | yes | |
| D18 | Two companies share the same domain | companies | 3 pairs | yes | |
| D19 | Deal with no associated company (`company_id` blank) | deals | 5 deals | yes | |
| D20 | Open deal with `close_date` before `AS_OF` | deals | 8 deals | yes | |
| D21 | Open deal in the same stage for more than 60 days | deals | 6 deals | yes | Needs a custom `stage_entered_at` property; see D21 below |
| D22 | Contact created on or after 2026-01-01 with blank `source` | contacts | 10 contacts | yes | |
| D23 | Open deal at `contractsent` with no `amount` | deals | 5 deals | yes | |

"Open" means `stage` is one of `appointmentscheduled`, `qualifiedtobuy`, `presentationscheduled`, `decisionmakerboughtin`, `contractsent`. "Closed" means `closedwon` or `closedlost`. A deal with an invalid stage (D09) is neither.

## Detection rules and manifest entries

Every manifest entry has `id`, `title`, `record_types`, `count`, `unit`, `hubspot_plantable`, `hubspot_note` and `record_ids`. `record_ids` always holds exactly `count` IDs. The rules below are the reference checks in `tests/test_generate.py`.

- **D01** Group contacts by `lower(trim(email))`. In each group with more than one contact, every contact except the lowest `contact_id` is a duplicate. `record_ids`: the duplicate contacts.
- **D02** `contacts.company_id` has no match in `companies`. `record_ids`: contacts. The generator only picks contacts with no tickets, so D02 never also triggers D15.
- **D03** `email` doesn't match `^[^@\s]+@[^@\s]+\.[^@\s]+$` (checked without trimming). `record_ids`: contacts.
- **D04** `industry` or `country` is blank. `record_ids`: companies.
- **D05** A company with `churned_at` has usage events dated after its churn day. `record_ids`: companies; `event_ids`: the 12 added events.
- **D06** `stage = closedwon` and `amount` is blank or `0`. `record_ids`: deals.
- **D07** `amount` is above 150,000, the top of the price list. Clean amounts run from 2,000 to 150,000. `record_ids`: deals.
- **D08** Stage is closed and `close_date` is earlier than the date part of `created_at`. `record_ids`: deals.
- **D09** `stage` is not in the picklist. `record_ids`: deals.
- **D10** `owner_id` is set but not an active owner in `owners.csv` (`OWN-11` and `OWN-12` are inactive; `OWN-99` doesn't exist). `record_ids`: deals.
- **D11** An `event_id` appears more than once. `record_ids`: the replayed event IDs.
- **D12** Date of `event_ts` is after `AS_OF`. `record_ids`: events.
- **D13** Among companies that are not churned, were created before the event window and have events, find a run of 3 or more days in the window with no events. Clean data has at least one event per company per day. `record_ids`: companies; `window_start` and `window_end`: the dropped days.
- **D14** `closed_at` is earlier than `opened_at`. `record_ids`: tickets.
- **D15** The ticket's contact has a different `company_id` from the ticket. `record_ids`: tickets.
- **D16** `lifecycle_stage` is blank. `record_ids`: contacts.
- **D17** `owner_id` is blank on a contact or a deal. `record_ids`: 4 contact IDs then 4 deal IDs.
- **D18** Group companies by `lower(domain)`. In each group with more than one company, every company except the lowest `company_id` is a duplicate. `record_ids`: the added duplicate companies; `pairs`: `[original, duplicate]`.
- **D19** `deals.company_id` is blank. `record_ids`: deals.
- **D20** Open deal with `close_date` earlier than `AS_OF`. Clean open deals close between 1 and 120 days after `AS_OF`. `record_ids`: deals.
- **D21** Open deal where `AS_OF - date(stage_entered_at)` is more than 60 days. Clean open deals entered their stage within the last 60 days. `record_ids`: deals.
- **D22** Date of `created_at` is on or after 2026-01-01 and `source` is blank. `record_ids`: contacts.
- **D23** `stage = contractsent` and `amount` is blank. `record_ids`: deals.

## Allowed blanks in clean data

Two kinds of blank value are normal and must not be flagged:

- Contacts created before 2026-01-01 may have a blank `source`. Attribution wasn't tracked then.
- Open deals before `decisionmakerboughtin` may have a blank `amount`.

A monitor that flags every blank field will fail the clean-data tests.

## Notes for planting in HubSpot

- **D10, D14**: these flags are a judgement beyond the five rejection rules above. Check them in a sandbox portal before relying on them.
- **D08**: confirmed on 2026-11-12 by probe P1 (`docs/hubspot-verification.md`). HubSpot accepted a `closedwon` deal created through the API with a `closedate` 30 days before its create time, and kept that date.
- **D21**: HubSpot's own `hs_date_entered_<stage>` properties are read-only. To plant this defect in a live portal, create a custom date property for `stage_entered_at`, or leave a deal in one stage until it is more than 60 days old.
- Created dates can't be backdated through the API, so any rule that depends on `created_at` (D08, D22) uses the real create time in a portal. D22 still works because records created today are after the 2026-01-01 cutoff.
