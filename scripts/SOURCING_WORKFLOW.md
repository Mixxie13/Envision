# Reusable Envision research runner

This is the implementation guide for the existing sourcing SOP, not a competing SOP or outreach system. Bridget supplies targets; retain original names/order/priorities/categories, batches of10, role/exclusion rules, honest3–5 contact shortfalls, the14-column sheet, five confidence labels, Lead Sourcing Owner review and Bridget final approval. No CRM write, sending, enrollment or permission-change operation exists in this runner. Paid Apollo enrichment/reveals and Findymail/NeverBounce credit-consuming operations remain disabled. Apollo People Search and Tavily Basic adapters are opt-in and require separate recorded authorization; neither is authorized for further calls now.

## Implemented versus unavailable

Implemented: CSV/XLSX values-only intake with explicit mapping; original row JSON preservation; deterministic batches; SQLite stage checkpoints and caches; project lock; per-company failure isolation; retained blocks and identifier-collision exceptions; cached/live narrow HubSpot reads; provisional official-site observations; Dripify all-visible and per-lead history collectors; atomic validated six-sheet updates/backups; persisted per-contact exceptions and prototype eligibility evaluation; explicit new-evidence rechecks. Import starts no research.

Implemented but disabled/blocked: the Tavily Basic transient-discovery adapter and Apollo People Search adapter. Prior authorized Tavily pilots did not produce confirmed current decision-makers or person-linked professional email candidates; further tests are paused. Apollo requests encountered HTTP 403 access challenges, so successful account access and response semantics remain unverified. Not implemented: dependable autonomous people discovery, general natural-language employment verification, unattended scheduling, paid authorization/budget execution, complete live provider-to-passing-evidence adapters or automatic approval. The offline employment assessor and per-person handoff accept conservative, person-specific reviewed evidence; they do not independently obtain it. The research collector extracts relevant structured Person, named heading/role and explicit named-attribution publication claims and professional mailto observations as provisional evidence, not confirmed employees or mailboxes. Unstructured or dynamically rendered leadership cards can still be missed. A bounded public HTML search adapter respects robots and stops on an access challenge; the live pilot was challenged, so it is not a reliable search backend. It cannot reliably source3–5 contacts per brand. It does not substitute corporate sites for required personal LinkedIn evidence.

PR #7 is merged into remote main at `412d160`; the user confirmed its Render deployment and earlier bounded live validation established lead, membership, activity, conversation and message retrieval. This sourcing branch inherits that reviewed relay unchanged and performs no new live validation or deployment. Successful retrieval does not establish account-wide historical coverage. Official API documentation at https://api.dripify.com/ was inspected on2026-11-11; the HTML embeds OpenAPI3.0.3. Documented GET paths: leads; leads/{id}; leads/{id}/activity; leads/{id}/conversation; conversations/{conversationId}/messages. Pagination uses cursor, limit1–100 except messages, which have cursor only. Conversation uses conversationId; activity has no unique id; messages use optional eventId. Preserve raw values and conservative completeness. Account/team visibility, archived/deleted retention, race conditions, active-state meaning and permissions still require account verification. Unfiltered listing spans visible campaigns, not proven all team users or historical deletions. Pagination_complete never sets coverage_complete=true. Repeated/invalid cursors, duplicate observations, unexpected schemas, page caps and errors retain positive items and fail closed. Message history needs an empty terminal page without cursor.

Subscription endpoint403 and sequence enrollment403 are cached existing limitations; do not repeatedly probe them or request communication_preferences.read_write. Contact properties and sequence snapshots are useful automatically but not complete suppression/history proof. Manual evidence is a targeted exception only when the automated source is unavailable and needed. No normal campaign-export requirement.

## Setup and intake

```
python3 -m venv .venv
.venv/bin/python -m pip install -r scripts/requirements.txt -r relay/requirements.txt
```

Keep venv/state/workbook files out of commits. openpyxl is the workbook dependency. All existing helpers/prototype contracts remain intact. Only HubSpot read stages use HUBSPOT_ACCESS_TOKEN; only Render reads use RELAY_TOKEN. Provider keys remain on Render; the public collector reads no credentials and sends no bearer headers.

Create a mapping JSON using exact source headers, for example:

```
{"company":"Brand","priority":"Upload Priority/Wave","category":"Category","website":"Website","price_tier":"Price Tier","hubspot_company_id":"HubSpot Record"}
```

Only company is required. Omit unavailable optional mappings; unknown websites remain pending. Do not invent source values. Existing categories including legacy CPG remain original; category proposals belong in evidence, not silent overwrite. Import never sorts by priority. Repeated company rows are retained and flagged; names do not establish duplicate identity. A different/reordered master cannot overwrite an existing project's source.

