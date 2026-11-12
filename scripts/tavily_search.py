"""Opt-in Basic search: transient results, durable attempt counters only.

Caller holds the existing project lock. No credential access before approval.
"""
import json
import os
import urllib.error
import urllib.request
import urllib.parse

from email_api import NoRedirect
from research import public_url
from sourcing_state import digest, now

LEDGER = 'tavily-ten-company-pilot-v1'
AUTHORIZATION = 'tavily_pilot_authorization'
RETEST_LEDGER = 'tavily-three-company-retest-v1'
RETEST_AUTHORIZATION = 'tavily_retest_authorization'


def build_query(company):
    """One focused people query, anchored to the supplied brand and known domain."""
    name = ' '.join(company['data']['company'].replace('"', '').split())
    website = company['data'].get('mapped', {}).get('website', '')
    domain = (urllib.parse.urlparse(website).hostname or '').removeprefix('www.')
    context = ' (' + domain + ')' if domain else ''
    return ('Who currently leads brand, marketing or brand partnerships at "' + name + '"' + context +
            '? Find named senior decision-makers, personal LinkedIn profiles and employer role evidence.')


def prepare(state):
    companies = state.companies(1)
    if len(companies) != 10:
        raise ValueError('Pilot requires the existing first ten company rows')
    rows = [c['id'] for c in companies]
    previous = state.meta(LEDGER)
    if previous and previous['company_ids'] != rows:
        raise ValueError('Pilot company rows changed; refusing budget reset')
    if not previous:
        state.set_meta(LEDGER, {'company_ids': rows, 'attempted': [], 'outcomes': {},
                               'max_searches': 10, 'max_credits': 10, 'enabled': False})
    return {'companies': [c['data']['company'] for c in companies],
            'enabled': False, 'max_searches': 10, 'max_credits': 10,
            'retention': 'No provider response, result URL list, titles or snippets persisted'}


def prepare_retest(state):
    wanted = {"Annie's", 'Beech-Nut', 'ATTITUDE'}
    companies = [c for c in state.companies(1) if c['data']['company'] in wanted]
    if len(companies) != 3 or {c['data']['company'] for c in companies} != wanted:
        raise ValueError('Retest requires the three original pilot company rows')
    rows = [c['id'] for c in companies]
    previous = state.meta(RETEST_LEDGER)
    if previous and previous['company_ids'] != rows:
        raise ValueError('Retest company rows changed; refusing budget reset')
    if not previous:
        state.set_meta(RETEST_LEDGER, {'company_ids': rows, 'attempted': [], 'outcomes': {},
            'max_searches': 3, 'max_credits': 3, 'enabled': False})
    return {'companies': [c['data']['company'] for c in companies],
            'enabled': False, 'max_searches': 3, 'max_credits': 3}


