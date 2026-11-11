# Email API relay

Requires Python 3.10+. From the repository root:

```sh
python3 -m venv /tmp/envision-relay-venv
/tmp/envision-relay-venv/bin/pip install -r relay/requirements.txt
```

Securely inject `FINDYMAIL_API_KEY`, `NEVERBOUNCE_API_KEY`, and a strong random
`RELAY_TOKEN` through your deployment's environment or secret manager. Never put
credentials in source, command arguments, or logs. Missing credentials fail closed.

Dripify read-only routes use `DRIPIFY_API_KEY`, already configured on Render.
Keep this key server-side on Render; Codex callers need only `RELAY_TOKEN`.

Start from the repository root:

```sh
/tmp/envision-relay-venv/bin/gunicorn --chdir relay --bind 127.0.0.1:8000 app:app
```

Use an HTTPS reverse proxy for remote access. Keep debug mode disabled and ensure
the proxy does not log Authorization headers or request bodies. Gunicorn access
logging is disabled by default. The relay itself does not log credentials.

All responses are JSON. `GET /health` returns `{"ok": true}` without authentication.
Every other route requires `Authorization: Bearer <RELAY_TOKEN>`.

| Relay route | JSON body | Fixed upstream endpoint (POST unless noted) |
| --- | --- | --- |
| `POST /findymail/find` | `{"name":"Full Name","domain":"company.com"}` | `https://app.findymail.com/api/search/name` |
| `POST /findymail/verify` | `{"email":"person@company.com"}` | `https://app.findymail.com/api/verify` |
| `GET /findymail/credits` | None | GET `https://app.findymail.com/api/credits?type=finder` |
| `GET /neverbounce/account` | None | `https://api.neverbounce.com/v4.2/account/info` |
| `POST /neverbounce/check` | `{"email":"person@company.com"}` | `https://api.neverbounce.com/v4.2/single/check` |
| `GET /dripify/campaigns` | None | GET `https://api.dripify.com/v1/open-api/campaigns` |
| `POST /dripify/leads/search` | `{"email":"person@company.com"}` and/or `{"linkedinUrl":"https://www.linkedin.com/in/example"}` | `https://api.dripify.com/v1/open-api/leads/search` |

The campaign-list and lead-search routes require the existing relay Bearer authentication. The relay
sends `DRIPIFY_API_KEY` only in the upstream `X-Api-Key` header to the fixed
`api.dripify.com` host, along with `Accept: application/json` and
`User-Agent: Envision-Relay/1.0` to avoid the observed Cloudflare 1010 rejection.
Lead search accepts either field or both in one JSON object;
empty objects, unknown fields, and blank or non-string values are rejected.
The LinkedIn URL is a search value, never a destination fetched by the relay.
These routes only list campaigns or search leads; no Dripify write/upload routes
are provided. They share the redirect blocking, verified TLS, 30-second timeout,
credential redaction, and sanitized HTTP-error handling described below.
No live Dripify calls were made during development.

Findymail credentials are sent in the upstream Authorization header. NeverBounce
credentials are sent only in the server-side JSON POST body, including for account
info; they are never placed in URL query strings. Unknown input fields are rejected.
Upstream redirects are refused, TLS verification stays enabled, and each request
has a 30-second timeout with no retries. Request bodies are limited to 16 KiB.

Successful responses contain the provider JSON, with any echoed credential values
redacted. Provider-level statuses must still be interpreted by the caller. Upstream
HTTP errors with an `application/json` object containing `error`, `errors`,
`message`, or `status` preserve the provider's 4xx/5xx status (including 402 and
429), returning only `{"error":"Provider HTTP error"}`. Provider error content
and headers are never forwarded. Other upstream HTTP errors (including HTML and
malformed JSON), transport failures, and malformed success JSON return generic
JSON errors with status 502; missing configuration returns 503. Invalid JSON/input
returns a JSON 4xx error.

`GET /findymail/credits` checks finder credits without consuming finder or verifier
credits. It requires relay authentication and uses the server-side Findymail key.

