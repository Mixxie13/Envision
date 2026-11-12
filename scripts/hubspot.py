"""Narrow HubSpot read-only client. Provider credentials never leave api.hubapi.com."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


class ReadError(Exception):
    def __init__(self, category, status=None, retry_after=None, required_scopes=None):
        super().__init__(category)
        self.category = category
        self.status = status
        self.retry_after = retry_after
        self.required_scopes = required_scopes or []

    def safe(self):
        return {'category': self.category, 'http_status': self.status,
                'retry_after': self.retry_after, 'required_scopes': self.required_scopes}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    """Only property discovery, record reads, searches and association reads."""
    def __init__(self):
        self._key = os.environ.get('HUBSPOT_ACCESS_TOKEN', '')
        self._opener = urllib.request.build_opener(NoRedirect())
        self._properties = {}

    @staticmethod
    def _object(kind):
        if kind not in ('contacts', 'companies'):
            raise ValueError('Unsupported object')
        return kind

    @staticmethod
    def _id(record_id):
        value = str(record_id)
        if not re.fullmatch(r'[0-9]+', value):
            raise ValueError('Invalid record ID')
        return value

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self._key, '[REDACTED]') if self._key else value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {self._redact(k): self._redact(v) for k, v in value.items()}
        return value

    def _request(self, method, path, body=None, query=None):
        # Defense in depth: even internal calls cannot target a write operation.
        allowed_get = re.fullmatch(
            r'/crm/v3/properties/(contacts|companies)|'
            r'/crm/v3/objects/(contacts|companies)/[0-9]+|'
            r'/crm/v4/objects/companies/[0-9]+/associations/contacts|'
            r'/crm/v4/objects/contacts/[0-9]+/associations/companies', path)
        allowed_post = re.fullmatch(r'/crm/v3/objects/(contacts|companies)/(search|batch/read)', path)
        if not ((method == 'GET' and allowed_get) or (method == 'POST' and allowed_post)):
            raise ValueError('Operation is not an allowlisted read')
        if not self._key.strip():
            raise ReadError('CREDENTIAL_MISSING')
        url = 'https://api.hubapi.com' + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        req = urllib.request.Request(url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + self._key, 'Accept': 'application/json',
                     'Content-Type': 'application/json'}, method=method)
        try:
            with self._opener.open(req, timeout=30) as response:
                partial = response.status == 207 and path.endswith('/batch/read')
                if response.status != 200 and not partial:
                    raise ReadError('PARTIAL_OR_UNEXPECTED_HTTP_STATUS', response.status)
                data = self._redact(json.load(response))
                if partial and isinstance(data, dict):
                    data['_partial_http_status'] = 207
        except urllib.error.HTTPError as exc:
            # Deliberately suppress arbitrary provider bodies/messages and URLs.
            retry = exc.headers.get('Retry-After') if exc.headers else None
            retry = int(retry) if isinstance(retry, str) and retry.isdigit() else None
            scopes = []
            try:
                error = self._redact(json.load(exc))
                if isinstance(error, dict):
                    for entry in error.get('errors', []):
                        context = entry.get('context', {}) if isinstance(entry, dict) else {}
                        if isinstance(context, dict):
                            for field in ('requiredScopes', 'requiredGranularScopes'):
                                values = context.get(field, [])
                                if isinstance(values, list):
                                    scopes.extend(v for v in values if isinstance(v, str)
                                                  and re.fullmatch(r'[a-z][a-z0-9_.]*', v))
            except (ValueError, TypeError, AttributeError, OSError):
                pass
            finally:
                exc.close()
            raise ReadError('RATE_LIMITED' if exc.code == 429 else 'HTTP_ERROR',
                            exc.code, retry, sorted(set(scopes))) from None
        except (ValueError, UnicodeError):
            raise ReadError('INVALID_JSON') from None
        except (OSError, urllib.error.URLError):
            raise ReadError('CONNECTION_FAILED') from None
        if not isinstance(data, dict):
            raise ReadError('UNEXPECTED_SCHEMA')
        return data

    def properties(self, kind):
        self._object(kind)
        data = self._request('GET', '/crm/v3/properties/' + kind)
        rows = data.get('results')
        if not isinstance(rows, list) or data.get('paging'):
            raise ReadError('INCOMPLETE_PROPERTY_METADATA')
        if (any(not isinstance(row, dict) or not isinstance(row.get('name'), str) or not row['name'].strip() for row in rows)
                or len({row['name'] for row in rows}) != len(rows)):
            raise ReadError('UNEXPECTED_PROPERTY_SCHEMA')
        self._properties[kind] = {row['name']: row for row in rows}
        return self._properties[kind]

    def _fields(self, kind, fields):
        known = self._properties.get(kind)
        if known is None:
            known = self.properties(kind)
        if any(field not in known for field in fields):
            raise ValueError('Property not present in discovered metadata')
        return list(fields)

    def get(self, kind, record_id, fields, history_fields=()):
        self._object(kind)
        query = {'properties': ','.join(self._fields(kind, fields))}
        if history_fields:
            query['propertiesWithHistory'] = ','.join(self._fields(kind, history_fields))
        data = self._request('GET', '/crm/v3/objects/' + kind + '/' + self._id(record_id), query=query)
        if str(data.get('id')) != str(record_id) or not isinstance(data.get('properties'), dict):
            raise ReadError('UNEXPECTED_RECORD_SCHEMA')
        return data

    def batch_read(self, kind, ids, fields, history_fields=()):
        self._object(kind)
        ids = list(dict.fromkeys(self._id(i) for i in ids))
        if len(ids) > 100:
            raise ValueError('Batch limit is 100')
        if not ids:
            return {'results': [], 'complete': True, 'error': None}
        body = {'properties': self._fields(kind, fields), 'inputs': [{'id': i} for i in ids]}
        if history_fields:
            body['propertiesWithHistory'] = self._fields(kind, history_fields)
        data = self._request('POST', '/crm/v3/objects/' + kind + '/batch/read', body=body)
        rows = data.get('results')
        if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('properties'), dict) for r in rows):
            raise ReadError('UNEXPECTED_BATCH_SCHEMA')
        returned = [str(r.get('id')) for r in rows]
        complete = (data.get('status') == 'COMPLETE' and not data.get('errors') and not data.get('_partial_http_status')
                    and set(returned) == set(ids) and len(returned) == len(ids))
        return {'results': rows, 'complete': complete,
                'error': None if complete else {'category': 'BATCH_INCOMPLETE'}}

    def _pages(self, fetch, search=False, max_pages=100):
        results, seen, after, total, pages = [], set(), None, None, 0
        while pages < max_pages:
            try:
                data = fetch(after)
                pages += 1
                rows = data.get('results')
                if not isinstance(rows, list) or any(not isinstance(r, dict) or not str(r.get('id', r.get('toObjectId', ''))).isdigit() for r in rows):
                    raise ReadError('UNEXPECTED_PAGE_SCHEMA')
                if search:
                    if any(not isinstance(r.get('properties'), dict) for r in rows):
                        raise ReadError('UNEXPECTED_RECORD_SCHEMA')
                    observed_total = data.get('total')
                    if type(observed_total) is not int or observed_total < 0:
                        raise ReadError('TOTAL_UNAVAILABLE')
                    if total is not None and total != observed_total:
                        raise ReadError('RESULT_SET_CHANGED')
                    total = observed_total
                results.extend(rows)
                paging = data.get('paging')
                if paging is not None and not isinstance(paging, dict):
                    raise ReadError('UNEXPECTED_PAGING_SCHEMA')
                next_page = paging.get('next') if paging else None
                if next_page is None:
                    identities = [str(r.get('id', r.get('toObjectId'))) for r in results]
                    if len(identities) != len(set(identities)):
                        raise ReadError('DUPLICATE_PAGE_RECORDS')
                    if search and (len(results) != total or total >= 10000):
                        raise ReadError('SEARCH_COVERAGE_INCOMPLETE')
                    return {'results': results, 'complete': True, 'pages': pages,
                            'total': total, 'error': None,
                            'coverage': 'Returned records for this exact query and token visibility only; not global absence'}
                if not isinstance(next_page, dict):
                    raise ReadError('UNEXPECTED_PAGING_SCHEMA')
                cursor = next_page.get('after')
                if not isinstance(cursor, (str, int)) or isinstance(cursor, bool) or not str(cursor) or str(cursor) in seen or not rows:
                    raise ReadError('INVALID_OR_REPEATED_CURSOR')
                after = str(cursor)
                seen.add(after)
            except ReadError as exc:
                return {'results': results, 'complete': False, 'pages': pages,
                        'total': total, 'error': exc.safe()}
        return {'results': results, 'complete': False, 'pages': pages,
                'total': total, 'error': {'category': 'PAGE_LIMIT_REACHED'}}

    def search(self, kind, groups, fields):
        self._object(kind)
        fields = self._fields(kind, fields)
        if not isinstance(groups, list) or not 1 <= len(groups) <= 5:
            raise ValueError('Provide 1–5 bounded filter groups')
        for group in groups:
            filters = group.get('filters') if isinstance(group, dict) else None
            if not isinstance(group, dict) or set(group) != {'filters'} or not isinstance(filters, list) or not filters:
                raise ValueError('Invalid filters')
            for item in filters:
                if (not isinstance(item, dict) or set(item) != {'propertyName', 'operator', 'value'} or item['operator'] not in ('EQ', 'CONTAINS_TOKEN')
                        or not isinstance(item['value'], str) or not item['value'].strip()):
                    raise ValueError('Only nonempty equality/token-match read filters are supported')
                self._fields(kind, [item['propertyName']])
        def fetch(after):
            body = {'filterGroups': groups, 'properties': fields, 'limit': 100}
            if after is not None:
                body['after'] = after
            return self._request('POST', '/crm/v3/objects/' + kind + '/search', body=body)
        return self._pages(fetch, search=True)

    def associations(self, company_id):
        path = '/crm/v4/objects/companies/' + self._id(company_id) + '/associations/contacts'
        return self._pages(lambda after: self._request('GET', path,
            query={'limit': 100, **({'after': after} if after is not None else {})}))

    def contact_associations(self, contact_id):
        """Read contact-to-company edges; never infer from a name or one primary ID."""
        path = '/crm/v4/objects/contacts/' + self._id(contact_id) + '/associations/companies'
        return self._pages(lambda after: self._request('GET', path,
            query={'limit': 100, **({'after': after} if after is not None else {})}))
