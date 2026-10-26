# Email API helpers

Python 3 standard library only. Credentials are read exclusively from
`FINDYMAIL_API_KEY` and `NEVERBOUNCE_API_KEY`; configure them securely in the
environment, never in files or command arguments.

The commands below make live requests and may consume credits. They are usage
examples only: follow the existing skill's paid-credit approval rules before use.

```sh
cd /workspace/Envision
python3 scripts/findymail.py find --name 'Example Person' --domain example.com
python3 scripts/findymail.py verify --email person@example.com
python3 scripts/neverbounce.py --email person@example.com
```

Each command emits JSON: `{ "ok": true, "service": "…", "operation": "…",
"data": … }` contains the provider's JSON response. Provider-level failures may
appear in `data`; `ok` indicates successful HTTP/JSON transport, not email validity.
Missing credentials, invalid arguments, HTTP errors, invalid JSON, and connection
failures produce `{ "ok": false, "service": "…", "error": "…" }` and exit 1.
Help exits 0. Requests time out after 30 seconds, never retry automatically, retain
TLS verification, and refuse redirects. Error response bodies and exception
text are suppressed; credential values are redacted from returned JSON.
NeverBounce sends the environment credential unchanged as the `key` value in a
JSON POST body, separately from the email input, for Codex HTTP-secret substitution.

These helpers do not rank contacts, interpret catch-all results, spend credits
automatically, or write to HubSpot. The sourcing methodology and approval rules in
`../SKILL.md` still apply unchanged.

Offline tests (no API requests or real credentials):

```sh
python3 -B -m unittest discover -s tests -v
```
