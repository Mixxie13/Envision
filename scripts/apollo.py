"""Disabled-by-default read-only Apollo people discovery. No enrichment or writes.

Compatibility parsing is tested synthetically; account schemas/visibility require
an authorized probe. No UI saved-view endpoint is used.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from email_api import NoRedirect, redact
from research import public_url, relevant_role
from reconcile_exports import linkedin
from sourcing_state import now, digest

PATH = '/api/v1/mixed_people/api_search'
TITLES = ['Brand Director', 'Head of Brand', 'Senior Brand Manager', 'Brand Manager',
          'Head of Marketing', 'Marketing Director', 'Marketing Manager', 'Digital Marketing Director', 'Digital Marketing Manager',
          'Social Media Manager', 'Influencer Marketing Manager', 'Community Manager',
          'Brand Partnerships', 'Marketing Partnerships', 'Gestionnaire Marketing']
EXCLUDED = ['Sales', 'Finance', 'Customer Support', 'Engineering', 'Operations', 'Human Resources', 'Designer']
TRANSLATIONS = {'gestionnaire marketing': 'Marketing Manager'}


class ApolloError(Exception):
    pass


def domain(value):
    if not isinstance(value, str): return None
    url = value if '://' in value else 'https://' + value
    if not public_url(url): return None
    p = urllib.parse.urlparse(url)
    if p.path not in ('', '/') or p.query or p.fragment: return None
    return (p.hostname or '').lower().removeprefix('www.')


def normalize_person(row, expected_domain, organization_id=None, parent=False):
    """Whitelist discovery fields. Missing/masked identity is never expanded."""
    if not isinstance(row, dict): return {'reason': 'UNKNOWN_PERSON_SCHEMA'}
    org = row.get('organization')
    if not isinstance(org, dict): return {'reason': 'CURRENT_ORGANIZATION_UNAVAILABLE'}
    current_domain = domain(org.get('primary_domain')) or domain(org.get('website_url'))
    verified_id_match = bool(organization_id and str(org.get('id')) == str(organization_id))
    if current_domain and current_domain != expected_domain or not current_domain and not verified_id_match:
        return {'reason': 'CURRENT_EMPLOYER_DOMAIN_MISMATCH_OR_MISSING'}
    if not isinstance(org.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', org['id']):
        return {'reason': 'CURRENT_ORGANIZATION_ID_UNAVAILABLE'}
    if organization_id and str(org.get('id')) != str(organization_id):
        return {'reason': 'ORGANIZATION_ID_CONFLICT'}
    title = row.get('title')
    if not isinstance(title, str): return {'reason': 'ROLE_MISSING'}
    role = TRANSLATIONS.get(title.casefold().strip(), title)
    if not relevant_role(role): return {'reason': 'ROLE_EXCLUDED_OR_UNSUPPORTED'}
    identifier = row.get('id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
        return {'reason': 'PERSON_ID_UNAVAILABLE'}
    first, last = row.get('first_name'), row.get('last_name')
    full = isinstance(first, str) and isinstance(last, str) and bool(first.strip()) and bool(last.strip()) and len(last.strip()) >= 2 and not row.get('last_name_obfuscated') and not any(c in first + last for c in '*•….')
    return {'apollo_person_id': identifier, 'name': (first.strip() + ' ' + last.strip()) if full else '',
            'display_first_name': first if isinstance(first, str) else '',
            'identity_complete': full, 'role': role, 'provider_title': title,
            'linkedin_url': row.get('linkedin_url') if linkedin(row.get('linkedin_url')) else '',
            'apollo_organization_id': str(org.get('id') or ''), 'current_employer_domain': current_domain or expected_domain,
            'current_employment_confirmed': False, 'outreach_ready': False,
            'brand_assignment_pending': parent,
            'reason': 'PARENT_BRAND_RESPONSIBILITY_PENDING' if parent else 'DISCOVERY_ONLY' if full else 'PARTIAL_OR_MASKED_IDENTITY',
            'risks': ['APOLLO_DISCOVERY_NOT_EMPLOYMENT_PROOF', 'HUBSPOT_AND_DRIPIFY_CHECKS_PENDING']}


class ApolloPeople:
    def __init__(self, state, enabled=False, fetch=None, max_requests=4, max_pages=2):
        self.state, self.enabled = state, enabled
        self.fetch = fetch or self._fetch
        self.remaining = min(max_requests, 4)
        self.max_pages = min(max_pages, 2)
        self.requests = 0
        self.stopped = False
        self.visited = set()

    @staticmethod
    def _fetch(params, key):
        # Only the documented read-only search endpoint can be dispatched.
        url = 'https://api.apollo.io' + PATH + '?' + urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request(url, data=b'', method='POST',
            headers={'X-Api-Key': key, 'Accept': 'application/json', 'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
                raw = response.read(2097153)
                if response.status != 200 or len(raw) > 2097152: raise ApolloError('INCOMPLETE_RESPONSE')
                return redact(json.loads(raw), key)
        except urllib.error.HTTPError as exc:
            raise ApolloError('APOLLO_HTTP_' + str(exc.code)) from None
        except (OSError, ValueError): raise ApolloError('APOLLO_READ_FAILED') from None

    def discover(self, company):
        pending = {'state': 'pending', 'candidates': [], 'outreach_ready': False, 'coverage_complete': False}
        if not self.enabled: return {**pending, 'reason': 'APOLLO_DISABLED'}
        auth = self.state.meta('apollo_read_authorization', {})
        if not (auth.get('approved') is True and auth.get('evidence_ref')):
            return {**pending, 'reason': 'APOLLO_LIVE_READ_NOT_AUTHORIZED'}
        binding = self.state.meta('apollo_company_binding:' + company['id'], {})
        expected = domain(binding.get('domain'))
        if not (binding.get('verified') is True and expected and binding.get('evidence_ref')):
            return {**pending, 'reason': 'VERIFIED_COMPANY_DOMAIN_REQUIRED'}
        parent = bool(binding.get('parent'))
        if parent and not binding.get('brand_routing_evidence_ref'):
            return {**pending, 'reason': 'PARENT_BRAND_ROUTING_UNVERIFIED'}
        authorized_budget = auth.get('max_requests', 1)
        if not isinstance(authorized_budget, int) or isinstance(authorized_budget, bool) or authorized_budget < 1:
            return {**pending, 'reason': 'APOLLO_REQUEST_BUDGET_UNDEFINED'}
        self.remaining = min(self.remaining, authorized_budget - self.requests)
        key = os.environ.get('APOLLO_API_KEY', '')
        if not key.strip(): return {**pending, 'reason': 'APOLLO_CREDENTIAL_NOT_CONFIGURED'}
        # Shared domain cache avoids repeating requests for duplicate source rows.
        cache_key = 'apollo-people-v1:' + digest([expected, binding.get('organization_id'), parent, TITLES])
        checkpoint = self.state.meta(cache_key, {'candidates': [], 'next_page': 1,
            'pagination_complete': False, 'seen_ids': [], 'rejections': {}, 'observations': []})
        if cache_key in self.visited or checkpoint['pagination_complete'] or checkpoint.get('stalled'):
            return self._result(pending, checkpoint)
        self.visited.add(cache_key)
        for _ in range(self.max_pages):
            if self.stopped or self.remaining <= 0: break
            page = checkpoint['next_page']
            params = {'q_organization_domains_list[]': [expected], 'person_titles[]': TITLES,
                      'person_not_titles[]': EXCLUDED, 'include_similar_titles': 'false', 'page': page, 'per_page': 25}
            if binding.get('organization_id'): params['organization_ids[]'] = [str(binding['organization_id'])]
            # No countries, saved-company/view filters, email status or reveal options.
            self.remaining -= 1; self.requests += 1; time.sleep(1.1)
            try:
                data = self.fetch(params, key)
                if not isinstance(data, dict) or not isinstance(data.get('people'), list): raise ApolloError('UNVERIFIED_RESPONSE_SCHEMA')
                rows = data['people']
                if any(not isinstance(r, dict) for r in rows): raise ApolloError('UNVERIFIED_PERSON_SCHEMA')
                for row in rows:
                    identifier = row.get('id')
                    if identifier and identifier in checkpoint['seen_ids']:
                        checkpoint['stalled'] = True
                        checkpoint['reason'] = 'OVERLAPPING_OR_CHANGED_PAGES';continue
                    if identifier: checkpoint['seen_ids'].append(identifier)
                    candidate = normalize_person(row, expected, binding.get('organization_id'), parent)
                    if 'apollo_person_id' in candidate:
                        checkpoint['candidates'].append(candidate)
                    else:
                        reason = candidate['reason']; checkpoint['rejections'][reason] = checkpoint['rejections'].get(reason, 0) + 1
                checkpoint['observations'].append({'observed_at': now(), 'page': page, 'rows': len(rows),
                    'identity_fields_present': {field: sum(field in row for row in rows) for field in ('id', 'first_name', 'last_name', 'last_name_obfuscated', 'title', 'linkedin_url', 'organization')},
                    'organization_fields_present': {field: sum(isinstance(row.get('organization'), dict) and field in row['organization'] for row in rows) for field in ('id', 'name', 'primary_domain', 'website_url')}})
                total = data.get('total_entries')
                pagination = data.get('pagination')
                if total is None and isinstance(pagination, dict): total = pagination.get('total_entries')
                if isinstance(total, int) and not isinstance(total, bool) and total >= 0 and total >= (page - 1) * 25 + len(rows):
                    checkpoint['pagination_complete'] = page * 25 >= total
                    if not rows and not checkpoint['pagination_complete']:
                        checkpoint['stalled'] = True; checkpoint['reason'] = 'INCOMPLETE_EMPTY_PAGE'
                else:
                    checkpoint['stalled'] = True; checkpoint['reason'] = 'PAGINATION_COVERAGE_UNVERIFIED'
                checkpoint['next_page'] = page + 1
                self.state.set_meta(cache_key, checkpoint)
                if checkpoint['pagination_complete'] or checkpoint.get('stalled'): break
            except ApolloError as exc:
                self.stopped = True
                checkpoint['reason'] = str(exc)
                self.state.set_meta(cache_key, checkpoint);break
        return self._result(pending, checkpoint)

    def _result(self, pending, checkpoint):
        organization_ids = {c['apollo_organization_id'] for c in checkpoint['candidates'] if c['apollo_organization_id']}
        if len(organization_ids) > 1:
            checkpoint['reason'] = 'MULTIPLE_CURRENT_ORGANIZATION_IDS_REVIEW_REQUIRED'
        from contact_research import role_rank
        shortlist = sorted([c for c in checkpoint['candidates'] if c['identity_complete'] and not c['brand_assignment_pending']], key=lambda c: (role_rank(c['role']), c['name']))[:5]
        if len(organization_ids) > 1: shortlist = []
        return {**pending, **checkpoint, 'shortlist': shortlist, 'reason': checkpoint.get('reason', 'DISCOVERY_REQUIRES_IDENTITY_EMPLOYMENT_AND_HISTORY_REVIEW'),
                'requests_this_run': self.requests, 'target': '3–5 where supported; no quota guarantee',
                'shortfall_to_three': max(0, 3 - len(shortlist)),
                'account_schema_verified': self.state.meta('apollo_read_authorization', {}).get('response_contract_verified') is True and bool(self.state.meta('apollo_read_authorization', {}).get('schema_review_ref'))}
