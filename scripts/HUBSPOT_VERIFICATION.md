# HubSpot capability-verification pack

Status: preparation only. No HubSpot account capability, entitlement, permission,
property, endpoint or provider response schema has been verified by this document.
It grants no authorization to contact people or change records.

## 1. Evidence categories

- **Repository facts:** the relay has no HubSpot routes. `scripts/evidence.py`
  accepts only `envision.internal-evidence.v1`, an internal contract, not a
  HubSpot schema. `scripts/eligibility.py` remains offline and always returns
  `authorized_to_send: false`. Both modules and the SOPs remain unchanged.
- **Official documentation to check:** use current HubSpot Developer Documentation
  and HubSpot Knowledge Base pages reached through `developers.hubspot.com` and
  `knowledge.hubspot.com`. The documentation topics below are research targets,
  not assertions that a particular API exists or is available to this account.
- **Account unknowns:** actual product tier, enabled features, application access,
  record visibility, history retention and supported data coverage.
- **Policy decisions:** Roxy/Bridget must approve applicability, freshness,
  recontact, evidence storage and approval rules; APIs cannot decide these.

Do not fill gaps with guessed scope names, properties, endpoints or JSON fields.
For each capability retain a verification entry containing: capability, status
(unverified / documented / account-confirmed / unavailable), official page URL and
review date, account entitlement evidence, exact documented read permissions,
verified read operation and field definitions, sanitized sample references,
coverage/retention limits, unresolved questions, reviewer and observation date.
A documentation citation alone is not account confirmation.

## 2. Administrator preparation and least privilege

1. Confirm who is authorized to inspect the intended account and which account is
   authoritative. Record a non-sensitive account reference in the approved location.
2. Review current official authentication, application-permission, scope, product
   entitlement and record-access documentation. Determine the supported access
   mechanism without sharing credentials with Codex or putting them in this pack.
3. For each capability below, record only the exact read permissions established
   by its official reference. Separate administrator UI access, application scopes,
   subscription entitlements and record visibility; one does not prove the others.
4. Confirm permissions for contact/company reads, associations, suppression and
   subscription evidence, sequence data and activity history individually where
   supported. Do not request broad access or writes to work around missing reads.
5. Confirm that any proposed operation is semantically read-only, regardless of
   HTTP method. Exclude create, update, delete, merge, enroll, subscribe, send,
   workflow triggers and contact modifications.
6. Prefer an existing approved test account or safe existing examples when
   supported. Do not create test contacts, send messages, trigger bounces, change
   subscriptions or enroll anyone to manufacture verification evidence.
7. Credential provisioning or API execution is a separate authorized activity.
   This checklist does not instruct Codex to access secrets or run requests.
   Any later permission change requires the account owner's normal approval.
8. If minimum read access or coverage is unavailable, record the limitation. Do
   not interpret denied access, hidden records or empty results as no match.

## 3. Capability checklist

For each item: review official documentation first, confirm account entitlement
and read visibility, inspect an authorized existing example, document limitations,
then have a reviewer approve the sanitized structure. Record unavailable cases
explicitly instead of inventing replacement fields.

