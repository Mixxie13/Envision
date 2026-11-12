"""Synthetic documented pagination contracts; no Dripify calls."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dripify_evidence
import urllib.error
from dripify_evidence import all_visible_leads, history, pages, ReadFailure


class DripifyPaginationTests(unittest.TestCase):
    def test_all_visible_listing_unfiltered_across_cursors(self):
        calls = []
        def fetch(path, query):
            calls.append((path, query))
            return {'items': [{'id': 2}]} if query.get('cursor') else {'items': [{'id': 1}], 'nextCursor': 'next'}
        result = all_visible_leads(fetch)
        self.assertEqual(len(result['items']), 2)
        self.assertTrue(result['pagination_complete'])
        self.assertFalse(result['coverage_complete'])
        self.assertTrue(all(path == 'leads' for path, _ in calls))
        self.assertFalse(any('campaignId' in q for _, q in calls))

    def test_rate_limit_pauses_further_reads_without_retry(self):
        with patch.dict('os.environ', {'RELAY_TOKEN': 'synthetic'}, clear=True), patch.object(dripify_evidence, '_paused_after_rate_limit', False), patch.object(dripify_evidence.urllib.request, 'build_opener') as transport:
            transport.return_value.open.side_effect = urllib.error.HTTPError('https://example.test', 429, 'Rate limit', {}, None)
            for _ in range(2):
                with self.assertRaises(ReadFailure):
                    dripify_evidence.read('leads')
            self.assertEqual(transport.return_value.open.call_count, 1)

    def test_partial_failure_retains_positive_matches(self):
        def fetch(cursor):
            if cursor:
                raise ReadFailure('RELAY_HTTP_403')
            return {'items': [{'id': 1}], 'nextCursor': 'next'}
        result = pages(fetch)
        self.assertEqual(result['items'], [{'id': 1}])
        self.assertFalse(result['pagination_complete'])

    def test_cursor_repetition_schema_and_duplicate_items_fail_closed(self):
        for fetch in (lambda c: {'items': [{'id': 1}], 'nextCursor': 'same'},
                      lambda c: {'items': [{'id': 1}, {'id': 1}]},
                      lambda c: {'items': [{}]}):
            self.assertFalse(pages(fetch)['pagination_complete'])

    def test_message_history_requires_empty_terminal_page(self):
        self.assertFalse(pages(lambda c: {'items': [{'id': 1}]}, messages=True)['pagination_complete'])
        fetch = lambda c: {'items': []} if c else {'items': [{'id': 1}], 'nextCursor': 'end'}
        self.assertTrue(pages(fetch, messages=True)['pagination_complete'])

    def test_documented_activity_and_conversation_field_names(self):
        def fetch(path, query=None):
            if path.endswith('/conversation'):
                return {'conversationId': 'opaque:conversation', 'leadInUserId': 1}
            if path.endswith('/activity'):
                return {'items': [{'leadInUserId': 1, 'sequenceEventType': 'CONNECT_SENT'}]}
            if '/messages' in path:
                return {'items': []}
            return {'id': 1, 'leadInCampaigns': []}
        result = history(1, fetch)
        self.assertTrue(result['activity']['pagination_complete'])
        self.assertTrue(result['messages']['pagination_complete'])
        self.assertFalse(result['coverage_complete'])




class ResumableCollectorTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from sourcing_state import State
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(self.tmp.name)
        self.addCleanup(self.state.close)
        self.pause = patch('time.sleep')
        self.pause.start()
        self.addCleanup(self.pause.stop)

    def test_restart_resumes_cursor_and_terminal_cache_avoids_network(self):
        calls = []
        def fetch(path, query):
            calls.append(query.get('cursor'))
            return {'items': [{'id': 2}]} if query.get('cursor') else {'items': [{'id': 1}], 'nextCursor': 'second'}
        a = dripify_evidence.Collector(self.state, fetch, max_pages=1).collect('leads')
        self.assertFalse(a['pagination_complete'])
        b = dripify_evidence.Collector(self.state, fetch, max_pages=1).collect('leads')
        self.assertTrue(b['pagination_complete'])
        self.assertFalse(b['coverage_complete'])
        dripify_evidence.Collector(self.state, fetch).collect('leads')
        self.assertEqual(calls, [None, 'second'])
        self.assertEqual(len(b['items']), 2)

    def test_failure_preserves_positive_and_retries_only_failed_page(self):
        def fail(path, query):
            if query.get('cursor'): raise ReadFailure('RELAY_HTTP_503')
            return {'items': [{'id': 1}], 'nextCursor': 'second'}
        a = dripify_evidence.Collector(self.state, fail).collect('leads')
        self.assertEqual(len(a['items']), 1)
        calls = []
        def recover(path, query):
            calls.append(query['cursor']); return {'items': [{'id': 2}]}
        b = dripify_evidence.Collector(self.state, recover).collect('leads')
        self.assertEqual(calls, ['second'])
        self.assertEqual(len(b['items']), 2)

    def test_global_budget_and_one_collection_per_run(self):
        fetch = MagicMock(return_value={'items': [{'id': 1}], 'nextCursor': 'second'})
        collector = dripify_evidence.Collector(self.state, fetch, max_requests=1)
        collector.collect('leads'); collector.collect('leads'); collector.resource('leads/1')
        self.assertEqual(fetch.call_count, 1)

    def test_overlapping_pages_stall_without_losing_matches_or_repeating(self):
        fetch = MagicMock(side_effect=[{'items': [{'id': 1}], 'nextCursor': 'second'}, {'items': [{'id': 1}]}])
        a = dripify_evidence.Collector(self.state, fetch).collect('leads')
        self.assertEqual(len(a['items']), 1)
        self.assertFalse(a['pagination_complete'])
        dripify_evidence.Collector(self.state, fetch).collect('leads')
        self.assertEqual(fetch.call_count, 2)

    def test_history_preserves_activity_with_empty_membership_and_conversation_404(self):
        def fetch(path, query=None):
            if path.endswith('activity'): return {'items': [{'sequenceEventType': 'CONNECT_SENT'}]}
            if path.endswith('conversation'): raise ReadFailure('RELAY_HTTP_404')
            return {'id': 1, 'leadInCampaigns': []}
        result = dripify_evidence.Collector(self.state, fetch).lead_history(1)
        self.assertEqual(result['activity']['items'][0]['sequenceEventType'], 'CONNECT_SENT')
        self.assertFalse(result['coverage_complete'])

    def test_message_metadata_only_and_empty_terminal_page(self):
        fetch = MagicMock(side_effect=[{'items': [{'eventId': '1', 'content': 'private message'}], 'nextCursor': 'end'}, {'items': []}])
        result = dripify_evidence.Collector(self.state, fetch).collect('conversations/abc/messages', messages=True)
        self.assertTrue(result['pagination_complete'])
        self.assertNotIn('private message', json.dumps(result))


class BatchDripifyBindingTests(unittest.TestCase):
    def setUp(self):
        ResumableCollectorTests.setUp(self)
        self.state.initialize([{'position': 1, 'source_row': 2, 'company': 'Synthetic',
            'original': {'Brand': 'Synthetic'}, 'mapped': {'company': 'Synthetic'}}], 'synthetic')
        self.company = self.state.companies(1)[0]
        lookup = patch.object(dripify_evidence.Collector, 'lookup', return_value={'items': [], 'reason': 'SYNTHETIC_NO_MATCH'})
        lookup.start()
        self.addCleanup(lookup.stop)

    def provider(self, rows):
        from provider_evidence import Providers
        p = Providers(self.state, live=True)
        p.dripify_collector = dripify_evidence.Collector(self.state, lambda path, query=None: {'items': rows} if path == 'leads' else {'id': 1})
        return p

    def test_name_only_is_not_match_missing_identifier_is_explicit(self):
        self.state.contact('p', self.company['id'], {'name': 'Same Name'})
        p = self.provider([{'id': 1, 'firstName': 'Same', 'lastName': 'Name'}])
        self.assertEqual(p.dripify(self.company)['contacts'][0]['reason'], 'MISSING_LINKEDIN_IDENTIFIER')

    def test_normalized_exact_match_preserves_outreach_without_membership(self):
        self.state.contact('p', self.company['id'], {'linkedin_url': 'https://www.linkedin.com/in/synthetic-person/'})
        p = self.provider([{'id': 1, 'linkedinProfileUrl': 'https://linkedin.com/in/synthetic-person'}])
        def fetch(path, query=None):
            if path.endswith('activity'): return {'items': [{'sequenceEventType': 'CONNECT_SENT'}]}
            if path.endswith('conversation'): raise ReadFailure('RELAY_HTTP_404')
            return {'id': 1, 'leadInCampaigns': []}
        p.dripify_collector.fetch = fetch
        p._dripify_index = {'items': [{'id': 1, 'linkedinProfileUrl': 'https://linkedin.com/in/synthetic-person'}]}
        row = p.dripify(self.company)['contacts'][0]
        self.assertEqual(row['reason'], 'CONFIRMED_PREVIOUS_OUTREACH')
        self.assertTrue(row['previous_outreach_confirmed'])
        self.assertFalse(row['outreach_ready'])

    def test_partial_unmatched_remains_pending_even_terminal_page(self):
        self.state.contact('p', self.company['id'], {'linkedin_url': 'https://linkedin.com/in/synthetic-person'})
        result = self.provider([]).dripify(self.company)
        self.assertEqual(result['contacts'][0]['reason'], 'NO_MATCH_WITHIN_PARTIAL_COVERAGE')
        self.assertFalse(result['coverage_complete'])

    def test_email_selected_does_not_fetch_person_history(self):
        self.state.contact('p', self.company['id'], {'channel': 'email', 'linkedin_url': 'https://linkedin.com/in/synthetic-person'})
        p = self.provider([])
        p.dripify_collector.lead_history = MagicMock()
        self.assertEqual(p.dripify(self.company)['contacts'][0]['reason'], 'EMAIL_SELECTED_CROSS_CHANNEL_POLICY_PENDING')
        p.dripify_collector.lead_history.assert_not_called()


class ProfileLookupTests(unittest.TestCase):
    setUp = ResumableCollectorTests.setUp

    def test_exact_profile_lookup_cached_no_name_binding(self):
        with patch('email_api.request', return_value={'data': [{'id': 1, 'linkedinProfileUrl': 'https://linkedin.com/in/other'}, {'id': 2, 'linkedinProfileUrl': 'https://www.linkedin.com/in/target/'}]}) as request:
            c = dripify_evidence.Collector(self.state)
            result = c.lookup('https://linkedin.com/in/target')
            self.assertEqual([r['id'] for r in result['items']], [2])
            dripify_evidence.Collector(self.state).lookup('https://linkedin.com/in/target')
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.kwargs['linkedin_url'], 'https://linkedin.com/in/target')
            self.assertFalse(result['coverage_complete'])

    def test_404_resource_cached_and_unbound_detail_rejected(self):
        def missing(path, query=None): raise ReadFailure('RELAY_HTTP_404')
        c = dripify_evidence.Collector(self.state, missing)
        c.resource('leads/1/conversation')
        fetch = MagicMock(return_value={'id': 99})
        c2 = dripify_evidence.Collector(self.state, fetch)
        self.assertEqual(c2.resource('leads/1/conversation')['reason'], 'RELAY_HTTP_404')
        fetch.assert_not_called()
        self.assertEqual(c2.resource('leads/1')['reason'], 'UNBOUND_LEAD_DETAIL')
