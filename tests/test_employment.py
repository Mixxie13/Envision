import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from employment import assess

class EmploymentTests(unittest.TestCase):
    def setUp(self):
        self.person={'name':'Jane Example','company':'Brand','role':'Brand Director','linkedin_url':'https://www.linkedin.com/in/jane-example'}
        self.claim={'name':'Jane Example','company':'Brand','role':'Brand Director','subject_profile_url':self.person['linkedin_url'],
            'source_url':self.person['linkedin_url'],'access_status':'observed','source_sha256':'synthetic-source-hash',
            'observed_at':'2030-01-01T10:00:00Z','quote':'Jane Example is currently Brand Director at Brand.',
            'claim_state':'current','current_review_date':'2030-01-01','current_role_explicit':True,'reviewed_original':True}
    def test_explicit_original_profile_can_confirm_as_of_without_outreach_clearance(self):
        result=assess(self.person,[self.claim],'2030-01-01T12:00:00Z')
        self.assertTrue(result['current_employment_confirmed']);self.assertFalse(result['outreach_ready'])
    def test_old_announcement_and_fetched_today_do_not_confirm(self):
        claim={**self.claim,'source_url':'https://brand.example.com/press','claim_state':'historical'}
        self.assertFalse(assess(self.person,[claim],'2030-01-01T12:00:00Z')['current_employment_confirmed'])
    def test_stale_conflicting_departed_and_aliases_fail_closed(self):
        for change in ({'stale':True},{'claim_state':'departed'},{'name':'Janet Example'},{'company':'Other Brand'},
                       {'role':'Marketing Manager'},{'current_review_date':'2029-01-01'},{'access_status':'blocked'},
                       {'subject_profile_url':'https://linkedin.com/in/another-person'}):
            self.assertFalse(assess(self.person,[{**self.claim,**change}],'2030-01-01T12:00:00Z')['current_employment_confirmed'])
    def test_adverse_history_not_overwritten_by_positive(self):
        result=assess(self.person,[self.claim,{**self.claim,'claim_state':'departed'}],'2030-01-01T12:00:00Z')
        self.assertEqual(result['status'],'conflicting')
        self.person['identity_conflict']=True;self.person['risks']=['HISTORICAL_HARD_BOUNCE']
        result=assess(self.person,[self.claim],'2030-01-01T12:00:00Z')
        self.assertFalse(result['current_employment_confirmed']);self.assertIn('HISTORICAL_HARD_BOUNCE',result['retained_risks'])
    def test_missing_or_unsupported_evidence_stays_pending(self):
        self.assertEqual(assess(self.person,[],'2030-01-01T12:00:00Z')['status'],'pending')
        self.assertFalse(assess(self.person,[{**self.claim,'quote':'Search snippet without attribution'}],'2030-01-01T12:00:00Z')['current_employment_confirmed'])
    def test_research_assessor_consumes_supported_review_without_qualifying_outreach(self):
        from contact_research import assess_person
        result=assess_person(self.person,[],employment_claims=[self.claim],as_of='2030-01-01T12:00:00Z')
        self.assertTrue(result['current_employment_confirmed'])
        self.assertFalse(result['qualified_contact_confirmed']);self.assertFalse(result['outreach_ready'])
    def test_review_today_cannot_refresh_old_snapshot_or_future_observation(self):
        for timestamp in ('2029-01-01T10:00:00Z','2030-01-01T23:00:00Z'):
            result=assess(self.person,[{**self.claim,'observed_at':timestamp}],'2030-01-01T12:00:00Z')
            self.assertFalse(result['current_employment_confirmed'])
