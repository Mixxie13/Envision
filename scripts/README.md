# Email API helpers

Python 3 standard library only. Credentials are read exclusively from
`RELAY_TOKEN`; configure it securely in the environment, never in files or command
arguments. Requests go only to `https://envision-email-relay.onrender.com`, using
`Authorization: Bearer <RELAY_TOKEN>`. Provider credentials
(`FINDYMAIL_API_KEY` and `NEVERBOUNCE_API_KEY`) belong only on Render; they are
not read or needed by these helpers. Do not configure them for Codex-side execution.

The commands below make live requests and may consume credits. They are usage
examples only: follow the existing skill's paid-credit approval rules before use.

```sh
cd /workspace/Envision
python3 scripts/findymail.py find --name 'Example Person' --domain example.com
python3 scripts/findymail.py verify --email person@example.com
python3 scripts/neverbounce.py --email person@example.com
```

Free account/credit checks (no finder or verification credits consumed):

```sh
python3 scripts/findymail.py credits
python3 scripts/neverbounce.py account
```

`python3 scripts/neverbounce.py verify --email person@example.com` is also
supported; the existing `--email` command remains compatible.

| Operation | Relay method and path |
| --- | --- |
| Findymail find | `POST /findymail/find` |
| Findymail verify | `POST /findymail/verify` |
| Findymail credits | `GET /findymail/credits` |
| NeverBounce account | `GET /neverbounce/account` |
| NeverBounce verify | `POST /neverbounce/check` |

Each command emits JSON: `{ "ok": true, "service": "…", "operation": "…",
"data": … }` contains the provider's JSON response. Provider-level failures may
appear in `data`; `ok` indicates successful HTTP/JSON transport, not email validity.
Missing credentials, invalid arguments, HTTP errors, invalid JSON, and connection
failures produce `{ "ok": false, "service": "…", "error": "…" }` and exit 1.
Help exits 0. Requests time out after 30 seconds, never retry automatically, retain
TLS verification, and refuse redirects. Error response bodies and exception
text are suppressed; credential values are redacted from returned JSON.
The relay token is sent only in the Authorization header, never in request bodies
or URLs. Only Render adds provider credentials to upstream requests.

These helpers do not rank contacts, interpret catch-all results, spend credits
automatically, or write to HubSpot. The sourcing methodology and approval rules in
`../SKILL.md` still apply unchanged.

Offline tests (no API requests or real credentials):

```sh
python3 -B -m unittest discover -s tests -v
```
