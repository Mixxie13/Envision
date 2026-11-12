"""Synthetic offline manual reconciliation contracts; no provider transport."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import reconcile_exports as r


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.contact = {'contact_key': 'hs:1', 'hubspot_contact_id': '1',
                        'linkedin_url': 'https://www.linkedin.com/in/example',
                        'email': 'person@example.test', 'baseline_flags': ['Confirmed existing']}
        self.pack = {'contacts': [self.contact]}
        self.row = {k: 'false' for k in r.BOOL_FIELDS}
        self.row.update(source='hubspot', contact_key='hs:1', hubspot_contact_id='1',
                        linkedin_url='', email='', identity_confirmed='true', existing='true',
                        coverage_complete='true', observed_at='2026-01-01T00:00:00Z',
                        evidence_ref='synthetic:record', coverage_ref='synthetic:coverage')

    def result(self, *rows):
        return r.reconcile(self.pack, list(rows))['contacts'][0]

    def test_missing_exports_never_mean_absence(self):
        result = self.result()
        self.assertIn('Not checked', result['flags'])
        self.assertIn('Checked with incomplete coverage', result['flags'])
        self.assertFalse(result['outreach_ready'])

    def test_existing_and_cross_channel_are_distinct(self):
        row = dict(self.row, source='dripify', linkedin_url='https://linkedin.com/in/example/')
        result = self.result(self.row, row)
        self.assertEqual(result['classification'], 'Confirmed cross-channel match')
        self.assertIn('Confirmed existing', result['flags'])
        self.assertFalse(result['outreach_ready'])

    def test_id_only_dripify_row_does_not_prove_cross_channel(self):
        self.assertNotIn('Confirmed cross-channel match', self.result(dict(self.row, source='dripify'))['flags'])

    def test_baseline_bounce_survives_negative_export(self):
        self.contact['baseline_flags'].append('Blocked')
        self.assertEqual(self.result(self.row)['classification'], 'Blocked')

    def test_adverse_evidence_survives_incomplete_provenance(self):
        for field in r.ADVERSE:
            result = self.result(dict(self.row, **{field: 'true'}, evidence_ref='', coverage_complete='unknown'))
            self.assertEqual(result['classification'], 'Blocked')
            self.assertIn('Checked with incomplete coverage', result['flags'])

    def test_identity_conflict_and_name_only_fail_closed(self):
        for changes in ({'hubspot_contact_id': '2'}, {'hubspot_contact_id': ''},
                        {'linkedin_url': 'https://linkedin.com/in/different'}):
            self.assertEqual(self.result(dict(self.row, **changes))['classification'], 'Ambiguous')

    def test_unknown_and_unsupported_source_stay_incomplete(self):
        for changes in ({'opt_out': 'unknown'}, {'source': 'unverified'}, {'identity_confirmed': 'false'},
                        {'coverage_complete': 'false'}, {'observed_at': 'not a date'},
                        {'observed_at': '2999-01-01T00:00:00Z'}):
            self.assertIn('Checked with incomplete coverage', self.result(dict(self.row, **changes))['flags'])

    def test_conflicting_assertions_retain_block_and_ambiguity(self):
        result = self.result(self.row, dict(self.row, hard_bounce='true'))
        self.assertEqual(result['classification'], 'Blocked')
        self.assertIn('Ambiguous', result['flags'])

    def test_previous_outreach_requires_review(self):
        self.assertIn('PREVIOUS_CONTACT_REQUIRES_REVIEW', self.result(dict(self.row, reply_received='true'))['reason_codes'])

    def test_unknown_contact_kept_for_review(self):
        result = r.reconcile(self.pack, [dict(self.row, contact_key='other')])
        self.assertEqual(len(result['unmatched_or_conflicting_rows']), 1)

    def test_csv_contract_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input.csv'
            with path.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=r.FIELDS)
                writer.writeheader()
                writer.writerow(self.row)
            self.assertEqual(r.read_csv(path), [self.row])
            path.write_text('name,email\nsomeone,test@example.test\n')
            with self.assertRaises(ValueError):
                r.read_csv(path)
        with self.assertRaises(ValueError):
            r.reconcile({'contacts': [self.contact, self.contact]}, [])

    def test_cli_writes_only_new_report_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / 'check.json', Path(tmp) / 'report.json'
            original = json.dumps(self.pack)
            source.write_text(original)
            cmd = [sys.executable, '-B', r.__file__, '--checklist', str(source), '--output', str(output)]
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            report = output.read_bytes()
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), report)
            self.assertEqual(source.read_text(), original)


if __name__ == '__main__':
    unittest.main()
