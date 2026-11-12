"""Offline handoff fixtures; no production/provider confirmations are invented."""
import sys,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from copy import deepcopy
from openpyxl import load_workbook
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from sourcing_state import State
from candidate_handoff import ingest,review,candidate_id,refresh,provider_review
from master_progress import snapshot
from source_batch import evaluate_contacts
from workbook import update,SHEETS
import evidence
NOW=datetime(2030,1,2,12,tzinfo=timezone.utc)

class CandidateHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=State(self.root/'state');self.addCleanup(self.state.close)
        self.state.initialize([{'position':1,'source_row':2,'company':'ATTITUDE','original':{'Brand':'ATTITUDE','Priority':'Original'},'mapped':{'company':'ATTITUDE'}}],'synthetic')
        self.company=self.state.companies(1)[0]
    def row(self,name='Amael Proulx-Lussier',title='Brand Manager',**kw):
        return {'company':'ATTITUDE','name':name,'job_title':title,'employer_attribution':'ATTITUDE',
          'linkedin_url':'','original_source_url':'https://app.apollo.io/#/people','evidence_date':'2030-01-02','research_date':'2030-01-02',**kw}
    def person(self,cid):return next(c['data'] for c in self.state.contacts() if c['id']==cid)
    def view(self,cid,clock=NOW):return review(self.person(cid),cid,'ATTITUDE',clock)
    def positive(self,email=False):
        row=self.row('Jane Example',linkedin_url='https://linkedin.com/in/jane-example',channel='linkedin',email='jane@attitudeliving.com' if email else '')
        cid=candidate_id(self.company['id'],row)
        subject={'contact_id':cid,'company':'ATTITUDE','full_name':'Jane Example','linkedin_url':row['linkedin_url'],'email':row['email']}
        row['employment_claims']=[{'name':'Jane Example','company':'ATTITUDE','role':'Brand Manager','source_url':row['linkedin_url'],'subject_profile_url':row['linkedin_url'],
          'access_status':'observed','source_sha256':'synthetic-profile-hash','observed_at':'2030-01-02T10:00:00Z','quote':'Jane Example currently works as Brand Manager at ATTITUDE.',
          'claim_state':'current','current_review_date':'2030-01-02','current_role_explicit':True,'reviewed_original':True}]
        results={'identity':'confirmed','company':'confirmed','role_company':'confirmed'}
        if email:results.update(professional_email='individual',verification='valid')
        row['envelopes']=[]
        for check,result in results.items():
            source='synthetic/'+check
            row['envelopes'].append({'schema':evidence.SCHEMA,'check':check,'identity':{'contact_id':cid,'basis':'linkedin','value':row['linkedin_url'],'confirmed_personal':True},
             'provenance':{'source_ref':source,'coverage_ref':'synthetic-coverage','capability_verified':True,'coverage_verified':True,'pagination_complete':True,'lookup_succeeded':True},
             'evidence':{'subject':subject,'result':result,'state':'ok','complete':True,'source':source,'policy_ref':'synthetic-policy-not-production',
                         'observed_at':'2030-01-02T10:00:00Z','expires_at':'2030-01-03T10:00:00Z'}})
        if email:row['email_evidence']=[{'email':row['email'],'person_name':'Jane Example','company':'ATTITUDE','explicitly_published':True,'shared':False,'source_url':'https://attitudeliving.com/team','evidence_date':'2030-01-02'}]
        return row,cid
    def test_attitude_manual_examples_enter_named_review_but_stay_provisional(self):
        ids=ingest(self.state,[self.row(),self.row('Maude Paquin','Marketing Manager')],clock=NOW)
        self.assertEqual(len(ids),2)
        for cid in ids:
            v=self.view(cid);self.assertTrue(v['discovered_candidate']);self.assertTrue(v['provisional_role_evidence'])
            self.assertFalse(v['confirmed_current_employment']);self.assertEqual(v['outreach_eligibility'],'pending')
            self.assertEqual(v['hubspot_status'],'pending');self.assertEqual(v['dripify_status'],'pending')
        path=self.root/'review.xlsx';update(self.state,path);book=load_workbook(path)
        self.assertEqual(book.sheetnames,list(SHEETS));self.assertEqual(book['New Prospect Contacts'].max_column,14)
        names={r[2] for r in list(book['Verification Evidence'].values)[1:]}
        self.assertTrue({'Amael Proulx-Lussier','Maude Paquin'}<=names)
        self.assertEqual(book['New Prospect Contacts'].max_row,1);book.close()
        counts=snapshot(self.state,NOW)['candidate_evidence_counts'];self.assertEqual(counts['discovered_candidate'],2);self.assertEqual(counts['confirmed_current_employment'],0)
    def test_exact_duplicate_source_observation_is_idempotent_not_identity_merge(self):
        row=self.row();a=ingest(self.state,[row,row],clock=NOW);before=len(self.state.observations())
        b=ingest(self.state,[row],clock=NOW)
        self.assertEqual(a,b);self.assertEqual(len(self.state.contacts()),1);self.assertEqual(len(self.state.observations()),before)
        other={**row,'original_source_url':'https://company.example.com/press'}
        ingest(self.state,[other],clock=NOW);self.assertEqual(len(self.state.contacts()),2)
        self.assertFalse(any(c['data']['evidence_record']['checks'].get('identity',{}).get('result')=='confirmed' for c in self.state.contacts()))
    def test_parent_company_and_conflicting_role_remain_pending(self):
        cid=ingest(self.state,[self.row(employer_attribution='Parent Company')],clock=NOW)[0]
        self.assertFalse(self.view(cid)['provisional_role_evidence']);self.assertEqual(self.view(cid)['outreach_eligibility'],'pending')
        row=self.row();cid=ingest(self.state,[row],clock=NOW)[0]
        ingest(self.state,[{**row,'job_title':'Marketing Director'}],clock=NOW)
        self.assertIn('CONFLICTING_ROLE_EVIDENCE',self.view(cid)['record']['unresolved_risks'])
    def test_complete_reviewed_evidence_drives_counts_without_clearing_outreach(self):
        row,cid=self.positive();ingest(self.state,[row],clock=NOW)
        v=self.view(cid);self.assertTrue(v['confirmed_current_employment']);self.assertEqual(v['outreach_eligibility'],'pending')
        p=snapshot(self.state,NOW);self.assertEqual(p['candidate_evidence_counts']['confirmed_current_employment'],1);self.assertEqual(p['companies_with_confirmed_contacts'],1)
        stale=self.view(cid,NOW+timedelta(days=2));self.assertFalse(stale['confirmed_current_employment'])
        self.assertEqual(snapshot(self.state,NOW+timedelta(days=2))['companies_with_confirmed_contacts'],0)
    def test_linked_mailbox_is_separate_from_deliverability_and_permission(self):
        row,cid=self.positive(email=True);ingest(self.state,[row],clock=NOW)
        self.assertTrue(self.view(cid)['person_linked_email_candidate']);self.assertTrue(self.view(cid)['deliverability_verified_email'])
        self.assertEqual(self.view(cid)['outreach_eligibility'],'pending')
        self.assertFalse(self.view(cid,NOW+timedelta(days=2))['deliverability_verified_email'])
        row,cid2=self.positive();row['name']='Other Person';row['email_evidence']=[{'email':'guess@attitudeliving.com','person_name':'Different Person'}]
        cid2=ingest(self.state,[row],clock=NOW)[0];self.assertFalse(self.view(cid2)['person_linked_email_candidate'])
    def test_original_bounce_and_alias_conflict_cannot_be_overwritten(self):
        self.state.contact('old',self.company['id'],{'name':'Annagiulia Quattrone','linkedin_url':'https://linkedin.com/in/quattrone','blocked':True,'risks':['HISTORICAL_HARD_BOUNCE','AMBIGUOUS_BASELINE_IDENTITY']})
        row=self.row('Giulia Quattrone',linkedin_url='https://linkedin.com/in/quattrone')
        cid=ingest(self.state,[row],clock=NOW)[0];refresh(self.state,NOW)
        self.assertFalse(self.person(cid).get('blocked'));self.assertEqual(self.view(cid)['outreach_eligibility'],'pending')
        self.assertEqual(self.view(cid)['employment_assessment']['status'],'conflicting');self.assertTrue(self.person('old')['blocked'])
    def test_missing_identifier_dates_and_stale_records_never_qualify(self):
        cid=ingest(self.state,[self.row(original_source_url=None,evidence_date=None,research_date=None,stale=True)],clock=NOW)[0]
        view=self.view(cid);self.assertFalse(view['confirmed_current_employment']);self.assertEqual(view['outreach_eligibility'],'pending')
        self.assertIn('PERSONAL_LINKEDIN_IDENTIFIER_MISSING',view['record']['unresolved_risks'])
    def test_duplicate_company_rows_require_explicit_source_row(self):
        other=State(self.root/'duplicates');self.addCleanup(other.close)
        other.initialize([{'position':i,'source_row':i+1,'company':'ATTITUDE','original':{'Brand':'ATTITUDE'},'mapped':{'company':'ATTITUDE'}} for i in (1,2)],'duplicates')
        with self.assertRaises(ValueError):ingest(other,[self.row()],clock=NOW)
        for c in other.companies():ingest(other,[self.row(company_row_id=c['id'])],clock=NOW)
        self.assertEqual(len(other.contacts()),2);self.assertEqual([c['position'] for c in other.companies()],[1,2])
    def test_provider_history_warning_stays_pending_and_never_becomes_clear(self):
        cid=ingest(self.state,[self.row(linkedin_url='https://linkedin.com/in/amael-example')],clock=NOW)[0]
        provider_review(self.state,self.company['id'],{'contacts':[{'contact_id':cid,'matches':[{'id':'synthetic'}],'previous_outreach_confirmed':True}]},'dripify',NOW)
        refresh(self.state,NOW);v=self.view(cid)
        self.assertFalse(v['provider_reviews']['dripify'][0]['coverage_complete']);self.assertEqual(v['outreach_eligibility'],'pending')
        self.assertIn('EXISTING_OR_PREVIOUS_OUTREACH_REQUIRES_REVIEW',v['record']['unresolved_risks'])
    def test_eligibility_runner_consumes_canonical_evidence_record(self):
        cid=ingest(self.state,[self.row()],clock=NOW)[0]
        result=evaluate_contacts(self.state,self.company)['contacts'][0]
        self.assertEqual(result['contact_id'],cid);self.assertEqual(result['decision'],'pending')
        self.assertTrue(any('IDENTITY' in code for code in result['reason_codes']))
    def test_workbook_and_progress_use_same_positive_record_and_expiry(self):
        row,cid=self.positive(email=True);ingest(self.state,[row],clock=NOW)
        path=self.root/'positive.xlsx';update(self.state,path,clock=NOW)
        book=load_workbook(path);metrics={r[2]:r[3] for r in list(book['Batch Summary'].values)[1:] if str(r[0]).startswith('AUTO-CANDIDATE-')};book.close()
        self.assertEqual(metrics['confirmed_current_employment'],1);self.assertEqual(metrics['deliverability_verified_email'],1)
        self.assertEqual(metrics['confirmed_current_employment'],snapshot(self.state,NOW)['candidate_evidence_counts']['confirmed_current_employment'])
        update(self.state,path,clock=NOW+timedelta(days=2));book=load_workbook(path)
        metrics={r[2]:r[3] for r in list(book['Batch Summary'].values)[1:] if str(r[0]).startswith('AUTO-CANDIDATE-')};book.close()
        self.assertEqual(metrics['confirmed_current_employment'],0);self.assertEqual(metrics['deliverability_verified_email'],0)
    def test_positive_verification_never_clears_historical_bounce(self):
        row,cid=self.positive(email=True);ingest(self.state,[row],clock=NOW)
        p=self.person(cid);p['blocked']=True;p['risks']=['HISTORICAL_HARD_BOUNCE'];self.state.contact(cid,self.company['id'],p)
        self.assertTrue(self.view(cid)['deliverability_verified_email'])
        self.assertEqual(self.view(cid)['outreach_eligibility'],'blocked');self.assertTrue(self.person(cid)['blocked'])
    def test_unverified_envelope_cannot_grant_current_employment(self):
        row,cid=self.positive();row['envelopes'][0]['provenance']['capability_verified']=False
        ingest(self.state,[row],clock=NOW)
        self.assertFalse(self.view(cid)['confirmed_current_employment']);self.assertEqual(self.view(cid)['outreach_eligibility'],'pending')
    def test_exact_profile_group_deduplicates_sources_but_preserves_facts(self):
        row=self.row(linkedin_url='https://www.linkedin.com/in/amael-example/')
        a=ingest(self.state,[row],clock=NOW)
        b=ingest(self.state,[{**row,'linkedin_url':'https://linkedin.com/in/amael-example','original_source_url':'https://attitudeliving.com/team'}],clock=NOW)
        self.assertEqual(a,b);self.assertEqual(len(self.state.contacts()),1)
        self.assertEqual(len(self.person(a[0])['handoff']['facts']),2)
        self.assertEqual(snapshot(self.state,NOW)['candidate_evidence_counts']['discovered_candidate'],1)
        self.assertEqual(snapshot(self.state,NOW)['companies_with_unresolved_issues'],1)

    def test_validate_only_preserves_every_file_and_missing_project(self):
        from unittest.mock import patch
        from contextlib import redirect_stdout, redirect_stderr
        import io
        from source_batch import main
        path=self.root/'input.json';path.write_text(json.dumps([self.row()]))
        def files():
            return {str(p): (p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        args=['source_batch','--project',str(self.state.directory),'candidate-handoff','--input',str(path),'--batch','1','--workbook',str(self.root/'never.xlsx')]
        before=files()
        with patch('sys.argv',args),redirect_stdout(io.StringIO()),patch('source_batch.write_reports',side_effect=AssertionError('write')),patch('source_batch.update',side_effect=AssertionError('write')):
            main()
        self.assertEqual(before,files())
        args[2]=str(self.root/'missing')
        with patch('sys.argv',args),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):main()
        self.assertFalse((self.root/'missing').exists())
        path.write_text(json.dumps([self.row(company='Wrong')]))
        args[2]=str(self.state.directory);before=files()
        with patch('sys.argv',args),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):main()
        self.assertEqual(before,files())

    def test_bounced_email_normalization_independent_of_profile_and_name(self):
        for profile in ('','https://linkedin.com/in/different'):
            with self.subTest(profile=profile):
                self.state.contact('bounce',self.company['id'],{'name':'Original Person','email':' BOUNCED@Example.COM ', 'blocked':True,'risks':['HISTORICAL_HARD_BOUNCE']})
                row=self.row(email='bounced@example.com',linkedin_url=profile)
                cid=ingest(self.state,[row],clock=NOW)[0]
                self.assertEqual(self.view(cid)['outreach_eligibility'],'blocked')
                self.assertIn('bounce',self.person(cid)['retained_bounce_sources'])
                self.assertTrue(self.person('bounce')['blocked'])
        clean=ingest(self.state,[self.row('Other Person',email='other@example.com')],clock=NOW)[0]
        self.assertNotEqual(self.view(clean)['outreach_eligibility'],'blocked')

    def test_refresh_inherits_later_bounce_by_email(self):
        cid=ingest(self.state,[self.row(email='known@example.com')],clock=NOW)[0]
        self.state.contact('later',self.company['id'],{'name':'Other','email':'KNOWN@example.com','risks':['HISTORICAL_HARD_BOUNCE']})
        refresh(self.state,NOW)
        self.assertEqual(self.view(cid)['outreach_eligibility'],'blocked')

    def test_profile_addition_and_correction_keep_identity_and_history(self):
        row=self.row();cid=ingest(self.state,[row],clock=NOW)[0]
        for profile in ('https://linkedin.com/in/first','https://linkedin.com/in/corrected'):
            self.assertEqual(ingest(self.state,[{**row,'linkedin_url':profile}],clock=NOW),[cid])
        self.assertEqual(len(self.state.contacts()),1)
        self.assertEqual(len(self.person(cid)['handoff']['facts']),3)
        self.assertIn('IDENTITY_CONFLICT_PROFILE_CORRECTION_REQUIRES_REVIEW',self.person(cid)['risks'])
        self.assertEqual(self.view(cid)['outreach_eligibility'],'pending')
        with self.assertRaises(ValueError):ingest(self.state,[{**row,'name':'Different Person','candidate_record_id':cid}],clock=NOW)

    def test_ambiguous_source_or_profile_requires_explicit_reconciliation(self):
        row=self.row();first=ingest(self.state,[row],clock=NOW)[0]
        other={**row,'original_source_url':'https://attitudeliving.com/team','linkedin_url':'https://linkedin.com/in/second'}
        second=ingest(self.state,[other],clock=NOW)[0]
        with self.assertRaises(ValueError):ingest(self.state,[{**row,'linkedin_url':other['linkedin_url']}],clock=NOW)
        self.assertEqual(len(self.state.contacts()),2)
        ids=ingest(self.state,[{**row,'candidate_record_id':first,'linkedin_url':'https://linkedin.com/in/reviewed'}],clock=NOW)
        self.assertEqual(ids,[first]);self.assertEqual(len(self.state.contacts()),2)

    def test_provider_absence_requires_complete_current_verified_evidence(self):
        for provider in ('hubspot','dripify'):
            for problem in ('none','stale','partial','unverified','failed'):
                with self.subTest(provider=provider,problem=problem):
                    row,cid=self.positive();env=deepcopy(row['envelopes'][0]);env['check']=provider+'_duplicate';env['evidence']['result']='absent'
                    if problem=='stale':env['evidence']['expires_at']='2030-01-01T00:00:00Z'
                    if problem=='partial':env['provenance']['pagination_complete']=False
                    if problem=='unverified':env['provenance']['coverage_verified']=False
                    if problem=='failed':env['provenance']['lookup_succeeded']=False
                    row['envelopes']=[env]
                    # Isolate each observation so old verified evidence cannot mask a failure.
                    state=State(self.root/(provider+problem));self.addCleanup(state.close)
                    state.initialize([self.company['data']],'fixture')
                    # initialize expects source import fields, already present in company payload.
                    ingest(state,[row],clock=NOW)
                    person=state.contacts()[0]
                    v=review(person['data'],person['id'],'ATTITUDE',NOW)
                    self.assertEqual(v[provider+'_status'],'absent' if problem=='none' else 'pending')

    def test_unresolved_rows_have_person_identifiers_and_evidence_idempotently(self):
        row=self.row(linkedin_url='https://linkedin.com/in/amael-example')
        cid=ingest(self.state,[row],clock=NOW)[0];path=self.root/'issues.xlsx'
        update(self.state,path,clock=NOW);update(self.state,path,clock=NOW)
        book=load_workbook(path);rows=list(book['Unresolved Issues'].values)[1:];book.close()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][3:6],(row['name'],row['job_title'],row['linkedin_url']))
        self.assertIn(cid,rows[0][6]);self.assertIn(row['original_source_url'],rows[0][6]);self.assertIn('pending_reasons',rows[0][6])

    def test_existing_blank_issue_rows_are_repaired_without_losing_history(self):
        row=self.row(linkedin_url='https://linkedin.com/in/amael-example')
        cid=ingest(self.state,[row],clock=NOW)[0];path=self.root/'legacy-issues.xlsx'
        update(self.state,path,clock=NOW);book=load_workbook(path)
        sheet=book['Unresolved Issues'];original=sheet.cell(2,7).value.split('; ')[0]
        for col in (4,5,6):sheet.cell(2,col).value=None
        sheet.cell(2,7).value=original;book.save(path);book.close()
        update(self.state,path,clock=NOW);book=load_workbook(path);sheet=book['Unresolved Issues']
        self.assertEqual(sheet.max_row,2);self.assertEqual(sheet.cell(2,4).value,row['name'])
        self.assertTrue(sheet.cell(2,7).value.startswith(original));self.assertIn(cid,sheet.cell(2,7).value);book.close()

    def test_all_ten_email_bounce_blocks_survive_intake_and_refresh(self):
        for n in range(10):
            self.state.contact('bounce-'+str(n),self.company['id'],{'name':'Prior '+str(n),'email':f'blocked{n}@example.com','blocked':True,'risks':['HISTORICAL_HARD_BOUNCE']})
            cid=ingest(self.state,[self.row('Candidate '+str(n),email=f' BLOCKED{n}@EXAMPLE.COM ')],clock=NOW)[0]
            self.assertEqual(self.view(cid)['outreach_eligibility'],'blocked')
        refresh(self.state,NOW)
        self.assertEqual(sum(c['data'].get('blocked') is True for c in self.state.contacts()),20)

    def test_missing_source_cannot_silently_merge_same_named_people(self):
        row=self.row(original_source_url=None)
        cid=ingest(self.state,[row],clock=NOW)[0]
        with self.assertRaises(ValueError):ingest(self.state,[{**row,'linkedin_url':'https://linkedin.com/in/new'}],clock=NOW)
        self.assertEqual(len(self.state.contacts()),1)
        self.assertEqual(ingest(self.state,[{**row,'candidate_record_id':cid,'linkedin_url':'https://linkedin.com/in/new'}],clock=NOW),[cid])
