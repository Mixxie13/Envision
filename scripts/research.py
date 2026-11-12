"""Bounded public-site evidence collection; text is data, never instructions."""
from html.parser import HTMLParser
import hashlib
import ipaddress
import json
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import urllib.request
import urllib.error

UA = 'Envision-Research/1.0'
SHARED = {'info', 'hello', 'support', 'sales', 'press', 'contact', 'office', 'admin', 'help', 'media', 'media.line', 'orders', 'collabs', 'protection', 'website', 'complianceamericas'}


def public_url(url):
    p = urlparse(url or '')
    host = (p.hostname or '').lower()
    try:
        port = p.port
    except ValueError:
        return False
    if p.scheme not in ('https', 'http') or not host or p.username or p.password or port not in (None, 80, 443):
        return False
    if '.' not in host or host.endswith(('.localhost', '.local', '.internal', '.test')) or host in ('localhost', 'metadata.google.internal'):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.text, self.jsonld = [], [], []
        self.anchors = []
        self.anchor = None
        self.publication_dates = []
        self.hidden = 0
        self.script = None
        self.tags = []
        self.segments = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta' and a.get('property') in ('article:published_time', 'article:modified_time') and a.get('content'):
            self.publication_dates.append({'kind': a['property'], 'value': a['content']})
        if tag == 'time' and a.get('datetime'):
            self.publication_dates.append({'kind': 'published_time_element', 'value': a['datetime']})
        if tag not in ('br', 'img', 'meta', 'link', 'input', 'hr', 'source', 'wbr'):
            self.tags.append(tag)
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag == 'script' and a.get('type') == 'application/ld+json':
            self.script = []
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
            self.anchor = {'href': a['href'], 'text': ''}

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor is not None:
            heading = next((s['text'] for s in reversed(self.segments) if s['tag'] in ('h1', 'h2', 'h3', 'h4')), '')
            self.anchor['heading_name'] = heading
            self.anchors.append(self.anchor)
            self.anchor = None
        if tag in self.tags:
            self.tags = self.tags[:len(self.tags) - 1 - self.tags[::-1].index(tag)]
        if tag == 'script' and self.script is not None:
            try:
                self.jsonld.append(json.loads(''.join(self.script)))
            except ValueError:
                pass
            self.script = None
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if not self.hidden:
            if self.anchor is not None: self.anchor['text'] += data
            self.text.append(data)
            value = ' '.join(data.split())
            if value:
                self.segments.append({'tag': next((t for t in reversed(self.tags) if t in ('h1', 'h2', 'h3', 'h4', 'p')), 'text'), 'text': value})


