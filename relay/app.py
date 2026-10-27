"""Authenticated relay for fixed Findymail and NeverBounce endpoints."""
import hmac
import json
import os
import urllib.error
import urllib.request

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@app.before_request
def authenticate():
    if request.path == '/health' and request.method == 'GET':
        return None
    token = os.environ.get('RELAY_TOKEN', '')
    if not token:
        return jsonify(error='Relay authentication is not configured'), 503
    expected = ('Bearer ' + token).encode('utf-8')
    supplied = request.headers.get('Authorization', '').encode('utf-8')
    if not hmac.compare_digest(supplied, expected):
        return jsonify(error='Unauthorized'), 401


@app.errorhandler(Exception)
def handle_error(exc):
    if isinstance(exc, HTTPException):
        return jsonify(error=exc.name), exc.code
    # Never echo exceptions, request headers, or provider error bodies.
    return jsonify(error='Internal server error'), 500


def redact(value):
    """Remove credentials even if a provider echoes them in its JSON."""
    if isinstance(value, str):
        for name in ('FINDYMAIL_API_KEY', 'NEVERBOUNCE_API_KEY', 'RELAY_TOKEN'):
            secret = os.environ.get(name)
            if secret:
                value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {redact(key): redact(item) for key, item in value.items()}
    return value


def upstream(url, variable, fields, neverbounce=False, method='POST'):
    key = os.environ.get(variable, '')
    if not key.strip():
        return jsonify(error='Provider credential is not configured'), 503
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    payload = dict(fields)
    if neverbounce:
        payload['key'] = key
    else:
        headers['Authorization'] = 'Bearer ' + key
    body = json.dumps(payload).encode('utf-8') if method == 'POST' else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            with exc:
                content_type = exc.headers.get_content_type() if exc.headers else ''
                error = json.load(exc) if content_type == 'application/json' else None
            if (400 <= exc.code <= 599 and isinstance(error, dict)
                    and any(field in error for field in ('error', 'errors', 'message', 'status'))):
                # Preserve status, but never forward arbitrary provider content,
                # HTML embedded in JSON, headers, or echoed credentials.
                return jsonify(error='Provider HTTP error'), exc.code
        except (ValueError, UnicodeError, OSError):
            pass
        return jsonify(error='Provider HTTP error'), 502
    except (TimeoutError, urllib.error.URLError, OSError):
        return jsonify(error='Provider connection failed or timed out'), 502
    except (ValueError, UnicodeError):
        return jsonify(error='Provider returned invalid JSON'), 502
    return jsonify(redact(data))


def inputs(*names):
    data = request.get_json()
    if not isinstance(data, dict) or set(data) != set(names):
        return None
    if any(not isinstance(data[name], str) or not data[name].strip() for name in names):
        return None
    return {name: data[name].strip() for name in names}


@app.get('/health')
def health():
    return jsonify(ok=True)


@app.post('/findymail/find')
def findymail_find():
    fields = inputs('name', 'domain')
    if fields is None:
        return jsonify(error='Provide only nonempty name and domain strings'), 400
    return upstream('https://app.findymail.com/api/search/name', 'FINDYMAIL_API_KEY', fields)


@app.get('/findymail/credits')
def findymail_credits():
    return upstream('https://app.findymail.com/api/credits?type=finder',
                    'FINDYMAIL_API_KEY', {}, method='GET')


@app.post('/findymail/verify')
def findymail_verify():
    fields = inputs('email')
    if fields is None:
        return jsonify(error='Provide only a nonempty email string'), 400
    return upstream('https://app.findymail.com/api/verify', 'FINDYMAIL_API_KEY', fields)


@app.get('/neverbounce/account')
def neverbounce_account():
    return upstream('https://api.neverbounce.com/v4.2/account/info',
                    'NEVERBOUNCE_API_KEY', {}, neverbounce=True)


@app.post('/neverbounce/check')
def neverbounce_check():
    fields = inputs('email')
    if fields is None:
        return jsonify(error='Provide only a nonempty email string'), 400
    return upstream('https://api.neverbounce.com/v4.2/single/check',
                    'NEVERBOUNCE_API_KEY', fields, neverbounce=True)
