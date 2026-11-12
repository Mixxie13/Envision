# Coordinated sourcing workflow

This is an implementation guide for the maintained SOP, not a replacement policy.
The driver makes no provider request by default. It cannot currently produce a
campaign-ready list from the available evidence. Passing fixtures are not live
sourcing results, and campaign performance targets are not guarantees.

## One upload, reviewed batches

Install the declared `scripts/requirements.txt` and `relay/requirements.txt` in an
isolated Python environment. Keep project directories outside Git.

```sh
python scripts/workflow.py --project /private/envision/current intake \
  --master /private/uploads/bridget.xlsx --sheet Companies \
  --suppression-project /private/envision/approved-suppression
python scripts/workflow.py --project /private/envision/current preflight
python scripts/workflow.py --project /private/envision/current next
python scripts/workflow.py --project /private/envision/current status
```

Header ambiguity requires an explicit JSON mapping (`--mapping`), using the same
fields as `sourcing_state.read_master`. Intake archives the exact upload, preserves
all source columns, original row order, duplicates, priorities and categories, and
publishes the project directory only after validation, workbook and reports succeed.
An identical re-upload is a no-op. A changed source cannot replace that project.
The suppression option copies only the approved private registry, not another
batch's contacts. `ENVISION_SUPPRESSION_PROJECT` is an equivalent local setting.
Without this registry preflight remains unresolved and email dispatch is forbidden.

`next` runs/resumes only the first unreviewed batch (ten companies, final remainder
allowed). Failures remain exceptions and do not imply success. Repeating it reuses
checkpoints and refreshes evidence assessments; it never silently advances past QC.
After an actual Lead Sourcing Owner review:

```sh
python scripts/workflow.py --project /private/envision/current review \
  --batch 1 --evidence-ref actual-review-record
```

This releases the next research batch. It is neither Bridget approval nor spending
or outreach authority. Every source row has a progress outcome, including duplicates,
shortfalls and pending companies. There is no background run-all or sending service.
The CLI uses the existing project lock. Status/preflight open state read-only.

## Shared evidence, company and person review

`next --candidates permitted-manual-candidates.json` uses the existing per-person
intake contract in `CANDIDATE_HANDOFF.md`. Existing named publication candidates
are adopted under their original record IDs. Discovery, provisional role evidence,
current employment, person-linked email, deliverability and permission remain
separate counts derived from those records. Apollo alone confirms none of them.

`company-evidence --input reviewed-company-evidence.json` accepts an internal
`envision.company-review.v1` array. Each item binds the exact `company_row_id` and
original company name to a reviewed original source URL/hash, quote, observation
and expiry, reviewer evidence reference and policy reference. Employee-domain
confirmation must be explicit. Parent/domain changes need a brand-routing evidence
reference. Conflicting bindings stay pending. These are reviewed internal records,
not invented provider fields or automatic company verification.

An incomplete old discovery fact can be superseded only by an explicit QC
`fact_resolutions` record linked by claim digest to sufficient, current original
person-profile evidence. Historical facts remain stored; identity conflicts and
suppression cannot be waived. Founder/owner exceptions require current, documented
small-company/no-marketer (or only-one-marketer) research under the existing SOP.
An exception never confers historical executive or paid-credit authority.

The six workbook sheets and 14 prospect columns are unchanged. Research-review rows
are inserted only with confirmed current employment and sufficient current duplicate
coverage. They are not campaign authorization. Missing emails stay blank. Existing
user-edited rows are preserved and flagged instead of overwritten. Additional
provenance, verification and eligibility live in linked supporting records.

## Explicit live boundaries

`next --live hubspot --live dripify` is available only after a separately recorded
`workflow_operation_authorization` with approved evidence reference, allowed batch
IDs, operation names, expiry and positive integer per-provider `max_requests`.
Actual dispatches reserve durable budgets before requests, including paginated
reads. Restarts do not reset those reservations. Offline mode, scoped provider
selection and disabled paid adapters remain the defaults. `public_web` uses bounded,
robots-respecting original-source access. Tavily is not enabled by this driver.

Apollo also requires a current `verified_provider_capability:apollo` record and the
existing domain/account/schema approvals. The account's known 403 is not retried.
Full names, employer/profile visibility and a usable response contract are still
unverified. Do not infer a paid-plan requirement from a gateway response alone.

Email operations are separately disabled by default. To use `--live email`, both
the scoped workflow authorization and `email_operation_authorization` are required.
The latter must identify approved subject IDs and batches, expiry, pricing evidence,
verified maximum cost, positive request/credit caps and verified per-operation cost.
No costs, consent rules or freshness windows are assumed. A nonempty private bounce
registry, confirmed person and current employee-domain evidence are prerequisites.

