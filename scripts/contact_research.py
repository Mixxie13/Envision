"""Repeatable public-source candidate discovery and conservative claim binding.

Extraction proves a publication claim, not current employment or CRM clearance.
"""
import re
from urllib.parse import urlparse

from research import relevant_role

ROLE = re.compile(r'(?:Vice President|VP|SVP|Senior Director|Director|Head|Senior Manager|Manager|Lead|Coordinator|Associate|Senior Brand Manager|Brand Manager|Marketing Director|Marketing Manager)[^.!?\n]{0,100}', re.I)
NAME = re.compile(r"^[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ’'\-]+(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ’'\-]+){1,4}$")
BAD_NAMES = re.compile(r'\b(team|leadership|marketing|brand|president|director|manager|contact|about|privacy|terms|nutrition|company|united|states|learn|read|social|media)\b', re.I)


def role_rank(title):
    t = title.lower()
    if 'brand director' in t or ('director' in t and 'brand' in t):
        return 1
    if 'head' in t and 'brand' in t:
        return 2
    if 'brand manager' in t:
        return 3
    if 'marketing' in t and ('director' in t or 'head' in t or 'president' in t):
        return 4
    if 'marketing' in t and 'manager' in t:
        return 5
    if any(word in t for word in ('social', 'influencer', 'community')):
        return 6
    return 7


def clean_role(value, name=''):
    value = re.sub(r'\s+', ' ', value).strip(' ,:;–-')
    if name:
        value = value.split(name)[0]
    value = re.split(r'\b(?:joined|brings|has been|has over|is responsible|read more|learn more|at|for)\b', value, flags=re.I)[0]
    return re.sub(r'\s+\d+$', '', value.strip(' ,:;–-'))[:120]


def discover(page, company, known_people=(), authority='unverified'):
    """Only named structured/card/explicit-attribution publication evidence.

    Known-name lookup requires the full name adjacent to a role in the source.
    It must not assign a role from the CRM record to an unrelated name mention.
    """
    url = page['url']
    text = page.get('text_excerpt', '')
    candidates = list(page.get('people', [])) + list(page.get('decision_maker_claims', []))
    segments = page.get('segments', [])
    for index, segment in enumerate(segments):
        name = segment.get('text', '').strip()
        if segment.get('tag') not in ('h1', 'h2', 'h3', 'h4') or not NAME.fullmatch(name) or BAD_NAMES.search(name):
            continue
        nearby = []
        for following_segment in segments[index + 1:index + 4]:
            if following_segment.get('tag') in ('h1', 'h2', 'h3', 'h4') or following_segment.get('text', '').strip().casefold() in ('linkedin', 'view profile'):
                break
            nearby.append(following_segment.get('text', ''))
        following = ' '.join(nearby)
        title = clean_role(following, name)
        if title and len(title.split()) <= 14 and relevant_role(title):
            candidates.append({'name': name, 'role': title, 'linkedin_url': '', 'extraction': 'named heading and adjacent role'})
    # Full-name + immediately following role supports existing research candidates
    # even on old snapshots whose HTML block structure was not retained.
    for person in known_people:
        name = person.get('name', '')
        if not name or '(' in name:
            continue
        pattern = re.compile(re.escape(name) + r'\s*[,–:\-]?\s*(' + ROLE.pattern + r')', re.I)
        for match in pattern.finditer(text):
            role = clean_role(match[1], name)
            if relevant_role(role):
                candidates.append({'name': name, 'role': role, 'linkedin_url': '', 'extraction': 'exact full name adjacent to published role', 'quote': match[0][:240]})
    # Quoted source attribution: “said First Last, Marketing Director ...”.
    quote = re.compile(r'(?:said|says)\s+([A-Z][a-z’\-]+\s+[A-Z][a-z’\-]+),\s*([^.!?\n]{4,120})')
    for match in quote.finditer(text):
        role = clean_role(match[2])
        if relevant_role(role):
            candidates.append({'name': match[1], 'role': role, 'linkedin_url': '', 'extraction': 'explicit named quotation attribution', 'quote': match[0]})
    unique = {}
    for candidate in candidates:
        name, role = candidate.get('name', ''), clean_role(candidate.get('role', ''), candidate.get('name', ''))
        if not NAME.fullmatch(name) or BAD_NAMES.search(name) or not relevant_role(role):
            continue
        links = [p['url'] for p in page.get('profile_links', []) if p.get('heading_name', '').casefold() == name.casefold()]
        if not candidate.get('linkedin_url') and len(set(links)) == 1:
            candidate = {**candidate, 'linkedin_url': links[0]}
        proof = {'source_url': url, 'source_observed_at': page.get('observed_at'),
                 'source_sha256': page.get('sha256'), 'authority': authority,
                 'publication_dates': page.get('publication_dates', []),
                 'personal_linkedin_access_confirmed': False,
                 'company': company, 'published_name': name, 'published_role': role,
                 'extraction': candidate.get('extraction', 'structured Person publication'),
                 'quote': candidate.get('quote', name + ' — ' + role),
                 'evidence_kind': 'announcement_or_spotlight_currentness_unresolved' if any(k in urlparse(url).path.lower() for k in ('press', 'news', 'spotlight')) else 'directory_publication_needs_current_profile',
                 'current_role_claim': authority == 'official' and any(k in urlparse(url).path.lower() for k in ('leadership', 'team', 'people')),
                 'date_limit': 'Observation date is not proof that employment remains current; policy freshness undefined'}
        unique[name.casefold()] = {**candidate, 'source_url': url, 'role_rank': role_rank(role),
            'company': company, 'person_evidence': proof,
            'official_role_supported': authority == 'official',
            'current_employment_confirmed': False, 'employment_confirmed': False,
            'research_status': 'official_role_supported_linkedin_pending' if authority == 'official' else 'provisional',
            'outreach_ready': False}
    return sorted(unique.values(), key=lambda p: (p['role_rank'], p['name']))


