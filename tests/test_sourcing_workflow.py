"""Synthetic end-to-end batch/resume, suppression and workbook contracts."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sourcing_state import State, project_lock, read_master
from source_batch import run, STAGES
from workbook import update, SHEETS, validate
from provider_evidence import contact_summary, Providers
from openpyxl import load_workbook


class FakeProviders:
    def __init__(self):
        self.calls = []

    def hubspot(self, company):
        self.calls.append(('hubspot', company['position']))
        if company['position'] == 2:
            raise OSError('synthetic provider failure')
        return {'state': 'pending', 'reason': 'synthetic partial coverage'}

    def identity(self, company):
        return {'state': 'pending', 'reason': 'identity unresolved'}

    def dripify(self, company):
        return {'state': 'pending', 'reason': 'account-wide unsupported'}


class FakeResearch:
    def __init__(self):
        self.calls = 0

    def collect(self, website):
        self.calls += 1
        return {'state': 'pending', 'reason': 'synthetic publication only', 'pages': []}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = State(self.root / 'project')
        self.addCleanup(self.state.close)
        self.rows = [{'position': i, 'source_row': i + 1, 'company': f'Brand {i}',
                      'original': {'Brand': f'Brand {i}', 'Category': 'Original category', 'Priority': str(30-i)},
                      'mapped': {'company': f'Brand {i}', 'category': 'Original category', 'priority': str(30-i), 'website': f'https://brand{i}.example.com'}} for i in range(1, 22)]
        self.state.initialize(self.rows, 'synthetic')
        self.workbook = self.root / 'research.xlsx'

    def test_import_preserves_order_values_and_batches(self):
        self.assertEqual([len(self.state.companies(b)) for b in (1, 2, 3)], [10, 10, 1])
        self.assertEqual(self.state.companies()[0]['data']['original'], self.rows[0]['original'])
        self.state.initialize(self.rows, 'synthetic')
        self.assertEqual(len(self.state.companies()), 21)
        with self.assertRaises(ValueError):
            self.state.initialize(list(reversed(self.rows)), 'different')

    def test_real_csv_and_xlsx_mapping_reject_formulas(self):
        p = self.root / 'master.csv'
        p.write_text('Brand,Priority,Category\nExact Brand,02,CPG\nOther Brand,01,Original\n')
        rows = read_master(p, {'company': 'Brand', 'priority': 'Priority', 'category': 'Category'})
        self.assertEqual(rows[0]['mapped']['priority'], '02')
        self.assertEqual(rows[1]['company'], 'Other Brand')
        p.write_text('Brand,Category\n=FORMULA,CPG\n')
        with self.assertRaises(ValueError):
            read_master(p, {'company': 'Brand'})
        p.write_text('Brand,Brand\na,b\n')
        with self.assertRaises(ValueError):
            read_master(p, {'company': 'Brand'})

    def test_duplicate_source_company_rows_are_preserved_and_flagged(self):
        state = State(self.root / 'duplicates')
        self.addCleanup(state.close)
        rows = [dict(self.rows[0]), dict(self.rows[0], position=2, source_row=3)]
        state.initialize(rows, 'duplicate source')
        self.assertEqual(len(state.companies()), 2)
        self.assertTrue(any(o['data'].get('reason') == 'REPEATED_MASTER_COMPANY_REVIEW' for o in state.observations()))

    def test_explicit_recheck_preserves_observations_and_blocks(self):
        run(self.state, 1, self.workbook, FakeProviders())
        count = len(self.state.observations())
        self.state.recheck(1, 'hubspot', 'synthetic changed source evidence')
        result = run(self.state, 1, self.workbook, FakeProviders())
        self.assertEqual(result['stages_executed'], 2)
        self.assertGreater(len(self.state.observations()), count)

    def test_xlsx_requires_sheet_and_preserves_original_values(self):
        from openpyxl import Workbook
        path = self.root / 'master.xlsx'
        book = Workbook()
        book.active.title = 'Targets'
        book.active.append(['Brand', 'Category'])
        book.active.append([' Exact Brand ', 'Original Category'])
        book.create_sheet('Notes')
        book.save(path)
        with self.assertRaises(ValueError):
            read_master(path, {'company': 'Brand'})
        rows = read_master(path, {'company': 'Brand', 'category': 'Category'}, 'Targets')
        self.assertEqual(rows[0]['company'], ' Exact Brand ')
        self.assertEqual(rows[0]['mapped']['category'], 'Original Category')

    def test_blocked_company_does_not_stop_other_research_and_resume(self):
        providers, research = FakeProviders(), FakeResearch()
        result = run(self.state, 1, self.workbook, providers, research, public_web=True)
        self.assertEqual(result['stages_executed'], 10 * len(STAGES))
        self.assertEqual(research.calls, 10)
        self.assertEqual(len(providers.calls), 10)
        count = len(self.state.observations())
        again = run(self.state, 1, self.workbook, providers, research, public_web=True)
        self.assertEqual(again['stages_executed'], 0)
        self.assertEqual(again['stages_reused'], 60)
        self.assertEqual(len(self.state.observations()), count)
        self.assertEqual(research.calls, 10)

    def test_restart_after_workbook_failure_recovers_without_provider_repeat(self):
        providers = FakeProviders()
        with patch('source_batch.update', side_effect=OSError('synthetic disk interruption')):
            with self.assertRaises(OSError):
                run(self.state, 1, self.workbook, providers)
        self.assertEqual(len(providers.calls), 1)
        result = run(self.state, 1, self.workbook, providers)
        self.assertEqual(result['stages_reused'], len(STAGES))
        self.assertEqual(len(providers.calls), 10)

    def test_batch_report_and_exceptions_do_not_mix_other_batches(self):
        first = self.state.companies(1)[0]
        second = self.state.companies(2)[0]
        self.state.contact('hs:1', first['id'], {'name': 'Earlier', 'blocked': True})
        self.state.contact('hs:2', second['id'], {'name': 'Current', 'blocked': False})
        run(self.state, 1, self.workbook, FakeProviders())
        self.state.review_batch(1, 'synthetic research review')
        result = run(self.state, 2, self.workbook, FakeProviders())
        self.assertEqual(result['contacts'], 1)
        self.assertEqual(result['retained_blocks'], 0)
        exceptions = json.loads((self.state.directory / 'batch-2-exceptions.json').read_text())
        self.assertEqual([e['contact_id'] for e in exceptions], ['hs:2'])

    def test_review_checkpoint_does_not_invent_approvals(self):
        with self.assertRaises(ValueError):
            self.state.permit_batch(2)
        with self.assertRaises(ValueError):
            self.state.review_batch(1, 'synthetic review')
        run(self.state, 1, self.workbook, FakeProviders())
        self.state.review_batch(1, 'synthetic actual research review reference')
        self.state.permit_batch(2)
        self.assertFalse(self.state.meta('bridget_approved', False))

    def test_contact_block_survives_reimport_and_provider_failures(self):
        company = self.state.companies()[0]
        self.state.contact('hs:123', company['id'], {'name': 'Example', 'blocked': True, 'risks': ['HISTORICAL_HARD_BOUNCE'], 'hubspot_contact_id': '123'})
        self.state.contact('hs:123', company['id'], {'name': 'Example', 'blocked': False, 'risks': []})
        result = run(self.state, 1, self.workbook, FakeProviders())
        self.assertEqual(result['retained_blocks'], 1)
        evaluations = [o for o in self.state.observations() if o['stage'] == 'evaluation']
        self.assertEqual(evaluations[0]['data']['contacts'][0]['decision'], 'blocked')
        self.assertFalse(evaluations[0]['data']['contacts'][0]['authorized_to_send'])

    def test_workbook_idempotence_schemas_and_original_category(self):
        run(self.state, 1, self.workbook, FakeProviders())
        book = load_workbook(self.workbook)
        validate(book)
        contents = {s.title: list(s.values) for s in book}
        book.close()
        update(self.state, self.workbook)
        book = load_workbook(self.workbook)
        self.assertEqual(contents, {s.title: list(s.values) for s in book})
        self.assertEqual(book['Company Research'].cell(2, 3).value, 'Original category')
        self.assertEqual(book['New Prospect Contacts'].max_column, 14)
        self.assertEqual(book['New Prospect Contacts'].max_row, 1)
        book.close()

    def test_unknown_workbook_and_wrong_order_not_overwritten(self):
        update(self.state, self.workbook)
        book = load_workbook(self.workbook)
        book['Company Research'].cell(2, 1).value = 'Wrong'
        book.save(self.workbook)
        before = self.workbook.read_bytes()
        with self.assertRaises(ValueError):
            update(self.state, self.workbook)
        self.assertEqual(before, self.workbook.read_bytes())

    def test_project_lock_excludes_concurrent_process(self):
        with project_lock(self.root / 'project'):
            with self.assertRaises(ValueError):
                with project_lock(self.root / 'project'):
                    pass

    def test_contact_id_collision_is_not_silently_merged(self):
        a, b = self.state.companies()[:2]
        self.state.contact('hs:1', a['id'], {'blocked': True, 'name': 'Existing'})
        self.state.contact('hs:1', b['id'], {'blocked': False, 'name': 'Conflicting'})
        self.assertEqual(len(self.state.contacts()), 1)
        self.assertTrue(self.state.contacts()[0]['data']['blocked'])
        self.assertTrue(any(o['data'].get('reason') == 'CONTACT_COMPANY_CONFLICT' for o in self.state.observations()))

    def test_cache_does_not_refetch_missing_or_failed_provider(self):
        calls = []
        fetch = lambda: calls.append(1) or {'error': '403', 'complete': False}
        self.state.cached(['query'], fetch)
        self.state.cached(['query'], fetch)
        self.assertEqual(calls, [1])

    def test_partial_and_null_provider_properties_never_clear(self):
        result = contact_summary({'id': '1', 'properties': {'hs_email_optout': None}})
        self.assertFalse(result['outreach_ready'])
        self.assertIn('not cleared', result['coverage'])
        result = contact_summary({'id': '1', 'properties': {'hs_email_optout_123': 'true'}})
        self.assertTrue(result['blocked'])

    def test_positive_sequence_count_blocks_even_if_boolean_is_blank(self):
        result = contact_summary({'id': '1', 'properties': {'hs_sequences_is_enrolled': None, 'hs_sequences_actively_enrolled_count': '1'}})
        self.assertTrue(result['blocked'])
        self.assertIn('ACTIVE_SEQUENCE', result['risks'])

    def test_alternate_email_search_requires_exact_returned_identity(self):
        company = self.state.companies()[0]
        self.state.contact('research:email', company['id'], {'name': 'Candidate', 'email': 'candidate@example.com'})
        providers = Providers(self.state, live=True)
        metadata = {k: {} for k in ('email', 'hs_additional_emails', 'hs_linkedin_url')}
        result = {'data': {'results': [{'id': '2', 'properties': {'email': 'other@example.com', 'hs_additional_emails': 'notcandidate@example.com'}}]}}
        with patch.object(providers, 'metadata', return_value=metadata), patch.object(providers, 'read', return_value=result):
            providers.identity(company)
        self.assertEqual(len(self.state.contacts()), 1)
        self.assertTrue(any(o['data'].get('reason') == 'UNBOUND_SEARCH_RESULT' for o in self.state.observations()))

    def test_exact_known_alternate_email_can_identify_existing_record(self):
        company = self.state.companies()[0]
        self.state.contact('research:email', company['id'], {'name': 'Candidate', 'email': 'candidate@example.com'})
        providers = Providers(self.state, live=True)
        metadata = {k: {} for k in ('email', 'hs_additional_emails', 'hs_linkedin_url')}
        result = {'data': {'results': [{'id': '2', 'properties': {'email': 'other@example.com', 'hs_additional_emails': 'candidate@example.com;third@example.com'}}]}}
        with patch.object(providers, 'metadata', return_value=metadata), patch.object(providers, 'read', return_value=result):
            providers.identity(company)
        self.assertIn('hs:2', [c['id'] for c in self.state.contacts()])

    def test_no_paid_api_or_live_calls_by_default(self):
        with patch('provider_evidence.Client') as client, patch('provider_evidence.request') as relay:
            run(self.state, 1, self.workbook)
        client.assert_not_called()
        relay.assert_not_called()