| Capability | Verify and consult official documentation | Account/read confirmation | Sanitized examples must demonstrate | If unavailable |
| --- | --- | --- | --- | --- |
| Contact lookup and existing matching | Contact read/search documentation: supported exact-email matching, result limits, merged/archived behavior and stable identity | Contact search/read access and actual visible record scope | One match, multiple/ambiguous matches, explicit no-match, record identifier linkage; never presume an empty result is complete | `identity` and `hubspot_duplicate` remain unresolved |
| Known alternate emails | Official contact email-field/search definitions: whether alternate addresses are exposed and searchable, alias and merge semantics | Access to the documented address data; applicability to actual records | Primary and alternate addresses linked to the same synthetic contact; unavailable/missing alias data; matching by alternate if supported | No complete duplicate/suppression claim across known addresses; pending |
| Company domain and associations | Company read/search and association documentation: domain matching, relationship meaning, parent/operator representation and pagination | Company reads and supported contact-company relationship access | Company-domain match, contacts associated with companies, multiple associations, missing association and domain conflict | `company`/duplicate coverage pending; company/name alone cannot identify a person |
| Historical hard bounces | Official delivery-event/history or suppression references, if supported: hard-bounce meaning, exact address linkage, history coverage and retention | Product entitlement and read access to the documented authoritative source | Positive historical hard bounce; distinction from temporary failure; event dates and address binding; incomplete history | `hard_bounce` cannot be `clear`; confirmed `bounced` evidence remains blocking |
| Email opt-outs | Official suppression/opt-out references: global versus narrower scope, contact/address/channel applicability and status meanings | Read access to authoritative applicable suppression information | Opt-out, explicit clear evidence with documented coverage, missing/unknown scope and source dates | `opt_out` pending; confirmed applicable opt-out blocks |
| Subscription restrictions | Official subscription-preferences/type/status documentation and product limits: relevance to intended outreach purpose | Applicable subscription categories, entitlement and read permissions | Prohibited/permitted/unknown cases under approved policy; distinction between absent data and affirmative permission | `subscriptions` pending; never infer permission from no record |
| Active sequence enrollment | Official sequence/enrollment read references, if available: contact-level membership and lifecycle semantics | Actual account entitlement, supported read operation and visibility | Active enrollment, explicit inactive evidence, ambiguous/unsupported state and relevant dates | `hubspot_sequence` pending; confirmed active enrollment blocks |
| Previous outreach, replies and engagements | Official CRM activity, email/engagement, association and reply-history references as applicable: what is logged, retrievable and omitted | Relevant email/activity/history read access; connected-tool coverage and retention | Outbound event, reply linked to contact, chronology, missing history and non-email channel gaps; redact all message content | `outreach_history` pending; prior contact/reply requires review, not automatic permission |
| Pagination, visibility and completeness | Official pagination/search-limit, archive, permission, rate-limit/error and consistency documentation for every chosen operation | Visible owners/teams/records, historical range, account limits and any hidden/excluded data | Multi-page traversal and terminal condition; capped/partial results; permission/error/unknown-schema responses; collection versus event dates | `complete` and coverage assertions cannot pass; relevant checks remain pending |

Specific administrator questions:

- Can documented reads locate known existing records by primary and known
  alternate addresses, rather than merely searching names?
- Does company-domain lookup return all relevant associations, including ambiguous
  parent/operator cases? It does not by itself confirm employee email ownership.
- Does the history cover every relevant sending system and address? Is absence
  meaningful within a documented retention range, or merely an unlogged event?
- Does sequence information establish contact-level activity rather than just a
  list of sequences? Can application access see the necessary users' records?
- Can all pages be obtained without truncation, undocumented caps or visibility
  gaps? Record event, source-update and collection timestamps separately.

A successful transport response, an empty list, UI visibility or an authentication
success alone does not establish complete API access or outreach permission.

## 4. Safe sanitized-example process

1. Obtain separate authorization for any administrator inspection or read-only
   response collection. Prefer already available approved structures; do not run
   live calls as part of preparing this pack.
2. Keep raw responses inside the organization's approved restricted environment.
   Do not paste raw output into chat, commit it, attach it to a PR or store it in
   this repository. Never copy request headers, tokens, cookies or credential files.
3. Produce a separate structure-only copy. Remove all real names, email addresses,
   customer/company details, identifiers, domains, URLs, message text, signatures,
   attachments, private custom fields and other identifying metadata. Replace
   necessary references consistently with clearly synthetic values, using reserved
   example domains. Treat timestamps, error bodies and pagination links as
   potentially identifying or credential-bearing too.
4. Preserve only approved structural meaning: types, relevant documented field
   names, nesting, null/absent distinctions, pagination shape and relationships
   between synthetic identifiers. Replace account-specific custom property names
   with placeholders and document that they still require a secure mapping review.
   Never present a redacted/placeholder field as a verified standard property.
