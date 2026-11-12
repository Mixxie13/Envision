"""Synthetic compatibility fixtures, not captured/verified Apollo account responses."""
import sys,json,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import Mock,patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from sourcing_state import State
from apollo import ApolloPeople,normalize_person,PATH

class ApolloTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state=State(self.tmp.name);self.addCleanup(self.state.close)
        self.state.initialize([{'position':i,'source_row':i+1,'company':name,'original':{'Brand':name},'mapped':{'company':name,'website':'https://attitudeliving.com'}} for i,name in ((1,'ATTITUDE'),(2,'ATTITUDE'))],'synthetic')
        self.company=self.state.companies(1)[0]
        pause=patch('apollo.time.sleep');pause.start();self.addCleanup(pause.stop)
    def authorize(self):
        self.state.set_meta('apollo_read_authorization',{'approved':True,'evidence_ref':'synthetic-permission','max_requests':4,'response_contract_verified':True,'schema_review_ref':'synthetic-contract'})
        self.state.set_meta('apollo_company_binding:'+self.company['id'],{'verified':True,'domain':'attitudeliving.com','evidence_ref':'synthetic-domain-proof'})
    def person(self,identifier='one',first='Amael',last='Proulx-Lussier',title='Brand Manager',domain='attitudeliving.com'):
        return {'id':identifier,'first_name':first,'last_name':last,'title':title,'organization':{'id':'org1','primary_domain':domain},'linkedin_url':'https://linkedin.com/in/synthetic-'+identifier}
    def test_disabled_and_unapproved_never_read_credential_or_dispatch(self):
        fetch=Mock()
        with patch('apollo.os.environ.get',side_effect=AssertionError('Secret accessed')):
            self.assertEqual(ApolloPeople(self.state,fetch=fetch).discover(self.company)['reason'],'APOLLO_DISABLED')
            self.assertEqual(ApolloPeople(self.state,True,fetch).discover(self.company)['reason'],'APOLLO_LIVE_READ_NOT_AUTHORIZED')
        fetch.assert_not_called()
    def test_verified_domain_required_before_lookup(self):
        self.state.set_meta('apollo_read_authorization',{'approved':True,'evidence_ref':'synthetic'})
        fetch=Mock();result=ApolloPeople(self.state,True,fetch).discover(self.company)
        self.assertEqual(result['reason'],'VERIFIED_COMPANY_DOMAIN_REQUIRED');fetch.assert_not_called()
    def test_manual_attitude_names_are_unverified_and_sales_role_excluded(self):
        self.authorize()
        people=[self.person(),self.person('two','Maude','Paquin','Gestionnaire Marketing'),self.person('three','Melissa','Lusignan','Vice President of Sales, North America')]
        fetch=Mock(return_value={'people':people,'total_entries':3})
        with patch.dict('os.environ',{'APOLLO_API_KEY':'synthetic-key'}):result=ApolloPeople(self.state,True,fetch).discover(self.company)
        self.assertEqual([p['name'] for p in result['shortlist']],['Amael Proulx-Lussier','Maude Paquin'])
        self.assertEqual(result['candidates'][1]['provider_title'],'Gestionnaire Marketing')
        self.assertFalse(any(p['current_employment_confirmed'] for p in result['candidates']))
        self.assertFalse(result['coverage_complete'])
        params=fetch.call_args.args[0]
        self.assertFalse(any('location' in k or 'email' in k or 'saved' in k for k in params))
        self.assertEqual(params['q_organization_domains_list[]'],['attitudeliving.com'])
    def test_past_or_similarly_named_employer_rejected(self):
        self.assertEqual(normalize_person(self.person(domain='other-attitude.com'),'attitudeliving.com')['reason'],'CURRENT_EMPLOYER_DOMAIN_MISMATCH_OR_MISSING')
        p=self.person();p['organization'].pop('primary_domain')
        self.assertEqual(normalize_person(p,'attitudeliving.com')['reason'],'CURRENT_EMPLOYER_DOMAIN_MISMATCH_OR_MISSING')
    def test_masked_missing_names_are_never_invented_and_email_phone_dropped(self):
        p=self.person();p.pop('last_name');p['last_name_obfuscated']='P***';p['email']='not-to-store@example.com';p['phone_numbers']=['not-to-store']
        result=normalize_person(p,'attitudeliving.com')
        self.assertFalse(result['identity_complete']);self.assertEqual(result['name'],'')
        self.assertNotIn('email',result);self.assertNotIn('phone_numbers',result)
    def test_parent_mapping_does_not_assign_brand_responsibility(self):
        result=normalize_person(self.person(),'attitudeliving.com',parent=True)
        self.assertTrue(result['brand_assignment_pending'])
        self.assertFalse(result['outreach_ready'])
    def test_page_resume_and_duplicate_source_rows_keep_separate_identity(self):
        self.authorize();calls=[]
        def fetch(params,key):
            calls.append(params['page']);return {'people':[self.person(str(params['page']))], 'total_entries':26}
        with patch.dict('os.environ',{'APOLLO_API_KEY':'synthetic-key'}):
            first=ApolloPeople(self.state,True,fetch,max_requests=1).discover(self.company)
            self.assertEqual(first['next_page'],2)
            second=ApolloPeople(self.state,True,fetch,max_requests=1).discover(self.company)
            self.assertTrue(second['pagination_complete'])
            ApolloPeople(self.state,True,fetch).discover(self.company)
        self.assertEqual(calls,[1,2]);self.assertEqual(len(self.state.companies()),2)
    def test_unknown_schema_or_rate_limit_stop_without_retry(self):
        self.authorize();fetch=Mock(return_value={'unknown':'schema'})
        with patch.dict('os.environ',{'APOLLO_API_KEY':'synthetic-key'}):
            client=ApolloPeople(self.state,True,fetch);result=client.discover(self.company);client.discover(self.company)
        self.assertEqual(fetch.call_count,1);self.assertEqual(result['reason'],'UNVERIFIED_RESPONSE_SCHEMA')
    def test_fixed_host_no_redirect_timeout_key_header(self):
        with patch('apollo.urllib.request.build_opener') as build:
            response=MagicMock();response.__enter__.return_value=response;response.status=200;response.read.return_value=b'{"people":[]}'
            build.return_value.open.return_value=response
            ApolloPeople._fetch({'page':1},'synthetic-key')
            req=build.return_value.open.call_args.args[0]
            self.assertTrue(req.full_url.startswith('https://api.apollo.io'+PATH+'?'))
            self.assertEqual(req.get_header('X-api-key'),'synthetic-key');self.assertNotIn('synthetic-key',req.full_url)
            self.assertEqual(build.return_value.open.call_args.kwargs['timeout'],20)
            from email_api import NoRedirect
            self.assertIsInstance(build.call_args.args[0],NoRedirect)
    def test_ambiguous_profile_keeps_candidate_pending_and_original_bounce(self):
        from provider_evidence import Providers
        self.authorize()
        self.state.contact('hs:old',self.company['id'],{'name':'Different Name','linkedin_url':self.person()['linkedin_url'],'blocked':True,'risks':['HISTORICAL_HARD_BOUNCE']})
        provider=Providers(self.state)
        provider.apollo_client=ApolloPeople(self.state,True,Mock(return_value={'people':[self.person()],'total_entries':1}))
        with patch.dict('os.environ',{'APOLLO_API_KEY':'synthetic-key'}):provider.apollo(self.company)
        rows=[c['data'] for c in self.state.contacts() if c['id'].startswith('apollo:')]
        self.assertFalse(rows[0]['blocked']);self.assertNotIn('HISTORICAL_HARD_BOUNCE',rows[0]['risks']);self.assertIn('IDENTITY_CONFLICT',rows[0]['risks'])
        self.assertTrue(next(c['data']['blocked'] for c in self.state.contacts() if c['id']=='hs:old'))

    def test_ambiguous_organization_cache_remains_pending_on_reuse(self):
        from provider_evidence import Providers
        self.authorize();one=self.person();two=self.person('two','Maude','Paquin','Marketing Manager');two['organization']['id']='other-org'
        fetch=Mock(return_value={'people':[one,two],'total_entries':2})
        with patch.dict('os.environ',{'APOLLO_API_KEY':'synthetic-key'}):
            for _ in range(2):
                provider=Providers(self.state);provider.apollo_client=ApolloPeople(self.state,True,fetch)
                result=provider.apollo(self.company)
                self.assertEqual(result['reason'],'MULTIPLE_CURRENT_ORGANIZATION_IDS_REVIEW_REQUIRED')
                self.assertEqual(result['shortlist'],[])
        self.assertEqual(len(self.state.contacts()),0);self.assertEqual(fetch.call_count,1)

    def test_probe_does_not_create_contacts_until_response_contract_review(self):
        from provider_evidence import Providers
        self.authorize()
        self.state.set_meta('apollo_read_authorization',{'approved':True,'evidence_ref':'synthetic-probe','max_requests':1})
        provider=Providers(self.state);provider.apollo_client=ApolloPeople(self.state,True,Mock(return_value={'people':[self.person()],'total_entries':1}))
        with patch.dict('os.environ',{'APOLLO_API_KEY':'synthetic-key'}):result=provider.apollo(self.company)
        self.assertFalse(result['account_schema_verified']);self.assertEqual(len(self.state.contacts()),0)
