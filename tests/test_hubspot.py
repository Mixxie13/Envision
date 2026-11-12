"""Offline contracts for narrow HubSpot reads; all transport is mocked."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import hubspot


class HubSpotReadTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict('os.environ', {'HUBSPOT_ACCESS_TOKEN': 'testsecret'}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.transport = patch.object(hubspot.urllib.request, 'build_opener')
        self.build = self.transport.start()
        self.addCleanup(self.transport.stop)
        self.client = hubspot.Client()
        self.open = self.build.return_value.open

    def response(self, data, status=200):
        response = MagicMock()
        stream = io.StringIO(json.dumps(data))
        stream.status = status
        response.__enter__.return_value = stream
        return response

    def metadata(self, kind='contacts'):
        self.client._properties[kind] = {name: {'name': name} for name in ('email', 'domain', 'firstname')}

    def assert_error(self, category, function, *args, **kwargs):
        with self.assertRaises(hubspot.ReadError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.category, category)
        return caught.exception

    def test_fixed_host_auth_timeout_and_no_redirect_handler(self):
        self.open.return_value = self.response({'results': []})
        self.client.properties('contacts')
        req = self.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.hubapi.com/crm/v3/properties/contacts')
        self.assertEqual(req.method, 'GET')
        self.assertEqual(req.get_header('Authorization'), 'Bearer testsecret')
        self.assertEqual(req.get_header('Accept'), 'application/json')
        self.assertEqual(self.open.call_args.kwargs['timeout'], 30)
        self.assertIsInstance(self.build.call_args.args[0], hubspot.NoRedirect)
        self.assertIsNone(hubspot.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://untrusted.example'))

    def test_write_operations_and_arbitrary_paths_never_connect(self):
        for method, path in [('POST', '/crm/v3/objects/contacts'),
                             ('PATCH', '/crm/v3/objects/contacts/123'),
                             ('DELETE', '/crm/v3/objects/companies/123'),
                             ('PUT', '/crm/v4/objects/companies/1/associations/contacts'),
                             ('GET', 'https://untrusted.example/'),
                             ('GET', '/crm/v3/objects/contacts/1/../2'),
                             ('GET', '/crm/v3/objects/contacts/1?redirect=https://untrusted.example')]:
            with self.subTest(method=method, path=path), self.assertRaises(ValueError):
                self.client._request(method, path)
        self.open.assert_not_called()

    def test_contact_company_associations_are_paginated_read_only(self):
        self.open.side_effect = [self.response({'results': [{'toObjectId': 1}], 'paging': {'next': {'after': 'next'}}}), self.response({'results': [{'toObjectId': 2}]})]
        result = self.client.contact_associations('7')
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['results']), 2)
        self.assertIn('/crm/v4/objects/contacts/7/associations/companies', self.open.call_args.args[0].full_url)
        with self.assertRaises(ValueError):
            self.client.contact_associations('../7')

    def test_missing_or_blank_token_fails_before_network(self):
        for value in ('', '   '):
            with patch.dict('os.environ', {'HUBSPOT_ACCESS_TOKEN': value}, clear=True):
                client = hubspot.Client()
                self.assert_error('CREDENTIAL_MISSING', client.properties, 'contacts')
        self.open.assert_not_called()

    def test_unsupported_objects_and_record_ids_fail_locally(self):
        for kind in ('deals', 'contacts/1', 'https://untrusted.example'):
            with self.assertRaises(ValueError):
                self.client.properties(kind)
        for value in ('../1', '1?x=1', 'https://untrusted.example', '-1'):
            with self.assertRaises(ValueError):
                self.client.associations(value)
        self.open.assert_not_called()

    def test_metadata_is_discovered_before_record_fields(self):
        self.open.side_effect = [self.response({'results': [{'name': 'email'}]}),
                                 self.response({'id': '7', 'properties': {'email': 'person@example.com'}})]
        record = self.client.get('contacts', '7', ['email'])
        self.assertEqual(record['id'], '7')
        urls = [call.args[0].full_url for call in self.open.call_args_list]
        self.assertEqual(urls[0], 'https://api.hubapi.com/crm/v3/properties/contacts')
        self.assertEqual(urls[1], 'https://api.hubapi.com/crm/v3/objects/contacts/7?properties=email')

    def test_unknown_property_is_not_guessed(self):
        self.open.return_value = self.response({'results': [{'name': 'email'}]})
        with self.assertRaises(ValueError):
            self.client.get('contacts', '7', ['unverified_property'])
        self.assertEqual(self.open.call_count, 1)

    def test_invalid_or_paged_metadata_is_not_complete(self):
        for data, category in [({'results': {}, 'paging': None}, 'INCOMPLETE_PROPERTY_METADATA'),
                               ({'results': [], 'paging': {'next': {'after': '1'}}}, 'INCOMPLETE_PROPERTY_METADATA'),
                               ({'results': [{}]}, 'UNEXPECTED_PROPERTY_SCHEMA'),
                               ({'results': [{'name': ''}]}, 'UNEXPECTED_PROPERTY_SCHEMA'),
                               ({'results': [{'name': 'email'}, {'name': 'email'}]}, 'UNEXPECTED_PROPERTY_SCHEMA')]:
            with self.subTest(data=data):
                self.open.return_value = self.response(data)
                self.assert_error(category, self.client.properties, 'contacts')

    def test_record_must_match_requested_identity_and_structure(self):
        self.metadata()
        for data in ({'id': '8', 'properties': {}}, {'id': '7', 'properties': []}):
            self.open.return_value = self.response(data)
            self.assert_error('UNEXPECTED_RECORD_SCHEMA', self.client.get, 'contacts', '7', ['email'])

    def test_search_uses_exact_filters_without_freeform_url(self):
        self.metadata()
        groups = [{'filters': [{'propertyName': 'email', 'operator': 'EQ', 'value': 'person@example.com'}]}]
        self.open.return_value = self.response({'results': [], 'total': 0})
        result = self.client.search('contacts', groups, ['email'])
        req = self.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.hubapi.com/crm/v3/objects/contacts/search')
        self.assertEqual(req.method, 'POST')
        self.assertEqual(json.loads(req.data), {'filterGroups': groups, 'properties': ['email'], 'limit': 100})
        self.assertTrue(result['complete'])
        self.assertIn('token visibility only', result['coverage'])
        self.assertNotIn(b'testsecret', req.data)

    def test_search_rejects_unsupported_and_unknown_filters(self):
        self.metadata()
        groups = [[], [{'filters': []}], [{'filters': ['invalid']}], [{'filters': [{'propertyName': 'email', 'operator': 'EQ', 'value': 'person'}], 'extra': True}], [{'filters': [{'propertyName': 'email', 'operator': 'NEQ', 'value': 'person'}]}],
                  [{'filters': [{'propertyName': 'unknown', 'operator': 'EQ', 'value': 'person'}]}],
                  [{'filters': [{'propertyName': 'email', 'operator': 'EQ', 'value': ' '}]}]]
        for group in groups:
            with self.subTest(group=group), self.assertRaises(ValueError):
                self.client.search('contacts', group, ['email'])
        self.open.assert_not_called()

    def test_token_discovery_filter_is_bounded_and_not_absence_proof(self):
        self.metadata('companies')
        groups = [{'filters': [{'propertyName': 'domain', 'operator': 'CONTAINS_TOKEN', 'value': '*example*'}]}]
        self.open.return_value = self.response({'results': [], 'total': 0})
        result = self.client.search('companies', groups, ['domain'])
        self.assertEqual(json.loads(self.open.call_args.args[0].data)['filterGroups'], groups)
        self.assertIn('not global absence', result['coverage'])

    def test_history_fields_are_explicit_and_discovered(self):
        self.metadata()
        self.open.return_value = self.response({'id': '7', 'properties': {'email': None}})
        self.client.get('contacts', '7', ['email'], history_fields=['email'])
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.open.call_args.args[0].full_url).query)
        self.assertEqual(query['propertiesWithHistory'], ['email'])
        self.open.return_value = self.response({'status': 'COMPLETE', 'results': [{'id': '7', 'properties': {}}]})
        result = self.client.batch_read('contacts', ['7'], ['email'], history_fields=['email'])
        self.assertTrue(result['complete'])
        self.assertEqual(json.loads(self.open.call_args.args[0].data)['propertiesWithHistory'], ['email'])
        self.open.reset_mock()
        with self.assertRaises(ValueError):
            self.client.batch_read('contacts', ['7'], ['email'], history_fields=['invented'])
        self.open.assert_not_called()

    def test_search_paginates_and_checks_total(self):
        self.metadata()
        self.open.side_effect = [self.response({'results': [{'id': '1', 'properties': {}}], 'total': 2,
                                              'paging': {'next': {'after': 'cursor-2'}}}),
                                 self.response({'results': [{'id': '2', 'properties': {}}], 'total': 2})]
        groups = [{'filters': [{'propertyName': 'email', 'operator': 'EQ', 'value': 'person@example.com'}]}]
        result = self.client.search('contacts', groups, ['email'])
        self.assertTrue(result['complete'])
        self.assertEqual(result['pages'], 2)
        self.assertEqual([row['id'] for row in result['results']], ['1', '2'])
        self.assertEqual(json.loads(self.open.call_args_list[1].args[0].data)['after'], 'cursor-2')

    def test_missing_changed_or_capped_totals_fail_closed(self):
        for pages, category in [([{'results': []}], 'TOTAL_UNAVAILABLE'),
                                ([{'results': [], 'total': True}], 'TOTAL_UNAVAILABLE'),
                                ([{'results': [{'id': '1', 'properties': {}}], 'total': 2}], 'SEARCH_COVERAGE_INCOMPLETE'),
                                ([{'results': [], 'total': 10000}], 'SEARCH_COVERAGE_INCOMPLETE'),
                                ([{'results': [{'id': '1', 'properties': {}}], 'total': 2, 'paging': {'next': {'after': '2'}}},
                                  {'results': [{'id': '2', 'properties': {}}], 'total': 3}], 'RESULT_SET_CHANGED')]:
            with self.subTest(category=category):
                iterator = iter(pages)
                result = self.client._pages(lambda after: next(iterator), search=True)
                self.assertFalse(result['complete'])
                self.assertEqual(result['error']['category'], category)

    def test_duplicate_ids_and_repeated_cursor_fail_closed(self):
        for pages, category in [([{'results': [{'id': '1', 'properties': {}}, {'id': '1', 'properties': {}}]}], 'DUPLICATE_PAGE_RECORDS'),
                                ([{'results': [{'id': '1', 'properties': {}}], 'paging': {'next': {'after': 'same'}}},
                                  {'results': [{'id': '2', 'properties': {}}], 'paging': {'next': {'after': 'same'}}}],
                                 'INVALID_OR_REPEATED_CURSOR')]:
            iterator = iter(pages)
            result = self.client._pages(lambda after: next(iterator))
            self.assertFalse(result['complete'])
            self.assertEqual(result['error']['category'], category)

    def test_invalid_pagination_and_page_limit_fail_closed(self):
        for data, category in [({'results': [{}]}, 'UNEXPECTED_PAGE_SCHEMA'),
                               ({'results': [{'id': '1', 'properties': {}}], 'paging': []}, 'UNEXPECTED_PAGING_SCHEMA'),
                               ({'results': [{'id': '1', 'properties': {}}], 'paging': {'next': []}}, 'UNEXPECTED_PAGING_SCHEMA'),
                               ({'results': [], 'paging': {'next': {'after': '2'}}}, 'INVALID_OR_REPEATED_CURSOR')]:
            result = self.client._pages(lambda after: data)
            self.assertFalse(result['complete'])
            self.assertEqual(result['error']['category'], category)
        result = self.client._pages(lambda after: {'results': [{'id': '1', 'properties': {}}], 'paging': {'next': {'after': '2'}}}, max_pages=1)
        self.assertEqual(result['error']['category'], 'PAGE_LIMIT_REACHED')
        self.assertFalse(result['complete'])

    def test_partial_failure_keeps_already_returned_records(self):
        self.open.side_effect = [self.response({'results': [{'toObjectId': 10}], 'paging': {'next': {'after': 'next'}}}),
                                 urllib.error.URLError('testsecret')]
        result = self.client.associations('123')
        self.assertFalse(result['complete'])
        self.assertEqual(result['results'], [{'toObjectId': 10}])
        self.assertEqual(result['error']['category'], 'CONNECTION_FAILED')
        self.assertNotIn('testsecret', json.dumps(result))
        queries = [urllib.parse.parse_qs(urllib.parse.urlsplit(call.args[0].full_url).query)
                   for call in self.open.call_args_list]
        self.assertEqual(queries[1]['after'], ['next'])

    def test_batch_read_deduplicates_input_and_requires_all_results(self):
        self.metadata()
        self.open.return_value = self.response({'status': 'COMPLETE', 'results': [{'id': '1', 'properties': {}}]})
        result = self.client.batch_read('contacts', ['1', '1'], ['email'])
        self.assertTrue(result['complete'])
        req = self.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.hubapi.com/crm/v3/objects/contacts/batch/read')
        self.assertEqual(req.method, 'POST')
        self.assertEqual(json.loads(req.data)['inputs'], [{'id': '1'}])
        self.open.return_value = self.response({'status': 'COMPLETE', 'results': [{'id': '1', 'properties': {}}]})
        self.assertFalse(self.client.batch_read('contacts', ['1', '2'], ['email'])['complete'])
        with self.assertRaises(ValueError):
            self.client.batch_read('contacts', list(range(101)), ['email'])

    def test_batch_partial_duplicate_and_error_results_not_complete(self):
        self.metadata()
        for data in ({'status': 'PENDING', 'results': [{'id': '1', 'properties': {}}]},
                     {'status': 'COMPLETE', 'results': [{'id': '1', 'properties': {}}], 'errors': [{'message': 'not logged'}]},
                     {'status': 'COMPLETE', 'results': [{'id': '1', 'properties': {}}, {'id': '1', 'properties': {}}]}):
            self.open.return_value = self.response(data)
            self.assertFalse(self.client.batch_read('contacts', ['1'], ['email'])['complete'])

    def test_batch_207_retains_records_but_never_complete(self):
        self.metadata()
        self.open.return_value = self.response({'status': 'COMPLETE', 'results': [{'id': '1', 'properties': {}}]}, status=207)
        result = self.client.batch_read('contacts', ['1'], ['email'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['results'], [{'id': '1', 'properties': {}}])
        self.assertEqual(result['error']['category'], 'BATCH_INCOMPLETE')

    def test_http_errors_preserve_status_retry_and_valid_scope_names_only(self):
        body = {'message': 'testsecret', 'errors': [{'context': {'requiredScopes': ['crm.objects.contacts.read',
                                                                                 'not a scope', 'crm.objects.contacts.read']}}]}
        self.open.side_effect = urllib.error.HTTPError('https://api.hubapi.com/private', 429, 'testsecret',
                                                       {'Retry-After': '12'}, io.BytesIO(json.dumps(body).encode()))
        error = self.assert_error('RATE_LIMITED', self.client.properties, 'contacts')
        self.assertEqual(error.safe(), {'category': 'RATE_LIMITED', 'http_status': 429,
                                       'retry_after': 12, 'required_scopes': ['crm.objects.contacts.read']})
        self.assertNotIn('testsecret', str(error))
        self.assertEqual(self.open.call_count, 1)  # no automatic retry

    def test_granular_scope_error_is_sanitized(self):
        body = {'errors': [{'context': {'requiredGranularScopes':
                ['automation.sequences.read', 'testsecret', 'not a scope']}}]}
        self.open.side_effect = urllib.error.HTTPError('https://api.hubapi.com/private', 403,
                'testsecret', {}, io.BytesIO(json.dumps(body).encode()))
        error = self.assert_error('HTTP_ERROR', self.client.properties, 'contacts')
        self.assertEqual(error.required_scopes, ['automation.sequences.read'])
        self.assertNotIn('testsecret', json.dumps(error.safe()))

    def test_error_metadata_cannot_echo_token(self):
        body = {'errors': [{'context': {'requiredScopes': ['testsecret', 'crm.objects.contacts.read']}}]}
        self.open.side_effect = urllib.error.HTTPError('https://api.hubapi.com/private', 403, 'testsecret', {},
                                                       io.BytesIO(json.dumps(body).encode()))
        error = self.assert_error('HTTP_ERROR', self.client.properties, 'contacts')
        self.assertNotIn('testsecret', json.dumps(error.safe()))
        self.assertNotIn('testsecret', str(error))

    def test_redirect_http_failure_is_not_followed(self):
        self.open.side_effect = urllib.error.HTTPError('https://api.hubapi.com/private', 302, 'redirect',
                                                       {'Location': 'https://untrusted.example'}, io.BytesIO(b'<html>private</html>'))
        error = self.assert_error('HTTP_ERROR', self.client.properties, 'contacts')
        self.assertEqual(error.status, 302)
        self.assertNotIn('untrusted', json.dumps(error.safe()))
        self.assertEqual(self.open.call_count, 1)

    def test_success_echoed_credentials_are_redacted_recursively(self):
        self.open.return_value = self.response({'testsecret': ['echo testsecret']})
        self.assertEqual(self.client._request('GET', '/crm/v3/properties/contacts'),
                         {'[REDACTED]': ['echo [REDACTED]']})

    def test_invalid_json_schema_timeout_and_unexpected_status(self):
        response = MagicMock(); stream = io.StringIO('not json'); stream.status = 200; response.__enter__.return_value = stream
        self.open.return_value = response
        self.assert_error('INVALID_JSON', self.client.properties, 'contacts')
        self.open.return_value = self.response([])
        self.assert_error('UNEXPECTED_SCHEMA', self.client.properties, 'contacts')
        for failure in (TimeoutError('testsecret'), urllib.error.URLError('testsecret')):
            self.open.side_effect = failure
            error = self.assert_error('CONNECTION_FAILED', self.client.properties, 'contacts')
            self.assertNotIn('testsecret', str(error))
        self.open.side_effect = None
        self.open.return_value = self.response({'results': []}, status=207)
        error = self.assert_error('PARTIAL_OR_UNEXPECTED_HTTP_STATUS', self.client.properties, 'contacts')
        self.assertEqual(error.status, 207)


if __name__ == '__main__':
    unittest.main()
