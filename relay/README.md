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

Both Dripify routes require the existing relay Bearer authentication. The relay
sends `DRIPIFY_API_KEY` only in the upstream `X-Api-Key` header to the fixed
`api.dripify.com` host. Lead search accepts either field or both in one JSON object;
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
