# Phase 1: offline pre-outreach eligibility review

`eligibility.py` evaluates one local JSON record using only the Python standard
library. It imports no API client, reads no credentials, fetches no data, changes
no records and never sends or enrolls anyone. It does not run verification.

```sh
python3 -B scripts/eligibility.py /path/to/linked-evidence.json
# Or provide the same JSON on stdin:
python3 -B scripts/eligibility.py - < /path/to/linked-evidence.json
python3 -B -m unittest discover -s tests -v
```

Exit 0 means `eligible_for_review`; exit 1 means `pending` or `blocked`.
Unreadable files or malformed JSON produce a pending JSON result without echoing
input or exception details. The importable function is `evaluate(record, now=...)`;
tests supply a timezone-aware evaluation time. Normal CLI use takes current UTC.

## Linked record contract (version 1)

Keep this evidence outside the prospecting sheet. The existing SOP, its exact
14 columns, five Email Confidence labels, and Roxy/Bridget review process are
unchanged. Link the record using Batch, Company, Full Name and LinkedIn; store
those in `subject` alongside a stable local `contact_id` and exact email candidate.
Names and company alone are not reliable identity matching. No HubSpot property
or provider field is implied by these internal names.

Top-level JSON object:

- `subject`: object containing nonempty `contact_id`, `company`, `full_name`;
  `email` for email review and `linkedin_url` for LinkedIn review. Also retain
  `batch`, `campaign` when known, and research/evidence linkage identifiers.
- `channel`: exactly `email` or `linkedin`. This is the proposed channel under
  review, not an automatically selected fallback. Existing SOP email priority
  still applies; a caller must not use LinkedIn to evade a failed email check.
- `checks`: object containing every applicable check from the table below.
- `unresolved_risks`: explicit list; only an empty list passes. Preserve catch-all,
  unknown results, conflicting provider results and ownership/domain uncertainty
  until evidence actually resolves them. A positive second check cannot erase them.
- `approvals`: independent `roxy` and `bridget` review records, described below.

Each check is an object with these fields:

```json
{
  "result": "confirmed",
  "subject": {"contact_id":"example-id","company":"Example","full_name":"Example Person","email":"person@example.com"},
  "source": "reference to retained evidence; never a credential",
  "observed_at": "2030-01-01T00:00:00Z",
  "expires_at": "2030-01-03T00:00:00Z",
  "policy_ref": "reference to the approved policy establishing applicability and expiry",
  "state": "ok",
  "complete": true
}
```

The dates above are **synthetic format examples, not approved freshness windows**.
Every check's `subject` must equal the full top-level subject exactly, including
its email and any linkage fields. Changing the address/contact invalidates earlier
evidence bindings. Store provider name, exact raw result, individual observations,
role evidence, ownership/employee-domain evidence, restrictions' scope and history
in additional fields or the retained evidence referenced by `source`. Extra fields
are retained by the input owner, not interpreted as permission by the evaluator.

| Check | Only passing normalized result | Required meaning/evidence |
| --- | --- | --- |
| `identity` | `confirmed` | Unambiguous person and identifiers; resolve collisions |
| `company` | `confirmed` | Current ownership/operator, employee domain and routing confirmed |
| `role_company` | `confirmed` | Current relevant role/company with personal LinkedIn evidence and observation date |
| `professional_email` | `individual` | Individual professional candidate, not shared inbox; email only |
| `verification` | `valid` | Positive result for exact email; source identifies provider and raw results/date; email only |
| `hard_bounce` | `clear` | Complete historical bounce evidence; not a fresh verifier result |
| `opt_out` | `clear` | Applicable opt-out/suppression evidence, with restriction scope |
| `subscriptions` | `permitted` | Affirmative, policy-supported applicable subscription/permission evidence |
| `hubspot_duplicate` | `absent` | Complete email/company/association duplicate check |
| `hubspot_sequence` | `inactive` | Authoritative absence of active sequence enrollment |
| `outreach_history` | `none` | Complete previous outreach/reply history check |
| `dripify_duplicate` | `absent` | Complete LinkedIn-first, secondary-email-if-needed lead check |
| `dripify_campaign` | `inactive` | Authoritative contact-level campaign/enrollment evidence |
| `cross_channel` | `clear` | No overlapping outreach conflicts across relevant channels/campaigns/batches |

