"""Offline one-upload, whole-list/review orchestration and cached handoff fixtures."""
import io,json,sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch,Mock
from contextlib import redirect_stdout
from openpyxl import Workbook,load_workbook
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from workflow import intake,advance,summary,company_reviews,ReadBudget,infer_mapping,main
from sourcing_state import State
from preflight import check
from candidate_handoff import adopt_existing
from workbook import update,SHEETS
NOW=datetime(2030,1,2,12,tzinfo=timezone.utc)


class WorkflowOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'master.csv';self.source.write_text('Brand,Priority,Category,Website\n'+''.join(f'Brand {i},{100-i},Original,https://brand{i}.example.com\n' for i in range(1,22)))
        self.project=self.root/'project'
    def state(self):
        intake(self.project,self.source);s=State(self.project);self.addCleanup(s.close);return s
    def test_one_upload_atomic_intake_preserves_every_row_and_original_bytes(self):
        result=intake(self.project,self.source);self.assertEqual(result['companies'],21);self.assertEqual(result['batches'],3)
        s=State(self.project);self.addCleanup(s.close)
        self.assertEqual([c['data']['company'] for c in s.companies()],[f'Brand {i}' for i in range(1,22)])
        self.assertEqual(Path(s.meta('source_archive')).read_bytes(),self.source.read_bytes())
        self.assertFalse(intake(self.project,self.source)['imported'])
        self.source.write_text(self.source.read_text().replace('Brand 1,','Replacement,'))
        with self.assertRaises(ValueError):intake(self.project,self.source)
    def test_failed_intake_publishes_no_project(self):
        self.source.write_text('Brand,Priority\n,1\n')
        with self.assertRaises(ValueError):intake(self.project,self.source)
        self.assertFalse(self.project.exists());self.assertFalse(list(self.root.glob('.envision-intake-*')))
    def test_header_ambiguity_requires_mapping_instead_of_guessing(self):
        self.source.write_text('Brand,Company\nOne,Two\n')
        with self.assertRaises(ValueError):infer_mapping(self.source)
        intake(self.project,self.source,{'company':'Brand'})
    def test_duplicate_company_rows_are_retained_in_order(self):
        self.source.write_text('Brand,Category\nSame,Original A\nSame,Original B\n')
        s=self.state();self.assertEqual(len(s.companies()),2)
        self.assertNotEqual(s.companies()[0]['id'],s.companies()[1]['id'])
    def test_resumes_ten_company_batch_then_waits_for_actual_research_review(self):
        s=self.state();book=s.meta('workflow_workbook')
        first=advance(s,book,clock=NOW);self.assertEqual(first['batch'],1);self.assertEqual(first['batch_report']['companies'],10)
        again=advance(s,book,clock=NOW);self.assertEqual(again['batch'],1);self.assertEqual(again['batch_report']['stages_executed'],0)
        self.assertFalse(s.job(s.companies(2)[0]['id'],'research'))
        s.review_batch(1,'synthetic-real-review-ref')
        second=advance(s,book,clock=NOW);self.assertEqual(second['batch'],2)
        s.review_batch(2,'synthetic-review-two');last=advance(s,book,clock=NOW);self.assertEqual(last['batch_report']['companies'],1)
        self.assertEqual(summary(s,NOW)['companies_received'],21);self.assertTrue(summary(s,NOW)['original_rows_accounted_for'])
    def test_cached_named_candidate_adopts_same_id_without_confirming_role(self):
        s=self.state();company=s.companies()[0]
        s.contact('publication:original',company['id'],{'name':'Jane Example','role':'Brand Director','source':'https://brand1.example.com/press','publication_date':'2029-01-01'})
        ids=adopt_existing(s,company['id'],NOW);self.assertEqual(ids,['publication:original'])
        self.assertEqual(adopt_existing(s,company['id'],NOW),[])
        result=summary(s,NOW);self.assertEqual(result['candidate_evidence_counts']['discovered_candidate'],1)
        self.assertEqual(result['candidate_evidence_counts']['confirmed_current_employment'],0)
    def test_preflight_does_not_read_credentials_or_probe_network(self):
        s=self.state()
        with patch('os.environ.get',side_effect=AssertionError('Credential inspected')),patch('socket.create_connection',side_effect=AssertionError('Network')):
            result=check(s,1,clock=NOW)
        self.assertEqual(result['external_calls'],0);self.assertEqual(result['live_readiness'],'blocked')
    def test_unapproved_live_mode_stops_before_provider_construction(self):
        s=self.state()
        with patch('provider_evidence.Providers',side_effect=AssertionError('Provider constructed')):
            with self.assertRaises(ValueError):advance(s,s.meta('workflow_workbook'),live_operations=['apollo'])
    def test_durable_read_budget_cannot_be_reset_or_exceeded(self):
        s=self.state();auth={'evidence_ref':'synthetic-auth','expires_at':'2099-01-01T00:00:00Z','max_requests':{'hubspot':1}}
        ReadBudget(s,auth).reserve('hubspot')
        with self.assertRaises(ValueError):ReadBudget(s,auth).reserve('hubspot')
    def test_company_reviews_require_current_attribution_and_parent_evidence(self):
        s=self.state();c=s.companies()[0]
        row={'schema':'envision.company-review.v1','company_row_id':c['id'],'company':'Brand 1','source_url':'https://brand1.example.com/about','source_sha256':'synthetic',
             'evidence_ref':'synthetic-company','policy_ref':'synthetic-policy','reviewed_original':True,'quote':'Brand 1 official company evidence',
             'observed_at':'2030-01-02T10:00:00Z','expires_at':'2030-01-03T10:00:00Z','employee_domain':'brand1.example.com','employee_domain_confirmed':True}
        company_reviews(s,[row],NOW);self.assertTrue(s.meta('verified_company_domain:'+c['id'])['verified'])
        changed={**row,'employee_domain':'unrelated.example.com'}
        with self.assertRaises(ValueError):company_reviews(s,[changed],NOW)
        changed['parent_routing_evidence_ref']='synthetic-parent-review';company_reviews(s,[changed],NOW)
        self.assertFalse(s.meta('verified_company_domain:'+c['id'])['verified'])
        company_reviews(s,[changed],NOW);self.assertFalse(s.meta('verified_company_domain:'+c['id'])['verified'])
    def test_workbook_readability_and_fixed_schema(self):
        s=self.state();b=load_workbook(s.meta('workflow_workbook'))
        self.assertEqual(b.sheetnames,list(SHEETS));self.assertEqual(b['New Prospect Contacts'].max_column,14)
        for sheet in b:
            self.assertEqual(sheet.freeze_panes,'A2');self.assertTrue(sheet['A1'].font.bold);self.assertIsNotNone(sheet.auto_filter.ref)
        self.assertEqual([r[0] for r in list(b['Company Research'].values)[1:]],[f'Brand {i}' for i in range(1,22)]);b.close()
    def test_status_and_preflight_commands_are_read_only(self):
        s=self.state();before={p.name:p.read_bytes() for p in self.project.iterdir() if p.is_file()}
        for command in ('status','preflight'):
            with patch('sys.argv',['workflow','--project',str(self.project),command]),redirect_stdout(io.StringIO()):main()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.project.iterdir() if p.is_file()})
    def test_master_incorrect_dimension_is_not_silently_truncated(self):
        from zipfile import ZipFile,ZIP_DEFLATED
        import re
        path=self.root/'master.xlsx';b=Workbook();b.active.append(['Brand']);b.active.append(['First']);b.active.append(['Second']);b.save(path);b.close()
        with ZipFile(path) as z:parts={n:z.read(n) for n in z.namelist()}
        parts['xl/worksheets/sheet1.xml']=re.sub(rb'<dimension ref="[^"]+"',b'<dimension ref="A1"',parts['xl/worksheets/sheet1.xml'])
        with ZipFile(path,'w',ZIP_DEFLATED) as z:
            for n,v in parts.items():z.writestr(n,v)
        self.assertEqual(intake(self.project,path)['companies'],2)
    def test_duplicate_unused_and_blank_headers_preserve_all_original_values(self):
        self.source.write_text('Brand,Notes,Notes,\nOne,First note,Second note,Unlabeled value\n')
        s=self.state();row=s.companies()[0]['data']
        self.assertEqual([v['value'] for v in row['original_columns']],['One','First note','Second note','Unlabeled value'])
        self.assertEqual([v['header'] for v in row['original_columns']],['Brand','Notes','Notes',''])
    def test_sparse_excel_rows_and_date_metadata_are_serializable_without_loss(self):
        from datetime import datetime
        path=self.root/'dates.xlsx';b=Workbook();b.active.append(['Brand','Research Date','Notes'])
        b.active.append(['One',datetime(2026,11,11)]);b.active.append(['Two']);b.save(path);b.close()
        intake(self.project,path);s=State(self.project);self.addCleanup(s.close)
        first=s.companies()[0]['data'];self.assertEqual(first['original']['Research Date'],'2026-11-11T00:00:00')
        self.assertEqual(first['original_columns'][1]['value_type'],'datetime')
        self.assertIsNone(s.companies()[1]['data']['original']['Notes'])
    def test_candidate_bundle_failure_rolls_back_prior_records_and_evidence(self):
        from candidate_handoff import ingest
        s=self.state()
        row={'company':'Brand 1','name':'Jane Example','job_title':'Brand Director','employer_attribution':'Brand 1',
             'source_reference':'synthetic','original_source_url':'https://brand1.example.com/team','research_date':'2030-01-02','evidence_date':'2030-01-02'}
        before=[dict(r) for r in s.db.execute('SELECT * FROM contacts')]
        bad={**row,'name':'Another Person','approvals':{'invalid':object()}}
        with self.assertRaises(TypeError):ingest(s,[row,bad])
        self.assertEqual([dict(r) for r in s.db.execute('SELECT * FROM contacts')],before)
        self.assertFalse(s.observations())
    def test_legacy_status_fallback_does_not_write_source_manifest(self):
        s=self.state();s.db.execute('DELETE FROM meta WHERE key=?',('source_manifest',));s.db.commit()
        before=(self.project/'state.sqlite').read_bytes()
        old=State(self.project,readonly=True)
        try:self.assertEqual(summary(old,NOW)['companies_received'],21)
        finally:old.close()
        self.assertEqual((self.project/'state.sqlite').read_bytes(),before)
    def test_new_candidate_gets_cached_crm_match_without_network_or_metadata_probe(self):
        from provider_evidence import Providers,contact_summary
        s=self.state();company=s.companies()[0]
        s.contact('crm',company['id'],contact_summary({'id':'123','properties':{'email':'known@example.com','firstname':'Jane','lastname':'Example'}}))
        s.contact('candidate',company['id'],{'name':'Jane Example','role':'Brand Director','email':'KNOWN@example.com'})
        p=Providers(s,live=False);p.metadata=Mock(side_effect=AssertionError('Unnecessary metadata request'))
        value=next(v for v in p.identity(company)['contacts'] if v['contact_id']=='candidate')
        self.assertEqual(value['matches'][0]['id'],'123');self.assertFalse(value['coverage_complete'])
    def test_formula_like_company_names_are_escaped_without_losing_original(self):
        self.source.write_text('Brand,Category\n@Example,Original\n')
        s=self.state();book=s.meta('workflow_workbook');update(s,book);update(s,book)
        self.assertEqual(s.companies()[0]['data']['company'],'@Example')
        b=load_workbook(book);self.assertEqual(b['Company Research'].cell(2,1).value,"'@Example");b.close()
    def test_new_master_inherits_private_suppression_without_copying_contacts(self):
        s=self.state();s.set_meta('offline-email-suppression-v1',{'entries':[{'id':'suppression:synthetic','email':'known-bounced@example.com','hubspot_contact_id':'999'}]})
        new=self.root/'future-project';intake(new,self.source,suppression_project=self.project)
        other=State(new);self.addCleanup(other.close)
        self.assertEqual(len(other.meta('offline-email-suppression-v1')['entries']),1)
        self.assertEqual(len(other.contacts()),0)
        other.contact('future',other.companies()[0]['id'],{'name':'Synthetic','email':'known-bounced@example.com'})
        self.assertTrue(other.contacts()[0]['data']['blocked'])
    def test_expired_read_authorization_cannot_reserve_dispatch(self):
        s=self.state();auth={'evidence_ref':'expired-auth','expires_at':'2000-01-01T00:00:00Z','max_requests':{'hubspot':1}}
        with self.assertRaises(ValueError):ReadBudget(s,auth).reserve('hubspot')
        self.assertIsNone(s.meta(ReadBudget(s,auth).key))
    def test_derived_bounce_blocks_are_not_counted_as_original_native_records(self):
        s=self.state();c=s.companies()[0]
        s.set_meta('offline-email-suppression-v1',{'entries':[{'id':'suppression:synthetic','email':'known-bounced@example.com'}]})
        s.contact('original',c['id'],{'name':'Original','email':'original@example.com','blocked':True,'risks':['HISTORICAL_HARD_BOUNCE']})
        s.contact('future',c['id'],{'name':'Future','email':'known-bounced@example.com'})
        self.assertTrue(next(v for v in s.contacts() if v['id']=='future')['data']['blocked'])
        self.assertEqual(summary(s,NOW)['known_native_bounce_records'],1)
