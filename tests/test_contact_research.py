"""Synthetic publication extraction; never claim an inaccessible profile passed."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from research import extract
from contact_research import discover, assess_person, DecisionMakerResearch, ownership_claims


class ContactResearchTests(unittest.TestCase):
    def test_named_dom_card_extracts_person_role_but_not_current_employment(self):
        html='<h2>Jane Example</h2><p>Brand Director</p><p>Leads the team.</p>'
        parsed=extract(html,'https://brand.example.com/leadership')
        page={'url':'https://brand.example.com/leadership','observed_at':'2030-01-01T00:00:00Z',**parsed}
        people=discover(page,'Brand',authority='official')
        self.assertEqual(people[0]['name'],'Jane Example')
        self.assertTrue(people[0]['official_role_supported'])
        self.assertFalse(people[0]['current_employment_confirmed'])
        assessment=assess_person(people[0],[people[0]['person_evidence']])
        self.assertTrue(assessment['current_official_publication'])
        self.assertFalse(assessment['qualified_contact_confirmed'])

    def test_known_name_needs_adjacent_published_role_not_crm_title(self):
        known=[{'name':'Jane Example','role':'Brand Director'}]
        page={'url':'https://brand.example.com/team','text_excerpt':'Jane Example joined the company. Marketing roles listed elsewhere.'}
        self.assertEqual(discover(page,'Brand',known,authority='official'),[])
        page['text_excerpt']='Jane Example Marketing Director Jane Example joined the company in 2020.'
        result=discover(page,'Brand',known,authority='official')
        self.assertEqual(result[0]['role'],'Marketing Director')

    def test_shared_name_mention_does_not_confirm_identity(self):
        page={'url':'https://conference.example.com/speakers','text_excerpt':'said Jane Example, Marketing Director at Brand.'}
        result=discover(page,'Brand',authority='corroborating')
        self.assertFalse(result[0]['official_role_supported'])
        self.assertFalse(result[0]['employment_confirmed'])

    def test_no_fabrication_or_executive_fill_to_quota(self):
        html='<h2>Jane Example</h2><p>CEO and Founder</p>'
        page={'url':'https://brand.example.com/team',**extract(html,'https://brand.example.com/team')}
        self.assertEqual(discover(page,'Brand',authority='official'),[])

    def test_source_plan_reuses_snapshots_and_reports_shortfall(self):
        calls=[]
        def fetch(url):
            calls.append(url)
            return {'state':'observed','body':'' if url.endswith('robots.txt') else '<h2>Jane Example</h2><p>Marketing Manager</p>','status':200}
        old={'url':'https://brand.example.com/team','state':'observed','text_excerpt':'Jane Example Marketing Director Jane Example joined in 2020.','observed_at':'2020-01-01T00:00:00Z'}
        result=DecisionMakerResearch(fetch,pause=lambda _:None).collect('https://brand.example.com','Brand',[{'name':'Jane Example'}],[{'url':old['url'],'authority':'official'}],[old],limit=1)
        self.assertEqual(calls,[])
        self.assertEqual(result['shortfall'],2)
        self.assertEqual(result['candidates'][0]['person_evidence']['source_observed_at'],old['observed_at'])
        self.assertEqual(result['confirmed_current_contacts'],0)

    def test_explicit_ownership_not_inferred_from_powered_by(self):
        url='https://brand.example.com/about'
        self.assertEqual(ownership_claims('Brand powered by Shopping Platform','Brand',url,'official'),[])
        self.assertEqual(ownership_claims('Brand may modify any part of these terms','Brand',url,'official'),[])
        self.assertEqual(ownership_claims('Brand logo as part of a link','Brand',url,'official'),[])
        self.assertEqual(len(ownership_claims('Brand is part of Parent Company.','Brand',url,'official')),1)
        self.assertEqual(ownership_claims('Other Brand is part of Parent.','Exact Brand',url,'official'),[])


class EmailEvidenceTests(unittest.TestCase):
    def test_explicit_named_mailto_is_linkage_not_verification(self):
        result=extract('<a href="mailto:jane@brand.example.com">Jane Example</a>','https://brand.example.com/team')
        email=result['emails'][0]
        self.assertEqual(email['published_name'],'Jane Example')
        self.assertEqual(email['person_linkage'],'explicit_named_mailto_publication')
        self.assertFalse(email['identity_confirmed'])
        self.assertEqual(email['verification'],'not run')

    def test_published_contacts_keep_agency_person_separate_and_generic_unknown(self):
        from contact_research import published_emails
        page={'url':'https://brand.example.com/press','observed_at':'2030-01-01', 'segments':[
            {'tag':'h2','text':'Contacts'}, {'tag':'p','text':'Agency'}, {'tag':'p','text':'Jane Example'},
            {'tag':'p','text':'jane@agency.example.com'}, {'tag':'p','text':'info@brand.example.com'}]}
        rows=published_emails(page,'Brand')
        self.assertEqual(rows[0]['published_name'],'Jane Example')
        self.assertFalse(rows[0]['identity_confirmed'])
        self.assertFalse(rows[0]['deliverability_verified'])
        self.assertTrue(rows[1]['shared'])
        self.assertNotIn('published_name',rows[1])

    def test_source_date_is_distinct_from_employment_confirmation(self):
        data=extract('<meta property="article:published_time" content="2020-01-01"><h2>Jane Example</h2><p>Marketing Director</p>','https://brand.example.com/news')
        result=discover({'url':'https://brand.example.com/news',**data},'Brand',authority='official')[0]
        self.assertEqual(result['person_evidence']['publication_dates'][0]['value'],'2020-01-01')
        self.assertFalse(result['current_employment_confirmed'])


class PilotFailureRegressionTests(unittest.TestCase):
    def test_heading_role_stops_before_next_person_and_scopes_profile(self):
        html='<h2>Jane Example</h2><p>Brand Director</p><a href="https://linkedin.com/in/jane-example">LinkedIn</a><h2>John Sample</h2><p>Marketing Director</p><a href="https://linkedin.com/in/john-sample">LinkedIn</a>'
        page={'url':'https://brand.example.com/team',**extract(html,'https://brand.example.com/team')}
        people=discover(page,'Brand',authority='official')
        jane=next(p for p in people if p['name']=='Jane Example')
        self.assertEqual(jane['role'],'Brand Director')
        self.assertEqual(jane['linkedin_url'],'https://linkedin.com/in/jane-example')
        self.assertFalse(jane['current_employment_confirmed'])

    def test_explicit_person_company_attribution_not_directory_brand_mention(self):
        from contact_research import company_bound
        self.assertTrue(company_bound({'person_evidence':{'quote':'said Jane Example, Brand Manager for Annie’s.'}},"Annie's"))
        self.assertFalse(company_bound({'person_evidence':{'quote':'Jane Example — Marketing Director, Other Brand'}},'ATTITUDE'))
        self.assertFalse(company_bound({'person_evidence':{'quote':'Jane Example — Marketing Director for ATTITUDE Alternative'}},'ATTITUDE'))
        self.assertTrue(company_bound({'published_employer':'ATTITUDE'},'ATTITUDE'))

    def test_explicit_publication_date_does_not_use_copyright_year(self):
        result=extract('<p>Published 09/10/2024</p><footer>Copyright 2026</footer>','https://brand.example.com/news')
        self.assertIn({'kind':'explicit_published_label','value':'09/10/2024'},result['publication_dates'])
        self.assertFalse(any(d['value']=='2026' for d in result['publication_dates']))