For all checks, require `complete: true`, `state: ok`, nonempty source and policy
references, observation at or before evaluation time, and expiry strictly later
than both observation and evaluation. Timestamps must include a timezone.
Unknown/failed/conflicting/ambiguous source states and unknown result vocabulary
remain pending. Missing expiry or policy means freshness/applicability is undefined,
not permanent validity. Nothing in this module establishes an approved TTL,
cooldown, consent rule or provider-to-normalized-result mapping.

## Decisions and approvals

Decision precedence is **blocked, then pending, then eligible_for_review**.

- `hard_bounce: bounced`, `opt_out: opted_out`, `subscriptions: prohibited`,
  `hubspot_sequence: active`, `dripify_campaign: active`, or
  `cross_channel: conflict` blocks. These positive adverse assertions continue to
  block if another field is missing or stale; stale negatives never establish clear.
- Email review also blocks `verification: invalid` or `professional_email: shared`.
  Unknown, catch-all, failed or unverified results remain pending. No automatic
  switch to LinkedIn occurs. Explicit LinkedIn review still requires suppression,
  subscription, history and cross-channel evidence; it does not need an email
  candidate/verification. Suppressions are conservatively blocking across proposed
  channels until their applicable scope and policy are resolved outside this gate.
- Existing HubSpot contacts or Dripify leads remain pending, not new-prospect
  eligible. Existence is not inherently a prohibition on all future contact, but
  there is no approved reuse/recontact policy encoded here. `contacted` or `replied`
  history also remains pending; priority for prior replies does not waive review.
- A verified address alone never grants eligibility. No confidence label affects
  the decision. Unresolved risks prevent eligibility even after a positive second
  provider check. Missing source records must not be supplied as `clear`/`absent`.

Each approval object has `state`, `subject`, `source`, and `observed_at`. Only
`state: approved` with matching full subject, retained review evidence and a
non-future timezone-aware date satisfies that person's review check. Missing,
rejected or otherwise unresolved reviews keep the overall decision pending. These
are assertions of separately obtained Roxy research review and Bridget approval;
the evaluator cannot authenticate them and does not manufacture either approval.
The existing Roxy-first order remains a procedural requirement, not a new workflow.

Output includes one overall `decision`, reason codes, `technical_eligibility`
computed before approval checks, independent approval records, proposed `channel`,
`evaluated_at`, and normalized evidence sources/observation/expiry/policy references.
`expires_at` is the earliest applicable evidence expiry when technically complete,
even if approvals are still pending; otherwise null. `authorized_to_send` is
**always false**, including when both reviews exist. Re-evaluate after changes and
before any future action; this record is not a durable sending permission.

Reasons are deterministic identifiers: e.g. `HARD_BOUNCE_BLOCKED`,
`VERIFICATION_INVALID`, `IDENTITY_UNRESOLVED`, `DRIPIFY_CAMPAIGN_MISSING`,
`VERIFICATION_STALE`, `SUBSCRIPTIONS_POLICY_UNDEFINED`, or `BRIDGET_APPROVAL_REQUIRED`.
Each reason identifies a check and issue, rather than guessing provider semantics.

## Limits and decisions needed before operational use

Phase 1 trusts supplied evidence; it cannot prove completeness, detect a dishonest
`clear` assertion, parse provider responses, discover omitted historical results,
authenticate approvals or prevent a person acting outside the repository. No
sending/enrollment path currently exists here to attach a mandatory action gate to.
Missing live integrations mean actual operational records stay pending until
required authoritative evidence and policy decisions are available.

