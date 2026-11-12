"""Offline policy tests using synthetic evidence, never provider calls."""
from copy import deepcopy
from datetime import datetime, timezone
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import eligibility

NOW = datetime(2030, 1, 2, tzinfo=timezone.utc)


def contact():
    subject = {'contact_id': 'fixture-1', 'company': 'Example', 'full_name': 'Example Person',
               'email': 'person@example.com', 'linkedin_url': 'https://www.linkedin.com/in/example'}
    evidence = {'subject': subject, 'source': 'synthetic evidence only', 'complete': True,
                'state': 'ok', 'observed_at': '2030-01-01T00:00:00Z',
                'expires_at': '2030-01-03T00:00:00Z', 'policy_ref': 'synthetic approved fixture policy'}
    return deepcopy({'subject': subject, 'channel': 'email', 'unresolved_risks': [],
                     'checks': {name: dict(evidence, result=value)
                                for name, value in eligibility.CHECKS.items()},
                     'approvals': {person: {'state': 'approved', 'source': 'synthetic review',
                                           'observed_at': '2030-01-01T00:00:00Z', 'subject': subject}
                                   for person in ('lead_sourcing_owner', 'bridget')}})


class EligibilityTests(unittest.TestCase):
    def evaluate(self, record):
        return eligibility.evaluate(record, now=NOW)

    def test_complete_contact_is_review_only(self):
        result = self.evaluate(contact())
        self.assertEqual(result['decision'], 'eligible_for_review')
        self.assertEqual(result['reason_codes'], [])
        self.assertEqual(result['expires_at'], '2030-01-03T00:00:00+00:00')
        self.assertEqual(result['channel'], 'email')
        self.assertFalse(result['authorized_to_send'])
        self.assertEqual(len(result['evidence']), len(eligibility.CHECKS))

    def test_verification_results(self):
        for value, decision in [('invalid', 'blocked'), ('unknown', 'pending'),
                                ('catch-all', 'pending'), ('unverified', 'pending')]:
            with self.subTest(value=value):
                record = contact(); record['checks']['verification']['result'] = value
                self.assertEqual(self.evaluate(record)['decision'], decision)
                self.assertEqual(self.evaluate(record)['channel'], 'email')

    def test_suppressions_and_active_conflicts(self):
        for name, value in [('hard_bounce', 'bounced'), ('opt_out', 'opted_out'),
                            ('subscriptions', 'prohibited'), ('hubspot_sequence', 'active'),
                            ('dripify_campaign', 'active'), ('cross_channel', 'conflict'),
                            ('professional_email', 'shared')]:
            with self.subTest(name=name):
                record = contact(); record['checks'][name]['result'] = value
                self.assertEqual(self.evaluate(record)['decision'], 'blocked')
                # Missing other evidence must not hide a known block.
                record['checks'].pop('identity')
                record['checks'][name]['expires_at'] = '2029-01-01T00:00:00Z'
                self.assertEqual(self.evaluate(record)['decision'], 'blocked')

    def test_no_channel_switch_bypasses_suppression(self):
        for name, value in [('hard_bounce', 'bounced'), ('opt_out', 'opted_out'),
                            ('subscriptions', 'prohibited')]:
            record = contact(); record['channel'] = 'linkedin'
            record['checks'][name]['result'] = value
            self.assertEqual(self.evaluate(record)['decision'], 'blocked')

    def test_duplicates_history_and_identity_need_review(self):
        for name, value in [('identity', 'ambiguous'), ('company', 'conflicting'),
                            ('role_company', 'departed'), ('hubspot_duplicate', 'existing'),
                            ('dripify_duplicate', 'existing'), ('outreach_history', 'contacted'),
                            ('outreach_history', 'replied')]:
            with self.subTest(name=name, value=value):
                record = contact(); record['checks'][name]['result'] = value
                self.assertEqual(self.evaluate(record)['decision'], 'pending')

    def test_every_required_check_fails_closed(self):
        for name in eligibility.CHECKS:
            with self.subTest(name=name):
                for mutation in ({'complete': False}, {'state': 'failed'}, {'state': 'ambiguous'},
                                 {'state': 'conflicting'}, {'source': ''}, {'policy_ref': ''},
                                 {'expires_at': None}, {'observed_at': None},
                                 {'expires_at': '2030-01-02T00:00:00Z'},
                                 {'observed_at': '2030-01-04T00:00:00Z'},
                                 {'observed_at': '2030-01-01'}, {'subject': {'contact_id': 'other'}}):
                    record = contact(); record['checks'][name].update(mutation)
                    self.assertEqual(self.evaluate(record)['decision'], 'pending')
                record = contact(); del record['checks'][name]
                self.assertEqual(self.evaluate(record)['decision'], 'pending')

    def test_verified_email_alone_is_insufficient(self):
        record = contact(); record['checks'] = {'verification': record['checks']['verification']}
        self.assertEqual(self.evaluate(record)['decision'], 'pending')

    def test_second_check_cannot_clear_unresolved_risk(self):
        record = contact(); record['unresolved_risks'] = ['NeverBounce catch-all; second check valid']
        self.assertEqual(self.evaluate(record)['decision'], 'pending')

    def test_missing_approvals_separate_from_technical_eligibility(self):
        for person in ('lead_sourcing_owner', 'bridget'):
            record = contact(); del record['approvals'][person]
            result = self.evaluate(record)
            self.assertEqual(result['decision'], 'pending')
            self.assertEqual(result['technical_eligibility'], 'eligible_for_review')
            self.assertIn(person.upper() + '_APPROVAL_REQUIRED', result['reason_codes'])
        record = contact(); record['approvals']['bridget']['state'] = 'rejected'
        self.assertEqual(self.evaluate(record)['decision'], 'pending')

    def test_approval_evidence_required(self):
        for field, value in [('subject', {}), ('source', ''), ('observed_at', None)]:
            record = contact(); record['approvals']['lead_sourcing_owner'][field] = value
            self.assertEqual(self.evaluate(record)['decision'], 'pending')

    def test_historical_roxy_is_preserved_but_not_current_approval(self):
        record = contact()
        legacy = record['approvals'].pop('lead_sourcing_owner')
        record['approvals']['roxy'] = legacy
        before = deepcopy(record)
        result = self.evaluate(record)
        self.assertEqual(result['decision'], 'pending')
        self.assertIn('LEAD_SOURCING_OWNER_APPROVAL_REQUIRED', result['reason_codes'])
        self.assertEqual(result['historical_approvals']['roxy'], legacy)
        self.assertEqual(record, before)
        result['historical_approvals']['roxy']['state'] = 'changed output only'
        self.assertEqual(record, before)

    def test_final_approval_cannot_precede_research_review(self):
        record = contact()
        record['approvals']['lead_sourcing_owner']['observed_at'] = '2030-01-01T01:00:00Z'
        result = self.evaluate(record)
        self.assertEqual(result['decision'], 'pending')
        self.assertIn('BRIDGET_APPROVAL_PRECEDES_RESEARCH_REVIEW', result['reason_codes'])

    def test_missing_malformed_or_wrong_subject(self):
        for record in [None, [], {}, {'checks': []}, {'subject': {}, 'approvals': []}]:
            self.assertEqual(self.evaluate(record)['decision'], 'pending')
        record = contact(); record['subject'] = dict(record['subject'], email='changed@example.com')
        self.assertEqual(self.evaluate(record)['decision'], 'pending')

    def test_channel_is_explicit_not_automatic_fallback(self):
        record = contact(); del record['channel']
        self.assertIsNone(self.evaluate(record)['channel'])
        self.assertEqual(self.evaluate(record)['decision'], 'pending')
        record = contact(); record['channel'] = 'linkedin'
        del record['checks']['verification']; del record['checks']['professional_email']
        self.assertEqual(self.evaluate(record)['decision'], 'eligible_for_review')
        del record['checks']['cross_channel']
        self.assertEqual(self.evaluate(record)['decision'], 'pending')

    def test_expiry_is_earliest_required_evidence(self):
        record = contact(); record['checks']['subscriptions']['expires_at'] = '2030-01-02T12:00:00Z'
        self.assertEqual(self.evaluate(record)['expires_at'], '2030-01-02T12:00:00+00:00')

    def test_cli_invalid_json_fails_closed(self):
        with patch.object(sys, 'argv', ['eligibility.py', '-']), \
             patch.object(sys, 'stdin', io.StringIO('invalid')), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(eligibility.main(), 1)
            self.assertEqual(json.loads(out.getvalue())['decision'], 'pending')


if __name__ == '__main__':
    unittest.main()
