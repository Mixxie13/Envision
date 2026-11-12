"""Mocked permitted public-source collection, no real network or sleep."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from research import PublicResearch, extract, public_url, relevant_role


class PublicResearchTests(unittest.TestCase):
    def test_structured_person_is_only_provisional_and_no_generic_substitution(self):
        html = '''<script type="application/ld+json">{"@type":"Person","name":"Synthetic Person","jobTitle":"Brand Director","sameAs":"https://www.linkedin.com/in/synthetic"}</script>
        <a href="mailto:hello@example.com">Contact</a><a href="mailto:person@example.com">Person</a>
        <a href="mailto:private@gmail.com">Private</a>'''
        result = extract(html, 'https://example.com/team')
        self.assertEqual(len(result['people']), 1)
        self.assertFalse(result['people'][0]['employment_confirmed'])
        self.assertEqual(len(result['emails']), 2)
        self.assertTrue(result['emails'][0]['shared'])
        self.assertEqual(result['emails'][1]['person_linkage'], 'unresolved')

    def test_robots_denial_and_inaccessible_robots_stop(self):
        def fetch(url):
            return {'state': 'observed', 'body': 'User-agent: *\nDisallow: /', 'status': 200}
        result = PublicResearch(fetch, pause=lambda _: None).collect('https://example.com')
        self.assertEqual(result['pages'], [])
        result = PublicResearch(lambda _: {'state': 'pending', 'status': 403}).collect('https://example.com')
        self.assertEqual(result['reason'], 'ROBOTS_ACCESS_UNRESOLVED')

    def test_bounded_same_site_no_following_external_or_auth_links(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return {'state': 'observed', 'body': '' if url.endswith('robots.txt') else '<a href="/team">Team</a><a href="https://other.example.com/about">Elsewhere</a>', 'status': 200}
        result = PublicResearch(fetch, pause=lambda _: None).collect('https://example.com', limit=2)
        self.assertEqual(len(result['pages']), 2)
        self.assertEqual(len(calls), 3)
        self.assertFalse(any('other.' in u for u in calls))

    def test_restricted_public_source_stops_without_retry(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return {'state': 'observed', 'body': '', 'status': 200} if url.endswith('robots.txt') else {'state': 'pending', 'status': 429}
        result = PublicResearch(fetch, pause=lambda _: None).collect('https://example.com')
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['state'], 'pending')

    def test_unsafe_source_urls_rejected_without_network(self):
        for u in ('http://localhost', 'http://127.0.0.1', 'http://169.254.169.254/', 'file:///etc/passwd', 'https://user:secret@example.com', 'http://service.internal', 'https://example.com:bad'):
            self.assertFalse(public_url(u), u)
        with patch('research.urllib.request.build_opener') as transport:
            PublicResearch().collect('http://127.0.0.1')
            transport.assert_not_called()

    def test_roles_do_not_automatically_use_executive_exceptions(self):
        self.assertTrue(relevant_role('Senior Brand Manager'))
        for title in ('Sales and Marketing Director', 'Founder', 'Chief Executive Officer', 'Design Manager', 'Chief Marketing Officer', 'General Manager, US Branded Beverages'):
            self.assertFalse(relevant_role(title))
