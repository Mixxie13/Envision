# Read-only screening: offline audit and current limits

This describes the implementation, not a grant of consent, freshness, API scope or
campaign approval. No live provider requests were made for this audit. Prior account
observations are documented in HUBSPOT_READONLY.md and SOURCING_WORKFLOW.md; they
are historical evidence, not a new permission check.

## Capability and permission audit

| Check | Implemented collection / enforcement | Unresolved requirement |
| --- | --- | --- |
| HubSpot records and duplicates | Fixed-host contact/company property discovery, exact searches, batch reads, both association directions; exact normalized email/known alternate emails or personal LinkedIn matching. Name-only results never establish identity. Email lookup continues if a profile property is unavailable. | Company/name match is not identity, brand responsibility or complete duplicate coverage. Missing records, metadata or identifiers stay pending. |
| HubSpot bounce/opt-out/sequence signals | Previously observed account properties: hs_email_hard_bounce_reason_enum / hs_email_hard_bounce_reason, hs_email_optout and discovered hs_email_optout_* fields, hs_sequences_is_enrolled and hs_sequences_actively_enrolled_count. Positive restrictions block; blanks never clear. | Complete historical bounce/event coverage, subscription applicability and enrollment history are not established by these snapshots. |
| HubSpot subscriptions and do-not-contact | Explicit normalized opt-out, do-not-contact and prohibited-subscription evidence blocks, including imported internal records. | No universal HubSpot do-not-contact property is assumed or newly mapped. Earlier subscription probe returned 403 with a read_write scope requirement; do not request it. Verify an authorized read-only alternative and actual account semantics. |
| HubSpot sequence/history | Existing contact properties and available property history can retain positive sequence/reply/contacted signals. | Earlier sequence probe reported automation.sequences.read missing; entitlement and complete enrollment/email/reply coverage require verification. Current client does not implement those denied endpoints. No scope is changed here. |
| Ownership and routing | Contact-company associations and cross-company ID/identity conflicts are retained; routing status remains pending. | No owner-field mapping, assignment authority, brand responsibility or routing policy is invented. Multiple associations do not establish the intended owner. |
| Dripify duplicates | Existing relay campaign list/search plus GET leads, lead detail, activity, conversation and conversation-message reads; exact normalized personal profile binding, no name-only confirmation. Historical profile versions are retained and conflicting versions flagged. | All-owner visibility, hidden/deleted/archived retention, identifier semantics and account-wide completeness remain unresolved. Campaign lists alone cannot establish coverage. |
| Dripify email-only cases | Existing search helper accepts secondary email; retained internally bound emails can carry known suppression between local records. | Automatic Dripify email-result identity/schema and complete email-only membership/history coverage are not verified. A missing personal profile remains pending, never absent. No new raw provider email-field mapping is added. |
| Prior Dripify outreach | Previously validated CONNECT_SENT / MESSAGE_SENT activity remains positive even with empty memberships, later failures or changed cached results. Invitation conflicts remain unresolved. | No generic interpretation of campaign status, conversation 404, inbound replies, completed campaigns or owner visibility grants permission. Prior outreach requires recontact-policy review. |
| Campaign conflicts | Explicit internal active-sequence, active-Dripify-campaign and cross-channel-conflict evidence blocks. | Raw Dripify status/owner semantics have not been verified sufficiently to generate a current passing or inactive campaign check. |

## Pagination, caches and uncertainty

HubSpot rejects repeated cursors, inconsistent totals, duplicate records, search
caps and incomplete batch results. Partial positive records are retained. Dripify
uses bounded run/page budgets, saved cursors, terminal snapshots and stalled-state
handling. Valid positive items now survive an invalid continuation cursor; the
snapshot stalls rather than implying completeness. Messages retain metadata only.
Provider errors are not empty successful checks and unchanged failures are not
silently retried.

Caches retain original observation dates. There is no approved universal TTL or
refresh policy: reuse is not freshness. HubSpot cached reads explicitly report
freshness unknown and account/history coverage incomplete. Dripify results likewise
remain incomplete regardless of terminal pagination. Date expiry, partial pages,
provider errors, unsupported schemas and conflicting identities cannot support
ABSENT/CLEAR. Current normalized checks require exact subject binding, complete
validated provenance, actual policy references and valid observation/expiry dates.
Unresolved record risks also prevent a negative status display.