`email_response_contracts` must contain reviewed entries for the enabled operation
(`findymail.find`, `findymail.verify`, `neverbounce.verify`): explicit `verified`,
`review_ref`, a nonempty result field path, exact result map, and email field path
for finder responses. These paths must be established from authorized official
examples/account responses; tests use synthetic fields only. A reviewed policy
reference and expiry are needed before a positive fact could qualify as fresh
verification evidence. No real production contract is fabricated by this code.

Reservations commit before transport. Unknown schemas/errors pause further email
requests. Interrupted dispatches never retry automatically: actual cost/outcome
requires provider reconciliation. Completed normalized receipts can be replayed into
local evidence after a recording failure without another paid request. Provider
facts do not fabricate person-to-email ownership, clear catch-all/unknown risks or
replace approvals. A valid result alone never permits outreach.

## Implementation and evidence matrix

| Component | Offline implementation | Actual proof / remaining limitation |
|---|---|---|
| Master intake, 10-company batches, whole-list tracking | Atomic upload, original rows/metadata, QC checkpoints, resumable jobs | Synthetic 10/10/1 reconciliation; full master not processed |
| Company/domain/parent evidence | Existing bounded public collectors plus reviewed evidence intake | Existing company evidence retained; conflicting ownership/domain stays pending |
| People discovery / original-source research | Apollo-first adapter, provisional extraction and per-person intake | Apollo 403; web pilots produced no confirmed people; not dependable autonomous discovery |
| Tavily | Existing opt-in transient Basic discovery | Historical authorized pilots completed with zero confirmed yield; paused |
| Employment and email linkage | Conservative original-source assessment; explicit evidence provenance | Historical publications/Apollo listings insufficient; accessible independent current evidence required |
| HubSpot matching and positive suppression | Existing read-only lookups, associations, exact identity and private bounce screening | Historical reads worked; subscription/sequence/history/ownership coverage incomplete |
| Dripify | Existing relay lead/membership/activity/conversation reads, pagination, cache and exact-profile matching | Historical routes worked; all-owner/account/history completeness unverified; no-match is not clearance |
| Hard-bounce import | Atomic/idempotent private registry, exact email blocks, ambiguous links pending | 464 private addresses, ten original blocks; absence from export proves nothing |
| Findymail / NeverBounce | Existing relay helpers plus disabled durable operations/budget bridge | Production pricing, response semantics, person binding and paid authorization not established |
| Eligibility / approvals | Shared fail-closed evaluator, Lead Sourcing Owner QC then Bridget | Technical eligibility never grants sending; historical Roxy approval not reassigned |
| Workbook / progress | Six sheets, 14 prospect columns, linked evidence, honest counts, edit protection | Actual cached pilot produces review evidence, not campaign-ready contacts |

## External blockers and smallest live pilot

1. Resolve legitimate Apollo People Search access and actual identity-field visibility,
   or supply an authorized manual Apollo candidate bundle. Do not retry blocked
   access, create keys or purchase upgrades speculatively. This is the highest-value
   prerequisite for discovering actual people.
2. Obtain sufficient permitted current person-specific evidence and verified brand /
   employee-domain attribution. Historical claims remain provisional. Personal
   LinkedIn access limitations cannot be replaced by unsupported assertions.
3. Confirm least-privilege HubSpot subscription/sequence/history and ownership
   coverage. Never request `communication_preferences.read_write` to read. Targeted
   authorized UI evidence is an exception when no adequate read-only API exists.
4. Establish relevant Dripify owners, pagination, retention and history coverage.
   Current empty membership or partial no-match cannot establish no previous outreach.
5. Have Bridget/Lead Sourcing Owner confirm freshness, recontact, subscription
   applicability, evidence storage and cross-channel policy. Preserve email-selected
   cross-channel pending until explicitly resolved.
6. Verify email response contracts and actual prices, then separately authorize a
   finite subject-scoped budget. No paid credits are authorized now. A meaningful
   ten-company pilot may cap discovery at one People Search request per company;
   email requests only for genuinely qualified candidates, under an independently
   approved maximum cost. Do not promise three-to-five contacts per company or
   invent a credit/dollar estimate before account pricing is established.

PR #9 remains a separate reviewed suppression change; do not merge automatically.
This follow-on branch depends on its code. No relay or sourcing SOP changes are
required by this implementation. No deployment, CRM write or outreach is performed.