def assess_person(person, observations, employment_claims=(), as_of=None):
    """Independent dimensions; no source agreement or mailbox validates a role."""
    evidence = [o for o in observations if o.get('published_name', '').casefold() == person.get('name', '').casefold()]
    official = [o for o in evidence if o.get('authority') == 'official']
    current = [o for o in official if o.get('current_role_claim')]
    from employment import assess
    from datetime import datetime, timezone
    employment = assess(person, list(employment_claims), as_of or datetime.now(timezone.utc).isoformat())
    return {'name': person.get('name'), 'official_role_supported': bool(official),
            'current_official_publication': bool(current),
            'personal_linkedin_confirmed': employment['current_employment_confirmed'],
            'current_employment_confirmed': employment['current_employment_confirmed'],
            'qualified_contact_confirmed': False, 'employment_assessment': employment, 'evidence': evidence,
            'pending': ([] if employment['current_employment_confirmed'] else ['Personal identity/current employment confirmation']) + ['Company ownership/employee email domain', 'Provider suppression/history and freshness'],
            'outreach_ready': False}


class DecisionMakerResearch:
    """A versioned source queue: explicit sources + bounded site discovery.

    Uses authorized public pages only. No search-engine restriction bypass,
    private database, paid reveal or automatic authority from page instructions.
    """
    def __init__(self, fetch=None, pause=None):
        from research import PublicResearch
        self.transport = PublicResearch(fetch=fetch, **({'pause': pause} if pause else {}))
        self.fetch = self.transport.fetch
        self.pause = self.transport.pause

    def collect(self, website, company, known_people=(), sources=(), cached_pages=(), limit=8, max_new_pages=3):
        from research import public_url, extract, UA
        from urllib.parse import urljoin
        from urllib.robotparser import RobotFileParser
        from datetime import datetime, timezone
        if not public_url(website):
            return {'state': 'pending', 'reason': 'OFFICIAL_WEBSITE_UNRESOLVED', 'pages': [], 'candidates': []}
        root = urlparse(website)
        root_host = (root.hostname or '').removeprefix('www.')
        queue = [{'url': x['url'], 'authority': x.get('authority', 'corroborating')} for x in sources]
        queue += [{'url': website, 'authority': 'official'}]
        cache = {p['url']: p for p in cached_pages if p.get('state') == 'observed'}
        for p in cached_pages:
            if p.get('state') == 'observed' and p['url'] not in {q['url'] for q in queue}:
                queue.append({'url': p['url'], 'authority': 'official' if (urlparse(p['url']).hostname or '').removeprefix('www.') == root_host else 'corroborating'})
        robots, pages, seen, candidates = {}, [], set(), []
        new_pages = 0
        while queue and len(pages) < limit:
            entry = queue.pop(0)
            url = entry['url'].split('#')[0]
            if url in seen or not public_url(url):
                continue
            seen.add(url)
            p = urlparse(url)
            origin = p.scheme + '://' + p.netloc
            if url in cache:
                page = {**cache[url], 'reused_snapshot': True}
            else:
                if new_pages >= max_new_pages:
                    pages.append({'url': url, 'state': 'pending', 'reason': 'NEW_PAGE_BUDGET_REACHED'})
                    continue
                if origin not in robots:
                    raw = self.fetch(origin + '/robots.txt')
                    parser = RobotFileParser()
                    if raw.get('state') == 'observed':
                        parser.parse(raw['body'].splitlines())
                        robots[origin] = parser
                    elif raw.get('status') == 404:
                        parser.parse([])
                        robots[origin] = parser
                    else:
                        robots[origin] = None
                rules = robots[origin]
                if rules is None or not rules.can_fetch(UA, url):
                    pages.append({'url': url, 'state': 'pending', 'reason': 'SOURCE_ACCESS_OR_ROBOTS_UNRESOLVED'})
                    continue
                delay = rules.crawl_delay(UA) or 1
                rate = rules.request_rate(UA)
                if rate and rate.requests:
                    delay = max(delay, rate.seconds / rate.requests)
                if delay > 60:
                    pages.append({'url': url, 'state': 'pending', 'reason': 'SOURCE_DELAY_REQUIRES_SCHEDULING'})
                    continue
                self.pause(delay)
                new_pages += 1
                page = {'url': url, **self.fetch(url), 'observed_at': datetime.now(timezone.utc).isoformat()}
                if page.get('state') == 'observed':
                    parsed = extract(page.pop('body'), url)
                    page.update(parsed)
                    # Choose research pages first; never crawl unrelated sites from arbitrary links.
                    links = []
                    for link in parsed['links']:
                        target = urljoin(url, link).split('#')[0]
                        t = urlparse(target)
                        if t.hostname != p.hostname or t.query or not public_url(target):
                            continue
                        if any(word in t.path.lower() for word in ('team', 'leadership', 'people', 'about', 'press', 'news')):
                            links.append({'url': target, 'authority': entry['authority']})
                    queue += sorted(links, key=lambda x: (0 if any(k in x['url'] for k in ('team', 'leadership')) else 1, x['url']))
                    page.pop('links', None)
                if page.get('status') in (401, 403, 429) or page.get('reason') == 'PUBLIC_ACCESS_CHALLENGE':
                    # Stop this host for the run; other companies/sources may continue.
                    robots[origin] = None
            page['authority'] = entry['authority']
            observations = discover(page, company, known_people, entry['authority']) if page.get('state') == 'observed' else []
            page['decision_maker_claims'] = observations
            candidates += observations
            pages.append(page)
        ranked = {}
        for person in sorted(candidates, key=lambda p: (not p['official_role_supported'], p['role_rank'])):
            ranked.setdefault(person['name'].casefold(), person)
        for candidate in ranked.values():
            from reconcile_exports import linkedin
            from employment import assess
            profile = linkedin(candidate.get('linkedin_url'))
            bound = [p for p in known_people if profile and linkedin(p.get('linkedin_url')) == profile]
            reviews = [claim for p in bound for claim in p.get('employment_claims', [])]
            subject = {**candidate, 'identity_conflict': any(p.get('identity_conflict') or p.get('name', '').casefold() != candidate['name'].casefold() for p in bound)}
            assessment = assess(subject, reviews, datetime.now(timezone.utc).isoformat())
            candidate['employment_assessment'] = assessment
            candidate['current_employment_confirmed'] = assessment['current_employment_confirmed']
            candidate['employment_confirmed'] = assessment['current_employment_confirmed']
        return {'state': 'pending', 'reason': 'PUBLIC_PERSON_CLAIMS_REQUIRE_CURRENT_IDENTITY_CONFIRMATION',
                'pages': pages, 'candidates': list(ranked.values()),
                'ownership_claims': [claim for page in pages for claim in ownership_claims(page.get('text_excerpt', ''), company, page['url'], page.get('authority'))],
                'target': '3–5 qualified people where supported; no quota fabrication',
                'confirmed_current_contacts': sum(p.get('current_employment_confirmed') is True for p in ranked.values()),
                'email_candidates': [e for page in pages for e in published_emails(page, company)],
                'new_page_reads': new_pages,
                'shortfall': max(0, 3 - len(ranked)),
                'source_coverage': 'Bounded authorized source plan and same-site discovery; not exhaustive web search'}


