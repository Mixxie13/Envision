# Offline per-person evidence handoff

This implements the existing SOP's evidence/review handoff. It does not authorize
research, provider calls, contact creation in a CRM, outreach or policy changes.

`candidate_handoff.py` accepts a JSON list. Each record requires the exact original
`company`, `name`, `job_title` and `employer_attribution`. Optional fields:
`company_row_id` (mandatory if original company names repeat), `linkedin_url`,
`original_source_url`, `source_reference`, `evidence_date`, `research_date`,
`employment_claims`, normalized internal `envelopes`, `email_evidence`, `channel`
and existing-schema `approvals`. Unknown source URLs/dates stay null and pending;
do not invent observation dates or substitute ingestion dates.

Example using the actual user-supplied manual discovery, not API-returned evidence:

```json
[
  {
    "company": "ATTITUDE",
    "name": "Amael Proulx-Lussier",
    "job_title": "Brand Manager",
    "employer_attribution": "ATTITUDE",
    "linkedin_url": null,
    "original_source_url": null,
    "source_reference": "User-supplied manual Apollo finding; original record URL not supplied",
    "evidence_date": null,
    "research_date": null
  }
]
```

Validate without modifying the local project/workbook:

```sh
python3 scripts/source_batch.py --project PROJECT candidate-handoff \
  --batch 1 --input CANDIDATES.json --workbook WORKBOOK.xlsx
```

`--apply` explicitly writes to that local project/workbook, runs existing
HubSpot/Dripify helper reconciliation in **cache-only mode**, and updates local
progress reports. Use an isolated project/workbook for review first. It makes no
external requests and requires no API credentials. It does not reset jobs or
search budgets. Existing source rows/values/order and six workbook sheets remain.

The packet retains original discovery facts, source dates, reviewed employment
claims, internal evidence envelopes, email evidence and provider-review warnings.
Repeated identical source facts are idempotent. Exact profile+name observations
within an original company row share a discovery group; this does not confirm
identity. Different sources without identifiers stay separate, unresolved
candidates rather than merging by name. Parent-company and role conflicts remain
pending. Multiple original company rows require explicit row IDs and are retained.

`review()` produces the shared projection consumed by employment assessment,
`source_batch.evaluate_contacts()`, workbook evidence/summary and master progress:

- discovered candidate;
- provisional role evidence;
- confirmed current employment;
- person-linked professional email candidate;
- deliverability-verified email;
- blocked / pending / eligible_for_review outreach eligibility.

Discovery facts alone cannot create passing checks. Normalized envelopes pass
through existing `evidence.assemble()` validation, with exact subject binding,
provenance, completeness, actual policy references and explicit observation/expiry
fields. No policy, consent, freshness window, provider schema or API capability is
inferred. Employment additionally uses the existing original-profile review path.
The record is reassessed at reading time, so expired evidence cannot remain counted
as current merely because an earlier stored projection was positive.

A published named mailbox and a positive deliverability result are separate facts;
verification never clears a historical bounce, opt-out, identity conflict, incomplete
history or approval requirement. Exact known identifier matches retain suppression
risks; name similarity cannot clear or merge identities. Email-selected cross-channel
pending behavior and Lead Sourcing Owner/Bridget requirements remain unchanged.

Per-person records appear in **Verification Evidence** and **Unresolved Issues**;
extra metrics use **Batch Summary** and the existing whole-list tracker. Provisional
candidates are not misclassified as confirmed-new contacts or added to the fixed
14-column New Prospect sheet without the required evidence. Five Email Confidence
labels and all existing sheets/headers are unchanged. Linked record IDs and source
provenance keep detailed evidence separate from the prospect sheet.

The provided Amael and Maude records remain provisional: original record URLs,
source dates, personal-profile/current-employment evidence, person-email linkage,
provider verification and complete duplicate/suppression/history evidence are still
missing. Successful synthetic tests are not production confirmations.

## Identity updates and fail-closed review

Validation opens an existing project database in read-only mode and does not create
locks, reports, checkpoints, workbooks or project directories. Invalid input also
leaves the project unchanged. `--apply` remains necessary for local writes.

Adding or correcting a profile on the same named, employer-attributed source
observation retains its candidate record ID and all earlier facts. An explicit
`candidate_record_id` can bind a reviewed update to an existing candidate in the
same original company row. It cannot rename another person. Competing source/profile
matches or unbound same-name updates require manual identity reconciliation rather
than an automatic merge. Profile corrections retain a pending identity-conflict
flag; an update is not an identity confirmation. Missing profile fields do not erase
an existing identifier.

Known hard-bounced addresses are matched by stripped, case-insensitive exact email,
independent of names or LinkedIn profiles. Their source record IDs and permanent
blocks are retained during intake and refresh; address matching alone does not
establish that two records identify the same person. No alias, plus-tag or dot
normalization is inferred.

Provider status is `pending` unless a subject-bound, complete, current check with
validated provenance supports its result. Missing, expired, failed or partial checks
cannot display `absent`. Known positive/history warnings and suppression remain
independent of any absence display. AUTO unresolved rows include available names,
roles, profile URLs, linked record/source references and pending reasons; older
blank AUTO rows are repaired without deleting historical reasons or changing columns.