```
python3 -B scripts/source_batch.py --project /private/envision-state init \
  --master /path/to/bridget-master.xlsx --sheet 'Targets' --mapping /path/to/mapping.json
python3 -B scripts/source_batch.py --project /private/envision-state status
```

Multi-sheet XLSX requires explicit worksheet selection. Formula cells are rejected; request a reviewed values-only copy instead of evaluating formulas. IDs stay strings when supplied as CSV strings; already-rounded numeric spreadsheet IDs cannot be recovered. Source physical row and original fields are retained. Formula-like output strings are escaped for spreadsheet safety; exact input stays in SQLite.

## Controlled batch and resume

```
python3 -B scripts/source_batch.py --project /private/envision-state run \
  --batch 1 --workbook /private/current-research.xlsx --public-web --live-read
```

Run only a separately authorized batch; this task authorized the existing ten-company pilot, not processing an entire master. No run-all option or background daemon exists. --public-web enables bounded same-site published-source collection, robots enforcement, size/timeout/crawl limits, no redirects or access-control bypass. --live-read enables the existing read-only CRM/relay operations; without it cached seed evidence is used and missing capability stays pending. Without --public-web research remains pending. Default mode makes no provider or paid request.

Stage order: HubSpot-first company/association research → public-source research → exact-person CRM identity matching → Dripify evidence → disabled paid-email stage → fail-closed evaluation. Individual failed/blocked contacts do not stop other work. No stage completion means a check passed: each job stores its actual pending/blocked state. Cached failures are not automatically retried. Public content is parsed as data; it cannot instruct the runner, call tools or change approval/state policy.

Re-running the same command reuses completed/pending checkpoints. Workbook evidence IDs prevent duplicate rows, contact IDs prevent duplicate CRM rows, identifier collisions remain exceptions. Repeated contact IDs across brands retain the original contact and log a company conflict; do not silently merge companies or add an existing contact as new. New prospects are not populated until reliable duplicate coverage is available; provisional candidates remain in evidence/state rather than falsely labeled new. No automatic new/eligible prospect insertion is implemented in this version.

Before changing/rechecking a failed source, obtain actual new evidence or capability. Then queue a targeted recheck:

```
python3 -B scripts/source_batch.py --project /private/envision-state recheck \
  --company-position 3 --stage hubspot --evidence-ref /private/changed-evidence.json
```

This invalidates only that stage/evaluation job and versions its request cache; old evidence and blocks are retained. It does not change scopes or spend credits. Rerun the same batch command. A new cache version does not confer freshness or authorize a previously prohibited action.

After an actual batch research review, record its reference:

```
python3 -B scripts/source_batch.py --project /private/envision-state record-review \
  --batch 1 --evidence-ref /private/lead-sourcing-owner-review.json
```

This only releases the research checkpoint for the next batch. It does not authenticate the assertion, grant Bridget approval, authorize spend or clear outreach. Next-batch processing still needs authorization; review is not automatically manufactured. A complete automatic overnight master-list service is NOT implemented.

## Migrate the existing batch

`seed --checklist PATH --hubspot-evidence DIRECTORY` imports the existing local36-subject and CRM evidence into an initialized project. The pilot mapping uses an explicit separate Research Website column derived from the current workbook; original supplied fields remain unchanged. No raw production evidence is committed as a test fixture. Seed migration requires unambiguous source company names. It retains dated evidence, prior no-match observations, ambiguous identities and known blocks. It does not refresh source snapshots, establish approved expiry windows or convert archived research into current verification.

## Workbook and evidence guarantees

All six sheet headers must match the established schema. Existing company rows/order/categories and prospect rows are retained; a mismatched schema/order rejects replacement. New workbook company rows preserve original mapped values. New evidence rows and pending issues use stable IDs; existing HubSpot blocks are never cleared. Company/social enrichment is not auto-confirmed; blank follower counts remain N/A, with no estimates. Exact12-category assignment is not inferred by this runner.

Workbook saves use a same-directory temporary file, validated reload, content-hash concurrency check, content-addressed backup and atomic replacement. Single-project operations are locked. External Excel edits during a write cause a refusal, not intentional overwrite; there is no distributed cross-project/remote-file locking. Never point unrelated projects at the same workbook. Private SQLite/checkpoints and report files need approved restricted storage/backups. Original master is never rewritten. SQLite transactions preserve completed observations; a crash after an observation but before its job marker may create a later attempt observation, but cached read results and stable workbook IDs prevent repeat side effects. Evidence provenance is not a claim that the supplied source is honest or complete.

