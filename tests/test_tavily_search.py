"""Synthetic Basic-only pilot; live sockets forbidden by the offline runner."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sourcing_state import State
from tavily_search import prepare, TavilyBasic, run_pilot, LEDGER, AUTHORIZATION


class TavilyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state = State(self.tmp.name);self.addCleanup(self.state.close)
        self.state.initialize([{'position':i,'source_row':i+1,'company':'Brand '+str(i),
            'original':{'Brand':'Brand '+str(i)},'mapped':{'company':'Brand '+str(i),'website':'https://brand.example.com'}} for i in range(1,11)],'synthetic')
        self.companies = self.state.companies(1)
        prepare(self.state)

    def authorize(self):
        self.state.set_meta(AUTHORIZATION,{'approved':True,'terms_confirmed':True,'evidence_ref':'synthetic-approval'})

    def test_disabled_and_unapproved_do_not_read_key_or_fetch(self):
        fetch=Mock()
        with patch('tavily_search.os.environ.get',side_effect=AssertionError('Credential read before authorization')):
            TavilyBasic(self.state,transport=fetch).collect(self.companies[0])
            TavilyBasic(self.state,enabled=True,transport=fetch).collect(self.companies[0])
        fetch.assert_not_called()

    def test_order_basic_payload_and_lifetime_ten_request_limit(self):
        self.authorize();fetch=Mock(return_value={'results':[],'usage':{'credits':1}})
        with patch.dict('os.environ',{'TAVILY_API_KEY':'synthetic-key'}):
            c=TavilyBasic(self.state,True,fetch)
            self.assertEqual(c.collect(self.companies[1])['reason'],'PILOT_ORIGINAL_ORDER_REQUIRED')
            for company in self.companies:c.collect(company)
            prepare(self.state)
            for company in self.companies:TavilyBasic(self.state,True,fetch).collect(company)
        self.assertEqual(fetch.call_count,10)
        self.assertEqual(len(self.state.meta(LEDGER)['attempted']),10)
        for call in fetch.call_args_list:
            p=call.args[0]
            self.assertEqual(p['search_depth'],'basic')
            self.assertFalse(p['auto_parameters']);self.assertFalse(p['include_raw_content']);self.assertFalse(p['include_answer'])
        self.assertTrue(all(('\"Brand '+str(i)+'\"') in call.args[0]['query'] for i,call in enumerate(fetch.call_args_list,1)))

    def test_failure_reserved_before_dispatch_no_retry_after_restart(self):
        self.authorize()
        def fail(payload,key):
            self.assertEqual(len(self.state.meta(LEDGER)['attempted']),1)
            raise OSError('secret response must not persist')
        with patch.dict('os.environ',{'TAVILY_API_KEY':'synthetic-key'}):
            TavilyBasic(self.state,True,fail).collect(self.companies[0])
            fetch=Mock();TavilyBasic(self.state,True,fetch).collect(self.companies[0]);TavilyBasic(self.state,True,fetch).collect(self.companies[1]);fetch.assert_not_called()
        self.assertNotIn('secret response',json.dumps(self.state.meta(LEDGER)))

    def test_nonbasic_usage_or_schema_stops(self):
        self.authorize()
        fetch=Mock(return_value={'results':[],'usage':{'credits':2}})
        with patch.dict('os.environ',{'TAVILY_API_KEY':'synthetic-key'}):
            c=TavilyBasic(self.state,True,fetch);c.collect(self.companies[0]);c.collect(self.companies[1])
        self.assertTrue(self.state.meta(LEDGER)['stopped']);self.assertEqual(fetch.call_count,1)

    def test_no_tavily_payloads_snippets_results_or_unverified_urls_persist(self):
        self.authorize();url='https://source.example.com/tavily-result-only'
        fetch=Mock(return_value={'results':[{'url':url,'title':'PROVIDER_TITLE_SENTINEL','content':'PROVIDER_SNIPPET_SENTINEL'}],'answer':'RAW_PROVIDER_SENTINEL'})
        researcher=Mock();researcher.collect.return_value={'pages':[{'url':url,'state':'pending','reason':'access denied'}]}
        with patch.dict('os.environ',{'TAVILY_API_KEY':'synthetic-key'}):
            run_pilot(self.state,Path(self.tmp.name)/'book.xlsx',True,fetch,researcher)
        persisted=' '.join(str(tuple(r)) for table in ('meta','observations','cache') for r in self.state.db.execute('SELECT * FROM '+table))
        for forbidden in (url,'PROVIDER_TITLE_SENTINEL','PROVIDER_SNIPPET_SENTINEL','RAW_PROVIDER_SENTINEL','synthetic-key'):
            self.assertNotIn(forbidden,persisted)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM cache').fetchone()[0],0)
        self.assertEqual(len(self.state.contacts()),0)

    def test_only_independently_fetched_source_facts_persist_and_blocks_survive(self):
        self.authorize();company=self.companies[0]
        self.state.contact('blocked',company['id'],{'name':'Jane Example','blocked':True,'risks':['HISTORICAL_HARD_BOUNCE']})
        page={'url':'https://brand.example.com/team','state':'observed','sha256':'verified-source-hash','observed_at':'2030-01-01',
            'text_excerpt':'DO_NOT_KEEP_WHOLE_PAGE','decision_maker_claims':[{'name':'Jane Example','person_evidence':{'quote':'Jane Example — Brand Director'}}]}
        researcher=Mock();researcher.collect.return_value={'pages':[page]}
        with patch.dict('os.environ',{'TAVILY_API_KEY':'synthetic-key'}):
            run_pilot(self.state,Path(self.tmp.name)/'book.xlsx',True,Mock(return_value={'results':[{'url':page['url'],'content':'PROVIDER_SNIPPET_SENTINEL'}]}),researcher)
        saved=json.dumps(self.state.observations())
        self.assertIn('verified-source-hash',saved);self.assertIn('Jane Example',saved)
        self.assertNotIn('PROVIDER_SNIPPET_SENTINEL',saved);self.assertNotIn('DO_NOT_KEEP_WHOLE_PAGE',saved)
        self.assertTrue(self.state.contacts()[0]['data']['blocked'])

    def test_transport_fixed_host_auth_timeout_and_redirect_block(self):
        with patch('tavily_search.urllib.request.build_opener') as build:
            response=MagicMock();response.__enter__.return_value=response;response.status=200;response.read.return_value=b'{"results":[]}'
            build.return_value.open.return_value=response
            TavilyBasic._transport({'search_depth':'basic'},'synthetic-key')
            args=build.return_value.open.call_args
            self.assertEqual(args.args[0].full_url,'https://api.tavily.com/search')
            self.assertEqual(args.args[0].get_header('Authorization'),'Bearer synthetic-key')
            self.assertEqual(args.kwargs['timeout'],20)
            from email_api import NoRedirect
            self.assertIsInstance(build.call_args.args[0],NoRedirect)

    def test_unbound_third_party_directory_does_not_create_company_contacts(self):
        self.authorize()
        researcher=Mock();researcher.collect.return_value={'pages':[{'url':'https://directory.example.com/all-people','state':'observed','sha256':'hash','observed_at':'2030-01-01','decision_maker_claims':[{'name':'Wrong Person','role':'Brand Director, Other Company'}]}]}
        with patch.dict('os.environ',{'TAVILY_API_KEY':'synthetic-key'}):
            run_pilot(self.state,Path(self.tmp.name)/'book.xlsx',True,Mock(return_value={'results':[{'url':'https://directory.example.com/all-people'}]}),researcher)
        self.assertEqual(len(self.state.contacts()),0)
        self.assertFalse(any(o['stage']=='independent-source-pilot' for o in self.state.observations()))

    def test_query_is_brand_domain_and_person_specific(self):
        from tavily_search import build_query
        query=build_query(self.companies[0])
        self.assertIn('"Brand 1"',query)
        self.assertIn('brand.example.com',query)
        self.assertIn('personal LinkedIn',query)
        self.assertIn('brand partnerships',query)

    def test_retest_is_separate_three_query_budget_in_original_order(self):
        from tavily_search import prepare_retest, RETEST_LEDGER, RETEST_AUTHORIZATION
        for i,name in ((2,"Annie's"),(4,'ATTITUDE'),(9,'Beech-Nut')):
            c=self.companies[i-1];payload={**c['data'],'company':name}
            with self.state.db:self.state.db.execute('UPDATE companies SET payload=? WHERE id=?',(json.dumps(payload),c['id']))
        plan=prepare_retest(self.state)
        self.assertEqual(plan['companies'],["Annie's",'ATTITUDE','Beech-Nut'])
        self.assertFalse(plan['enabled'])
        self.authorize() # Original ten-company approval cannot authorize the retest.
        fetch=Mock(return_value={'results':[],'usage':{'credits':1}})
        c=TavilyBasic(self.state,True,fetch,RETEST_LEDGER,RETEST_AUTHORIZATION)
        selected=[self.state.companies(1)[i-1] for i in (2,4,9)]
        self.assertEqual(c.collect(selected[0])['reason'],'TAVILY_APPROVAL_OR_TERMS_PENDING')
        self.state.set_meta(RETEST_AUTHORIZATION,{'approved':True,'terms_confirmed':True,'evidence_ref':'synthetic-retest'})
        with patch.dict('os.environ',{'TAVILY_API_KEY':'synthetic-key'}):
            for company in selected:c.collect(company)
            prepare_retest(self.state)
            for company in selected:c.collect(company)
        self.assertEqual(fetch.call_count,3)
        self.assertEqual(self.state.meta(LEDGER)['attempted'],[])