## Suppression reconciliation and consistent outputs

screening.py applies direct evidence-bound restrictions before company filtering.
Exact primary-email matches to a suppressed address always block. Reviewed aliases
or an identity-bound CRM record can support a direct match only without conflicting
identity claims. Bare CRM IDs, unverified aliases and LinkedIn-only associations
remain pending; they cannot form transitive suppression chains. Names never link identities. Separate records and original company rows are never merged.
Address/profile changes on an existing local ID retain old identifiers and adverse
evidence. Imported old projects are screened on read without a database migration.
Direct positive suppression is retained in either import order and across candidate
IDs. Native restrictions and previously suppressed addresses remain intact; a later
clear assertion cannot erase them. Derived projections are not saved as new native
suppression sources, so a blocked intermediary cannot poison another record. Suppression sources remain
referenced. Address reassignment or conflicting identifiers requires review; these
conservative blocks may over-block and cannot be automatically cleared.

No system can bind a completely changed, unlinked identity from a name alone.
Such a record still lacks the evidence needed for eligibility and stays pending.
DNC/opt-out and active-conflict restrictions never authorize switching channels.

The runner, per-person handoff, workbook and whole-list tracker consume the same
screening projection. Legacy and per-person records retain blocked/pending reasons.
Named screening observations, source references and provider dates are stored in
Verification Evidence / Unresolved Issues; company outcomes include screening
counts without changing the six sheets, 14 prospect columns or five labels.
Assessment dates never replace provider observation dates. Prior evidence/issues
remain historical; no rows are silently cleared or deleted.

campaign_ready and authorized_to_send remain false. eligible_for_review is a
technical review disposition, not campaign permission or a claim of full coverage.
Email-selected Dripify skipping/cross-channel pending, current-employment checks,
paid-credit restrictions and Lead Sourcing Owner/Bridget approvals are unchanged.

## Next verification gate

Before any controlled live work, agree on the missing evidence contract: authorized
read-only subscription/sequence/history access or targeted UI evidence, ownership
routing semantics, Dripify owner/retention coverage, and approved freshness/recontact
policies. Verify actual account responses under a separately authorized bounded
read-only test; do not widen permissions or infer schemas from this document.
Until then suppression coverage is insufficient for campaign-ready decisions.

## HubSpot subscription findings and targeted export fallback

The user confirmed that Marketing Information and One to One are active account
subscription types and contact records offer an Opt out of email action. These
facts do not establish a particular contact's preferences, applicable outreach
permission, API type IDs or field mappings. Do not infer those mappings from names
or from an available action. No write-capable permission is requested.

`hubspot_signals()` now reports the positive properties separately from subscription
clearance and history coverage. Nonempty observed bounce reasons, explicit true
opt-outs (global or discovered type-specific properties) and positive active-sequence
snapshots retain blocks. Missing/blank/false opt-out properties do not establish a
subscription. There is no verified universal native do-not-contact property here;
explicit internal/manual DNC restrictions are retained without guessing one.

Previously observed reply dates, notes_last_contacted and positive enrollment counts
produce a prior-contact review warning, not a complete activity log or recontact
permission. A recorded contact-activity indicator does not prove an email send or
person-specific reply unless the underlying event evidence supports that claim.

An administrator with existing authorized UI/report/export access may supply only
the missing evidence for the current review subjects, without changing app scopes:

- Exact contact ID as a string, primary/known alternate email and available profile
  identifier. Preserve all conflicting identities rather than silently choosing one.
- Current global restrictions and each relevant subscription's actual type ID,
  displayed name, explicit contact status, status/event date if available, and the
  observation date. Include both confirmed active types; record applicability as
  unknown until approved policy establishes it. Do not claim false/blank flags mean
  subscribed or consenting.
- Available hard-bounce evidence linked to the exact address, plus event versus
  property-update dates where distinguishable. Never clear the ten retained blocks.
- Available logged activity/reply and sequence enrollment evidence, relevant owners,
  event/enrollment dates, filters, time range, pagination/export completeness and
  visibility/retention limits. Do not include message bodies or attachments unless
  separately justified and authorized.
- Evidence/source and coverage references, exporting/reviewing user and extraction
  date. Store private exports and detailed type/activity evidence in approved storage,
  not Git. Use UI review when a report/export does not expose the required data;
  if UI access also cannot establish it, keep the check pending.