The current code approval contract requires lead_sourcing_owner research/QC followed by bridget final approval. Legacy approvals.roxy records are preserved as historical_approvals.roxy and never mapped to current authority. No verification/suppression gate was weakened. The email-selected Dripify pending rule remains. The runner does not fabricate any approval or normalize uncertain provider results into passing evidence. Source coverage/freshness and cross-channel policy still require resolution; authorized_to_send/outreach_ready remains false.

## Tests and readiness

Run `.venv/bin/python -B -m unittest discover -s tests -v` after installing both requirements files. The inherited relay tests require Flask; sourcing/workbook tests require openpyxl. If Flask is unavailable, run all sourcing tests and report the inherited relay tests as dependency-blocked, never as passing. Tests are synthetic/mocked and include all previous tests, restart/idempotence, batches10/10/1, failures continuing, lock/schema protection, original categories, repeated rows, blocks surviving re-import, robots restrictions, no paid calls and documented Dripify pagination/relay restrictions.

Readiness requires actual reliable contact discovery/current-employment evidence, deployed/tested read-only Dripify coverage with confirmed scope/retention, verified suppression/history adapters, approved policies and actual current-role review records, and a successful qualifying batch pilot. Until those are met, this implementation is a reusable research foundation, NOT an unattended master-list sourcing service. No full manual CRM/campaign export is required as a normal step.

## Operational source plan and measured research

The runner uses decision-maker source discovery by default when --public-web is
enabled. An optional `sources --file PLAN.json --evidence-ref REF` supplies an
audited map from original company names to URL/authority entries (official or
corroborating); it cannot introduce target companies. Cross-domain parent sources
need recorded support, not automatic ownership inference. The bounded source plan
reuses cached publication snapshots without changing their dates. Public search
URLs only nominate sources; snippets never confirm identity/employment. Robots,
access challenges and rate limits stop that source, while other research continues.

`contact_research.py` separates official role support, current official publication,
personal LinkedIn confirmation, company ownership/domain evidence and actual
qualified-contact status. A named role on an official page is not automatically
current employment or a verified mailbox. General-management and brand-ambassador
results are excluded. Alternate/same-surname people are flagged, not merged.
Read-only HubSpot contact-to-company edges can now be paginated for known IDs;
positive type-level opt-outs and positive active-sequence counts retain blocks.
Exact profile and primary/alternate-email search results require returned identity
agreement; name/company alone never establish a match.

## Whole-master row accounting (required)

The pilot is never treated as the entire master. Import Bridget's complete file
once when supplied; all nonempty company rows retain their original physical row,
position, name, priority/category and all other raw values. Fully empty spreadsheet
rows are not companies; their bytes remain in the original archived file. Invalid
nonempty rows fail import instead of being silently skipped. Potential duplicates
remain separate source rows with cross-referenced positions. No sorting, deletion,
replacement or independent target-company discovery is allowed.

Import archives the original source bytes privately and stores a hash-checked
source manifest. This is a local integrity baseline, not cryptographic proof of
who supplied the data. Progress/final reconciliation checks source hash, original
row values/positions, stable IDs, batch assignment, missing/extra state records,
stage attempts and recorded research reviews. Legacy projects are migrated only
when their existing source signature matches; lost rows cannot be reconstructed
from company names and must not be hidden.

`master-progress.json` and `master-progress.csv` are automatically refreshed after
intake/stages/reviews; the existing Batch Summary holds the six requested counters.
No seventh workbook sheet or change to the14-column prospect schema is introduced.
Counts use original source rows, including flagged duplicates; categories overlap:

- Total companies received: imported company-row count, not an inferred full-list
  total when only the pilot was supplied.
- Companies researched: at least one retained public research-source observation;
  this is not complete company/contact verification.
- Companies with confirmed contacts: at least one identity/current-role/company
  record with bound, current, complete normalized evidence and approved expiry
  policy. A raw `confirmed` flag, directory claim or stored CRM title is insufficient.
- Companies with existing HubSpot contacts: at least one retained actual CRM record
  linked to that research row; company relationship/current employment and reuse
  permission can still be unresolved. A supplied ID without a record does not count.
- Companies with unresolved research/verification issues: started rows with missing,
  pending, blocked or incomplete checks, plus source-integrity/duplicate exceptions.
- Companies not yet processed: no stage started. Separate counters show partial
  work and documented processed outcomes. Disabled research is not completed work.