Roxy/Bridget need to establish:

1. Evidence-store/unresolved-log locations, identity linkage and trusted reviewers.
2. Freshness/expiry rules for each evidence type and approval scope/revalidation.
3. Applicable subscription/consent/suppression scope; no implicit permission from
   absent subscription data, historical replies or channel switching.
4. Treatment of existing contacts/leads and prior outreach/replies, including any
   recontact/cooldown rules. None is invented or automatically waived here.
5. Authoritative HubSpot and Dripify access and mappings, source coverage, pagination,
   campaign/enrollment definitions, and cross-channel reconciliation. Account-level
   campaign listing alone does not prove contact-level membership or inactivity.
6. How a future mandatory cross-channel gate obtains evidence without bypassing
   Step 6a's current instruction to skip Dripify lookup for email-selected contacts.
   This phase does not change that rule or silently run additional lookups.

## Later integration, not implemented

Add read-only adapters only after verifying actual account permissions, documented
endpoints/properties and real response semantics. HubSpot evidence must cover
company/name/domain and contact matches, historical hard bounces, opt-outs and
applicable subscriptions, active sequences, and outreach/reply history. Dripify
adapters must establish complete lead matches, contact campaign membership/states
and result completeness. Keep provider credentials on the relay and Codex access
through the configured relay token; no new credential setup is part of this phase.

Adapters should bind evidence to the subject, retain raw result references and
observation dates, exhaust verified pagination and normalize only known statuses.
An error, missing capability, unknown schema or incomplete page produces unresolved
evidence, never an empty successful result. Use approved policies to assign expiry.
Only then feed normalized evidence to this same evaluator. Before adding any send,
HubSpot write or enrollment capability, make the action path require a fresh gate
result plus independently verified approvals and a final conflict check. A local
check alone cannot provide concurrency protection against another process enrolling
or contacting the person after evaluation.

## Phase 2A: offline evidence intake foundation

Implemented in `scripts/evidence.py`, with synthetic fixtures built in
`tests/test_evidence.py`. This is **not** a HubSpot/Dripify response adapter or a
live collector. No production provider schema, status, property, endpoint or
permission is defined or implied. There is no network, filesystem, credential or
external-write access in the intake module.

Use the existing Phase 1 record as the header: `subject`, `channel`, `approvals`
and an explicit `unresolved_risks` list. Supply evidence separately as a list of
internal envelopes. Header `checks` are ignored to prevent bypassing intake.
The module does not read or change the prospecting sheet, confidence labels, SOPs,
provider helpers or relay.

```python
from evidence import assemble, evaluate_evidence

# All arguments are already-loaded local data. No fetch occurs.
assembled = assemble(record_header, internal_envelopes)
report = evaluate_evidence(record_header, internal_envelopes, now=evaluation_time)
# report: {"record": Phase_1_record, "intake": provenance_trace,
#          "evaluation": unchanged_Phase_1_result}
```

Only `schema: envision.internal-evidence.v1` is supported. This identifier names
our local intake contract, never a provider schema. Each envelope has:

```json
{
  "schema": "envision.internal-evidence.v1",
  "check": "identity",
  "identity": {
    "contact_id": "synthetic-1",
    "basis": "email",
    "value": "person@example.com"
  },
  "provenance": {
    "source_ref": "synthetic/identity",
    "coverage_ref": "synthetic coverage only",
    "capability_verified": true,
    "coverage_verified": true,
    "pagination_complete": true,
    "lookup_succeeded": true
  },
  "evidence": {
    "subject": {
      "contact_id": "synthetic-1",
      "company": "Example",
      "full_name": "Example Person",
      "email": "person@example.com"
    },
    "result": "confirmed",
    "state": "ok",
    "complete": true,
    "source": "synthetic/identity",
    "observed_at": "2030-01-01T00:00:00Z",
    "expires_at": "2030-01-03T00:00:00Z",
    "policy_ref": "synthetic policy; not production"
  }
}
```

