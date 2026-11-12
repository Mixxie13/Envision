"""Synthetic direct-match and recoverable multi-file import regressions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from openpyxl import Workbook, load_workbook
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import suppression_import as imp
from screening import reconcile, project, enforce
from sourcing_state import State, project_lock
from workbook import update
from master_progress import write_reports


class DirectSuppressionTests(unittest.TestCase):
    def registry(self):
        return {'entries':[{'id':'suppression:source','email':'bounced@example.com','email_aliases':[], 'hubspot_contact_id':'123'}]}
    def row(self,cid,**fields):return {'id':cid,'data':{'name':'Synthetic',**fields}}

    def test_conflicting_transitive_crm_chain_never_blocks_unrelated_address(self):
        rows=[self.row('exact',email='bounced@example.com',hubspot_contact_id='999'),
              self.row('unrelated',email='clean@example.com',hubspot_contact_id='999')]
        result=reconcile(rows,self.registry())
        self.assertTrue(result[0]['data']['blocked'])
        self.assertFalse(result[1]['data']['blocked'])
        self.assertIn('AMBIGUOUS_IDENTITY_LINK_REQUIRES_REVIEW',result[1]['data']['risks'])
        self.assertEqual(project(result[1]['data'],'unrelated','Brand')['decision'],'pending')

    def test_alias_and_profile_chains_are_not_authoritative(self):
        rows=[self.row('exact',email='bounced@example.com',linkedin_url='https://linkedin.com/in/shared'),
              self.row('alias',email='clean@example.com',email_aliases=['bounced@example.com'],linkedin_url='https://linkedin.com/in/shared'),
              self.row('profile',email='other@example.com',linkedin_url='https://linkedin.com/in/shared')]
        result=reconcile(rows,self.registry())
        self.assertEqual([r['data']['blocked'] for r in result],[True,False,False])
        # Re-projecting must not promote any derived block to a new source.
        again=reconcile(result,self.registry())
        self.assertEqual([r['data']['blocked'] for r in again],[True,False,False])

    def test_direct_alias_with_conflicting_crm_id_stays_pending(self):
        rows=[self.row('ambiguous',email='clean@example.com',confirmed_email_aliases=['bounced@example.com'],hubspot_contact_id='999')]
        result=reconcile(rows,self.registry())[0]['data']
        self.assertFalse(result['blocked']);self.assertIn('AMBIGUOUS_SUPPRESSION_LINK_REQUIRES_REVIEW',result['risks'])

    def test_exact_address_stays_blocked_despite_conflicting_name_id_and_profile(self):
        result=reconcile([self.row('exact',name='Different',email=' BOUNCED@EXAMPLE.COM ',hubspot_contact_id='999',linkedin_url='https://linkedin.com/in/different')],self.registry())
        self.assertTrue(result[0]['data']['blocked'])

    def test_unverified_crm_hint_does_not_block_but_verified_record_does(self):
        rows=[self.row('hint',hubspot_contact_id='123'),
              self.row('verified',hubspot_contact_id='123',raw_record={'id':'123','properties':{}})]
        self.assertEqual([r['data']['blocked'] for r in reconcile(rows,self.registry())],[False,True])

    def test_derived_checks_cannot_be_persisted_as_new_native_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=State(tmp);s.initialize([{'position':1,'source_row':2,'company':'Brand','original':{},'mapped':{}}],'fixture')
            company=s.companies()[0]['id']
            s.contact('exact',company,{'name':'Person A','email':'bounced@example.com'})
            s.set_meta(imp.REGISTRY,self.registry())
            observed=s.contacts()[0]['data']
            observed['evidence_record']=enforce(observed,{'subject':{},'checks':{}})
            s.contact('exact',company,observed)
            raw=json.loads(s.db.execute('SELECT payload FROM contacts WHERE id=?',('exact',)).fetchone()[0])
            self.assertFalse(raw.get('blocked'));self.assertNotIn('KNOWN_SUPPRESSION_RESTRICTION',raw['risks'])
            self.assertNotIn('_suppression_projection',raw)
            self.assertTrue(s.contacts()[0]['data']['blocked']);s.close()


class AtomicImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.project=self.root/'project';self.book=self.root/'book.xlsx';self.source=self.root/'source.xlsx'
        s=State(self.project);s.initialize([{'position':1,'source_row':2,'company':'Brand','original':{},'mapped':{}}],'fixture')
        s.contact('p',s.companies()[0]['id'],{'name':'Jane Example','email':'bounced@example.com','role':'Brand Manager'})
        update(s,self.book);write_reports(s);s.close()
        b=Workbook();b.active.append(['Record ID - Contact','Email']);b.active.append(['123','bounced@example.com']);b.save(self.source);b.close()
        self.info=imp.inspect_export(self.source)
        self.paths=[self.project/'state.sqlite',self.book,self.project/'master-progress.json',self.project/'master-progress.csv']
    def snapshot(self):return {str(p):(p.read_bytes(),p.stat().st_mode & 0o777,p.stat().st_mtime_ns) if p.exists() else None for p in self.paths}
    def assert_clean(self,before):
        self.assertEqual(self.snapshot(),before);self.assertFalse((self.project/imp.JOURNAL).exists())
        self.assertFalse(list(self.project.glob('.suppression-stage-*')))

    def test_invalid_workbook_leaves_all_data_unchanged(self):
        b=Workbook();b.save(self.book);b.close();before=self.snapshot()
        with self.assertRaises(ValueError):imp.apply_atomic(self.project,self.book,self.info)
        self.assert_clean(before)

    def test_malformed_later_row_is_rejected_before_any_write(self):
        before=self.snapshot();bad=json.loads(json.dumps(self.info));bad['entries'].append({});bad['data_rows']=2
        with self.assertRaises(ValueError):imp.apply_atomic(self.project,self.book,bad)
        self.assert_clean(before)
        b=load_workbook(self.source);b.active.append(['124','not-an-email']);b.save(self.source);b.close()
        with self.assertRaises(ValueError):imp.inspect_export(self.source)
        self.assert_clean(before)

    def test_interrupted_workbook_save_rolls_back_nothing_was_published(self):
        before=self.snapshot()
        with patch('openpyxl.workbook.workbook.Workbook.save',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):imp.apply_atomic(self.project,self.book,self.info)
        self.assert_clean(before)

    def test_report_failure_after_staged_json_leaves_original_reports_unchanged(self):
        before=self.snapshot()
        import master_progress
        original=master_progress.atomic_text
        def fail_csv(path,text):
            if str(path).endswith('.csv'):raise OSError('synthetic interrupted report save')
            return original(path,text)
        with patch('master_progress.atomic_text',side_effect=fail_csv):
            with self.assertRaises(OSError):imp.apply_atomic(self.project,self.book,self.info)
        self.assert_clean(before)

    def test_mid_publication_failure_restores_registry_workbook_and_reports(self):
        before=self.snapshot();publish=imp._publish;calls=[]
        def fail_second(staged,destination):
            calls.append(destination)
            if len(calls)==2:raise OSError('synthetic interruption')
            publish(staged,destination)
        with patch.object(imp,'_publish',side_effect=fail_second):
            with self.assertRaises(OSError):imp.apply_atomic(self.project,self.book,self.info)
        self.assert_clean(before)

    def test_last_publication_keyboard_interrupt_restores_even_already_written_reports(self):
        before=self.snapshot();publish=imp._publish;calls=[]
        def fail_last(staged,destination):
            calls.append(destination);publish(staged,destination)
            if len(calls)==4:raise KeyboardInterrupt()
        with patch.object(imp,'_publish',side_effect=fail_last):
            with self.assertRaises(KeyboardInterrupt):imp.apply_atomic(self.project,self.book,self.info)
        self.assert_clean(before)

    def test_process_crash_is_guarded_then_recovery_restores_all_outputs(self):
        before=self.snapshot()
        code="""import sys,os
