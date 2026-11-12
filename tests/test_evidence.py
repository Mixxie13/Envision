"""Synthetic internal envelopes only: these are not provider response fixtures."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import eligibility
import evidence

NOW = datetime(2030, 1, 2, tzinfo=timezone.utc)


def fixture(channel='linkedin'):
    subject = {'contact_id': 'synthetic-1', 'company': 'Example', 'full_name': 'Example Person',
               'email': 'person@example.com', 'linkedin_url': 'https://www.linkedin.com/in/example'}
    record = {'subject': subject, 'channel': channel, 'unresolved_risks': [],
              'approvals': {person: {'state': 'approved', 'source': 'synthetic review',
                                    'subject': subject, 'observed_at': '2030-01-01T00:00:00Z'}
                            for person in ('lead_sourcing_owner', 'bridget')}}
    envelopes = []
    for name, result in eligibility.CHECKS.items():
        envelopes.append({'schema': evidence.SCHEMA, 'check': name,
                          'identity': {'contact_id': subject['contact_id'], 'basis': 'email',
                                       'value': subject['email']},
                          'provenance': {'source_ref': 'synthetic/' + name,
                                         'coverage_ref': 'synthetic coverage only',
                                         'capability_verified': True, 'coverage_verified': True,
                                         'pagination_complete': True, 'lookup_succeeded': True},
                          'evidence': {'subject': subject, 'result': result, 'state': 'ok',
                                       'complete': True, 'source': 'synthetic/' + name,
                                       'observed_at': '2030-01-01T00:00:00Z',
                                       'expires_at': '2030-01-03T00:00:00Z',
                                       'policy_ref': 'synthetic policy; not production'}})
    return deepcopy(record), deepcopy(envelopes)


class EvidenceTests(unittest.TestCase):
    def run_gate(self, record, envelopes):
        return evidence.evaluate_evidence(record, envelopes, now=NOW)

    def test_internal_contract_handoff_and_provenance(self):
        record, envelopes = fixture()
        before = deepcopy((record, envelopes))
        result = self.run_gate(record, envelopes)
        self.assertEqual(result['evaluation']['decision'], 'eligible_for_review')
        self.assertFalse(result['evaluation']['authorized_to_send'])
        self.assertEqual(result['intake'][0]['provenance'], envelopes[0]['provenance'])
        self.assertEqual(result['record']['checks']['identity']['subject'], record['subject'])
        self.assertEqual(result['evaluation']['expires_at'], '2030-01-03T00:00:00+00:00')
        self.assertEqual((record, envelopes), before)

    def test_email_cross_channel_policy_remains_pending(self):
        record, envelopes = fixture('email')
        result = self.run_gate(record, envelopes)
        self.assertEqual(result['evaluation']['decision'], 'pending')
        self.assertIn('EMAIL_CROSS_CHANNEL_POLICY_UNRESOLVED', result['record']['unresolved_risks'])
        self.assertEqual(result['evaluation']['channel'], 'email')

    def test_base_checks_cannot_bypass_intake(self):
        record, envelopes = fixture()
        record['checks'] = {item['check']: item['evidence'] for item in envelopes}
        self.assertEqual(self.run_gate(record, [])['evaluation']['decision'], 'pending')

    def test_unknown_schema_never_interprets_empty_or_success(self):
        record, envelopes = fixture()
        for raw in ({}, {'ok': True, 'data': []}, {'schema': 'unknown', 'check': 'identity',
                                                 'evidence': {'result': 'confirmed'}}):
            result = self.run_gate(record, envelopes + [raw])
            self.assertEqual(result['evaluation']['decision'], 'pending')
        envelopes[0]['schema'] = 'hubspot-unverified-schema'
        result = self.run_gate(record, envelopes)
        self.assertNotIn('identity', result['record']['checks'])
        self.assertEqual(result['evaluation']['decision'], 'pending')

    def test_missing_failed_incomplete_or_unverified_provenance(self):
        for key, value in [('source_ref', ''), ('coverage_ref', None),
                           ('capability_verified', False), ('coverage_verified', False),
                           ('pagination_complete', False), ('lookup_succeeded', False)]:
            with self.subTest(key=key):
                record, envelopes = fixture()
                envelopes[0]['provenance'][key] = value
                self.assertEqual(self.run_gate(record, envelopes)['evaluation']['decision'], 'pending')
        record, envelopes = fixture()
        del envelopes[0]['provenance']
        self.assertEqual(self.run_gate(record, envelopes)['evaluation']['decision'], 'pending')

    def test_stale_or_missing_dates_and_policy_remain_pending(self):
        for change in ({'expires_at': '2030-01-02T00:00:00Z'}, {'observed_at': None},
                       {'policy_ref': None}, {'state': 'failed'}, {'complete': False}):
            record, envelopes = fixture()
            envelopes[0]['evidence'].update(change)
            self.assertEqual(self.run_gate(record, envelopes)['evaluation']['decision'], 'pending')

    def test_conflicting_identity_and_company_only_match(self):
        for identity in ({'basis': 'company', 'value': 'Example', 'contact_id': 'synthetic-1'},
                         {'basis': 'email', 'value': 'other@example.com', 'contact_id': 'synthetic-1'},
                         {'basis': 'email', 'value': 'PERSON@example.com', 'contact_id': 'synthetic-1'},
                         {'basis': 'email', 'value': 'person@example.com', 'contact_id': 'different'}):
            record, envelopes = fixture()
            envelopes[0]['identity'] = identity
            result = self.run_gate(record, envelopes)
            self.assertEqual(result['evaluation']['decision'], 'pending')
            self.assertNotIn('identity', result['record']['checks'])

    def test_confirmed_linkedin_identity_is_separate_from_email(self):
        record, envelopes = fixture()
        item = envelopes[0]
        item['identity'] = {'basis': 'linkedin', 'value': record['subject']['linkedin_url'],
                            'contact_id': record['subject']['contact_id'], 'confirmed_personal': True}
        self.assertEqual(self.run_gate(record, envelopes)['evaluation']['decision'], 'eligible_for_review')
        item['identity']['confirmed_personal'] = False
        self.assertEqual(self.run_gate(record, envelopes)['evaluation']['decision'], 'pending')
        item['identity']['confirmed_personal'] = True
        item['identity']['value'] = 'https://www.linkedin.com/company/example'
        self.assertEqual(self.run_gate(record, envelopes)['evaluation']['decision'], 'pending')

    def test_subject_conflict_is_not_silently_rebound(self):
        record, envelopes = fixture()
        envelopes[0]['evidence']['subject'] = dict(record['subject'], full_name='Another Person')
        self.assertEqual(self.run_gate(record, envelopes)['evaluation']['decision'], 'pending')

    def test_multiple_observations_require_review_not_last_wins(self):
        record, envelopes = fixture()
        second = deepcopy(envelopes[0]); second['evidence']['result'] = 'ambiguous'
        for ordered in (envelopes + [second], [second] + envelopes):
            self.assertEqual(self.run_gate(record, ordered)['evaluation']['decision'], 'pending')

    def test_bound_adverse_evidence_survives_clear_and_other_failures(self):
        for name, value in [('hard_bounce', 'bounced'), ('opt_out', 'opted_out'),
                            ('subscriptions', 'prohibited'), ('hubspot_sequence', 'active'),
                            ('dripify_campaign', 'active'), ('cross_channel', 'conflict'),
                            ('verification', 'invalid')]:
            with self.subTest(name=name):
                record, envelopes = fixture('email')
                harmful = deepcopy(next(item for item in envelopes if item['check'] == name))
                harmful['evidence']['result'] = value
                harmful['evidence']['expires_at'] = '2029-01-01T00:00:00Z'
                harmful['provenance']['lookup_succeeded'] = False
                for ordered in (envelopes + [harmful, {'ok': True}], [harmful] + envelopes):
                    result = self.run_gate(record, ordered)
                    self.assertEqual(result['evaluation']['decision'], 'blocked')
                    self.assertEqual(result['record']['checks'][name]['result'], value)
                    self.assertTrue(result['record']['unresolved_risks'])

    def test_unbound_adverse_is_pending_not_assigned_to_other_person(self):
        record, envelopes = fixture()
        harmful = deepcopy(envelopes[0]); harmful['check'] = 'opt_out'
        harmful['evidence']['result'] = 'opted_out'; harmful['identity']['value'] = 'other@example.com'
        self.assertEqual(self.run_gate(record, envelopes + [harmful])['evaluation']['decision'], 'pending')

    def test_malformed_missing_inputs_and_risks_fail_closed(self):
        for record, envelopes in [(None, None), ({}, []), ([], [None]), ({}, [{}])]:
            self.assertEqual(self.run_gate(record, envelopes)['evaluation']['decision'], 'pending')
        record, envelopes = fixture(); del record['unresolved_risks']
        self.assertEqual(self.run_gate(record, envelopes)['evaluation']['decision'], 'pending')

    def test_missing_approvals_remain_independent(self):
        record, envelopes = fixture(); del record['approvals']
        result = self.run_gate(record, envelopes)['evaluation']
        self.assertEqual(result['technical_eligibility'], 'eligible_for_review')
        self.assertEqual(result['decision'], 'pending')


if __name__ == '__main__':
    unittest.main()