This example is entirely synthetic; its dates and booleans do not establish real
policy, provider coverage or account access. The full `evidence.subject` must
exactly equal the header subject, including linkage fields. `evidence.source` must
match `provenance.source_ref`; both source and coverage references must be nonblank.
All four provenance flags must be explicitly true for a passing intake. They are
trusted local assertions, not facts independently established by this module.
`coverage_ref` must eventually point to evidence of scope/retention/completeness;
setting a flag true is not a substitute for that evidence.

Identity binding uses the local contact ID plus exactly one identity basis:

- `basis: email` requires exact equality to the subject email. No case folding,
  alias inference or email-to-LinkedIn conversion is performed.
- `basis: linkedin` requires exact equality to the subject LinkedIn URL and
  `confirmed_personal: true`. No URL normalization, scraping or assumption that
  a company page identifies a person is performed.
- Company/name alone never bind identity. Both identities are linked only by the
  explicitly supplied matching subject and local ID; this does not prove that a
  real provider record or account owns either identifier.

Unsupported schemas/checks, malformed inputs, failed lookups, unverified coverage
or capabilities, incomplete pagination and identity mismatches add unresolved risks.
An empty response or `ok: true` HTTP wrapper cannot establish a passing check.
Missing evidence stays missing; incomplete evidence cannot turn into confirmed
absence. Phase 1 performs the existing result, observation-date, expiry, policy and
approval checks. No freshness window or consent policy is added by intake.

Every envelope's provenance, original internal evidence, identity basis and intake
issues remain in the separate `intake` trace. Inputs are copied, not modified.
Do not put tokens or credentials in these local records; the module has no secrets
and does not attempt to sanitize arbitrary input for public sharing. Retain the
report in the approved evidence location, not the 14-column prospecting sheet.

Multiple bound observations for one check conservatively require review rather
than selecting the newest/last clear result. A recognized, identity-bound adverse
Phase 1 result takes precedence over clear assertions, even when stale or another
lookup fails. Unbound or unsupported raw adverse data is not assigned to a person:
it creates a pending issue. Previously confirmed adverse evidence must therefore
be retained as its own supported bound envelope; do not replace it with an unknown
raw provider response. All original observations stay in the trace.

For `channel: email`, intake always adds
`EMAIL_CROSS_CHANNEL_POLICY_UNRESOLVED` and marks any cross-channel check unresolved.
This preserves Step 6a's email-selected Dripify skip rule. A synthetic clear flag
cannot resolve the SOP conflict. Email records remain pending unless a known block
makes them blocked. Roxy must approve a resolution before a later implementation
can change this behavior. Do not switch to LinkedIn to escape this pending check
or any suppression. LinkedIn fixtures can exercise a technically complete handoff;
they are not proof of production permission or a new routing policy.

### Still unsupported before live integration

- Real HubSpot contact/company identity and association fields, historical bounce
  coverage, opt-out/subscription scope, sequence enrollment and outreach/reply data.
- Real Dripify search/membership/campaign schemas, status meanings, pagination and
  contact-level enrollment completeness. Account campaign lists are insufficient.
- Verified account permissions, capabilities and source retention limits.
- Provider-specific normalization and authoritative evidence collection.
- Approved freshness/expiry, consent, recontact/cooldown and approval-scope policies;
  trusted reviewers, the evidence/log location and cross-channel SOP resolution.

These require documentation, account-capability verification and approved sanitized
fixtures in a separately authorized phase. Until then, provider data is unsupported
and missing policy stays pending. The unchanged evaluator still returns
`authorized_to_send: false`. Phase 2A cannot authenticate local assertions, prove
absence, enforce actions elsewhere or prevent concurrent outreach.