class TavilyBasic:
    def __init__(self, state, enabled=False, transport=None, ledger_key=LEDGER, authorization_key=AUTHORIZATION):
        self.state, self.enabled = state, enabled
        self.ledger_key, self.authorization_key = ledger_key, authorization_key
        self.transport = transport or self._transport

    @staticmethod
    def _transport(payload, key):
        request = urllib.request.Request('https://api.tavily.com/search',
            data=json.dumps(payload).encode(), method='POST',
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                     'Accept': 'application/json', 'User-Agent': 'Envision-Research/1.0'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
                raw = response.read(2097153)
                if response.status != 200 or len(raw) > 2097152:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            return {'_failure_code': 'HTTP_' + str(exc.code)}
        except (OSError, ValueError):
            # No retry, error body, headers, query/result text or exception logging.
            return None

    def collect(self, company):
        empty = {'state': 'pending', 'reason': 'TAVILY_DISABLED', 'sources': []}
        if not self.enabled:
            return empty
        auth = self.state.meta(self.authorization_key, {})
        if not (auth.get('approved') is True and auth.get('terms_confirmed') is True
                and auth.get('evidence_ref')):
            return {**empty, 'reason': 'TAVILY_APPROVAL_OR_TERMS_PENDING'}
        ledger = self.state.meta(self.ledger_key)
        if not ledger:
            return {**empty, 'reason': 'PILOT_NOT_PREPARED'}
        cid = company['id']
        if cid in ledger['attempted']:
            return {**empty, 'reason': 'ALREADY_ATTEMPTED_NO_RESULTS_RETAINED'}
        if ledger.get('stopped') or len(ledger['attempted']) >= min(10, ledger.get('max_searches', 10), ledger.get('max_credits', 10)):
            return {**empty, 'reason': 'PILOT_STOPPED_OR_BUDGET_EXHAUSTED'}
        position = len(ledger['attempted'])
        if cid != ledger['company_ids'][position]:
            return {**empty, 'reason': 'PILOT_ORIGINAL_ORDER_REQUIRED'}
        key = os.environ.get('TAVILY_API_KEY', '')
        if not key.strip():
            return {**empty, 'reason': 'TAVILY_CREDENTIAL_NOT_CONFIGURED'}
        # Reserve before dispatch: crash/timeout/unknown billing cannot repeat a query.
        ledger['attempted'].append(cid)
        ledger['outcomes'][cid] = {'state': 'reserved', 'at': now(), 'credits_reserved': 1}
        self.state.set_meta(self.ledger_key, ledger)
        name = company['data']['company']
        payload = {'query': build_query(company),
                   'topic': 'general', 'search_depth': 'basic', 'max_results': 5,
                   'auto_parameters': False, 'include_answer': False,
                   'include_raw_content': False, 'include_images': False, 'include_usage': True}
        try:
            data = self.transport(payload, key)
        except Exception:
            data = None
        valid = isinstance(data, dict) and isinstance(data.get('results'), list) and all(isinstance(r, dict) for r in data['results'])
        usage = data.get('usage', {}) if isinstance(data, dict) else {}
        mismatch = isinstance(usage, dict) and 'credits' in usage and usage['credits'] != 1
        if not valid or mismatch:
            ledger['stopped'] = True
            reason = 'TAVILY_FAILURE_OR_USAGE_UNVERIFIED_STOP'
            urls = []
        else:
            # The only provider output crossing this boundary is an in-memory URL queue.
            urls = list(dict.fromkeys(r['url'] for r in data['results']
                if isinstance(r.get('url'), str) and key not in r['url'] and public_url(r['url'])))[:5]
            reason = 'TRANSIENT_URLS_REQUIRE_INDEPENDENT_SOURCE_VERIFICATION'
        ledger['outcomes'][cid] = {'state': 'returned' if valid and not mismatch else 'stopped',
                                    'at': now(), 'credits_reserved': 1,
                                    'credits_reported': usage.get('credits') if isinstance(usage, dict) and isinstance(usage.get('credits'), (int, float)) else None,
                                    'failure_code': data.get('_failure_code') if isinstance(data, dict) else 'TRANSPORT_OR_JSON_FAILURE'}
        self.state.set_meta(self.ledger_key, ledger)
        return {'state': 'pending', 'reason': reason, 'sources': urls}


def run_pilot(state, workbook_path, enabled=False, transport=None, researcher=None, retest=False):
    """Independent source research only; never cache/persist the discovery queue."""
    from contact_research import DecisionMakerResearch
    from workbook import update
    state.permit_batch(1)
    (prepare_retest if retest else prepare)(state)
    ledger_key = RETEST_LEDGER if retest else LEDGER
    authorization_key = RETEST_AUTHORIZATION if retest else AUTHORIZATION
    search = TavilyBasic(state, enabled=enabled, transport=transport, ledger_key=ledger_key, authorization_key=authorization_key)
    researcher = researcher or DecisionMakerResearch()
    counts = []
    if not enabled:
        return {'enabled': False, 'searches': 0, 'outreach_ready': False}
    selected = state.meta(ledger_key)['company_ids']
    for company in (c for c in state.companies(1) if c['id'] in selected):
        lookup = search.collect(company)
        saved = 0
        if lookup['sources']:
            # These URLs are never passed to State.cached, meta, observe or a report.
            root = company['data']['mapped'].get('website') or ''
            host = (urllib.parse.urlparse(root).hostname or '').removeprefix('www.')
            sources = [{'url': u, 'authority': 'official' if
                (urllib.parse.urlparse(u).hostname or '').removeprefix('www.') == host else 'corroborating'} for u in lookup['sources']]
            retained = {}
            for old in state.observations():
                if old['company_id'] == company['id'] and old['stage'] in ('research', 'research-operational-v1'):
                    for page in old['data'].get('pages', []):
                        if page.get('state') == 'observed':
                            retained[page['url']] = page
            # Known original-source publications are reused without refreshing their dates.
            for page in retained.values():
                sources.append({'url': page['url'], 'authority': page.get('authority', 'corroborating')})
            result = researcher.collect(root, company['data']['company'],
                [c['data'] for c in state.contacts(company['id'])], sources=sources,
                cached_pages=list(retained.values()), limit=16, max_new_pages=3)
            for page in result.get('pages', []):
                if page.get('state') != 'observed' or not page.get('sha256') or not page.get('observed_at'):
                    continue
                # Save only independently fetched publication facts, not provider excerpts.
                same_company_site = (urllib.parse.urlparse(page['url']).hostname or '').removeprefix('www.') == host
                from contact_research import company_bound
                claims = [c for c in page.get('decision_maker_claims', []) if isinstance(c, dict) and
                          company_bound(c, company['data']['company'], same_company_site)]
                emails = [e for e in page.get('emails', []) if same_company_site and e.get('published_name') and not e.get('shared')]
                if not claims and not emails:
                    continue
                evidence = {'reason': 'INDEPENDENT_PUBLICATION_REQUIRES_IDENTITY_REVIEW',
                    'source_url': page['url'], 'source_sha256': page['sha256'],
                    'observed_at': page['observed_at'], 'claims': claims, 'emails': emails,
                    'confirmed_current_claims_count': sum(c.get('employment_assessment', {}).get('current_employment_confirmed') is True for c in claims), 'outreach_ready': False}
                for claim in claims:
                    if not isinstance(claim, dict) or not claim.get('name') or not claim.get('role'):
                        continue
                    candidate_id = 'publication:' + digest([company['id'], claim['name'].casefold()])[:24]
                    self_contained = {**claim, 'blocked': False,
                        'current_employment_confirmed': claim.get('employment_assessment', {}).get('current_employment_confirmed') is True, 'outreach_ready': False,
                        'source': page['url'], 'risks': ['RESEARCH_REVIEW_PENDING' if claim.get('employment_assessment', {}).get('current_employment_confirmed') is True else 'PROVISIONAL_PUBLICATION_NOT_EMPLOYMENT_CONFIRMATION', 'HUBSPOT_AND_DRIPIFY_CHECKS_PENDING']}
                    state.contact(candidate_id, company['id'], self_contained)
                state.observe(company['id'], '', 'independent-source-pilot', 'pending', evidence)
                saved += 1
        # Only our own row IDs, statuses and counts persist for failed/empty discovery.
        state.observe(company['id'], '', 'tavily-pilot-attempt', 'pending',
                      {'reason': lookup['reason'], 'independent_evidence_records': saved})
        counts.append({'company': company['data']['company'], 'independent_evidence_records': saved,
                       'status': lookup['reason']})
        stop = lookup['reason'] in ('TAVILY_FAILURE_OR_USAGE_UNVERIFIED_STOP', 'TAVILY_CREDENTIAL_NOT_CONFIGURED')
        lookup.clear()
        if stop:
            break
    update(state, workbook_path)
    ledger = state.meta(ledger_key)
    return {'companies': counts, 'searches_reserved': len(ledger['attempted']),
            'credits_reserved': len(ledger['attempted']), 'outreach_ready': False}
