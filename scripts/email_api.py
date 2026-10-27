"""Small JSON-only API client; credentials stay in memory and are never logged."""
import argparse
import json
import os
import urllib.error
import urllib.request


class ClientError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials to a redirect destination.


def redact(value, key):
    if isinstance(value, str):
        return value.replace(key, '[REDACTED]')
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, dict):
        return {str(redact(k, key)): redact(v, key) for k, v in value.items()}
    return value


def request(service, operation, **fields):
    key = os.environ.get('RELAY_TOKEN')
    if not key or not key.strip():
        raise ClientError('Missing credential: set RELAY_TOKEN in the environment.')
    method, path, required = {
        ('findymail', 'find'): ('POST', '/findymail/find', {'name', 'domain'}),
        ('findymail', 'verify'): ('POST', '/findymail/verify', {'email'}),
        ('findymail', 'credits'): ('GET', '/findymail/credits', set()),
        ('neverbounce', 'account'): ('GET', '/neverbounce/account', set()),
        ('neverbounce', 'verify'): ('POST', '/neverbounce/check', {'email'}),
    }[(service, operation)]
    if set(fields) != required:
        raise ClientError('Invalid fields for this operation.')
    req = urllib.request.Request(
        'https://envision-email-relay.onrender.com' + path,
        data=json.dumps(fields).encode() if method == 'POST' else None,
        headers={'Authorization': 'Bearer ' + key,
                 'Content-Type': 'application/json', 'Accept': 'application/json'},
        method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ClientError(f'API HTTP error {exc.code}; response details suppressed.') from None
    except (ValueError, UnicodeError):
        raise ClientError('API returned invalid JSON.') from None
    except (OSError, urllib.error.URLError):
        raise ClientError('API connection failed or timed out; details suppressed.') from None
    return {'ok': True, 'service': service, 'operation': operation,
            'data': redact(data, key)}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo arbitrary command-line input.
        raise ClientError('Invalid arguments; use --help for usage.')


def main(service):
    parser = Parser(description='JSON API helper. Requests may consume paid credits; obtain required approval first.')
    if service == 'findymail':
        sub = parser.add_subparsers(dest='operation', required=True, parser_class=Parser)
        sub.add_parser('credits')
        find = sub.add_parser('find')
        find.add_argument('--name', required=True, help='Full name')
        find.add_argument('--domain', required=True, help='Company domain')
        verify = sub.add_parser('verify')
        verify.add_argument('--email', required=True)
    else:
        parser.add_argument('operation', nargs='?', choices=('verify', 'account'), default='verify')
        parser.add_argument('--email')
    try:
        args = vars(parser.parse_args())
        operation = args.pop('operation')
        if service == 'neverbounce':
            if operation == 'account':
                if args['email'] is not None:
                    raise ClientError('Account info does not accept an email.')
                args.pop('email')
            elif args['email'] is None:
                raise ClientError('Verification requires --email.')
        if any(not value.strip() for value in args.values()):
            raise ClientError('Input fields must not be blank.')
        result = request(service, operation, **args)
    except ClientError as exc:
        print(json.dumps({'ok': False, 'service': service, 'error': str(exc)}))
        return 1
    except Exception:
        print(json.dumps({'ok': False, 'service': service,
                          'error': 'Request failed; details suppressed to protect credentials.'}))
        return 1
    print(json.dumps(result))
    return 0