def ownership_claims(text, company, source_url, authority):
    """Retain explicit relationship wording; no owner inferred from web hosting."""
    if authority != 'official':
        return []
    claims = []
    # Wording must bind the supplied brand itself, not another brand on the page.
    pattern = re.compile(re.escape(company) + r'\s*(?:[®™]\s*)?(?:,\s*)?(?:(?:is|was|became|remains|now|proudly|a|an|wholly)\s+){0,4}(?:owned by|part of|subsidiary of|brand of)\b[^.!?\n]{2,100}', re.I)
    for match in pattern.finditer(text):
        claims.append({'company': company, 'source_url': source_url, 'quote': match[0],
                       'result': 'explicit_published_relationship',
                       'limit': 'Legal ownership/currentness and employee domain need corroboration; not complete company verification'})
    return claims


def published_emails(page, company):
    """Explicit published mailboxes only; never infer an address or its owner.

    Adjacent-name linkage is restricted to a visible Contact section. Agency
    contacts remain distinct from the brand's intended marketing decision-makers.
    """
    from research import SHARED
    result = {e['email'].casefold(): dict(e) for e in page.get('emails', [])}
    contact_section = False
    previous = ''
    for segment in page.get('segments', []):
        value = segment.get('text', '').strip()
        if segment.get('tag') in ('h1', 'h2', 'h3', 'h4'):
            contact_section = value.casefold() in ('contacts', 'media contacts', 'press contacts')
        if re.fullmatch(r'[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', value):
            local, domain = value.rsplit('@', 1)
            if domain.casefold() not in ('gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com'):
                evidence = result.setdefault(value.casefold(), {'email': value, 'published': True, 'source_url': page['url'], 'person_linkage': 'unresolved'})
                evidence['shared'] = local.casefold() in SHARED
                if contact_section and NAME.fullmatch(previous) and not BAD_NAMES.search(previous) and not evidence['shared']:
                    evidence.update(published_name=previous, person_linkage='explicit_adjacent_name_in_contacts_section')
        previous = value
    for row in result.values():
        row.update(company=company, source_url=page['url'], research_date=page.get('observed_at'),
            verification='not run', deliverability_verified=False, identity_confirmed=False,
            outreach_ready=False, requirement='Confirm named person/current role, employee domain, independent verification and suppression coverage')
    return list(result.values())


def company_bound(claim, company, same_company_site=False):
    """Bind a person-level claim, never a directory-wide brand mention."""
    import unicodedata
    def normalize(value):
        return unicodedata.normalize('NFKC', value).replace('’', "'").casefold().strip()
    target = normalize(company)
    if same_company_site or normalize(str(claim.get('published_employer') or '')) == target:
        return True
    quote = normalize(claim.get('person_evidence', {}).get('quote') or claim.get('quote', ''))
    return bool(re.search(r'\b(?:at|for|of)\s+' + re.escape(target) + r'\s*(?=$|[.,;:!?"“”])', quote))
