# Disabled ten-company Tavily Basic pilot

The adapter is implemented and disabled by default. Earlier authorized ten-query and three-query pilots completed without confirmed current decision-makers or person-linked professional email candidates. Further Tavily requests are paused; this commit does not renew their authorization. The command below only prepares/inspects the existing ledger and does not reset it:

```sh
python3 scripts/source_batch.py --project PROJECT tavily-pilot --workbook WORKBOOK.xlsx
```

The existing first ten rows are fixed in original order. A private ledger records only row IDs, attempt status, dates and one reserved credit per attempt. It is never reset by preparation. A search is reserved before dispatch, so crashes, failures and restarts cannot repeat a company's query. Basic search has a documented one-credit cost; at most ten attempts are allowed. Errors or unexpected reported usage stop further queries. No retries, advanced mode, auto-parameters, answers, extraction or crawling API calls are enabled.

Live execution additionally requires explicit authorization recorded under `tavily_pilot_authorization` (`approved: true`, `terms_confirmed: true`, and an actual `evidence_ref`), a securely configured `TAVILY_API_KEY` restricted to `api.tavily.com`, and the explicit `--execute` flag. Preparation does not set approval or read credentials. Historical authorization/usage records may remain in an existing private project; they are not fresh authorization to retry or expand the pilot. Do not reset ledgers or use `--execute` without a new explicit authorization.

Responses, snippets, titles and discovery URL queues stay in memory only. They are never sent to the project cache, workbook, reports or logs. Separately fetched public pages must pass existing access/robots controls. Only supported publication facts and necessary original-source evidence are retained; inaccessible pages and source URLs without independently obtained facts are not retained as evidence. Newly discovered people remain provisional, with current-employment, HubSpot and Dripify checks pending. No paid email verification, CRM writes or outreach occurs.

Before any newly authorized live testing, reconfirm that the applicable Tavily terms permit transient URL discovery for this internal prospect-research use and subsequent independent public-source fact collection. Avoiding result storage does not itself establish permission. Confirm that original-source evidence retention is permitted; source access restrictions still apply. No general result-caching or indefinite provider-output retention right is assumed.

The pilot measures relevant named candidates, exact personal LinkedIn identifiers, independently supported current-role evidence, person-linked professional email candidates, unresolved checks and actual Basic search usage. It cannot promise 3–5 confirmed people per company, verify deliverability without supported checks, or clear outreach. The existing workbook, source order, master progress, ten bounce blocks and incomplete-history warnings remain intact.