def walk(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def extract(html, source):
    page = Page()
    page.feed(html)
    people = []
    for item in walk(page.jsonld):
        if item.get('@type') != 'Person' or not isinstance(item.get('name'), str):
            continue
        title = item.get('jobTitle')
        if not isinstance(title, str) or not relevant_role(title):
            continue
        profiles = item.get('sameAs', [])
        profiles = [profiles] if isinstance(profiles, str) else profiles if isinstance(profiles, list) else []
        li = next((u for u in profiles if isinstance(u, str) and urlparse(u).hostname in ('linkedin.com', 'www.linkedin.com') and urlparse(u).path.startswith('/in/')), '')
        people.append({'name': item['name'], 'role': title, 'linkedin_url': li,
                       'source_url': source, 'employment_confirmed': False,
                       'published_employer': item.get('worksFor', {}).get('name') if isinstance(item.get('worksFor'), dict) else None,
                       'employment_end_date': item.get('endDate'),
                       'note': 'Structured publication claim only; current personal employment evidence required'})
    emails = []
    # Published mailto links, not patterns, personal domains, or arbitrary text harvesting.
    for href in page.links:
        if not href.lower().startswith('mailto:'):
            continue
        value = href[7:].split('?')[0]
        if not re.fullmatch(r'[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', value):
            continue
        domain = value.rsplit('@', 1)[1].lower()
        if domain in ('gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com'):
            continue
        emails.append({'email': value, 'published': True, 'source_url': source,
                       'shared': value.split('@')[0].lower() in SHARED,
                       'person_linkage': 'unresolved', 'verification': 'not run'})
    # Explicit JSON-LD and named anchor associations are publication linkage only.
    for item in walk(page.jsonld):
        if item.get('@type') == 'Person' and isinstance(item.get('name'), str) and isinstance(item.get('email'), str):
            address = item['email'].removeprefix('mailto:')
            if re.fullmatch(r'[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', address):
                emails.append({'email': address, 'published': True, 'published_name': item['name'],
                    'source_url': source, 'shared': address.split('@')[0].lower() in SHARED,
                    'person_linkage': 'explicit_structured_person_publication', 'verification': 'not run',
                    'identity_confirmed': False})
    for email in emails:
        labels = [' '.join(a['text'].split()) for a in page.anchors if a['href'].lower().startswith('mailto:') and a['href'][7:].split('?')[0].lower() == email['email'].lower()]
        named = [label for label in labels if re.fullmatch(r"[A-Z][A-Za-z’'\-]+(?: [A-Z][A-Za-z’'\-]+){1,3}", label) and label.casefold() not in ('contact us', 'customer service', 'general inquiries', 'media contact')]
        if len(set(named)) == 1 and not email['shared']:
            email.update(published_name=named[0], person_linkage='explicit_named_mailto_publication', identity_confirmed=False)
    emails = [e for e in emails if e['email'].rsplit('@', 1)[1].lower() not in ('gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com')]
    for item in walk(page.jsonld):
        for field in ('datePublished', 'dateModified'):
            if isinstance(item.get(field), str):
                page.publication_dates.append({'kind': field, 'value': item[field]})
    visible = ' '.join(page.text)
    for match in re.finditer(r'\bPublished\s+(\d{1,2}/\d{1,2}/\d{4})\b', visible):
        page.publication_dates.append({'kind': 'explicit_published_label', 'value': match[1]})
    from reconcile_exports import linkedin
    profile_links = [{'url': a['href'], 'heading_name': a.get('heading_name', '')} for a in page.anchors if linkedin(a['href'])]
    return {'people': people, 'emails': emails, 'profile_links': profile_links, 'publication_dates': page.publication_dates,
            'links': page.links, 'segments': page.segments[:2500], 'text_excerpt': ' '.join(page.text)[:80000]}


def relevant_role(title):
    value = title.lower()
    excluded = ('sales', 'finance', 'customer support', 'customer service', 'engineering', 'operations', 'human resources', 'design', 'product manager', 'chief executive', 'ceo', 'founder', 'owner', 'cmo', 'chief marketing', 'general manager', 'general management', 'managing director')
    if any(s in value for s in excluded):
        return False  # Founder/executive exceptions require evidence/review, never automatic.
    has_function = bool(re.search(r'\b(?:marketing|brand|social media|community|influencer|growth|digital|ecommerce)\b', value))
    seniority = bool(re.search(r'\b(?:director|head|manager|lead|president|vp|svp)\b', value))
    secondary = bool(re.search(r'\b(?:social media|community|influencer)\b', value)) and bool(re.search(r'\b(?:coordinator|associate)\b', value))
    return has_function and (seniority or secondary)


class PublicResearch:
    """No browser/search-engine bypass; same-site bounded published evidence."""
    def __init__(self, fetch=None, pause=time.sleep):
        self.fetch = fetch or self._fetch
        self.pause = pause

    @staticmethod
    def _fetch(url):
        if not public_url(url):
            return {'state': 'pending', 'reason': 'NONPUBLIC_OR_UNSAFE_URL'}
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,text/plain'}, method='GET')
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
                raw = response.read(2097153)
                if len(raw) > 2097152:
                    return {'state': 'pending', 'reason': 'PAGE_SIZE_LIMIT'}
                typ = response.headers.get_content_type()
                body = raw.decode('utf-8', errors='replace')
                if any(term in body.lower() for term in ('/.well-known/sgcaptcha/', 'challenge-form', 'cf-chl-', 'verify you are human', 'anomaly.js')):
                    return {'state': 'pending', 'status': response.status, 'reason': 'PUBLIC_ACCESS_CHALLENGE'}
                if response.status != 200:
                    return {'state': 'pending', 'status': response.status, 'reason': 'PUBLIC_NON_SUCCESS_RESPONSE'}
                if typ not in ('text/html', 'text/plain', 'application/xml', 'text/xml', 'application/json'):
                    return {'state': 'pending', 'reason': 'UNSUPPORTED_CONTENT_TYPE'}
                return {'state': 'observed', 'status': response.status,
                        'body': body, 'sha256': hashlib.sha256(raw).hexdigest()}
        except urllib.error.HTTPError as exc:
            return {'state': 'pending', 'status': exc.code, 'reason': 'PUBLIC_HTTP_ERROR'}
        except (OSError, ValueError):
            return {'state': 'pending', 'reason': 'PUBLIC_FETCH_FAILED'}

    def collect(self, website, limit=3):
        if not public_url(website):
            return {'state': 'pending', 'reason': 'OFFICIAL_WEBSITE_UNRESOLVED', 'pages': []}
        p = urlparse(website)
        origin = p.scheme + '://' + p.netloc
        robots = self.fetch(origin + '/robots.txt')
        parser = RobotFileParser()
        if robots.get('state') == 'observed':
            parser.parse(robots['body'].splitlines())
        elif robots.get('status') == 404:
            parser.parse([])
        else:
            return {'state': 'pending', 'reason': 'ROBOTS_ACCESS_UNRESOLVED', 'pages': []}
        delay = parser.crawl_delay(UA) or 1
        rate = parser.request_rate(UA)
        if rate and rate.requests:
            delay = max(delay, rate.seconds / rate.requests)
        if delay > 60:
            return {'state': 'pending', 'reason': 'CRAWL_DELAY_REQUIRES_LATER_SCHEDULING', 'pages': []}
        queue, seen, pages = [website], set(), []
        while queue and len(pages) < limit:
            url = queue.pop(0).split('#')[0]
            if url in seen:
                continue
            seen.add(url)
            if urlparse(url).netloc != p.netloc or not parser.can_fetch(UA, url):
                continue
            self.pause(delay)
            result = self.fetch(url)
            if result.get('state') == 'observed':
                parsed = extract(result.pop('body'), url)
                result.update(parsed)
                for link in parsed['links']:
                    target = urljoin(url, link)
                    if any(word in urlparse(target).path.lower() for word in ('team', 'leadership', 'about', 'press', 'contact')) and urlparse(target).netloc == p.netloc:
                        queue.append(target)
                result.pop('links', None)
            pages.append({'url': url, **result})
            if result.get('status') in (401, 403, 429):
                break
        return {'state': 'pending', 'reason': 'RESEARCH_OBSERVATIONS_REQUIRE_IDENTITY_REVIEW',
                'pages': pages, 'search_coverage': 'Bounded official-site collection only; no general web/Apollo/LinkedIn search integration'}