Calls may consume paid credits. Follow the existing sourcing skill's approval rules
before making live requests. No live requests were made during development.

### Read-only Dripify expansion — patch awaiting approval

Render settings stay unchanged: branch `main`, root `relay/`, build
`pip install -r requirements.txt`, start **`gunicorn app`**. The code exports
`application = app` so Gunicorn's default WSGI name loads the same Flask app;
`app:app` also remains valid. No separate server/application is created.

These additional GET routes were checked against the official OpenAPI embedded
at https://api.dripify.com/ (snapshot reviewed 2026-11-11). Every upstream URL
starts with the fixed `https://api.dripify.com/v1/open-api/` prefix:

| New relay route | Upstream suffix | Returned provider JSON | Accepted query |
| --- | --- | --- | --- |
| `/dripify/leads` | `leads` | Visible-lead summary page: `items`, `limit`, optional `nextCursor` | `cursor`, `limit` |
| `/dripify/leads/<numeric-id>` | `leads/<id>` | Lead detail, including `leadInCampaigns` membership observations when supplied | None |
| `/dripify/leads/<numeric-id>/activity` | `leads/<id>/activity` | Activity-event page with available campaign/event/time/error observations | `cursor`, `limit` |
| `/dripify/leads/<numeric-id>/conversation` | `leads/<id>/conversation` | Available conversation metadata, including `conversationId` when supplied | None |
| `/dripify/conversations/<id>/messages` | `conversations/<encoded-id>/messages` | Message page with available direction/content/timestamps and `nextCursor` | `cursor` only |

Every new route requires the existing Bearer token. All upstream operations are
GET with no request body; there is no send, enrollment, campaign-update, upload,
remove or record-write path. GET message history does not send a message.
No provider credentials are accepted from callers or returned in responses.

Pagination is one provider page per relay request. The relay does not fetch all
pages, interpret statuses, select campaigns, prove absence, or emit a fabricated
`coverage_complete`/`outreach_ready` flag. The listing intentionally has no campaign
filter. Preserve raw provider JSON/cursors; validate completeness in the caller.
`limit` accepts integers 1–100 and `cursor` nonempty strings of at most128 characters
without control characters. Duplicate/unknown query parameters are rejected.
Messages reject `limit`: the documented page size is up to20 and history terminates
only at an empty page without `nextCursor`, not merely a short message page.
Lead IDs are bounded to the nonnegative signed-64-bit range declared by the provider;
encoded conversation path segments cannot select another host. Oversized numeric
query strings and control characters are rejected before any provider request.

Existing security/error behavior is shared unchanged: fixed host, `X-Api-Key`,
`Accept: application/json`, `User-Agent: Envision-Relay/1.0`, verified TLS, blocked
redirects,30-second timeout, no retries, credential redaction and generic sanitized
JSON errors. A provider404 remains an error, never an empty successful result or evidence of
absence/completeness. Safe JSON provider errors preserve their HTTP status; HTML/non-JSON,
malformed success JSON and connection failures return502. Rate-limited calls must
stop on429. Provider error headers (including Retry-After) are not forwarded by the
existing sanitizer; do not retry blindly or infer a reset time. Apply the verified
provider/key limits during controlled collection; no bulk scan starts on deployment.

**Unverified:** production authorization for these reads; all-owner/team visibility;
historical/archived/deleted retention; membership-state semantics; activity/reply
completeness; response shape and message pagination in this account. An unfiltered
visible-lead listing is not proof of complete account-wide history. A no-match or
empty response never establishes outreach permission. Existing suppression and
bounce blocks stay in the separate eligibility workflow.

Standalone offline patch tests (install `relay/requirements.txt` first):

```sh
python3 -B -m unittest discover -s tests -p 'test_relay_dripify.py' -v
# From relay/: validate the configured Render entry point without starting a server.
gunicorn --check-config app
```

Mocks use synthetic credentials and make no network requests. No live provider
validation, secret changes or deployment is performed by these tests. The
production source at9852e61 and current main lack the new routes; a new approved
commit containing this patch must reach main before Render can deploy it.
