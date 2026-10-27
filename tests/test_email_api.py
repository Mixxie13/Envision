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
        with patch.dict('os.environ', {'RELAY_TOKEN': 'test-secret'}, clear=True), \
             patch.object(email_api.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = response
            result = email_api.request(service, operation, **fields)
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(build.return_value.open.call_args.kwargs['timeout'], 30)
            self.assertTrue(result['ok'])
            self.assertEqual(result['service'], service)
            self.assertEqual(result['operation'], operation)
            self.assertEqual(result['data'], {'result': 'valid'})
            return req

    def test_free_checks(self):
        for service, operation, path in (
                ('findymail', 'credits', '/findymail/credits'),
                ('neverbounce', 'account', '/neverbounce/account')):
            req = self.call(service, operation)
            self.assertEqual(req.full_url, 'https://envision-email-relay.onrender.com' + path)
            self.assertEqual(req.method, 'GET')
            self.assertIsNone(req.data)
            self.assertEqual(req.get_header('Authorization'), 'Bearer test-secret')

    def test_provider_keys_cannot_replace_relay_token(self):
        with patch.dict('os.environ', {'FINDYMAIL_API_KEY': 'unused',
                                      'NEVERBOUNCE_API_KEY': 'unused'}, clear=True), \
             patch.object(email_api.urllib.request, 'build_opener') as build:
            with self.assertRaisesRegex(email_api.ClientError, 'RELAY_TOKEN'):
                email_api.request('findymail', 'credits')
            build.assert_not_called()

    def test_cli_operations(self):
        for service, argv, operation, fields in (
                ('findymail', ['credits'], 'credits', {}),
                ('neverbounce', ['account'], 'account', {}),
                ('neverbounce', ['--email', 'person@example.com'], 'verify',
                 {'email': 'person@example.com'}),
                ('neverbounce', ['verify', '--email', 'person@example.com'], 'verify',
                 {'email': 'person@example.com'})):
            with patch.object(sys, 'argv', ['helper.py'] + argv), \
                 patch.object(email_api, 'request', return_value={'ok': True}) as call, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(email_api.main(service), 0)
                self.assertTrue(json.loads(out.getvalue())['ok'])
                call.assert_called_once_with(service, operation, **fields)

    def test_invalid_arguments_never_connect(self):
        for argv in ([], ['account', '--email', 'person@example.com'],
                     ['--email', ' '], ['--url', 'https://example.com']):
            with patch.object(sys, 'argv', ['neverbounce.py'] + argv), \
                 patch.object(email_api, 'request') as call, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(email_api.main('neverbounce'), 1)
                self.assertFalse(json.loads(out.getvalue())['ok'])
                call.assert_not_called()

    def test_echoed_relay_token_redacted(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = io.StringIO('{"test-secret":["test-secret"]}')
        with patch.dict('os.environ', {'RELAY_TOKEN': 'test-secret'}, clear=True), \
             patch.object(email_api.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = response
            result = email_api.request('findymail', 'credits')
            self.assertEqual(result['data'], {'[REDACTED]': ['[REDACTED]']})

    def test_findymail_find(self):
        req = self.call('findymail', 'find', name='Example Person', domain='example.com')
        self.assertEqual(req.full_url, 'https://envision-email-relay.onrender.com/findymail/find')
        self.assertEqual(req.method, 'POST')
        self.assertEqual(req.get_header('Authorization'), 'Bearer test-secret')
        self.assertEqual(json.loads(req.data), {'name': 'Example Person', 'domain': 'example.com'})

    def test_findymail_verify(self):
        req = self.call('findymail', 'verify', email='person@example.com')
        self.assertEqual(req.full_url, 'https://envision-email-relay.onrender.com/findymail/verify')
        self.assertEqual(json.loads(req.data), {'email': 'person@example.com'})

    def test_neverbounce(self):
        email = 'person+tag&test@example.com'
        req = self.call('neverbounce', 'verify', email=email)
        self.assertEqual(req.full_url, 'https://envision-email-relay.onrender.com/neverbounce/check')
        self.assertEqual(req.method, 'POST')
        self.assertEqual(req.get_header('Content-type'), 'application/json')
        self.assertEqual(json.loads(req.data),
                         {'email': email})
        self.assertEqual(req.get_header('Authorization'), 'Bearer test-secret')
        self.assertNotIn(b'test-secret', req.data)

    def test_missing_credentials_never_connect(self):
        for service in ('findymail', 'neverbounce'):
            with patch.dict('os.environ', {}, clear=True), \
                 patch.object(email_api.urllib.request, 'build_opener') as build:
                with self.assertRaisesRegex(email_api.ClientError, 'Missing credential'):
                    email_api.request(service, 'verify', email='person@example.com')
                build.assert_not_called()

    def test_http_errors_hide_secrets(self):
        with patch.dict('os.environ', {'RELAY_TOKEN': 'test-secret'}, clear=True), \
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
            with patch.dict('os.environ', {'RELAY_TOKEN': 'test-secret'}, clear=True), \
                 patch.object(email_api.urllib.request, 'build_opener') as build:
                build.return_value.open.side_effect = failure
                with self.assertRaises(email_api.ClientError) as caught:
                    email_api.request('findymail', 'verify', email='person@example.com')
                self.assertNotIn('test-secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