Every original row has a visible outcome: not started, in progress, or processed
with confirmed contacts/no confirmed contacts. Per-row email outcomes distinguish
no current verified individual address from actual supported verification evidence;
none grants outreach eligibility or overrides a bounce. Unknown/catch-all/stale/
conflicting evidence and missing policy remain unresolved. A blocked email does not
make a person's otherwise established identity disappear; identity, address status
and outreach permission stay separate.

Read progress or request a reconciliation report without any provider processing:

```
python3 -B scripts/source_batch.py --project /private/envision-state status
python3 -B scripts/source_batch.py --project /private/envision-state completion-report
```

The latter writes `master-completion-report.md` even when incomplete, clearly
labeled INCOMPLETE. It can report research coverage complete only when every
original row has all attempted stages and a documented outcome, every batch has
recorded research review, the retained source bytes match and reconciliation has
no missing/altered/extra rows. Actual unresolved/no-contact cases stay in the final
report; completion means accounted-for research coverage, not successful sourcing
for every brand. Bridget's final approval remains separate and is never inferred
from research review, a tracker count or a completed batch. All outreach flags stay
false. Preserve all existing sourcing, paid-credit, suppression and cross-channel
rules. No whole-master run or automatic approval is introduced by this tracker.

### Resumable live Dripify duplicate research

The ordinary Dripify batch stage now shares one collector across the batch. To
continue only this read-only stage for an already attempted/reviewed batch:

```sh
python3 scripts/source_batch.py --project PATH check-dripify --batch 1 --workbook WORKBOOK.xlsx
```

Run under the existing project lock. Each invocation permits at most 12 relay
requests, two pages per resource, paced reads, no retries within the invocation,
and a process-wide pause after HTTP 429. Exact normalized personal LinkedIn URLs
bind matches; names alone never bind identity. Email-selected prospects retain
the cross-channel pending rule and skip individual Dripify history collection.

Private SQLite checkpoints save opaque continuation cursors after each accepted
page. Successful detail reads and terminal pages are reused across companies and
process restarts. Failed pages retain the last accepted cursor and positive
records for a later bounded attempt. Changed/overlapping result sets, invalid
schemas/cursors and unverified message termination stall rather than looping or
claiming completeness. Review stalled snapshots before explicitly starting a new
collection; retain the old dated evidence. This is a dated snapshot, not a live
freshness guarantee. No freshness window or owner/retention policy is invented.

Historical CONNECT_SENT/MESSAGE_SENT events remain positive outreach evidence
even with empty current memberships or conversation 404s. Invitation errors are
conflicts, not successful sends. Message text/attachments are not persisted by
the collector. Provider limitations, missing identifiers, ambiguous multiple
exact matches and unmatched records within partial coverage remain pending.
Even terminal pagination cannot establish all-owner/account/history coverage.
The evaluator is not granted a clearance pass by this integration.

The earlier live-validation export did not retain opaque cursor values. Its
500 leads and known history can be retained as dated evidence; continuing a new
resumable snapshot needs a one-time bounded first-page bootstrap. Subsequent
runs resume the saved cursor and do not restart accepted pages. An explicit
future refresh policy and independently verified account/history visibility
remain necessary before any negative duplicate conclusion is supported.

Operational sourcing still requires authorized, accessible public search and
person-specific current-role evidence; publication claims alone are provisional.
Professional email discovery must bind an address to the person. Paid Apollo,
Findymail and NeverBounce operations remain disabled without separate approval;
public/pattern candidates are not deliverability-verified. Approval, verification
freshness, subscription/sequence-history and cross-channel gaps remain fail-closed.

### Per-person evidence handoff

Manual Apollo observations and permitted source evidence can now enter the shared
offline per-person packet described in [CANDIDATE_HANDOFF.md](CANDIDATE_HANDOFF.md).
The same `review()` projection drives employment/eligibility, named workbook
evidence rows and progress counts. `candidate-handoff` validates by default;
`--apply` changes only the specified local project/workbook with cache-only
HubSpot/Dripify follow-up. Missing identifiers, source dates, company attribution,
reviewed current-role evidence, provider coverage or approvals remain unresolved.
The isolated ATTITUDE preview demonstrates named provisional records; it does not
establish confirmed employment, usable mailboxes, new CRM contacts or outreach.

### Suppression audit and shared screening

[SUPPRESSION_COVERAGE.md](SUPPRESSION_COVERAGE.md) records the offline endpoint,
permission, pagination, cache and coverage audit. `screening.py` projects retained
suppression across exact-linked records before company filtering; all runner,
workbook and progress decisions use that projection. Email-only HubSpot matching
and partial-result handling retain adverse evidence. Complete subscription,
sequence/history, ownership and Dripify account coverage remain pending. This does
not add a live integration, grant campaign readiness or change approval policy.
