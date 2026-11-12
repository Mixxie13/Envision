"""Narrow Render reads for all-visible-lead and per-lead history evidence.

Pagination completion is not account/team visibility or retention completeness.
"""
import json
import hashlib
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from email_api import NoRedirect, redact


_paused_after_rate_limit = False


class ReadFailure(Exception):
    pass


def read(path, query=None):
    global _paused_after_rate_limit
    if _paused_after_rate_limit:
        raise ReadFailure('SERVICE_PAUSED_AFTER_RATE_LIMIT')
    if not re.fullmatch(r'leads(?:/[0-9]+(?:/(?:activity|conversation))?)?|conversations/[A-Za-z0-9%_.~-]+/messages', path):
        raise ValueError('Unsupported read path')
    key = os.environ.get('RELAY_TOKEN', '')
    if not key.strip():
        raise ReadFailure('RELAY_CREDENTIAL_MISSING')
    url = 'https://envision-email-relay.onrender.com/dripify/' + path
    if query:
        url += '?' + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, method='GET', headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            if response.status != 200:
                raise ReadFailure('PARTIAL_RESPONSE')
            return redact(json.load(response), key)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            _paused_after_rate_limit = True
        raise ReadFailure('RELAY_HTTP_' + str(exc.code)) from None
    except (OSError, ValueError):
        raise ReadFailure('READ_FAILED') from None


def pages(fetch, messages=False, max_pages=100, identity=None):
    items, cursors, identities, cursor = [], set(), set(), None
    for _ in range(max_pages):
        try:
            data = fetch(cursor)
            if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                raise ReadFailure('UNEXPECTED_PAGE_SCHEMA')
            rows = data['items']
            for row in rows:
                if not isinstance(row, dict):
                    raise ReadFailure('UNEXPECTED_ITEM_SCHEMA')
                if identity is None and ('id' not in row or not isinstance(row['id'], (int, str))):
                    raise ReadFailure('UNEXPECTED_ITEM_SCHEMA')
                item_id = str(row['id']) if identity is None else identity(row)
                if item_id in identities:
                    raise ReadFailure('DUPLICATE_ITEMS_OR_CHANGED_RESULT_SET')
                identities.add(item_id)
            items.extend(rows)
            next_cursor = data.get('nextCursor')
            if next_cursor is None:
                if messages and rows:
                    raise ReadFailure('MESSAGE_HISTORY_TERMINATION_UNVERIFIED')
                return {'items': items, 'pagination_complete': True,
                        'coverage_complete': False, 'reason': 'ACCOUNT_VISIBILITY_AND_RETENTION_UNVERIFIED'}
            if not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 128 or next_cursor in cursors:
                raise ReadFailure('INVALID_OR_REPEATED_CURSOR')
            cursors.add(next_cursor)
            cursor = next_cursor
        except ReadFailure as exc:
            return {'items': items, 'pagination_complete': False, 'coverage_complete': False, 'reason': str(exc)}
    return {'items': items, 'pagination_complete': False, 'coverage_complete': False, 'reason': 'PAGE_LIMIT_REACHED'}


def all_visible_leads(fetch=read):
    return pages(lambda cursor: fetch('leads', {'limit': '100', **({'cursor': cursor} if cursor is not None else {})}))


def history(lead_id, fetch=read):
    if not str(lead_id).isdigit():
        raise ValueError('Numeric lead ID required')
    prefix = 'leads/' + str(lead_id)
    result = {'coverage_complete': False}
    try:
        result['detail'] = fetch(prefix)
        result['activity'] = pages(lambda cursor: fetch(prefix + '/activity', {'limit': '100', **({'cursor': cursor} if cursor is not None else {})}), identity=lambda row: hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest())
        result['conversation'] = fetch(prefix + '/conversation')
        conv = result['conversation']
        # No undocumented conversation-ID fallback or absent-conversation semantics.
        identifier = conv.get('conversationId') if isinstance(conv, dict) else None
        if isinstance(identifier, str) and identifier:
            path = 'conversations/' + urllib.parse.quote(identifier, safe='') + '/messages'
            result['messages'] = pages(lambda cursor: fetch(path, {'cursor': cursor} if cursor is not None else {}), messages=True, identity=lambda row: str(row.get('eventId')) if row.get('eventId') else hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest())
        else:
            result['message_history_reason'] = 'CONVERSATION_ID_OR_SCHEMA_UNRESOLVED'
    except ReadFailure as exc:
        result['reason'] = str(exc)
    return result


