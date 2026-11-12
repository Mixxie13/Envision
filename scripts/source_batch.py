"""Resumable Envision research batches. No paid or external-write operations."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import eligibility
from provider_evidence import Providers, seed_existing
from research import PublicResearch
from contact_research import DecisionMakerResearch
from public_search import OfficialSiteSearch, BraveSearch
from urllib.parse import urlparse
from sourcing_state import State, digest, now, project_lock, read_master
from workbook import update
from master_progress import write_reports

STAGES = ('hubspot', 'research', 'identity', 'dripify', 'email', 'evaluation')


def evaluate_contacts(state, company):
    results = []
    for row in state.contacts(company['id']):
        person = row['data']
        if person.get('handoff'):
            from candidate_handoff import review
            result = review(person, row['id'], company['data']['company'])['eligibility_assessment']
            results.append({**result, 'contact_id': row['id']})
            continue
        subject = {'contact_id': row['id'], 'company': company['data']['company'],
                   'full_name': person.get('name') or '', 'email': person.get('email') or '',
                   'linkedin_url': person.get('linkedin_url') or ''}
        checks = {}
        if person.get('blocked'):
            # Existing known suppression is never cleared by incomplete providers.
            mapping = {'HISTORICAL_HARD_BOUNCE': ('hard_bounce', 'bounced'),
                       'EMAIL_OPT_OUT': ('opt_out', 'opted_out'),
                       'ACTIVE_SEQUENCE': ('hubspot_sequence', 'active')}
            for reason in person.get('risks', []):
                if reason in mapping:
                    check, result = mapping[reason]
                    checks[check] = {'result': result, 'subject': subject,
                                     'source': person.get('source', 'Retained adverse CRM observation'),
                                     'complete': False, 'state': 'unresolved'}
        record = {'subject': subject, 'channel': person.get('channel'), 'checks': checks,
                  'unresolved_risks': ['SOURCE_COVERAGE_AND_POLICY_UNRESOLVED'] + person.get('risks', []),
                  'approvals': {}}
        result = eligibility.evaluate(record)
        if person.get('blocked') and result['decision'] != 'blocked':
            result['decision'] = 'blocked'
            result['technical_eligibility'] = 'blocked'
            result['reason_codes'].append('RETAINED_BASELINE_BLOCK')
        result['contact_id'] = row['id']
        results.append(result)
    return {'state': 'blocked' if any(r['decision'] == 'blocked' for r in results) else 'pending',
            'reason': 'RESEARCH_REVIEW_ONLY_NOT_OUTREACH_CLEARANCE', 'contacts': results,
            'approval_policy': 'Lead Sourcing Owner research/QC review, then Bridget final approval; historical Roxy records never satisfy current review'}


def run(state, batch, workbook, providers=None, researcher=None, public_web=False):
    state.permit_batch(batch)
    providers = providers or Providers(state)
    custom_researcher = researcher is not None
    researcher = researcher or PublicResearch()
    searcher = OfficialSiteSearch()
    authorization = state.meta('public_search_authorization', {})
    approved_search = BraveSearch(approved=bool(authorization.get('provider') == 'brave' and authorization.get('approved') is True and authorization.get('evidence_ref')),
                                  max_queries=authorization.get('max_queries', 0))
    completed, reused = 0, 0
    for company in state.companies(batch):
        for stage in STAGES:
            if state.job(company['id'], stage):
                reused += 1
                continue
            state.cache_revision = state.meta('revision:' + company['id'] + ':' + stage, '')
            try:
                if stage == 'hubspot':
                    result = providers.hubspot(company)
                elif stage == 'identity':
                    result = providers.identity(company)
                    from candidate_handoff import provider_review, refresh
                    provider_review(state, company['id'], result, 'hubspot'); refresh(state, company_id=company['id'])
                elif stage == 'dripify':
                    result = providers.dripify(company)
                    from candidate_handoff import provider_review, refresh
                    provider_review(state, company['id'], result, 'dripify'); refresh(state, company_id=company['id'])
                elif stage == 'research':
                    # Primary people discovery precedes source corroboration and the
                    # existing identity/Dripify stages; it is separately opt-in.
                    apollo_result = providers.apollo(company) if hasattr(providers, 'apollo') else {'state': 'pending', 'reason': 'APOLLO_NOT_CONFIGURED'}
                    state.observe(company['id'], '', 'apollo-discovery', 'pending', apollo_result)
                    website = company['data']['mapped'].get('website') or ''
                    source_plan = state.meta('public_source_plan', {})
                    if public_web and not custom_researcher:
                        cached_pages = []
                        for old in state.observations():
                            if old['company_id'] == company['id'] and old['stage'] == 'research':
                                cached_pages += [dict(p, observed_at=p.get('observed_at') or old['data'].get('observed_at')) for p in old['data'].get('pages', [])]
                        known = [c['data'] for c in state.contacts(company['id'])]
                        sources = list(source_plan.get(company['data']['company'], []))
                        site_search = state.cached(['official-site-search-v1', company['id']], lambda: searcher.collect(company['data']['company'], website))
                        api_search = state.cached(['approved-public-search-v1', company['id'], digest(authorization)], lambda: approved_search.collect(company['data']['company'], website))
                        search = {**site_search, 'approved_api': api_search, 'sources': list(dict.fromkeys(site_search.get('sources', []) + api_search.get('sources', [])))}
                        state.observe(company['id'], '', 'public-search-discovery', 'pending', search)
                        site_host = (urlparse(website).hostname or '').removeprefix('www.')
                        for url in search.get('sources', []):
                            if url not in [x['url'] for x in sources]:
                                sources.append({'url': url, 'authority': 'official' if (urlparse(url).hostname or '').removeprefix('www.') == site_host else 'corroborating'})
                        cached_urls = {p['url'] for p in cached_pages if p.get('state') == 'observed'}
                        sources.sort(key=lambda source: source['url'] in cached_urls)
                        result = state.cached(['decision-maker-research-v4', company['id'], digest(sources)], lambda: DecisionMakerResearch().collect(website, company['data']['company'], known, sources, cached_pages))
                        for claim in result.get('candidates', []):
                            matching = [c for c in state.contacts(company['id']) if c['data'].get('name', '').casefold() == claim['name'].casefold()]
                            state.observe(company['id'], matching[0]['id'] if len(matching) == 1 else '', 'publication-claim', 'pending',
                                          {'reason': 'NAMED_ROLE_PUBLICATION_IDENTITY_REVIEW', **claim, 'name_only_candidate_ids': [c['id'] for c in matching]})
                            if not matching:
                                identifier = digest([company['id'], claim['name'].casefold()])[:24]
                                state.contact('publication:' + identifier, company['id'], {**claim, 'blocked': False, 'risks': ['PROVISIONAL_PUBLICATION_NOT_EMPLOYMENT_CONFIRMATION']})
                        for email in result.get('email_candidates', []):
                            state.observe(company['id'], '', 'published-email-evidence-v1', 'pending',
                                          {'reason': 'PUBLISHED_MAILBOX_REQUIRES_IDENTITY_AND_DELIVERABILITY_VERIFICATION',
                                           'name': email.get('published_name', ''), **email})
                        # Claims above are already reconciled separately from name-only IDs.
                        result['pages'] = [dict(p, people=[]) for p in result.get('pages', [])]
                    else:
                        result = state.cached(['public-research-v1', website], lambda: researcher.collect(website)) if public_web else {'state': 'pending', 'reason': 'PUBLIC_RESEARCH_NOT_ENABLED'}
                    for page in result.get('pages', []):
                        for person in page.get('people', []):
                            identifier = person.get('linkedin_url') or digest([person['name'], page['url']])
                            state.contact('public:' + digest(identifier)[:24], company['id'],
                                          {**person, 'source': page['url'], 'blocked': False,
                                           'risks': ['PROVISIONAL_PUBLICATION_NOT_EMPLOYMENT_CONFIRMATION']})
                elif stage == 'email':
                    result = {'state': 'pending', 'reason': 'PAID_DISCOVERY_AND_VERIFICATION_DISABLED',
                              'note': 'No Apollo, Findymail or NeverBounce paid call exists in this runner. Published mailto observations are not verified addresses.'}
                else:
                    result = evaluate_contacts(state, company)
                result = {**result, 'observed_at': result.get('observed_at') or now()}
            except Exception:
                # Do not print exception text, credentials or raw provider bodies.
                result = {'state': 'pending', 'reason': stage.upper() + '_COLLECTION_FAILED', 'observed_at': now()}
            state.observe(company['id'], '', stage, result['state'], result)
            state.finish(company['id'], stage, result['state'])
            completed += 1
        # Durable per-company checkpoint, including when another company fails.
        update(state, workbook)
    batch_company_ids = {c['id'] for c in state.companies(batch)}
    batch_contacts = [c for c in state.contacts() if c['company_id'] in batch_company_ids]
    report = {'project_id': state.meta('project_id'), 'batch': batch,
              'companies': len(state.companies(batch)), 'stages_executed': completed,
              'stages_reused': reused, 'contacts': len(batch_contacts),
              'research_excluded_records': sum(bool(c['data'].get('research_excluded')) for c in batch_contacts),
              'active_research_records': sum(not c['data'].get('research_excluded') for c in batch_contacts),
              'retained_blocks': sum(bool(c['data'].get('blocked')) for c in batch_contacts),
              'outreach_cleared': 0, 'paid_calls': 0, 'crm_write_calls': 0,
              'batch_status': 'research_attempted_review_required',
              'master_processing_authorized': False,
              'limitations': ['Account-wide Dripify history unavailable',
                              'Public source discovery is bounded; Apollo access/schema and independent employment coverage require verification',
                              'Current employment, subscription/sequence history and freshness pending',
                              'No automatic approval or new-prospect classification']}
    exceptions = []
    for row in batch_contacts:
        person = row['data']
        exceptions.append({'contact_id': row['id'], 'name': person.get('name'),
                           'hubspot_contact_id': person.get('hubspot_contact_id'),
                           'linkedin_url': person.get('linkedin_url'),
                           'decision': 'blocked' if person.get('blocked') else 'pending',
                           'risks': person.get('risks', []),
                           'missing': ['Current person/role/company evidence', 'Applicable subscription and authoritative sequence/outreach history', 'Account-wide Dripify membership/history', 'Approved freshness/approval policies'],
                           'action': 'Continue independent research; manual evidence only when automated source unavailable and needed',
                           'outreach_ready': False})
    exception_path = state.directory / ('batch-' + str(batch) + '-exceptions.json')
    exception_path.write_text(json.dumps(exceptions, indent=2))
    exception_path.chmod(0o600)
    path = state.directory / ('batch-' + str(batch) + '-report.json')
    path.write_text(json.dumps(report, indent=2))
    path.chmod(0o600)
    write_reports(state)
    return report


def discover_apollo(state, batch, workbook, enabled=False, live_checks=False):
    state.permit_batch(batch)
    providers = Providers(state, live=live_checks, apollo_enabled=enabled)
    if not enabled:
        return {'enabled': False, 'requests': 0, 'reason': 'APOLLO_DISABLED'}
    results = []
    for company in state.companies(batch):
        result = providers.apollo(company)
        state.observe(company['id'], '', 'apollo-discovery', 'pending', result)
        # Existing read-only helpers retain exact matching and incomplete-coverage rules.
        identity = providers.identity(company)
        dripify = providers.dripify(company)
        state.observe(company['id'], '', 'apollo-identity-followup', 'pending', identity)
        state.observe(company['id'], '', 'apollo-dripify-followup', 'pending', dripify)
        results.append({'company': company['data']['company'], 'discovery': result})
    update(state, workbook)
    return {'enabled': True, 'requests': providers.apollo_client.requests,
            'companies': results, 'outreach_ready': False}


def check_dripify(state, batch, workbook, providers=None):
    """Resume bounded duplicate research without rerunning completed paid/research jobs."""
    state.permit_batch(batch)
    providers = providers or Providers(state, live=True)
    results = []
    for company in state.companies(batch):
        result = providers.dripify(company)
        results.append({'company': company['data']['company'], **result})
        for contact in result['contacts']:
            state.observe(company['id'], contact['contact_id'], 'dripify-live-v2', 'pending', contact)
    update(state, workbook)
    return {'batch': batch, 'companies': results, 'coverage_complete': False,
            'requests_this_run': providers.dripify_collector.requests,
            'outreach_ready': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--master', required=True)
    init.add_argument('--sheet')
    init.add_argument('--mapping', required=True, help='JSON mapping file: company/priority/category/website/etc to exact source headers')
    seed = sub.add_parser('seed')
    seed.add_argument('--checklist', required=True)
    seed.add_argument('--hubspot-evidence', required=True)
    execute = sub.add_parser('run')
    execute.add_argument('--batch', type=int, required=True)
    execute.add_argument('--workbook', required=True)
    execute.add_argument('--public-web', action='store_true')
    execute.add_argument('--live-read', action='store_true')
    execute.add_argument('--apollo-read', action='store_true', help='Separately authorized zero-credit People Search only')
    dripify = sub.add_parser('check-dripify')
    dripify.add_argument('--batch', type=int, required=True)
    dripify.add_argument('--workbook', required=True)
    tavily = sub.add_parser('tavily-pilot')
    tavily.add_argument('--workbook', required=True)
    tavily.add_argument('--three-company-retest', action='store_true')
    tavily.add_argument('--execute', action='store_true', help='Requires recorded pilot and terms approval; default prepares only')
    apollo = sub.add_parser('apollo-discovery')
    apollo.add_argument('--batch', type=int, required=True)
    apollo.add_argument('--workbook', required=True)
    apollo.add_argument('--execute', action='store_true')
    apollo.add_argument('--live-checks', action='store_true', help='Existing authorized read-only HubSpot/Dripify; default cache/pending only')
    handoff = sub.add_parser('candidate-handoff')
    handoff.add_argument('--input', required=True)
    handoff.add_argument('--batch', type=int, required=True)
    handoff.add_argument('--workbook', required=True)
    handoff.add_argument('--apply', action='store_true', help='Local project/workbook only; default validates without changes')
    review = sub.add_parser('record-review')
    review.add_argument('--batch', required=True, type=int)
    review.add_argument('--evidence-ref', required=True)
    recheck = sub.add_parser('recheck')
    recheck.add_argument('--company-position', type=int, required=True)
    recheck.add_argument('--stage', choices=STAGES, required=True)
    recheck.add_argument('--evidence-ref', required=True)
    sources = sub.add_parser('sources')
    sources.add_argument('--file', required=True)
    sources.add_argument('--evidence-ref', required=True)
    sub.add_parser('status')
    sub.add_parser('completion-report')
    args = parser.parse_args()
    try:
        if args.command == 'candidate-handoff' and not args.apply:
            from candidate_handoff import validate_rows
            rows = json.loads(Path(args.input).read_text())
            state = State(args.project, readonly=True)
            try:
                state.permit_batch(args.batch)
                validate_rows(state, rows, args.batch)
                print(json.dumps({'validated_records': len(rows), 'applied': False, 'provider_calls': 0}))
            finally:
                state.close()
            return
        with project_lock(args.project):
            state = State(args.project)
            try:
                if args.command == 'init':
                    rows = read_master(args.master, json.loads(Path(args.mapping).read_text()), args.sheet)
                    state.initialize(rows, hashlib.sha256(Path(args.master).read_bytes()).hexdigest())
                    state.archive_source(args.master)
                    result = {'companies_imported': len(rows), 'batches': (len(rows) + 9) // 10, 'processing_started': False}
                elif args.command == 'seed':
                    seed_existing(state, args.checklist, args.hubspot_evidence)
                    result = {'contacts_retained': len(state.contacts()), 'processing_started': False}
                elif args.command == 'sources':
                    plan = json.loads(Path(args.file).read_text())
                    names = {c['data']['company'] for c in state.companies()}
                    if not isinstance(plan, dict) or set(plan) - names:
                        raise ValueError('Source plan may not introduce target companies')
                    for items in plan.values():
                        if not isinstance(items, list) or any(not isinstance(x, dict) or x.get('authority') not in ('official', 'corroborating') or not isinstance(x.get('url'), str) for x in items):
                            raise ValueError('Source plan schema invalid')
                    state.set_meta('public_source_plan', plan)
                    state.set_meta('public_source_plan_ref', args.evidence_ref)
                    result = {'source_plan_saved': True, 'processing_started': False}
                elif args.command == 'run':
                    if not state.meta('master_signature'):
                        raise ValueError('Import master first')
                    result = run(state, args.batch, args.workbook, Providers(state, live=args.live_read, apollo_enabled=args.apollo_read), public_web=args.public_web)
                elif args.command == 'tavily-pilot':
                    from tavily_search import prepare, prepare_retest, run_pilot
                    result = run_pilot(state, args.workbook, enabled=True, retest=args.three_company_retest) if args.execute else (prepare_retest(state) if args.three_company_retest else prepare(state))
                elif args.command == 'candidate-handoff':
                    from candidate_handoff import ingest, resolve_company, provider_review, refresh
                    rows = json.loads(Path(args.input).read_text())
                    if not isinstance(rows, list): raise ValueError('Candidate list required')
                    state.permit_batch(args.batch)
                    for row in rows: resolve_company(state, row, args.batch)
                    if not args.apply:
                        result = {'validated_records': len(rows), 'applied': False, 'provider_calls': 0}
                    else:
                        ids = ingest(state, rows, args.batch)
                        readers = Providers(state, live=False)
                        for company in state.companies(args.batch):
                            provider_review(state, company['id'], readers.identity(company), 'hubspot')
                            provider_review(state, company['id'], readers.dripify(company), 'dripify')
                            refresh(state, company_id=company['id'])
                        update(state, args.workbook); write_reports(state)
                        result = {'candidate_ids': ids, 'applied': True, 'provider_calls': 0, 'outreach_ready': False}
                elif args.command == 'apollo-discovery':
                    result = discover_apollo(state, args.batch, args.workbook, enabled=args.execute, live_checks=args.live_checks)
                elif args.command == 'check-dripify':
                    result = check_dripify(state, args.batch, args.workbook)
                elif args.command == 'recheck':
                    state.recheck(args.company_position, args.stage, args.evidence_ref)
                    result = {'recheck_queued': args.company_position, 'stage': args.stage, 'processing_started': False}
                elif args.command == 'record-review':
                    state.review_batch(args.batch, args.evidence_ref)
                    result = {'recorded_research_review': args.batch, 'outreach_approval': False}
                else:
                    result = write_reports(state, completion=args.command == 'completion-report')
                    result = {k: v for k, v in result.items() if k != 'companies'}
                if args.command not in ('status', 'completion-report'):
                    write_reports(state, completion=args.command == 'record-review')
                print(json.dumps(result))
            finally:
                state.close()
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        parser.exit(2, 'Validation failed; source/CRM unchanged. Check mapping, prior review, project lock and workbook schema.\n')


if __name__ == '__main__':
    main()
