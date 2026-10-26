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
    variable = {'findymail': 'FINDYMAIL_API_KEY',
                'neverbounce': 'NEVERBOUNCE_API_KEY'}[service]
    key = os.environ.get(variable)
    if not key or (service == 'findymail' and not key.strip()):
        raise ClientError(f'Missing credential: set {variable} in the environment.')
    if service == 'findymail':
        path = {'find': '/api/search/name', 'verify': '/api/verify'}[operation]
        req = urllib.request.Request(
            'https://app.findymail.com' + path,
            data=json.dumps(fields).encode(),
            headers={'Authorization': 'Bearer ' + key,
                     'Content-Type': 'application/json', 'Accept': 'application/json'},
            method='POST')
    else:
        req = urllib.request.Request(
            'https://api.neverbounce.com/v4.2/single/check',
            data=json.dumps({'key': key, 'email': fields['email']}).encode(),
            headers={'Content-Type': 'application/json',
                     'Accept': 'application/json'}, method='POST')
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
        find = sub.add_parser('find')
        find.add_argument('--name', required=True, help='Full name')
        find.add_argument('--domain', required=True, help='Company domain')
        verify = sub.add_parser('verify')
        verify.add_argument('--email', required=True)
    else:
        parser.add_argument('--email', required=True)
        parser.set_defaults(operation='verify')
    try:
        args = vars(parser.parse_args())
        operation = args.pop('operation')
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
