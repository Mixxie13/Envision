"""Synthetic permitted-search and access-challenge behavior, no network."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from public_search import PublicSearch


class SearchTests(unittest.TestCase):
    def test_results_are_source_candidates_not_confirmation(self):
        def fetch(url):
            return {'state':'observed','body':'User-agent: *\nAllow: /' if url.endswith('robots.txt') else '<a class="result__a" href="https://brand.example.com/team">Team</a>'}
        result=PublicSearch(fetch,pause=lambda _:None).collect('Brand')
        self.assertEqual(result['sources'],['https://brand.example.com/team'])
        self.assertEqual(result['state'],'pending')

    def test_challenge_stops_subsequent_queries_without_bypass(self):
        calls=[]
        def fetch(url):
            calls.append(url)
            return {'state':'observed','body':'User-agent: *\nAllow: /' if url.endswith('robots.txt') else '<form id="challenge-form">Verify you are human</form>'}
        search=PublicSearch(fetch,pause=lambda _:None)
        self.assertEqual(search.collect('One')['reason'],'PUBLIC_SEARCH_ACCESS_CHALLENGE')
        self.assertEqual(search.collect('Two')['reason'],'PUBLIC_SEARCH_STOPPED_FOR_ACCESS_LIMIT')
        self.assertEqual(len(calls),2)

    def test_robots_denial_does_not_issue_query(self):
        calls=[]
        def fetch(url):calls.append(url);return {'state':'observed','body':'User-agent: *\nDisallow: /html/'}
        result=PublicSearch(fetch,pause=lambda _:None).collect('Brand')
        self.assertEqual(result['reason'],'PUBLIC_SEARCH_NOT_PERMITTED')
        self.assertEqual(len(calls),1)


class AuthorizedSearchTests(unittest.TestCase):
    def test_unapproved_api_does_not_fetch(self):
        from public_search import BraveSearch
        from unittest.mock import Mock
        fetch=Mock()
        self.assertEqual(BraveSearch(fetch=fetch).collect('Brand')['reason'],'PUBLIC_SEARCH_NOT_AUTHORIZED')
        fetch.assert_not_called()

    def test_api_nominates_public_urls_only_and_obeys_budget(self):
        from public_search import BraveSearch
        calls=[]
        def fetch(url):
            calls.append(url)
            return {'data': {'web': {'results': [{'url':'https://brand.example.com/team'}, {'url':'http://localhost/private'}]}}}
        search=BraveSearch(True,1,fetch)
        self.assertEqual(search.collect('Brand')['sources'],['https://brand.example.com/team'])
        self.assertEqual(search.collect('Brand')['sources'],[])
        self.assertEqual(len(calls),1)
        self.assertTrue(calls[0].startswith('https://api.search.brave.com/res/v1/web/search?'))

    def test_api_failure_stops_other_queries(self):
        from public_search import BraveSearch
        from unittest.mock import Mock
        fetch=Mock(return_value={'reason':'BRAVE_HTTP_429'})
        search=BraveSearch(True,10,fetch)
        search.collect('First');search.collect('Second')
        self.assertEqual(fetch.call_count,1)

    def test_sitemaps_reject_external_urls_and_honor_robots(self):
        from public_search import OfficialSiteSearch
        def fetch(url):
            return {'state':'observed','body':'User-agent: *\nDisallow: /private' if url.endswith('robots.txt') else '<urlset><url><loc>https://brand.example.com/team</loc></url><url><loc>https://brand.example.com/private/team</loc></url><url><loc>https://other.example.com/team</loc></url></urlset>'}
        result=OfficialSiteSearch(fetch,lambda _:None).collect('Brand','https://brand.example.com')
        self.assertEqual(result['sources'],['https://brand.example.com/team'])

    def test_sitemap_challenge_stops_host(self):
        from public_search import OfficialSiteSearch
        calls=[]
        def fetch(url):
            calls.append(url)
            return {'state':'observed','body':'User-agent: *\nAllow: /'} if url.endswith('robots.txt') else {'state':'pending','reason':'PUBLIC_ACCESS_CHALLENGE'}
        result=OfficialSiteSearch(fetch,lambda _:None).collect('Brand','https://brand.example.com')
        self.assertEqual(len(calls),2)
        self.assertEqual(result['sources'],[])
