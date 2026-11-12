"""Synthetic email-operation contracts, budgets and fail-closed provider facts."""
import sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from sourcing_state import State
from candidate_handoff import ingest,review
from email_workflow import EmailOperations
import test_candidate_handoff as fixtures
NOW=fixtures.NOW


class EmailWorkflowTests(unittest.TestCase):
    setUp=fixtures.CandidateHandoffTests.setUp
    row=fixtures.CandidateHandoffTests.row
    positive=fixtures.CandidateHandoffTests.positive
    person=fixtures.CandidateHandoffTests.person
    view=fixtures.CandidateHandoffTests.view
    def configure(self,result='valid',email=True):
        row,cid=self.positive(email=email)
        if result=='invalid':row['channel']='email'
        ingest(self.state,[row],clock=NOW)
        self.state.set_meta('offline-email-suppression-v1',{'entries':[{'id':'suppression:synthetic','email':'known-bounced@example.com','hubspot_contact_id':'999'}]})
        self.cid=cid;self.contact=next(c for c in self.state.contacts() if c['id']==cid)
        self.state.set_meta('verified_company_domain:'+self.company['id'],{'verified':True,'domain':'attitudeliving.com','evidence_ref':'synthetic-domain','observed_at':'2030-01-02T10:00:00Z','expires_at':'2030-01-03T10:00:00Z'})
        self.state.set_meta('email_operation_authorization',{'approved':True,'evidence_ref':'synthetic-authorization','pricing_ref':'synthetic-max-cost','maximum_cost_verified':True,
            'expires_at':'2030-01-03T10:00:00Z','subject_ids':[cid],'batches':[1],'max_requests':2,'max_credits':2,
            'credits_per_operation':{'neverbounce.verify':1,'findymail.find':1,'findymail.verify':1}})
        self.state.set_meta('email_response_contracts',{operation:{'verified':True,'review_ref':'synthetic-only-not-production','result_path':['synthetic_result'],
            'email_path':['synthetic_email'],'result_map':{result:result}} for operation in ('neverbounce.verify','findymail.find','findymail.verify')})
        return Mock(return_value={'data':{'synthetic_result':result,'synthetic_email':'jane@attitudeliving.com'}})
    def call(self,client,operation='neverbounce.verify',fields=None):
        return client.operation(self.company,self.contact,operation,fields or {'email':'jane@attitudeliving.com'})
    def test_disabled_never_reads_secret_or_calls_transport(self):
        fetch=self.configure();client=EmailOperations(self.state,False,fetch,lambda:NOW)
        with patch('os.environ.get',side_effect=AssertionError('Secret access')):result=self.call(client)
        self.assertEqual(result['reason'],'EMAIL_OPERATIONS_DISABLED');fetch.assert_not_called()
    def test_missing_contract_or_authorization_never_dispatches(self):
        fetch=self.configure();self.state.set_meta('email_response_contracts',{})
        self.assertEqual(self.call(EmailOperations(self.state,True,fetch,lambda:NOW))['reason'],'PROVIDER_RESPONSE_CONTRACT_UNVERIFIED');fetch.assert_not_called()
    def test_pending_employment_and_suppression_prevent_paid_use(self):
        fetch=self.configure();p=self.person(self.cid);p['blocked']=True;self.state.contact(self.cid,self.company['id'],p)
        self.assertEqual(self.call(EmailOperations(self.state,True,fetch,lambda:NOW))['reason'],'PERSON_SUPPRESSED_OR_CURRENT_EMPLOYMENT_UNCONFIRMED');fetch.assert_not_called()
    def test_success_is_durable_and_not_outreach_permission(self):
        fetch=self.configure();client=EmailOperations(self.state,True,fetch,lambda:NOW)
        result=self.call(client);self.assertEqual(result['fact']['result'],'valid');self.assertFalse(result['campaign_ready'])
        self.call(EmailOperations(self.state,True,fetch,lambda:NOW));self.assertEqual(fetch.call_count,1)
        self.assertEqual(len(self.person(self.cid)['handoff']['email_operations']),1)
        self.assertEqual(self.view(self.cid)['outreach_eligibility'],'pending')
    def test_reservation_survives_interruption_without_retry(self):
        fetch=self.configure();fetch.side_effect=KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):self.call(EmailOperations(self.state,True,fetch,lambda:NOW))
        result=self.call(EmailOperations(self.state,True,fetch,lambda:NOW))
        self.assertEqual(result['reason'],'UNCERTAIN_DISPATCH_NO_AUTOMATIC_RETRY');self.assertEqual(fetch.call_count,1)
    def test_error_or_unknown_schema_stops_subsequent_requests(self):
        fetch=self.configure();fetch.return_value={'data':{'unexpected':'not a provider contract'}}
        client=EmailOperations(self.state,True,fetch,lambda:NOW)
        self.assertEqual(self.call(client)['reason'],'PROVIDER_ERROR_OR_UNVERIFIED_RESPONSE_NO_RETRY')
        self.call(client,'findymail.verify');self.assertEqual(fetch.call_count,1)
    def test_invalid_result_blocks_and_cannot_be_duplicated_on_replay(self):
        fetch=self.configure('invalid');client=EmailOperations(self.state,True,fetch,lambda:NOW)
        self.call(client);self.call(client)
        self.assertEqual(self.view(self.cid)['outreach_eligibility'],'blocked')
        self.assertEqual(len([e for e in self.person(self.cid)['handoff']['envelopes'] if e['check']=='verification']),2)
    def test_budget_cannot_be_reset_by_restart(self):
        fetch=self.configure();auth=self.state.meta('email_operation_authorization');auth['max_requests']=auth['max_credits']=1;self.state.set_meta('email_operation_authorization',auth)
        self.call(EmailOperations(self.state,True,fetch,lambda:NOW))
        self.assertEqual(self.call(EmailOperations(self.state,True,fetch,lambda:NOW),'findymail.verify')['reason'],'AUTHORIZED_BUDGET_EXHAUSTED_OR_PROVIDER_PAUSED');self.assertEqual(fetch.call_count,1)
    def test_stale_domain_or_wrong_email_cannot_dispatch(self):
        fetch=self.configure();client=EmailOperations(self.state,True,fetch,lambda:NOW)
        self.assertEqual(self.call(client,fields={'email':'someone-else@example.com'})['reason'],'REQUEST_SUBJECT_OR_DOMAIN_MISMATCH')
        self.state.set_meta('verified_company_domain:'+self.company['id'],{'verified':True,'domain':'attitudeliving.com','evidence_ref':'stale'})
        self.assertEqual(self.call(client)['reason'],'EMPLOYEE_DOMAIN_EVIDENCE_REQUIRED');fetch.assert_not_called()
    def test_catchall_cannot_be_overridden_by_second_positive_provider(self):
        fetch=self.configure('catch_all');self.call(EmailOperations(self.state,True,fetch,lambda:NOW))
        contracts=self.state.meta('email_response_contracts');contracts['findymail.verify']['result_map']={'valid':'valid'};self.state.set_meta('email_response_contracts',contracts)
        fetch.return_value={'data':{'synthetic_result':'valid'}}
        self.call(EmailOperations(self.state,True,fetch,lambda:NOW),'findymail.verify')
        self.assertIn('UNRESOLVED_EMAIL_PROVIDER_RISK',self.view(self.cid)['record']['unresolved_risks'])
        self.assertEqual(self.view(self.cid)['outreach_eligibility'],'pending')
    def test_finder_fact_does_not_invent_public_person_email_confirmation(self):
        fetch=self.configure('candidate',email=False)
        result=self.call(EmailOperations(self.state,True,fetch,lambda:NOW),'findymail.find',{'name':'Jane Example','domain':'attitudeliving.com'})
        self.assertEqual(result['fact']['result'],'candidate');self.assertFalse(self.view(self.cid)['person_linked_email_candidate'])
        self.assertFalse(self.view(self.cid)['deliverability_verified_email'])
    def test_completed_receipt_recovers_after_local_save_failure_without_new_request(self):
        fetch=self.configure();client=EmailOperations(self.state,True,fetch,lambda:NOW)
        with patch.object(client,'_record',side_effect=OSError('synthetic interrupted local save')):
            with self.assertRaises(OSError):self.call(client)
        self.assertNotIn('email_operations',self.person(self.cid)['handoff'])
        EmailOperations(self.state,False,fetch,lambda:NOW).collect(self.company)
        self.assertEqual(len(self.person(self.cid)['handoff']['email_operations']),1)
        self.assertEqual(fetch.call_count,1)
    def test_missing_or_unverified_maximum_credit_cost_cannot_dispatch(self):
        fetch=self.configure();auth=self.state.meta('email_operation_authorization');auth['maximum_cost_verified']=False;self.state.set_meta('email_operation_authorization',auth)
        self.assertEqual(self.call(EmailOperations(self.state,True,fetch,lambda:NOW))['reason'],'EMAIL_BUDGET_OR_SCOPE_NOT_AUTHORIZED');fetch.assert_not_called()
    def test_email_dispatch_requires_private_suppression_before_any_credit_use(self):
        fetch=self.configure();self.state.set_meta('offline-email-suppression-v1',{})
        self.assertEqual(self.call(EmailOperations(self.state,True,fetch,lambda:NOW))['reason'],'PRIVATE_SUPPRESSION_REGISTRY_REQUIRED');fetch.assert_not_called()
