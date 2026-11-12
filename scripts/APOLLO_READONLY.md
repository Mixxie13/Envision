# Apollo-first people discovery — disabled pending account verification

The saved view **Envision — Lead Sourcing | Bridget's Master List** is a separate human workspace. This adapter does not read or change saved views, Roxy's views, saved contacts or historical records. API filters are independent of that UI view. No country/location, known-company, email-availability or saved-contact restriction is sent.

## Confirmed documentation / account unknowns

Previously retrieved official [People API Search documentation](https://docs.apollo.io/reference/people-api-search) specifies:

- `POST https://api.apollo.io/api/v1/mixed_people/api_search`, a read-only search operation.
- API-key access to that endpoint (or a master key); OAuth equivalent `mixed_people_api_search`.
- Eligible free accounts require a work-email registration and may have additional eligibility requirements. Having an Apollo account or saved view does not verify endpoint permission.
- Zero credits for People API Search. Email/phone results are not returned by this endpoint; enrichment is separate and must remain disabled.
- Title, exclusion, seniority and organization filters; page/per_page pagination. The documented display ceiling is 50,000 records, at most 100 per page and 500 pages. This is not an account rate-limit entitlement.
- Domain matching can include **current or previous employers**, so a domain query alone cannot bind a current employer.

The user configured `APOLLO_API_KEY` and authorized bounded capability probes, including an endpoint-scoped restricted key. Those attempts returned HTTP 403 gateway/access challenges; no successful account response contract was established. Further Apollo requests are blocked pending Bridget's decision. Account access, plan-specific minute/hour/day limits, full-name/profile visibility and actual response schema remain **unverified**. A fresh documentation refresh returned HTTP errors; the previously retrieved official documentation informed this implementation. No access restriction was bypassed.

## Local implementation

`scripts/apollo.py` is a narrow, fixed-host search client. It reads `APOLLO_API_KEY` only after explicit enablement, recorded live-read approval and verified company-domain evidence. It uses `X-Api-Key`, TLS verification, redirects blocked, a 20-second timeout, a 2 MiB response limit, no retries, and a stop after an HTTP/schema failure. No enrichment/reveal, phone, CRM-write, sequence, view or account-management endpoint is implemented.

The legacy collector permits at most four requests per invocation, reduced by the approval's `max_requests` (default one), with at most two pages per company/domain. Checkpoints reuse accepted pages across runs and duplicate source rows. Missing pagination totals, overlaps and incomplete pages remain pending. Cached observations stay dated; no refresh policy, account completeness or current employment is invented.

Compatibility parsing accepts a `people` list and explicit total counts if provided. Candidate field allowlisting supports IDs, first/last names, title, personal LinkedIn URL and organization fields **if present**. Actual account fields must be checked by a probe. Missing or masked last names are not reconstructed. Email and phone fields are discarded even if unexpectedly present. Unknown schemas fail closed. Fixtures are synthetic; none is represented as a captured production response.

Current-employer domain must exactly match the verified company binding, or the returned current organization ID must match a separately verified ID/domain mapping. Similar company names and past-domain matches are insufficient. Multiple current organization IDs remain ambiguous. Parent staff require verified routing and remain unassigned to the brand until brand responsibility is evidenced. They do not become brand contact records automatically.

Full, domain-bound records enter the existing local research state only after response-contract review. They remain candidates with independent employment and HubSpot/Dripify checks pending. Exact LinkedIn collisions inherit known suppression risks/blocks; name disagreement flags identity conflict. Names alone do not merge records. Existing company rows, duplicate rows and their order remain unchanged.

The ordinary research stage runs Apollo discovery first when specifically enabled, then existing source corroboration, identity and Dripify stages. For completed jobs, a separate continuation command preserves job progress:

```sh
# Safe default: no key read, search, workbook mutation or integration call.
python3 scripts/source_batch.py --project PROJECT apollo-discovery --batch 1 --workbook WORKBOOK.xlsx
```

`--execute` additionally requires recorded Apollo authorization and verified bindings. `--live-checks` is separately explicit for existing read-only HubSpot/Dripify follow-up; without it, those checks use retained evidence/pending outcomes. Normal batch runs use `--apollo-read` separately from `--live-read`. Nothing enables Apollo by default.

## ATTITUDE manual findings

The user supplied these as manual Apollo discoveries, **not API response evidence**:

- Amael Proulx-Lussier — Brand Manager: relevant provisional candidate; current role/identity not verified.
- Maude Paquin — Gestionnaire Marketing: relevant provisional candidate. Adapter preserves the French title and records Marketing Manager as its canonical role translation.
- Melissa Lusignan — VP Sales, North America: excluded from this SOP's shortlist as sales-only. Her Apollo record is not deleted or changed.

The examples are tested offline but were not inserted into the live workbook as independently verified contacts.

## Requirements for any future, separately authorized probe

1. Obtain Bridget's decision on the existing access blocker. An endpoint-scoped private key has already been tried; do not create or rotate another key or repeat the request without explicit authorization. Have the administrator establish whether API access is supported before another probe.
2. Keep any configured `APOLLO_API_KEY` in managed secrets, restricted to `api.apollo.io`. No key setup or secret change is part of this commit. Never put it in chat, source files, URLs or logs.
3. Confirm the ATTITUDE organization-domain mapping from its verified company identity and Apollo organization page. Record `apollo_company_binding:<source-row-id>` with `verified: true`, `domain: attitudeliving.com`, an actual `evidence_ref`, and the verified `organization_id` if available. Parent mappings also require `parent: true` and a `brand_routing_evidence_ref`; do not invent one from a similar name.
4. Explicitly authorize one read-only People Search capability probe. Record `apollo_read_authorization` with `approved: true`, `evidence_ref` and `max_requests: 1`. Do not mark the response contract verified before reviewing it.
5. Inspect the probe's actual IDs, name masking, titles, current organization data, LinkedIn field availability, pagination and errors. Unknown/missing identity data remains pending; never call paid enrichment automatically to fill gaps. Confirm semantic/schema support before recording `response_contract_verified: true` plus a real `schema_review_ref` and allowing candidate handoff.

Earlier probes were executed under bounded authorization and failed. The adapter remains disabled; those approvals do not authorize another attempt. If full identities are unavailable under existing access, report that limitation before considering any paid enrichment or plan change.

The coordinated `workflow.py` driver additionally requires a current verified capability record and a scoped durable read budget. A separately authorized ten-company pilot is capped at ten requests and one page per company; partial coverage remains pending. Known HTTP failures are checkpointed and not retried on restart. This does not authorize another live probe.
