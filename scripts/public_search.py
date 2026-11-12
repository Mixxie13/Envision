"""Bounded public HTML discovery with robots and explicit CAPTCHA stop.

Search results nominate source URLs only; snippets never confirm employment.
"""
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin, parse_qs, urlencode
from urllib.robotparser import RobotFileParser
import time

from research import PublicResearch, public_url, UA


class Results(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag != 'a' or 'result__a' not in a.get('class', '').split():
            return
        target = urljoin('https://html.duckduckgo.com', a.get('href', ''))
        if (urlparse(target).hostname or '').endswith('duckduckgo.com'):
            target = parse_qs(urlparse(target).query).get('uddg', [''])[0]
        if public_url(target):
            self.urls.append(target)


class PublicSearch:
    def __init__(self, fetch=None, pause=time.sleep):
        self.fetch = fetch or PublicResearch._fetch
        self.pause = pause
        self.rules = None
        self.stopped = False

    def collect(self, company, limit=5):
        query = '"' + company.replace('"', '') + '" ("brand manager" OR "marketing director" OR "head of marketing" OR "brand director")'
        url = 'https://html.duckduckgo.com/html/?' + urlencode({'q': query})
        if self.stopped:
            return {'state': 'pending', 'reason': 'PUBLIC_SEARCH_STOPPED_FOR_ACCESS_LIMIT', 'query': query, 'sources': []}
        if self.rules is None:
            response = self.fetch('https://html.duckduckgo.com/robots.txt')
            if response.get('state') != 'observed':
                self.stopped = True
                return {'state': 'pending', 'reason': 'PUBLIC_SEARCH_ROBOTS_UNAVAILABLE', 'query': query, 'sources': []}
            self.rules = RobotFileParser()
            self.rules.parse(response['body'].splitlines())
        if not self.rules.can_fetch(UA, url):
            self.stopped = True
            return {'state': 'pending', 'reason': 'PUBLIC_SEARCH_NOT_PERMITTED', 'query': query, 'sources': []}
        delay = self.rules.crawl_delay(UA) or 1
        rate = self.rules.request_rate(UA)
        if rate and rate.requests:
            delay = max(delay, rate.seconds / rate.requests)
        if delay > 60:
            self.stopped = True
            return {'state': 'pending', 'reason': 'PUBLIC_SEARCH_DELAY_UNSUPPORTED', 'query': query, 'sources': []}
        self.pause(delay)
        result = self.fetch(url)
        body = result.get('body', '')
        challenge = any(term in body.lower() for term in ('anomaly.js', 'challenge-form', 'bots use duckduckgo', 'verify you are human', 'captcha'))
        if result.get('state') != 'observed' or challenge:
            self.stopped = True
            return {'state': 'pending', 'reason': 'PUBLIC_SEARCH_ACCESS_CHALLENGE' if challenge else 'PUBLIC_SEARCH_UNAVAILABLE', 'query': query, 'sources': []}
        parser = Results(); parser.feed(body)
        sources = list(dict.fromkeys(parser.urls))[:limit]
        return {'state': 'pending', 'reason': 'SEARCH_URLS_REQUIRE_SOURCE_VERIFICATION', 'query': query, 'search_url': url, 'sources': sources,
                'limit': 'One unpaged public query; search snippets are not person/company evidence'}


class BraveSearch:
    """Official opt-in API. No request or credential read without explicit approval.

    Returned titles/snippets nominate URLs; they are never employment evidence.
    """
    def __init__(self, approved=False, max_queries=0, fetch=None):
        self.approved = approved
        self.remaining = min(max_queries, 10) if isinstance(max_queries, int) and max_queries > 0 else 0
        self.fetch = fetch or self._fetch
        self.stopped = False

    @staticmethod
    def _fetch(url):
        import json, os, urllib.request, urllib.error
        from research import NoRedirect
        key = os.environ.get('BRAVE_SEARCH_API_KEY', '')
        if not key.strip():
            return {'reason': 'BRAVE_CREDENTIAL_NOT_CONFIGURED'}
        req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': UA,
            'X-Subscription-Token': key}, method='GET')
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
                raw = response.read(2097153)
                if response.status != 200 or len(raw) > 2097152:
                    return {'reason': 'BRAVE_RESPONSE_INCOMPLETE'}
                data = json.loads(raw)
                from email_api import redact
                return {'data': redact(data, key)}
        except urllib.error.HTTPError as exc:
            return {'reason': 'BRAVE_HTTP_' + str(exc.code)}
        except (OSError, ValueError):
            return {'reason': 'BRAVE_REQUEST_FAILED'}

    def collect(self, company, website='', limit=5):
        from datetime import datetime, timezone
        result = {'state': 'pending', 'sources': [], 'provider': 'Brave Search API',
                  'reason': 'PUBLIC_SEARCH_NOT_AUTHORIZED'}
        if not self.approved:
            return result
        if self.stopped or self.remaining <= 0:
            return {**result, 'reason': 'PUBLIC_SEARCH_BUDGET_OR_PROVIDER_STOP'}
        self.remaining -= 1
        query = '"' + company.replace('"', '') + '" ("brand director" OR "head of marketing" OR "marketing director" OR "brand partnerships")'
        url = 'https://api.search.brave.com/res/v1/web/search?' + urlencode({'q': query, 'count': min(limit, 10)})
        response = self.fetch(url)
        data = response.get('data', {})
        if 'reason' in response:
            self.stopped = True
            return {**result, 'reason': response['reason']}
        rows = data.get('web', {}).get('results') if isinstance(data, dict) and isinstance(data.get('web'), dict) else None
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            self.stopped = True
            return {**result, 'reason': 'PUBLIC_SEARCH_SCHEMA_UNVERIFIED'}
        return {**result, 'reason': 'SEARCH_URLS_REQUIRE_SOURCE_VERIFICATION',
                'sources': list(dict.fromkeys(r['url'] for r in rows if isinstance(r.get('url'), str) and public_url(r['url'])))[:limit],
                'query': query, 'observed_at': datetime.now(timezone.utc).isoformat(),
                'coverage': 'Bounded ranked search; no completeness or current-employment assertion'}