sys.path.insert(0,sys.argv[1])
import suppression_import as m
publish=m._publish
def crash(staged,destination):
 publish(staged,destination)
 os._exit(73)
m._publish=crash
m.apply_atomic(sys.argv[2],sys.argv[3],m.inspect_export(sys.argv[4]))
"""
        result=subprocess.run([sys.executable,'-B','-c',code,str(Path(imp.__file__).parent),str(self.project),str(self.book),str(self.source)],capture_output=True)
        self.assertEqual(result.returncode,73)
        with self.assertRaises(ValueError):State(self.project,readonly=True)
        with project_lock(self.project):imp.recover_import(self.project,self.book)
        self.assert_clean(before)

    def test_repeated_success_has_no_duplicate_registry_or_evidence_records(self):
        result=imp.apply_atomic(self.project,self.book,self.info)
        self.assertEqual(result['newly_blocked_existing_records'],1)
        s=State(self.project,readonly=True);registry=s.meta(imp.REGISTRY);count=len(s.observations());s.close()
        b=load_workbook(self.book);rows={n:b[n].max_row for n in b.sheetnames};b.close()
        again=imp.apply_atomic(self.project,self.book,self.info)
        s=State(self.project,readonly=True);self.assertEqual(s.meta(imp.REGISTRY),registry);self.assertEqual(len(s.observations()),count);s.close()
        b=load_workbook(self.book);self.assertEqual({n:b[n].max_row for n in b.sheetnames},rows);b.close()
        self.assertEqual(again['new_registry_emails'],0)
        renamed=self.root/'renamed-copy.xlsx';renamed.write_bytes(self.source.read_bytes())
        imp.apply_atomic(self.project,self.book,imp.inspect_export(renamed))
        s=State(self.project,readonly=True);self.assertEqual(s.meta(imp.REGISTRY),registry);self.assertEqual(len(s.observations()),count);s.close()


class AmbiguousConfirmedClaimsTests(unittest.TestCase):
    def test_contradictory_raw_crm_records_do_not_authorize_id_only_blocks(self):
        registry={'entries':[{'id':'suppression:x','email':'bounced@example.com','hubspot_contact_id':'123'}]}
        rows=[{'id':str(i),'data':{'name':'Person '+str(i),'email':f'clean{i}@example.com','hubspot_contact_id':'123',
              'raw_record':{'id':'123','properties':{'email':f'clean{i}@example.com'}}}} for i in (1,2)]
        result=reconcile(rows,registry)
        self.assertTrue(all(not r['data']['blocked'] for r in result))
        self.assertTrue(all('AMBIGUOUS_SUPPRESSION_LINK_REQUIRES_REVIEW' in r['data']['risks'] for r in result))

    def test_conflicting_confirmed_alias_claims_remain_pending(self):
        registry={'entries':[{'id':'suppression:x','email':'bounced@example.com','hubspot_contact_id':'123'}]}
        rows=[{'id':str(i),'data':{'name':'Person '+str(i),'email':f'clean{i}@example.com',
              'confirmed_email_aliases':['bounced@example.com']}} for i in (1,2)]
        self.assertTrue(all(not r['data']['blocked'] for r in reconcile(rows,registry)))


class AdditionalAtomicPreflightTests(unittest.TestCase):
    setUp = AtomicImportTests.setUp
    snapshot = AtomicImportTests.snapshot
    assert_clean = AtomicImportTests.assert_clean

    def test_failed_staging_backup_cleans_up_and_preserves_outputs(self):
        before=self.snapshot()
        connect=imp.sqlite3.connect;calls=[]
        def fail_backup(*args,**kwargs):
            calls.append(args[0])
            if len(calls)==2:raise OSError('synthetic staging failure')
            return connect(*args,**kwargs)
        with patch.object(imp.sqlite3,'connect',side_effect=fail_backup):
            with self.assertRaises(OSError):imp.apply_atomic(self.project,self.book,self.info)
        self.assert_clean(before)

    def test_source_order_and_missing_project_are_validated_before_writes(self):
        missing=self.root/'missing'
        with self.assertRaises(ValueError):imp.apply_atomic(missing,self.book,self.info)
        self.assertFalse(missing.exists())
        b=load_workbook(self.book);b['Company Research'].cell(2,1).value='Wrong company';b.save(self.book);b.close()
        before=self.snapshot()
        with self.assertRaises(ValueError):imp.apply_atomic(self.project,self.book,self.info)
        self.assert_clean(before)
