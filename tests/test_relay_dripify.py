"""Standalone relay deployment tests; synthetic credentials, mocked HTTP only."""
import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import urllib.error


class DripifyRelayDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('relay_deployment_test', Path(__file__).resolve().parents[1] / 'relay/app.py')
        cls.relay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.relay)

    def setUp(self):
        env = patch.dict('os.environ', {'RELAY_TOKEN': 'fake-relay-secret', 'DRIPIFY_API_KEY': 'fake-provider-secret'}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        transport = patch.object(self.relay.urllib.request, 'build_opener')
        self.opener = transport.start()
        self.addCleanup(transport.stop)
        self.open = self.opener.return_value.open
        self.open.return_value = self.response({'items': [], 'limit': 25})
        self.client = self.relay.app.test_client()
        self.auth = {'Authorization': 'Bearer fake-relay-secret'}

    @staticmethod
    def response(data):
        result = MagicMock()
        result.__enter__.return_value = io.StringIO(json.dumps(data))
        return result

    @staticmethod
    def http_error(status, content_type, body):
        headers = Message()
        headers['Content-Type'] = content_type
        return urllib.error.HTTPError('https://api.dripify.com/fixed', status, 'suppressed', headers, io.BytesIO(body))

    def test_wsgi_alias_preserves_existing_start_command(self):
        self.assertIs(self.relay.application, self.relay.app)

    def test_all_five_reads_are_registered_get_only(self):
        paths = ['/dripify/leads', '/dripify/leads/1', '/dripify/leads/1/activity', '/dripify/leads/1/conversation', '/dripify/conversations/opaque%3Aid/messages']
        upstream_paths = ['leads', 'leads/1', 'leads/1/activity', 'leads/1/conversation', 'conversations/opaque%3Aid/messages']
        for path, suffix in zip(paths, upstream_paths):
            with self.subTest(path=path):
                self.open.return_value = self.response({'items': [], 'limit': 25})
                self.assertEqual(self.client.get(path, headers=self.auth).status_code, 200)
                self.assertEqual(self.open.call_args.args[0].full_url, 'https://api.dripify.com/v1/open-api/' + suffix)
                self.assertEqual(self.client.post(path, headers=self.auth).status_code, 405)
                self.assertEqual(self.client.patch(path, headers=self.auth).status_code, 405)
                self.assertEqual(self.client.delete(path, headers=self.auth).status_code, 405)
        self.assertEqual(self.open.call_count, len(paths))
        self.assertTrue(all(c.args[0].method == 'GET' and c.args[0].data is None for c in self.open.call_args_list))

    def test_missing_wrong_or_malformed_bearer_never_reaches_provider(self):
        for headers in ({}, {'Authorization': 'Bearer wrong'}, {'Authorization': 'fake-relay-secret'}, {'Authorization': 'Basic fake-relay-secret'}):
            self.assertEqual(self.client.get('/dripify/leads', headers=headers).status_code, 401)
        self.open.assert_not_called()

    def test_health_stays_public(self):
        self.assertEqual(self.client.get('/health').json, {'ok': True})
        self.open.assert_not_called()

    def test_missing_relay_and_provider_keys_fail_closed(self):
        with patch.dict('os.environ', {'RELAY_TOKEN': ''}):
            self.assertEqual(self.client.get('/dripify/leads', headers=self.auth).status_code, 503)
        with patch.dict('os.environ', {'DRIPIFY_API_KEY': ''}):
            self.assertEqual(self.client.get('/dripify/leads', headers=self.auth).status_code, 503)
        self.open.assert_not_called()

    def test_fixed_headers_host_get_timeout_and_redirect_handler(self):
        self.client.get('/dripify/leads?limit=100&cursor=opaque%2Bvalue', headers=self.auth)
        req = self.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.dripify.com/v1/open-api/leads?cursor=opaque%2Bvalue&limit=100')
        self.assertEqual(req.get_header('X-api-key'), 'fake-provider-secret')
        self.assertEqual(req.get_header('Accept'), 'application/json')
        self.assertEqual(req.get_header('User-agent'), 'Envision-Relay/1.0')
        self.assertIsNone(req.get_header('Authorization'))
        self.assertEqual(self.open.call_args.kwargs['timeout'], 30)
        handler = self.opener.call_args.args[0]
        self.assertIsInstance(handler, self.relay.NoRedirect)
        self.assertIsNone(handler.redirect_request(None, None, 302, '', {}, 'https://other.example'))

    def test_pagination_validation_rejects_unknown_duplicate_and_bad_values(self):
        bad = ['limit=0', 'limit=101', 'limit=abc', 'limit=-1', 'limit=1&limit=2', 'cursor=', 'cursor=' + 'x'*129, 'cursor=%0A', 'url=https://untrusted.example', 'campaignId=1']
        for query in bad:
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/dripify/leads?' + query, headers=self.auth).status_code, 400)
        self.open.assert_not_called()

    def test_message_page_accepts_cursor_but_not_limit(self):
        response = self.client.get('/dripify/conversations/opaque%3Aid/messages?cursor=next', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.open.call_args.args[0].full_url, 'https://api.dripify.com/v1/open-api/conversations/opaque%3Aid/messages?cursor=next')
        self.open.reset_mock()
        self.assertEqual(self.client.get('/dripify/conversations/id/messages?limit=20', headers=self.auth).status_code, 400)
        self.open.assert_not_called()

    def test_nonpaged_reads_reject_query_and_bad_ids(self):
        for path in ('/dripify/leads/1?cursor=x', '/dripify/leads/-1', '/dripify/conversations/' + 'x'*256 + '/messages'):
            self.assertIn(self.client.get(path, headers=self.auth).status_code, (400, 404))
        self.open.assert_not_called()

    def test_page_json_passes_through_without_claiming_coverage(self):
        provider = {'items': [{'id': 1}], 'limit': 25, 'nextCursor': 'opaque'}
        self.open.return_value = self.response(provider)
        response = self.client.get('/dripify/leads', headers=self.auth)
        self.assertEqual(response.json, provider)
        self.assertNotIn('coverage_complete', response.json)
        self.assertNotIn('outreach_ready', response.json)

    def test_membership_and_history_are_returned_without_status_reinterpretation(self):
        provider = {'id': 1, 'leadInCampaigns': [{'campaignId': 2, 'leadInCampaignStatus': 'PROCESSING_PAUSED'}]}
        self.open.return_value = self.response(provider)
        self.assertEqual(self.client.get('/dripify/leads/1', headers=self.auth).json, provider)

    def test_credentials_redacted_even_when_provider_echoes_keys(self):
        self.open.return_value = self.response({'echo': ['fake-relay-secret', 'fake-provider-secret'], 'fake-provider-secret': 'value'})
        response = self.client.get('/dripify/leads', headers=self.auth)
        self.assertNotIn('fake-relay-secret', response.text)
        self.assertNotIn('fake-provider-secret', response.text)
        self.assertIn('[REDACTED]', response.text)

    def test_safe_json_error_statuses_preserved_without_error_details(self):
        for status in (400, 401, 403, 404, 429, 500):
            self.open.side_effect = self.http_error(status, 'application/json', b'{"message":"fake-provider-secret <html>unsafe</html>"}')
            response = self.client.get('/dripify/leads', headers=self.auth)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json, {'error': 'Provider HTTP error'})

    def test_unsupported_provider_reads_never_become_empty_complete_results(self):
        for path in ('/dripify/leads', '/dripify/leads/1', '/dripify/leads/1/activity', '/dripify/leads/1/conversation', '/dripify/conversations/id/messages'):
            self.open.side_effect = self.http_error(404, 'application/json', b'{"message":"Unsupported endpoint"}')
            response = self.client.get(path, headers=self.auth)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json, {'error': 'Provider HTTP error'})
            self.assertNotIn('items', response.json)
            self.assertNotIn('coverage_complete', response.json)
            self.assertNotIn('outreach_ready', response.json)

    def test_extreme_parameters_and_control_characters_fail_before_upstream(self):
        for path in ('/dripify/leads?limit=' + '0'*5000 + '1', '/dripify/leads?cursor=%7F', '/dripify/leads/9223372036854775808', '/dripify/leads/9223372036854775808/activity', '/dripify/leads/9223372036854775808/conversation', '/dripify/conversations/%7F/messages'):
            self.assertEqual(self.client.get(path, headers=self.auth).status_code, 400)
        self.open.assert_not_called()

    def test_html_nonjson_and_redirect_errors_are_generic502(self):
        for status, typ, body in ((403, 'text/html', b'<html>Cloudflare fake-provider-secret</html>'), (500, 'application/json', b'bad json'), (302, 'application/json', b'{"message":"redirect"}')):
            self.open.side_effect = self.http_error(status, typ, body)
            response = self.client.get('/dripify/leads', headers=self.auth)
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json, {'error': 'Provider HTTP error'})

    def test_timeout_and_invalid_success_json_are_safe502(self):
        self.open.side_effect = TimeoutError('private provider detail')
        response = self.client.get('/dripify/leads', headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('private', response.text)
        self.open.side_effect = None
        response = MagicMock()
        response.__enter__.return_value = io.StringIO('<html>unsafe</html>')
        self.open.return_value = response
        self.assertEqual(self.client.get('/dripify/leads', headers=self.auth).status_code, 502)

    def test_legacy_provider_transport_contracts_are_preserved(self):
        cases = [
            ('POST', '/findymail/find', {'name': 'Example Person', 'domain': 'company.example'}, 'https://app.findymail.com/api/search/name', 'POST'),
            ('POST', '/findymail/verify', {'email': 'person@company.example'}, 'https://app.findymail.com/api/verify', 'POST'),
            ('GET', '/findymail/credits', None, 'https://app.findymail.com/api/credits?type=finder', 'GET'),
            ('GET', '/neverbounce/account', None, 'https://api.neverbounce.com/v4.2/account/info', 'POST'),
            ('POST', '/neverbounce/check', {'email': 'person@company.example'}, 'https://api.neverbounce.com/v4.2/single/check', 'POST'),
            ('GET', '/dripify/campaigns', None, 'https://api.dripify.com/v1/open-api/campaigns', 'GET'),
            ('POST', '/dripify/leads/search', {'linkedinUrl': 'https://www.linkedin.com/in/example'}, 'https://api.dripify.com/v1/open-api/leads/search', 'POST'),
        ]
        with patch.dict('os.environ', {'FINDYMAIL_API_KEY': 'fake-findy', 'NEVERBOUNCE_API_KEY': 'fake-never'}):
            for method, path, body, url, upstream_method in cases:
                self.open.return_value = self.response({'ok': True})
                response = self.client.open(path, method=method, json=body, headers=self.auth)
                self.assertEqual(response.status_code, 200)
                req = self.open.call_args.args[0]
                self.assertEqual((req.full_url, req.method), (url, upstream_method))
                if '/neverbounce/' in path:
                    self.assertEqual(json.loads(req.data)['key'], 'fake-never')
                    self.assertIsNone(req.get_header('Authorization'))
                elif '/findymail/' in path:
                    self.assertEqual(req.get_header('Authorization'), 'Bearer fake-findy')
                else:
                    self.assertEqual(req.get_header('X-api-key'), 'fake-provider-secret')

    def test_existing_findymail_neverbounce_and_dripify_search_routes_unchanged(self):
        paths = {rule.rule for rule in self.relay.app.url_map.iter_rules()}
        self.assertTrue({'/findymail/find', '/findymail/verify', '/findymail/credits', '/neverbounce/account', '/neverbounce/check', '/dripify/campaigns', '/dripify/leads/search'} <= paths)


if __name__ == '__main__':
    unittest.main()