class OfficialSiteSearch:
    """Same-origin public sitemap URL discovery, bounded and robots-compliant.

    This is a limited company-site source search, not a general web index.
    """
    def __init__(self, fetch=None, pause=time.sleep):
        self.fetch = fetch or PublicResearch._fetch
        self.pause = pause

    def collect(self, company, website='', limit=5):
        import xml.etree.ElementTree as ET
        from datetime import datetime, timezone
        result = {'state': 'pending', 'provider': 'Official company sitemaps', 'sources': [],
                  'observed_at': datetime.now(timezone.utc).isoformat(), 'checks': []}
        if not public_url(website):
            return {**result, 'reason': 'OFFICIAL_WEBSITE_UNRESOLVED'}
        p = urlparse(website); origin = p.scheme + '://' + p.netloc
        robots = self.fetch(origin + '/robots.txt')
        rules = RobotFileParser()
        if robots.get('state') == 'observed': rules.parse(robots['body'].splitlines())
        elif robots.get('status') == 404: rules.parse([])
        else: return {**result, 'reason': 'ROBOTS_ACCESS_UNRESOLVED'}
        delay = rules.crawl_delay(UA) or 1
        rate = rules.request_rate(UA)
        if rate and rate.requests: delay = max(delay, rate.seconds / rate.requests)
        if delay > 60: return {**result, 'reason': 'CRAWL_DELAY_REQUIRES_SCHEDULING'}
        queue = rules.site_maps() or [origin + '/sitemap.xml']
        seen = set(); urls = []
        while queue and len(seen) < 3:
            url = queue.pop(0)
            if url in seen or not public_url(url) or urlparse(url).netloc != p.netloc or not rules.can_fetch(UA, url): continue
            seen.add(url); self.pause(delay); response = self.fetch(url)
            result['checks'].append({'url': url, 'state': response.get('state'), 'status': response.get('status'), 'reason': response.get('reason')})
            if response.get('state') != 'observed':
                if response.get('status') in (401, 403, 429) or response.get('reason') == 'PUBLIC_ACCESS_CHALLENGE': break
                continue
            try: root = ET.fromstring(response.get('body', ''))
            except ET.ParseError: continue
            locations = [n.text.strip() for n in root.iter() if n.tag.rsplit('}', 1)[-1] == 'loc' and n.text]
            if root.tag.rsplit('}', 1)[-1] == 'sitemapindex':
                children = [u for u in locations if not any(x in urlparse(u).path.lower() for x in ('product', 'image', 'video'))]
                queue += sorted(children, key=lambda u: (not any(x in u for x in ('page', 'post', 'blog', 'news')), u))
            elif root.tag.rsplit('}', 1)[-1] == 'urlset':
                urls += [u for u in locations if not any(x in urlparse(u).path.lower() for x in ('newsletter', 'subscription', 'sign-up', 'signup')) and public_url(u) and urlparse(u).netloc == p.netloc and rules.can_fetch(UA, u) and any(x in urlparse(u).path.lower() for x in ('leadership', 'team', 'people', 'press', 'news', 'about', 'contact', 'partnership', 'marketing'))]
        result['sources'] = sorted(set(urls), key=lambda u: (not any(k in u.lower() for k in ('leadership', 'team', 'people')), not any(k in u.lower() for k in ('press', 'news')), u))[:limit]
        return {**result, 'reason': 'OFFICIAL_SOURCE_URLS_REQUIRE_PERSON_VERIFICATION' if urls else 'NO_SOURCE_URLS_IN_BOUNDED_SITE_SEARCH',
                'coverage': 'At most three same-origin sitemap documents; no general-web coverage'}
