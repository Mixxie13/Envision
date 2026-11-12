# HubSpot read-only client

`scripts/hubspot.py` uses Python's standard library and the configured
`HUBSPOT_ACCESS_TOKEN`. It sends credentials only to fixed `https://api.hubapi.com`.
Never put the key in files, arguments, examples or logs. Keep inherited proxy/CA
settings. The client does not create records, update records or enroll contacts.

The user-authorized service connection has contact/company read scopes. Codex's
HubSpot connection is independent of the existing Render relay; provider relay
helpers and their credentials remain unchanged. No additional scopes are requested.

```python
from hubspot import Client

client = Client()
metadata = client.properties('contacts')
# Inspect actual property metadata before selecting fields or constructing filters.
# Example only if metadata confirms these names:
result = client.search('contacts', [
    {'filters': [{'propertyName': 'email', 'operator': 'EQ',
                  'value': 'person@example.com'}]}
], ['firstname', 'lastname', 'email', 'jobtitle'])
```

Available methods:

- `properties(kind)` discovers contact/company properties; no LinkedIn field is
  assumed. Unsupported/invalid or paged metadata fails closed.
- `get(kind, record_id, fields)` reads one numeric ID with discovered fields.
- `search(kind, groups, fields)` supports only explicit exact-equality filters.
  Company names/domains and available contact identity properties use the same
  operation; it does not perform fuzzy matching or infer identity from names.
- `associations(company_id)` reads company-to-contact associations with cursors.
- `batch_read(kind, ids, fields)` reads at most 100 contact/company IDs per call.

`kind` is only contacts/companies. Transport itself allowlists only property reads,
record reads, search POST, batch-read POST and company-contact association GET.
No caller-supplied host, arbitrary URL or write method is supported. Redirects are
blocked, TLS verification remains enabled and requests time out after 30 seconds.
Credentials are redacted from parsed responses; arbitrary provider errors are
suppressed. `ReadError.safe()` exposes only category, HTTP status, numeric
Retry-After and syntactically valid redacted required-scope identifiers. Do not
log entire successful records: they contain authorized CRM personal information.

Search/association results contain `results`, `complete`, `pages`, `total` and
`error`. Cursor links are ignored; only the cursor is sent back to a fixed route.
Repeated cursors, duplicate IDs, changing totals, malformed pages, search caps,
page limits and failures produce `complete: false`. Earlier valid pages survive a
later failure. Batch status/errors/returned IDs are checked; HTTP207 results are
retained but never labeled complete. HTTP429 fails with Retry-After metadata and
no automatic retries: pause the affected operation and resume only after the
specified wait or an approved retry policy. Do not loop unchanged failures.

**Completeness is query/token visibility only**, not full CRM history, archived
coverage, alternate-email matching, a stable transaction or permission to contact.
Empty exact queries are not global absence. Identity matching remains separate:
exact known email/profile evidence is stronger than name/association alone. Preserve
ambiguous matches, contradictory roles, regions, duplicate company records and IDs.
A CRM title/address is not proof of current employment, mailbox deliverability or
outreach eligibility. No adapter maps raw records into passing eligibility evidence.

## Live validation of the existing ten-company batch

Connection, company/contact metadata, company/contact batch reads, exact company
name/domain searches, exact known-email and provisional-name/profile searches, and
v4 company-contact association reads succeeded on 2026-11-11. Properties actually
present include `hs_linkedin_url`, `hs_additional_emails`, `hs_email_optout`,
`hs_email_hard_bounce_reason_enum`, `hs_sequences_is_enrolled`,
`hs_sequences_actively_enrolled_count`, `hs_email_last_reply_date` and
`notes_last_contacted`. These names were discovered in this account, not assumed.

Contact properties provide useful partial evidence: a nonempty hard-bounce reason
is adverse; null opt-out/reply/bounce properties do not establish absence. Current
sequence booleans/counts are snapshots, not complete enrollment/history evidence.
Relevant property metadata/descriptions and exact returned values are retained
privately with the workbook, not committed as production fixtures.

Coverage probes, performed read-only and without changing scopes:

- Subscription v4 status GET returned HTTP403 MISSING_SCOPES; server reported
  `communication_preferences.read_write`. This is broader than the authorized
  read-only connection; **do not add it automatically**. Resolve the documented
  read-scope/server requirement discrepancy or obtain an approved read-only
  alternative/export. Subscription applicability/permission remains pending.
- Sequence enrollment GET returned HTTP403 MISSING_SCOPES requiring
  `automation.sequences.read`. Do not add it automatically; account entitlement
  and visibility also require confirmation.

No full email/reply-event history, complete historical bounce audit, subscription
clearance, contact freshness policy or cross-channel Dripify check is established
by these reads. The unchanged offline evaluator and role-approval mismatch retain
their existing limits; no contact is cleared or sent. No paid providers are called.

Run offline checks with `python3 -B -m unittest discover -s tests -v`.
Tests use synthetic credentials and mocked transport; never production fixtures.

Identity review supports bounded `CONTAINS_TOKEN` discovery filters as documented
in HubSpot's official CRM search guide. Discovery results are not identity proof.
Optional `propertiesWithHistory` requests use only discovered property names.
A property-version timestamp is not necessarily the time of a delivery event;
imports and later updates must not be presented as bounce occurrence dates.

Operational extension: `contact_associations(contact_id)` reads only the fixed
GET `/crm/v4/objects/contacts/<numeric-id>/associations/companies` route, with
the same strict pagination checks. The controlled pilot returned complete edge
results for all30 known CRM subjects; this confirms associations, not identity,
employment, consent or complete historical coverage. No write permission added.
