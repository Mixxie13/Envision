"""Offline request contract and secret-handling tests."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import email_api


class HelpersTest(unittest.TestCase):
    def call(self, service, operation, **fields):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = io.StringIO('{"result":"valid"}')
        with patch.dict('os.environ', {'FINDYMAIL_API_KEY': 'test-secret',
                                     'NEVERBOUNCE_API_KEY': 'mock-placeholder:+/=%&'}, clear=True), \
             patch.object(email_api.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = response
            result = email_api.request(service, operation, **fields)
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(build.return_value.open.call_args.kwargs['timeout'], 30)
            self.assertTrue(result['ok'])
            return req

    def test_findymail_find(self):
        req = self.call('findymail', 'find', name='Example Person', domain='example.com')
        self.assertEqual(req.full_url, 'https://app.findymail.com/api/search/name')
        self.assertEqual(req.method, 'POST')
        self.assertEqual(req.get_header('Authorization'), 'Bearer test-secret')
        self.assertEqual(json.loads(req.data), {'name': 'Example Person', 'domain': 'example.com'})

    def test_findymail_verify(self):
        req = self.call('findymail', 'verify', email='person@example.com')
        self.assertEqual(req.full_url, 'https://app.findymail.com/api/verify')
        self.assertEqual(json.loads(req.data), {'email': 'person@example.com'})

    def test_neverbounce(self):
        email = 'person+tag&test@example.com'
        req = self.call('neverbounce', 'verify', email=email,
                        key='user-input-must-not-override-environment')
        self.assertEqual(req.full_url, 'https://api.neverbounce.com/v4.2/single/check')
        self.assertEqual(req.method, 'POST')
        self.assertEqual(req.get_header('Content-type'), 'application/json')
        self.assertEqual(json.loads(req.data),
                         {'key': 'mock-placeholder:+/=%&', 'email': email})
        self.assertIn(b'mock-placeholder:+/=%&', req.data)

    def test_missing_credentials_never_connect(self):
        for service in ('findymail', 'neverbounce'):
            with patch.dict('os.environ', {}, clear=True), \
                 patch.object(email_api.urllib.request, 'build_opener') as build:
                with self.assertRaisesRegex(email_api.ClientError, 'Missing credential'):
                    email_api.request(service, 'verify', email='person@example.com')
                build.assert_not_called()

    def test_http_errors_hide_secrets(self):
        with patch.dict('os.environ', {'FINDYMAIL_API_KEY': 'test-secret'}, clear=True), \
             patch.object(email_api.urllib.request, 'build_opener') as build:
            build.return_value.open.side_effect = urllib.error.HTTPError(
                'https://example.com/test-secret', 401, 'test-secret', {}, None)
            with self.assertRaises(email_api.ClientError) as caught:
                email_api.request('findymail', 'verify', email='person@example.com')
            self.assertNotIn('test-secret', str(caught.exception))
            self.assertIn('401', str(caught.exception))

    def test_redaction_and_redirects(self):
        self.assertEqual(email_api.redact({'test-secret': ['echo test-secret']}, 'test-secret'),
                         {'[REDACTED]': ['echo [REDACTED]']})
        self.assertIsNone(email_api.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))

    def test_cli_errors_are_json(self):
        with patch.object(sys, 'argv', ['findymail.py', 'verify', '--email', 'person@example.com']), \
             patch.dict('os.environ', {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(email_api.main('findymail'), 1)
            self.assertFalse(json.loads(out.getvalue())['ok'])

    def test_invalid_json_and_connection_errors(self):
        for failure in (ValueError('test-secret'), urllib.error.URLError('test-secret')):
            with patch.dict('os.environ', {'FINDYMAIL_API_KEY': 'test-secret'}, clear=True), \
                 patch.object(email_api.urllib.request, 'build_opener') as build:
                build.return_value.open.side_effect = failure
                with self.assertRaises(email_api.ClientError) as caught:
                    email_api.request('findymail', 'verify', email='person@example.com')
                self.assertNotIn('test-secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
