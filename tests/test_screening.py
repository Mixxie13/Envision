"""Synthetic suppression/coverage regressions. No production data or provider IO."""
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sourcing_state import State
from screening import project, RISK_CHECKS
from provider_evidence import Providers, contact_summary
from hubspot import ReadError
from source_batch import evaluate_contacts
from master_progress import snapshot
from workbook import update, SHEETS
from openpyxl import load_workbook
from eligibility import CHECKS
from candidate_handoff import provider_review

NOW = datetime(2030, 1, 2, 12, tzinfo=timezone.utc)


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = State(self.root / 'state'); self.addCleanup(self.state.close)
        self.state.initialize([{'position': i, 'source_row': i+1, 'company': 'Brand',
            'original': {'Brand': 'Brand'}, 'mapped': {'company': 'Brand'}} for i in (1, 2)], 'synthetic')
        self.company = self.state.companies()[0]; self.other = self.state.companies()[1]

    def put(self, cid, **fields):
        self.state.contact(cid, self.company['id'], {'name': 'Jane Example', 'role': 'Brand Director', **fields})

    def person(self, cid):
        return next(c['data'] for c in self.state.contacts() if c['id'] == cid)

    def screen(self, cid):
        return project(self.person(cid), cid, 'Brand', NOW)

    def test_email_suppression_is_bidirectional_and_independent_of_linkedin(self):
        self.put('candidate', email=' Person@Example.COM ', linkedin_url='https://linkedin.com/in/new')
        self.put('crm', email='person@example.com', linkedin_url='https://linkedin.com/in/old', risks=['HISTORICAL_HARD_BOUNCE'])
        self.assertEqual(self.screen('candidate')['decision'], 'blocked')
        self.assertEqual(self.screen('crm')['decision'], 'blocked')
        self.assertEqual(self.screen('candidate')['hubspot_status'], 'pending')
        self.assertIn('crm', self.person('candidate')['suppression_sources'])

    def test_all_restrictions_apply_even_without_blocked_boolean(self):
        for n, risk in enumerate(RISK_CHECKS):
            with self.subTest(risk=risk):
                self.put('source'+str(n), email=f'person{n}@example.com', risks=[risk])
                self.put('target'+str(n), email=f'PERSON{n}@EXAMPLE.COM')
                self.assertEqual(self.screen('target'+str(n))['decision'], 'blocked')

    def test_late_import_of_raw_legacy_rows_preserves_all_ten_bounces(self):
        # Bypass the writer to simulate a pre-migration SQLite project.
        for n in range(10):
            for cid, payload in ((f'candidate{n}', {'name': 'Candidate', 'email': f'b{n}@example.com'}),
                                 (f'bounce{n}', {'name': 'Other identity', 'email': f' B{n}@EXAMPLE.COM ', 'risks': ['HISTORICAL_HARD_BOUNCE']})):
                self.state.db.execute('INSERT INTO contacts VALUES (?,?,?)', (cid, self.company['id'], json.dumps(payload)))
        self.state.db.commit()
        for n in range(10): self.assertEqual(self.screen(f'candidate{n}')['decision'], 'blocked')
        self.assertEqual(len(self.state.contacts()), 20)

    def test_identifier_history_retains_old_email_and_profile_blocks(self):
        self.put('old', email='original@example.com', linkedin_url='https://linkedin.com/in/original', risks=['EMAIL_OPT_OUT'])
        self.put('old', email='changed@example.com', linkedin_url='https://linkedin.com/in/changed', risks=[])
        self.put('new', email='ORIGINAL@example.com')
        self.assertEqual(self.screen('new')['decision'], 'blocked')
        self.put('profile-only', linkedin_url='https://linkedin.com/in/original')
        self.assertEqual(self.screen('profile-only')['decision'], 'pending')

    def test_email_alias_and_cross_company_link_preserve_blocks(self):
        self.state.contact('crm', self.other['id'], contact_summary({'id': '123', 'properties': {
            'email': 'primary@example.com', 'hs_additional_emails': ' ALIAS@EXAMPLE.COM ;other@example.com',
            'hs_email_optout': 'true'}}))
        self.put('new', email='alias@example.com')
        self.assertTrue(self.state.contacts(self.company['id'])[0]['data']['blocked'])
        self.assertEqual(self.screen('new')['decision'], 'blocked')
        self.assertEqual(len(self.state.companies()), 2)

    def test_name_only_and_wrong_linkedin_host_never_bind(self):
        self.put('known', email='old@example.com', linkedin_url='https://linkedin.com/in/same', risks=['DO_NOT_CONTACT'])
        self.put('different', email='other@example.com', linkedin_url='https://unrelated.example.com/in/same')
        self.assertEqual(self.screen('different')['decision'], 'pending')
        self.assertFalse(self.person('different').get('blocked'))

    def test_same_crm_id_with_new_candidate_id_retains_suppression(self):
        self.put('old', hubspot_contact_id='123', risks=['EMAIL_OPT_OUT'])
        self.put('new', hubspot_contact_id='123', raw_record={'id':'123','properties':{}})
        self.assertEqual(self.screen('new')['decision'], 'blocked')

    def test_typed_adverse_record_survives_later_clear_overwrite(self):
        self.put('old', email='same@example.com', evidence_record={'checks': {'subscriptions': {'result': 'prohibited'}}})
        self.put('old', email='same@example.com', evidence_record={'checks': {'subscriptions': {'result': 'permitted'}}})
        self.put('new', email='same@example.com')
        self.assertEqual(self.screen('new')['decision'], 'blocked')

    def test_same_named_different_profiles_are_not_silently_merged(self):
        self.put('one', linkedin_url='https://linkedin.com/in/one')
        self.put('two', linkedin_url='https://linkedin.com/in/two')
        self.assertEqual(len(self.state.contacts()), 2)
        self.assertEqual(self.screen('two')['decision'], 'pending')

    def test_ownership_and_routing_conflict_stays_pending(self):
        self.put('p', risks=['OWNERSHIP_ROUTING_CONFLICT'])
        value = self.screen('p')
        self.assertEqual(value['decision'], 'pending'); self.assertEqual(value['ownership_routing_status'], 'pending')
        self.assertFalse(value['campaign_ready'])

    def test_provider_error_cache_is_not_fresh_or_coverage_and_not_retried(self):
        providers = Providers(self.state)
        read = Mock(side_effect=ReadError('MISSING_SCOPES', 403))
        first = providers.read(['synthetic'], read); second = providers.read(['synthetic'], read)
        self.assertEqual(first, second); self.assertEqual(read.call_count, 1)
        self.assertEqual(first['freshness_status'], 'unknown'); self.assertFalse(first['coverage_complete'])

    def provider(self, properties, results, complete=False):
        providers = Providers(self.state, live=True)
        providers.metadata = Mock(return_value={p: {} for p in properties})
        client = Mock(); client.search.return_value = {'results': results, 'complete': complete, 'error': None}
        providers.client = Mock(return_value=client)
        return providers, client

    def test_email_lookup_runs_when_profile_property_unavailable(self):
        self.put('p', email=' EXACT@EXAMPLE.COM ', linkedin_url='https://linkedin.com/in/person')
        providers, client = self.provider(['email', 'hs_email_optout'], [{'id': '7', 'properties': {
            'email': 'exact@example.com', 'hs_email_optout': 'true'}}])
        value = providers.identity(self.company)
        self.assertEqual(value['contacts'][0]['matches'], [{'id': '7', 'basis': 'email'}])
        self.assertEqual(client.search.call_count, 1)
        self.assertEqual(self.screen('p')['decision'], 'blocked')

    def test_missing_records_or_properties_never_report_absence(self):
        self.put('p', email='person@example.com')
        providers, client = self.provider(['email'], [])
        value = providers.identity(self.company)
        self.assertEqual(value['state'], 'pending'); self.assertFalse(value['contacts'][0]['coverage_complete'])
        self.assertEqual(self.screen('p')['hubspot_status'], 'pending')
        providers, client = self.provider([], [])
        value = providers.identity(self.company); client.search.assert_not_called()
        self.assertEqual(value['contacts'][0]['reason'], 'SUPPORTED_IDENTIFIER_PROPERTY_UNAVAILABLE')

    def test_partial_identifier_page_keeps_bounce_and_profile_conflict(self):
        self.put('p', email='same@example.com', linkedin_url='https://linkedin.com/in/candidate')
        providers, client = self.provider(['email', 'hs_linkedin_url', 'hs_email_hard_bounce_reason_enum'], [
            {'id': '7', 'properties': {'email': 'SAME@example.com', 'hs_linkedin_url': 'https://linkedin.com/in/other',
                                    'hs_email_hard_bounce_reason_enum': 'UNKNOWN_USER'}}])
        result = providers.identity(self.company)
        self.assertEqual(result['contacts'][0]['reason'], 'EMAIL_MATCH_PROFILE_CONFLICT')
        self.assertEqual(self.screen('p')['decision'], 'blocked')
        self.assertIn('IDENTITY_CONFLICT_EMAIL_PROFILE_DISAGREEMENT', self.person('p')['risks'])

    def test_subscription_type_positive_is_requested_and_retained(self):
        self.put('p', email='same@example.com')
        providers, client = self.provider(['email', 'hs_email_optout_123'], [
            {'id': '7', 'properties': {'email': 'same@example.com', 'hs_email_optout_123': 'true'}}])
        providers.identity(self.company)
        self.assertIn('hs_email_optout_123', client.search.call_args.args[2])
        self.assertEqual(self.screen('p')['decision'], 'blocked')

    def test_old_cache_with_successful_transport_does_not_grant_absence(self):
        providers = Providers(self.state)
        old = {'observed_at': '2001-01-01T00:00:00Z', 'data': {'complete': True, 'results': []}}
        self.state.cached(['hubspot', ['old']], lambda: old)
        operation = Mock(side_effect=AssertionError('Must not refetch'))
        value = providers.read(['old'], operation)
        self.assertEqual(value['observed_at'], old['observed_at']); operation.assert_not_called()
        self.assertEqual(value['freshness_status'], 'unknown'); self.assertFalse(value['coverage_complete'])

    def test_prior_outreach_survives_empty_memberships_and_later_failure(self):
        self.put('p', linkedin_url='https://linkedin.com/in/person')
        provider_review(self.state, self.company['id'], {'contacts': [{'contact_id': 'p', 'matches': [{'id': 1}],
            'previous_outreach_confirmed': True}]}, 'dripify', NOW)
        provider_review(self.state, self.company['id'], {'contacts': [{'contact_id': 'p', 'reason': 'PROVIDER_ERROR'}]}, 'dripify', NOW)
        providers = Providers(self.state, live=False)
        value = providers.dripify(self.company)['contacts'][0]
        self.assertTrue(value['previous_outreach_confirmed']); self.assertFalse(value['coverage_complete'])
        self.assertEqual(self.screen('p')['decision'], 'pending')

    def test_retained_profile_version_is_not_erased_by_same_lead_new_profile(self):
        self.put('p', linkedin_url='https://linkedin.com/in/old')
        self.state.set_meta('dripify-retained-index-v2', {'items': [{'id': 1, 'linkedinProfileUrl': 'https://linkedin.com/in/old'}]})
        providers = Providers(self.state)
        providers._dripify_index = {'items': [{'id': 1, 'linkedinProfileUrl': 'https://linkedin.com/in/new'}]}
        value = providers.dripify(self.company)['contacts'][0]
        self.assertEqual(len(value['matches']), 1); self.assertEqual(value['reason'], 'CONFLICTING_LEAD_PROFILE_HISTORY')
        self.assertFalse(value['coverage_complete']); self.assertFalse(value['outreach_ready'])

    def test_dripify_missing_profile_email_only_stays_unresolved(self):
        self.put('p', email='person@example.com')
        value = Providers(self.state).dripify(self.company)
        self.assertEqual(value['contacts'][0]['reason'], 'MISSING_LINKEDIN_IDENTIFIER')
        self.assertFalse(value['coverage_complete']); self.assertEqual(self.screen('p')['dripify_status'], 'pending')

    def test_workbook_tracker_and_evaluator_share_blocks_and_schema(self):
        self.put('p', email='blocked@example.com')
        self.put('restriction', email='BLOCKED@example.com', risks=['DO_NOT_CONTACT'])
        path = self.root/'book.xlsx'; update(self.state, path, NOW); update(self.state, path, NOW)
        book=load_workbook(path); self.assertEqual(book.sheetnames, list(SHEETS))
        self.assertEqual(book['New Prospect Contacts'].max_column, 14)
        issues=[r for r in list(book['Unresolved Issues'].values)[1:] if 'SUPPRESSION_SCREENING' in str(r[6])]
        self.assertTrue(all(r[3]=='Jane Example' and r[4]=='Brand Director' and 'record_id' in r[6] for r in issues))
        self.assertEqual(len(issues), 2); book.close()
        self.assertTrue(all(v['decision']=='blocked' for v in evaluate_contacts(self.state,self.company)['contacts']))
        progress=snapshot(self.state,NOW)['companies'][0]
        self.assertEqual(progress['screening_counts']['blocked'],2)
        self.assertTrue(progress['unresolved']); self.assertFalse(progress['outreach_ready'])

    def test_complete_but_wrong_subject_record_never_grants_review(self):
        subject={'contact_id':'other','company':'Brand','full_name':'Jane Example','email':'','linkedin_url':'https://linkedin.com/in/person'}
        record={'subject':subject,'channel':'linkedin','unresolved_risks':[], 'approvals':{},
                'checks':{k:{'result':v,'subject':subject,'source':'synthetic','policy_ref':'synthetic',
                            'state':'ok','complete':True,'observed_at':'2030-01-01T00:00:00Z','expires_at':'2030-02-01T00:00:00Z'} for k,v in CHECKS.items()}}
        self.put('p', linkedin_url=subject['linkedin_url'], evidence_record=record)
        self.assertEqual(self.screen('p')['technical_eligibility'],'pending')
        self.assertEqual(self.screen('p')['hubspot_status'],'pending')

    def test_raw_legacy_suppression_without_summary_flags_is_preserved(self):
        self.put('legacy', email='blocked@example.com', raw_record={'id': '123', 'properties': {
            'hs_email_hard_bounce_reason_enum': 'UNKNOWN_USER'}})
        self.put('new', email='blocked@example.com')
        self.assertEqual(self.screen('new')['decision'], 'blocked')

    def test_malformed_record_does_not_raise_or_clear(self):
        for value in ([], {'checks': None}, {'checks': {'hubspot_duplicate': []}}):
            self.put('p', evidence_record=value)
            self.assertEqual(self.screen('p')['decision'], 'pending')
            self.assertEqual(self.screen('p')['hubspot_status'], 'pending')

    def test_invalid_pagination_keeps_valid_positive_items_and_stalls(self):
        from dripify_evidence import Collector
        fetch = Mock(return_value={'items': [{'id': 1, 'linkedinProfileUrl': 'https://linkedin.com/in/person'}], 'nextCursor': 123})
        with patch('time.sleep'):
            value = Collector(self.state, fetch).collect('leads')
            Collector(self.state, fetch).collect('leads')
        self.assertEqual(len(value['items']), 1); self.assertTrue(value['stalled'])
        self.assertFalse(value['pagination_complete']); self.assertFalse(value['coverage_complete'])
        self.assertEqual(fetch.call_count, 1)

    def test_missing_later_page_keeps_existing_match_pending(self):
        from dripify_evidence import Collector, ReadFailure
        fetch = Mock(side_effect=[{'items': [{'id': 1, 'linkedinProfileUrl': 'https://linkedin.com/in/person'}],
                                  'nextCursor': 'next'}, ReadFailure('RELAY_HTTP_503')])
        with patch('time.sleep'): value = Collector(self.state, fetch).collect('leads')
        self.put('p', linkedin_url='https://linkedin.com/in/person')
        providers = Providers(self.state); providers._dripify_index = value
        row = providers.dripify(self.company)['contacts'][0]
        self.assertEqual(len(row['matches']), 1); self.assertFalse(value['pagination_complete'])
        self.assertEqual(self.screen('p')['decision'], 'pending'); self.assertFalse(row['coverage_complete'])

    def test_routing_conflict_issue_includes_known_person_from_other_company(self):
        self.put('p', email='person@example.com')
        self.state.contact('p', self.other['id'], {'name': 'Should not replace original'})
        path=self.root/'routing.xlsx'; update(self.state,path,NOW)
        book=load_workbook(path)
        rows=[r for r in list(book['Unresolved Issues'].values)[1:] if 'CONTACT_COMPANY_CONFLICT' in str(r[6])]
        self.assertEqual(len(rows),1); self.assertEqual(rows[0][3], 'Jane Example'); book.close()
        self.assertEqual(self.person('p')['name'], 'Jane Example')

    def test_unsupported_clear_and_existing_record_cannot_display_absence(self):
        subject={'contact_id':'p','company':'Brand','full_name':'Jane Example','email':'','linkedin_url':''}
        check={'subject':subject,'source':'synthetic','policy_ref':'synthetic','state':'ok','complete':True,
               'observed_at':'2030-01-01T00:00:00Z','expires_at':'2030-02-01T00:00:00Z'}
        record={'subject':subject,'checks':{'hubspot_duplicate':{**check,'result':'clear'}},'unresolved_risks':[]}
        self.put('p',evidence_record=record)
        self.assertEqual(self.screen('p')['hubspot_status'],'pending')
        record['checks']['hubspot_duplicate']['result']='absent'
        self.put('p',hubspot_contact_id='123',evidence_record=record)
        self.assertEqual(self.screen('p')['hubspot_status'],'pending')

    def test_false_blank_and_missing_subscription_flags_never_clear(self):
        from screening import hubspot_signals
        for props in ({}, {'hs_email_optout': None}, {'hs_email_optout': False},
                      {'hs_email_optout': 'false', 'hs_email_optout_123': 'false', 'hs_email_optout_456': 'false'}):
            value=hubspot_signals(props)
            self.assertEqual(value['suppression_status'],'pending')
            self.assertEqual(value['subscription_clearance'],'pending')
            self.assertFalse(value['campaign_ready'])

    def test_named_subscription_types_and_ui_action_are_not_contact_status(self):
        from screening import hubspot_signals
        value=hubspot_signals({'subscription_types':['Marketing Information','One to One'],
                               'available_action':'Opt out of email'})
        self.assertEqual(value['positive_restrictions'],[])
        self.assertEqual(value['subscription_clearance'],'pending')

    def test_positive_type_optout_and_whitespace_true_block(self):
        record=contact_summary({'id':'123','properties':{'email':'person@example.com','hs_email_optout_123':' TRUE '}})
        self.assertTrue(record['blocked'])
        self.assertIn('EMAIL_OPT_OUT',record['risks'])
        self.assertEqual(record['hubspot_suppression']['positive_restrictions'][0]['property'],'hs_email_optout_123')
        self.assertEqual(record['hubspot_suppression']['subscription_clearance'],'pending')

    def test_prior_reply_and_contact_activity_require_review_without_suppression(self):
        for field in ('hs_email_first_reply_date','hs_email_last_reply_date','notes_last_contacted','hs_sequences_enrolled_count'):
            value='2' if field.endswith('count') else '2029-12-01T00:00:00Z'
            record=contact_summary({'id':'123','properties':{field:value}})
            self.assertFalse(record['blocked'])
            self.assertIn('PREVIOUS_CONTACT_REQUIRES_REVIEW',record['risks'])
            self.assertEqual(record['hubspot_suppression']['activity_coverage'],'pending')

    def test_blank_history_never_establishes_no_previous_contact(self):
        from screening import hubspot_signals
        result=hubspot_signals({'notes_last_contacted':'','hs_email_last_reply_date':None})
        self.assertEqual(result['activity_indicators'],[])
        self.assertEqual(result['activity_coverage'],'pending')

    def test_explicit_internal_dnc_survives_missing_native_property(self):
        self.put('p',email='person@example.com',risks=['DO_NOT_CONTACT'],raw_record={'id':'123','properties':{}})
        self.assertEqual(self.screen('p')['decision'],'blocked')
        self.assertEqual(self.screen('p')['hubspot_suppression']['subscription_clearance'],'pending')