5. Preserve relevant chronology with clearly synthetic dates, not copied private
   dates. Replace identifiers and cursors consistently without publishing the
   original-to-synthetic lookup table. Do not publish reusable pagination URLs.
6. Have a second authorized reviewer inspect the entire example and metadata for
   secrets, personal information and private customer facts. A pattern scan alone
   is insufficient. If safe sanitization loses meaning, retain the issue as pending.
7. Label each sample as an approved sanitized structure derived from an authorized
   read, or as a purely synthetic example. Never label invented data as an actual
   account response. Keep official-reference links, schema/version context,
   limitations and reviewer/date in a separate non-sensitive manifest.
8. Share only the approved structure and manifest through the approved location.
   Creating production-derived fixtures in the repository requires a later review;
   this preparation adds no fixtures or private evidence.

## 5. Mapping to the existing internal contract

Future verified adapters can feed `evidence.evaluate_evidence()` without changing
`evidence.py` or `eligibility.py`. Provider responses must first be validated and
normalized; raw HubSpot JSON is unsupported today.

- Bind a local subject to exact email or confirmed personal LinkedIn identity and
  stable local contact ID. Retain provider-reference mappings securely. Company
  names and associations support company context, not person identity by themselves.
- Contact/company checks support `identity`, `company` and `hubspot_duplicate`;
  confirmed existing contacts remain pending under current evaluator rules.
- Historical bounce evidence supports `hard_bounce`; applicable suppression and
  subscription evidence supports `opt_out` and `subscriptions`; enrollment supports
  `hubspot_sequence`; activity coverage supports `outreach_history`.
- HubSpot alone cannot establish Dripify absence, inactivity or cross-channel clear.
  It also does not replace SOP-required personal LinkedIn employment confirmation
  or Findymail/NeverBounce verification and its paid-credit approvals.
- For each internal envelope retain source/coverage references, exact subject,
  observation date, approved expiry and policy reference. Only assert capability,
  coverage, pagination and lookup success when independently justified. Retain
  event dates and source gaps in the supporting evidence; never refresh an old
  verification date merely because the record was fetched again.
- Unsupported schemas, missing permissions, incomplete pages, conflicting identities
  and unverified coverage stay unresolved. Preserve bound adverse evidence even
  when another lookup fails. Never convert an unavailable capability to `clear`.
- Keep evidence in a separate linked record. Do not alter Roxy's exact 14-column
  prospecting sheet or five Email Confidence labels. Keep Roxy research review and
  Bridget approval separate from technical findings; eligibility is not sending
  authorization, and the evaluator always returns `authorized_to_send: false`.
- Email-selected records remain pending for cross-channel checks until Roxy
  approves a resolution. This pack does not change the Dripify skip rule or either
  SOP, and cannot authorize switching channels to evade suppression.

## 6. Decisions needed from Roxy and Bridget

- Verification/employment/suppression/enrollment freshness, expiry and recheck rules.
- Existing-contact handling, prior replies, recontact/cooldown rules and active
  outreach conflict definitions; no periods or reuse permission are assumed.
- Subscription applicability, outreach purpose and suppression scope across channels.
- Evidence/log location, access, retention, reviewers and contact linkage.
- Roxy-first/Bridget approval scope, proof and reapproval after evidence changes.
- How to resolve cross-channel Dripify checks for email-selected prospects while
  preserving the current pending state until Roxy explicitly approves a change.

## 7. Completion criteria

- Every capability has a dated verification entry, official references, confirmed
  account/read-permission evidence or an explicit unavailable/unknown outcome.
- Approved sanitized structures demonstrate identity, adverse findings, no-match
  limitations, failures and completeness for each supported capability.
- Exact least-privilege read permissions are documented without requesting writes;
  no credential values or private contact/customer information are shared.
- Roxy/Bridget policy decisions and unresolved capability gaps are recorded in the
  approved location. Missing capability or policy remains pending, not waived.
- Only after separate approval should fixture-backed provider adapters and any
  narrowly scoped relay reads be implemented. This pack is not live validation,
  a provider implementation, an approval to send or evidence of safe absence.