class Collector:
    """Private state-backed read checkpoints shared across a batch, not per company.

    No TTL is invented: cached observations stay dated and cannot clear outreach.
    Caller must hold the project's existing single-writer lock.
    """
    def __init__(self, state, fetch=read, max_requests=12, max_pages=2):
        self.state, self.fetch = state, fetch
        self.remaining, self.max_pages = max_requests, max_pages
        self.requests = 0
        self.visited = set()
        self.lookup_results = {}

    def _read(self, path, query=None):
        if self.remaining <= 0:
            raise ReadFailure('RUN_REQUEST_BUDGET_REACHED')
        self.remaining -= 1
        self.requests += 1
        import time
        time.sleep(1.1)
        return self.fetch(path, query)

    def resource(self, path):
        from sourcing_state import now
        key = 'dripify-resource-v2:' + path
        cached = self.state.meta(key)
        if cached and ('data' in cached or cached.get('reason') == 'RELAY_HTTP_404'):
            return cached
        if path in self.visited:
            return cached or {'reason': 'ALREADY_ATTEMPTED_THIS_RUN'}
        self.visited.add(path)
        try:
            data = self._read(path)
            if not isinstance(data, dict):
                raise ReadFailure('UNEXPECTED_RESOURCE_SCHEMA')
            if re.fullmatch(r'leads/[0-9]+', path) and str(data.get('id')) != path.split('/')[-1]:
                raise ReadFailure('UNBOUND_LEAD_DETAIL')
            result = {'data': data, 'observed_at': now()}
        except ReadFailure as exc:
            result = {'reason': str(exc), 'observed_at': now()}
        self.state.set_meta(key, result)
        return result

    def collect(self, path, messages=False):
        from sourcing_state import now
        key = 'dripify-pages-v2:' + path
        result = self.state.meta(key, {'items': [], 'next_cursor': None,
            'seen_cursors': [], 'pagination_complete': False, 'pages': []})
        if path in self.visited or result['pagination_complete'] or result.get('stalled'):
            return {**result, 'coverage_complete': False}
        self.visited.add(path)
        for _ in range(self.max_pages):
            cursor = result['next_cursor']
            try:
                data = self._read(path, {'limit': '100', **({'cursor': cursor} if cursor is not None else {})} if not messages else ({'cursor': cursor} if cursor else {}))
                if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                    raise ReadFailure('UNEXPECTED_PAGE_SCHEMA')
                rows = data['items']
                if any(not isinstance(r, dict) for r in rows):
                    raise ReadFailure('UNEXPECTED_ITEM_SCHEMA')
                if re.fullmatch(r'leads/[0-9]+/activity', path) and any('leadInUserId' in r and str(r['leadInUserId']) != path.split('/')[1] for r in rows):
                    raise ReadFailure('UNBOUND_ACTIVITY_PAGE')
                if path == 'leads' and any(not str(r.get('id', '')).isdigit() for r in rows):
                    raise ReadFailure('UNEXPECTED_LEAD_ID')
                nxt = data.get('nextCursor')
                if nxt is not None and (not isinstance(nxt, str) or not nxt or len(nxt) > 128 or nxt == cursor or nxt in result['seen_cursors']):
                    raise ReadFailure('INVALID_OR_REPEATED_CURSOR')
                # Keep event metadata, not message text/attachments.
                if messages:
                    rows = [{k: r[k] for k in ('eventId', 'createdAt', 'type', 'direction', 'senderType') if k in r} for r in rows]
                def identity(r):
                    return str(r['id']) if path == 'leads' else hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
                existing = {identity(r) for r in result['items']}
                fresh = []
                overlap = False
                for row in rows:
                    rid = identity(row)
                    if rid in existing:
                        overlap = True
                    else:
                        fresh.append(row)
                        existing.add(rid)
                result['items'].extend(fresh)
                result['pages'].append({'observed_at': now(), 'count': len(rows), 'has_next_cursor': nxt is not None})
                if overlap:
                    # Preserve positives; changed page boundaries prevent completeness.
                    result['reason'] = 'OVERLAPPING_OR_CHANGED_RESULT_SET'
                    result['pagination_complete'] = False
                    result['stalled'] = True
                    self.state.set_meta(key, result)
                    break
                result['next_cursor'] = nxt
                if cursor is not None:
                    result['seen_cursors'].append(cursor)
                result['pagination_complete'] = nxt is None and (not messages or not rows)
                result['stalled'] = bool(nxt is None and messages and rows)
                result['reason'] = ('ACCOUNT_VISIBILITY_AND_RETENTION_UNVERIFIED' if result['pagination_complete'] else 'MESSAGE_HISTORY_TERMINATION_UNVERIFIED' if nxt is None else 'PAGE_BUDGET_REACHED')
                self.state.set_meta(key, result)
                if nxt is None:
                    break
            except ReadFailure as exc:
                result['reason'] = str(exc)
                if str(exc) in ('INVALID_OR_REPEATED_CURSOR', 'UNEXPECTED_PAGE_SCHEMA', 'UNEXPECTED_ITEM_SCHEMA', 'UNEXPECTED_LEAD_ID'):
                    result['stalled'] = True
                self.state.set_meta(key, result)
                break
        return {**result, 'coverage_complete': False}

    def lookup(self, profile):
        """Existing POST search route is a read-only lookup, sharing the run budget."""
        from sourcing_state import now
        from email_api import request, ClientError
        from reconcile_exports import linkedin
        profile = linkedin(profile)
        if not profile:
            return {'items': [], 'reason': 'MISSING_LINKEDIN_IDENTIFIER'}
        key = 'dripify-profile-lookup-v2:' + profile
        if profile in self.lookup_results:
            return self.lookup_results[profile]
        cached = self.state.meta(key)
        if cached is not None:
            return cached
        if self.remaining <= 0 or _paused_after_rate_limit:
            return {'items': [], 'reason': 'RUN_REQUEST_BUDGET_OR_RATE_LIMIT_REACHED'}
        import time
        self.remaining -= 1
        self.requests += 1
        time.sleep(1.1)
        try:
            data = request('dripify', 'search', linkedin_url='https://' + profile)['data']
            if not isinstance(data, list) or any(not isinstance(r, dict) or not str(r.get('id', '')).isdigit() for r in data):
                raise ReadFailure('UNEXPECTED_LOOKUP_SCHEMA')
            result = {'items': [r for r in data if linkedin(r.get('linkedinProfileUrl')) == profile],
                      'observed_at': now(), 'coverage_complete': False,
                      'reason': 'EXACT_PROFILE_LOOKUP_NOT_ACCOUNT_ABSENCE_PROOF'}
            self.state.set_meta(key, result)
            self.lookup_results[profile] = result
            return result
        except (ClientError, ReadFailure) as exc:
            # No error payloads or arbitrary exception text are persisted.
            if '429' in str(exc):
                globals()['_paused_after_rate_limit'] = True
            result = {'items': [], 'reason': 'PROFILE_LOOKUP_FAILED', 'observed_at': now()}
            self.lookup_results[profile] = result
            return result

    def lead_history(self, lead_id):
        if not str(lead_id).isdigit():
            raise ValueError('Numeric lead ID required')
        prefix = 'leads/' + str(lead_id)
        detail = self.resource(prefix)
        activity = self.collect(prefix + '/activity')
        conversation = self.resource(prefix + '/conversation')
        result = {'detail': detail, 'activity': activity, 'conversation': conversation,
                  'coverage_complete': False}
        identifier = conversation.get('data', {}).get('conversationId')
        if isinstance(identifier, str) and identifier:
            result['messages'] = self.collect('conversations/' + urllib.parse.quote(identifier, safe='') + '/messages', messages=True)
        return result