A contacts CSV alone does not necessarily contain subscription-type status, sequence
history or all marketing/sales/inbox events. Export/report availability depends on
existing account features and user permissions; no new capability is asserted here.
No full CRM export is required. Existing contact/company reads need not be repeated.

The existing normalized CSV contract in reconcile_exports.py remains unchanged.
Use a separate normalized copy and reference the detailed type/activity evidence via
evidence_ref and coverage_ref. Boolean fields are reviewed internal assertions,
not automatically mapped raw HubSpot columns. Blank/unsupported evidence stays
unknown. A claimed coverage_complete or two complete_sources_claimed entries never
establish subscription clearance, freshness, activity completeness or campaign access.
Conflicting identifiers remain ambiguous; adverse evidence sharing an exact known
ID/email/profile is additionally retained as blocking instead of being discarded.
An entirely unbound row is retained for reconciliation, not merged into another person.

## Uploaded hard-bounce list intake (offline review)

`suppression_import.py` reads an explicitly designated XLSX suppression list.
Default execution only validates the upload; it creates no project or report files.
Local `--apply --confirm-suppression-list --workbook PATH` requires an existing
reviewed project/workbook workflow and records list membership in a private durable
suppression registry, not as new contacts or companies. Review in an isolated copy
first. Never commit uploaded exports, private registry data or preview workbooks.

The supplied HubSpot workbook declares only A1 despite having 464 data rows and
20 columns. Intake resets dimensions so these records are not silently lost.
Duplicate unused export labels are allowed; duplicate identifier headers, formulas,
ambiguous email cells, imprecise IDs or missing required identifiers reject intake.
Only exact emails, explicitly mapped alias cells, string-preserved contact IDs,
source hash/sheet/row and evidence references enter the registry. Phone numbers,
locations and other unrelated exported values are not retained by the importer.

This export has no bounce-reason/date, opt-out, subscription-status or LinkedIn
columns. Its uploaded suppression-list designation authorizes precautionary blocks;
filename alone does not prove a provider bounce result or the export's filter.
List membership is labeled separately from provider-verified hard-bounce evidence.
An import timestamp is not a bounce or subscription observation date.

The registry is applied at every contact read, including future exact-email
matches and unambiguous verified aliases/CRM bindings, without creating extra source-company rows or a seventh
sheet. Earlier source lists and known hard-bounce restrictions are never removed
by a later subset export. Matches create named evidence/unresolved rows; Batch
Summary records source rows and unique suppressed addresses. Missing list matches
remain pending and never establish delivery, subscription or history clearance.

For the uploaded file: 464 rows, 464 unique addresses and 464 contact IDs; 10 existing
project records match and were already blocked. All ten prior hard-bounce records
appear in this export. The other 454 addresses add future suppression coverage;
they are not new prospects or independently verified employment/email findings.
The prepared review workbook/project is isolated; production project activation
remains subject to review. The actual suppression filter, bounce reasons/dates,
subscription evidence and complete activity/history coverage still need confirmation.


## Atomic import publication and recovery

The apply command requires an existing initialized project and six-sheet workbook.
Source rows, exact IDs, workbook schema and original company order are validated
before modifying persistent data. Inspection parses the same bytes it hashes.
Registry and evidence rows are prepared together in a SQLite transaction; the CLI
stages a private database copy, workbook and both progress reports before publishing
any of them. Evidence paths refer to the real project, not deleted staging paths.

A durable intent journal and private pre-import backups protect grouped publication.
Ordinary exceptions and KeyboardInterrupt restore the prior registry/database,
workbook and progress files. A process crash leaves a journal: supported State readers
refuse to read the incomplete project. The next locked apply attempt recovers it
before continuing, or an authorized caller can use recover_import under project_lock.
A pre-commit interrupted journal rolls back; a completed commit marker retains the
complete new result. Repeating the same source does not duplicate evidence or rows.

All cooperating readers/writers must honor the project lock and journal. Individual
file replacement is atomic; cross-file publication is a recoverable logical
transaction, not a single filesystem-wide rename. External Excel tools must not
edit or consume these outputs during publication. If external changes or an I/O
failure prevent safe rollback, retain the journal/backups and require reconciliation;
do not read the project as successfully imported or issue clearance. No permissions,
provider requests or production changes are involved in recovery testing.
