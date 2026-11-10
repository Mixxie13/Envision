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
