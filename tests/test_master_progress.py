"""Whole-list accounting with synthetic data only; never starts provider work."""
from datetime import datetime, timezone
import hashlib
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from sourcing_state import State, digest
from master_progress import snapshot, write_reports, STAGES

NOW = datetime(2030,1,2,tzinfo=timezone.utc)


class MasterProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=State(self.root/'state');self.addCleanup(self.state.close)
        self.rows=[{'position':i,'source_row':i+1,'company':'Brand '+str(i),'original':{'Brand':'Brand '+str(i),'Priority':str(30-i),'Category':'Original'},'mapped':{'company':'Brand '+str(i)}} for i in range(1,24)]
        self.source=self.root/'master.csv';self.source.write_text('synthetic source bytes')
        self.state.initialize(self.rows,hashlib.sha256(self.source.read_bytes()).hexdigest());self.state.archive_source(self.source)

    def mark(self, company, observed=True):
        self.state.observe(company['id'],'','research','pending',{'reason':'SYNTHETIC_ATTEMPT','pages':[{'state':'observed','url':'https://example.com'}] if observed else []})
        for stage in STAGES:self.state.finish(company['id'],stage,'pending')

    def test_every_imported_row_is_listed_without_processing(self):
        report=snapshot(self.state,NOW)
        self.assertEqual(report['total_companies_received'],23)
        self.assertEqual(report['companies_not_yet_processed'],23)
        self.assertEqual([r['original_position'] for r in report['companies']],list(range(1,24)))
        self.assertEqual([sum(r['batch']==b for r in report['companies']) for b in (1,2,3)],[10,10,3])
        self.assertFalse(report['research_coverage_complete'])

    def test_first_batch_does_not_equal_entire_master(self):
        for c in self.state.companies(1):self.mark(c)
        report=snapshot(self.state,NOW)
        self.assertEqual(report['companies_researched'],10)
        self.assertEqual(report['companies_not_yet_processed'],13)
        self.assertEqual(report['companies_with_documented_processed_outcomes'],10)
        self.assertEqual(report['companies_with_unresolved_issues'],10)
        self.assertFalse(report['research_coverage_complete'])

    def test_complete_source_accounting_can_preserve_unresolved_no_contact_outcomes(self):
        for c in self.state.companies():self.mark(c,observed=False)
        for b in (1,2,3):self.state.review_batch(b,'synthetic actual QC review '+str(b))
        report=snapshot(self.state,NOW)
        self.assertTrue(report['research_coverage_complete'])
        self.assertEqual(report['companies_with_confirmed_contacts'],0)
        self.assertEqual(report['companies_with_unresolved_issues'],23)
        self.assertTrue(all('NO_CURRENT_QUALIFIED_CONTACT_CONFIRMED' in r['reason_codes'] for r in report['companies']))
        self.assertFalse(report['outreach_ready'])
        self.assertIn('APPROVAL_PENDING',report['completion_status'])

    def test_unreviewed_batch_blocks_final_completion(self):
        for c in self.state.companies():self.mark(c)
        self.assertFalse(snapshot(self.state,NOW)['research_coverage_complete'])

    def test_disabled_research_and_partial_stages_are_not_completed_research(self):
        c=self.state.companies()[0]
        for stage in STAGES:self.state.finish(c['id'],stage,'pending')
        self.state.observe(c['id'],'','research','pending',{'reason':'PUBLIC_RESEARCH_NOT_ENABLED'})
        report=snapshot(self.state,NOW)
        self.assertEqual(report['companies_with_documented_processed_outcomes'],0)
        self.assertEqual(report['companies_in_progress'],1)

    def test_duplicate_names_keep_both_source_rows_and_original_values(self):
        state=State(self.root/'duplicate');self.addCleanup(state.close)
        rows=[self.rows[0],dict(self.rows[0],position=2,source_row=5)]
        state.initialize(rows,'synthetic')
        report=snapshot(state,NOW)
        self.assertEqual(report['total_companies_received'],2)
        self.assertEqual([r['source_row'] for r in report['companies']],[2,5])
        self.assertEqual(report['companies'][0]['original_values'],rows[0]['original'])
        self.assertEqual(report['companies'][1]['potential_duplicate_positions'],[1,2])

    def test_missing_reordered_or_extra_state_records_fail_reconciliation(self):
        c=self.state.companies()[0]
        self.state.db.execute('DELETE FROM companies WHERE id=?',(c['id'],));self.state.db.commit()
        report=snapshot(self.state,NOW)
        self.assertEqual(len(report['companies']),23)
        self.assertFalse(report['all_original_rows_accounted_for'])
        self.assertFalse(report['companies'][0]['source_row_intact'])
        self.assertFalse(report['research_coverage_complete'])

    def test_reordering_and_extra_state_rows_are_not_silently_accepted(self):
        first, second = self.state.companies()[:2]
        self.state.db.execute('UPDATE companies SET position=0 WHERE id=?',(first['id'],))
        self.state.db.execute('UPDATE companies SET position=1 WHERE id=?',(second['id'],))
        self.state.db.execute('UPDATE companies SET position=2 WHERE id=?',(first['id'],))
        self.state.db.execute('INSERT INTO companies VALUES (?,?,?,?)',('extra',24,3,json.dumps(self.rows[0])))
        self.state.db.commit()
        report=snapshot(self.state,NOW)
        self.assertFalse(report['all_original_rows_accounted_for'])
        self.assertEqual(len(report['companies']),23)
        self.assertTrue(any(i['issue']=='STATE_HAS_ROWS_NOT_IN_ORIGINAL_SOURCE' for i in report['integrity_issues']))

    def test_confirmed_identity_is_separate_from_blocked_email_and_send_approval(self):
        company=self.state.companies()[0];self.mark(company)
        subject={'contact_id':'person','company':company['data']['company'],'full_name':'Synthetic Person'}
        check={'subject':subject,'result':'confirmed','state':'ok','complete':True,'source':'synthetic only','policy_ref':'synthetic policy','observed_at':'2030-01-01T00:00:00Z','expires_at':'2030-01-03T00:00:00Z'}
        person={'name':'Synthetic Person','blocked':True,'evidence_record':{'subject':subject,'checks':{k:dict(check) for k in ('identity','company','role_company')}},'hubspot_contact_id':'12','raw_record':{'id':'12','properties':{}}}
        self.state.contact('person',company['id'],person)
        report=snapshot(self.state,NOW)
        self.assertEqual(report['companies_with_confirmed_contacts'],1)
        self.assertEqual(report['companies_with_existing_hubspot_contacts'],1)
        self.assertTrue(report['companies'][0]['unresolved'])
        self.assertFalse(report['outreach_ready'])

    def test_original_archive_and_values_tampering_are_detected(self):
        archive=Path(self.state.meta('source_archive'));archive.write_text('changed')
        self.assertFalse(snapshot(self.state,NOW)['source_bytes_verified'])
        with self.assertRaises(ValueError):self.state.archive_source(self.source)

    def test_legacy_manifest_is_migrated_only_if_source_signature_matches(self):
        self.state.db.execute("DELETE FROM meta WHERE key='source_manifest'");self.state.db.commit()
        self.assertTrue(snapshot(self.state,NOW)['all_original_rows_accounted_for'])
        self.state.db.execute("DELETE FROM meta WHERE key='source_manifest'")
        self.state.db.execute('DELETE FROM companies WHERE position=1');self.state.db.commit()
        with self.assertRaises(ValueError):snapshot(self.state,NOW)

    def test_person_boolean_alone_never_counts_as_confirmation(self):
        c=self.state.companies()[0]
        self.state.contact('person',c['id'],{'name':'Synthetic Person','current_employment_confirmed':True,'hubspot_contact_id':'12'})
        report=snapshot(self.state,NOW)
        self.assertEqual(report['companies_with_confirmed_contacts'],0)
        self.assertEqual(report['companies_with_existing_hubspot_contacts'],0)

    def test_email_results_do_not_override_unknown_risk_staleness_or_bounce(self):
        company=self.state.companies()[0];self.mark(company)
        subject={'contact_id':'person','company':company['data']['company'],'full_name':'Synthetic Person','email':'person@example.test'}
        check={'subject':subject,'state':'ok','complete':True,'source':'synthetic only','policy_ref':'synthetic policy','observed_at':'2030-01-01T00:00:00Z','expires_at':'2030-01-03T00:00:00Z'}
        base={'name':'Synthetic Person','email':'person@example.test','evidence_record':{'subject':subject,'unresolved_risks':[],'checks':{'professional_email':dict(check,result='individual'),'verification':dict(check,result='valid')}}}
        self.state.contact('person',company['id'],base)
        self.assertEqual(snapshot(self.state,NOW)['companies'][0]['verified_professional_emails'],1)
        for mutation in ('catch-all','stale','risk','blocked'):
            person=deepcopy(base)
            if mutation=='catch-all':person['evidence_record']['checks']['verification']['result']='catch-all'
            if mutation=='stale':person['evidence_record']['checks']['verification']['expires_at']='2030-01-02T00:00:00Z'
            if mutation=='risk':person['evidence_record']['unresolved_risks']=['unresolved provider conflict']
            if mutation=='blocked':person['blocked']=True
            self.state.contact('person',company['id'],person)
            report=snapshot(self.state,NOW)
            self.assertEqual(report['companies'][0]['verified_professional_emails'],0)
            self.assertFalse(report['outreach_ready'])

    def test_reports_cover_all_source_rows_and_are_repeatable(self):
        first=write_reports(self.state,completion=True)
        second=write_reports(self.state,completion=True)
        self.assertEqual(first,second)
        self.assertEqual(len(json.loads((self.state.directory/'master-progress.json').read_text())['companies']),23)
        self.assertIn('INCOMPLETE',(self.state.directory/'master-completion-report.md').read_text())
        self.assertEqual(self.source.read_text(),'synthetic source bytes')
