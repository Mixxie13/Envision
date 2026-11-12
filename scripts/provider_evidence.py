"""Read-only collection and conservative summaries, never provider clearance."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from hubspot import Client, ReadError
from email_api import request, ClientError
from reconcile_exports import linkedin
from sourcing_state import now, digest
import dripify_evidence

CONTACT_FIELDS = ('firstname', 'lastname', 'jobtitle', 'email', 'hs_additional_emails',
                  'hs_linkedin_url', 'associatedcompanyid', 'hs_email_hard_bounce_reason',
                  'hs_email_hard_bounce_reason_enum', 'hs_email_optout',
                  'hs_sequences_is_enrolled', 'hs_sequences_actively_enrolled_count',
                  'hs_sequences_enrolled_count', 'hs_latest_sequence_finished_date',
                  'hs_email_first_reply_date', 'hs_email_last_reply_date', 'notes_last_contacted')


def contact_summary(record):
    p = record.get('properties', {})
    adverse = []
    if p.get('hs_email_hard_bounce_reason_enum') or p.get('hs_email_hard_bounce_reason'):
        adverse.append('HISTORICAL_HARD_BOUNCE')
    if any(str(value).lower() == 'true' for key, value in p.items() if key == 'hs_email_optout' or key.startswith('hs_email_optout_')):
        adverse.append('EMAIL_OPT_OUT')
    active_count = str(p.get('hs_sequences_actively_enrolled_count') or '')
    if str(p.get('hs_sequences_is_enrolled')).lower() == 'true' or (active_count.isdigit() and int(active_count) > 0):
        adverse.append('ACTIVE_SEQUENCE')
    return {'name': ' '.join(str(p.get(k) or '') for k in ('firstname', 'lastname')).strip(),
            'role': p.get('jobtitle') or '', 'email': p.get('email') or '',
            'linkedin_url': p.get('hs_linkedin_url') or '', 'hubspot_contact_id': str(record['id']),
            'hubspot_company_id': p.get('associatedcompanyid') or '',
            'blocked': bool(adverse), 'risks': adverse,
            'outreach_ready': False, 'current_employment_confirmed': False,
            'coverage': 'Existing record only; subscription/history/freshness not cleared',
            'raw_record': record}


class Providers:
    def __init__(self, state, live=False, apollo_enabled=False):
        self.state, self.live = state, live
        self._client = None
        self.halted = set()
        self.dripify_collector = dripify_evidence.Collector(state)
        self._dripify_index = None
        from apollo import ApolloPeople
        self.apollo_client = ApolloPeople(state, enabled=apollo_enabled)

    def read(self, key, operation):
        def collect():
            if 'hubspot' in self.halted:
                return {'complete': False, 'error': {'category': 'SERVICE_PAUSED_AFTER_RATE_LIMIT'}}
            try:
                return {'observed_at': now(), 'data': operation()}
            except ReadError as exc:
                if exc.category == 'RATE_LIMITED':
                    self.halted.add('hubspot')
                return {'observed_at': now(), 'complete': False, 'error': exc.safe()}
        return self.state.cached(['hubspot', key], collect)

    def client(self):
        if self._client is None:
            self._client = Client()
        return self._client

    def metadata(self, kind):
        result = self.read(['metadata', kind], lambda: self.client().properties(kind))
        if 'data' not in result:
            raise ValueError('HUBSPOT_METADATA_UNAVAILABLE')
        self.client()._properties[kind] = result['data']
        return result['data']

    def hubspot(self, company):
        cid = company['id']
        existing = self.state.meta('seed_company:' + cid)
        if existing:
            return {'state': 'pending', 'reason': 'CACHED_COMPANY_QUERY_COVERAGE_ONLY', 'evidence': existing}
        if not self.live:
            return {'state': 'pending', 'reason': 'LIVE_READ_NOT_ENABLED'}
        known = self.metadata('companies')
        fields = [n for n in ('name', 'domain', 'hs_additional_domains') if n in known]
        def group(prop, val):
            return {'filters': [{'propertyName': prop, 'operator': 'EQ', 'value': val}]}
        groups = [group('name', company['data']['company'])]
        website = company['data']['mapped'].get('website') or ''
        if website and urlparse(website).hostname and 'domain' in known:
            groups.append(group('domain', urlparse(website).hostname.removeprefix('www.')))
        result = self.read(['company-search', groups], lambda: self.client().search('companies', groups, fields))
        evidence = [result]
        data = result.get('data', {})
        companies = {str(r['id']): r for r in data.get('results', [])}
        supplied = company['data']['mapped'].get('hubspot_company_id') or ''
        if supplied:
            match = re.fullmatch(r'(?:https://app\.hubspot\.com/contacts/[0-9]+/record/0-2/)?([0-9]+)', str(supplied))
            if match and match[1] not in companies:
                extra = self.read(['company-id', match[1]], lambda: self.client().get('companies', match[1], fields))
                evidence.append(extra)
                if 'data' in extra:
                    companies[match[1]] = extra['data']
        if len(companies) > 1:
            self.state.observe(cid, '', 'identity', 'pending', {'reason': 'MULTIPLE_COMPANY_RECORDS', 'record_ids': sorted(companies)})
        ids = set()
        for company_id in companies:
            assoc = self.read(['company-contacts', company_id], lambda company_id=company_id: self.client().associations(company_id))
            evidence.append(assoc)
            ids.update(str(r['toObjectId']) for r in assoc.get('data', {}).get('results', []))
        contact_meta = self.metadata('contacts')
        properties = [n for n in CONTACT_FIELDS if n in contact_meta]
        properties += [n for n in contact_meta if n.startswith('hs_email_optout_')]
        identifiers = sorted(ids)
        for start in range(0, len(identifiers), 100):
            batch = identifiers[start:start + 100]
            read = self.read(['contacts', batch, properties], lambda batch=batch: self.client().batch_read('contacts', batch, properties))
            evidence.append(read)
            for record in read.get('data', {}).get('results', []):
                self.state.contact('hs:' + str(record['id']), cid, contact_summary(record))
        return {'state': 'pending', 'reason': 'SUBSCRIPTION_SEQUENCE_HISTORY_AND_IDENTITY_COVERAGE_PENDING', 'evidence': evidence}

    def apollo(self, company):
        result = self.apollo_client.discover(company)
        if not result.get('account_schema_verified'):
            return result  # Capability probe results stay in evidence until schema review.
        for candidate in result.get('candidates', []):
            # Missing identity, ambiguous organizations or unassigned parent staff
            # stay in discovery evidence, never become brand contact records.
            if not candidate.get('identity_complete') or candidate.get('brand_assignment_pending') or result['reason'] == 'MULTIPLE_CURRENT_ORGANIZATION_IDS_REVIEW_REQUIRED':
                continue
            profile = linkedin(candidate.get('linkedin_url'))
            known = [c['data'] for c in self.state.contacts() if profile and linkedin(c['data'].get('linkedin_url')) == profile]
            risks = set(candidate['risks'])
            for old in known:
                risks.update(old.get('risks', []))
                if old.get('name', '').casefold() != candidate['name'].casefold():
                    risks.add('IDENTITY_CONFLICT')
            payload = {**candidate, 'blocked': any(c.get('blocked') for c in known),
                       'risks': sorted(risks), 'source': 'Apollo People API Search: ' + candidate['apollo_person_id'],
                       'research_status': 'apollo_candidate_current_employment_pending'}
            self.state.contact('apollo:' + digest([company['id'], candidate['apollo_person_id']])[:24], company['id'], payload)
        return result

    def identity(self, company):
        results = []
        for subject in self.state.contacts(company['id']):
            person = subject['data']
            if person.get('research_excluded'):
                results.append({'contact_id': subject['id'], 'reason': 'EXCLUDED_ROLE_NO_FURTHER_PROVIDER_LOOKUP'})
                continue
            if person.get('hubspot_contact_id') or person.get('hubspot_lookup_cached'):
                cid = person.get('hubspot_contact_id')
                assoc = self.state.meta('contact_assoc:' + str(cid)) if cid else None
                if cid and assoc is None and self.live:
                    assoc = self.read(['contact-company-edges', cid], lambda cid=cid: self.client().contact_associations(cid))
                results.append({'contact_id': subject['id'], 'reason': 'RETAINED_CRM_ID_OR_PRIOR_QUERY', 'all_company_association_read': assoc})
                continue
            profile = linkedin(person.get('linkedin_url'))
            address = person.get('email') or ''
            if not self.live or not (profile or address):
                results.append({'contact_id': subject['id'], 'reason': 'EXACT_PERSON_IDENTIFIER_UNAVAILABLE'})
                continue
            known = self.metadata('contacts')
            if profile and 'hs_linkedin_url' not in known:
                results.append({'contact_id': subject['id'], 'reason': 'PROFILE_PROPERTY_UNAVAILABLE'})
                continue
            path = profile.split('linkedin.com', 1)[1] if profile else ''
            variants = ['https://www.linkedin.com' + path, 'https://linkedin.com' + path] if profile else []
            groups = [{'filters': [{'propertyName': 'hs_linkedin_url', 'operator': 'EQ', 'value': v}]} for v in variants]
            if address and 'email' in known:
                groups.append({'filters': [{'propertyName': 'email', 'operator': 'EQ', 'value': address}]})
            if address and 'hs_additional_emails' in known:
                groups.append({'filters': [{'propertyName': 'hs_additional_emails', 'operator': 'CONTAINS_TOKEN', 'value': address}]})
            fields = [n for n in CONTACT_FIELDS if n in known]
            lookup = self.read(['person-identifiers', profile, address, fields], lambda: self.client().search('contacts', groups, fields))
            results.append({'contact_id': subject['id'], 'lookup': lookup, 'meaning': 'Returned exact profile matches only, not employment or absence proof'})
            for record in lookup.get('data', {}).get('results', []):
                props = record.get('properties', {})
                emails = [props.get('email')] + str(props.get('hs_additional_emails') or '').split(';')
                if not ((profile and linkedin(props.get('hs_linkedin_url')) == profile) or (address and address in emails)):
                    self.state.observe(company['id'], subject['id'], 'identity', 'pending', {'reason': 'UNBOUND_SEARCH_RESULT', 'record_id': str(record['id'])})
                    continue
                self.state.contact('hs:' + str(record['id']), company['id'], contact_summary(record))
        return {'state': 'pending', 'reason': 'IDENTITY_BINDING_AND_DUPLICATE_COVERAGE_REQUIRE_REVIEW', 'contacts': results}

    def dripify(self, company):
        output = []
        if self._dripify_index is None:
            self._dripify_index = (self.dripify_collector.collect('leads') if self.live else
                {'items': [], 'coverage_complete': False, 'reason': 'LIVE_COLLECTION_NOT_ENABLED'})
        index = self._dripify_index
        retained_index = self.state.meta('dripify-retained-index-v2', {})
        visible = {str(r['id']): r for r in retained_index.get('items', [])}
        visible.update({str(r['id']): r for r in index.get('items', [])})
        for contact in self.state.contacts(company['id']):
            person = contact['data']
            profile = linkedin(person.get('linkedin_url'))
            row = {'contact_id': contact['id'], 'name': person.get('name', ''),
                   'linkedin_url': person.get('linkedin_url', ''),
                   'source_url': str(self.state.directory / 'state.sqlite'),
                   'reviewed_at': now(), 'outreach_ready': False,
                   'retained_positive_evidence': person.get('known_dripify_matches') or person.get('prior_dripify_observation'),
                   'retained_exact_matches': [r for r in person.get('known_dripify_matches', []) if isinstance(r, dict) and profile and linkedin(r.get('linkedinProfileUrl')) == profile]}
            if person.get('research_excluded'):
                row['reason'] = 'EXCLUDED_ROLE_NO_FURTHER_PROVIDER_LOOKUP'
            elif person.get('channel') == 'email':
                row['reason'] = 'EMAIL_SELECTED_CROSS_CHANNEL_POLICY_PENDING'
            elif not profile:
                row['reason'] = 'MISSING_LINKEDIN_IDENTIFIER'
            else:
                matches = [r for r in visible.values() if linkedin(r.get('linkedinProfileUrl')) == profile]
                # Known IDs are lookup hints only. Rebind to the returned exact profile.
                candidates = list(person.get('known_dripify_matches') or [])
                candidates += self.state.meta('dripify-profile-hints-v2:' + profile, [])
                for hint in candidates:
                    lid = hint.get('id') or hint.get('lead_id') or hint.get('dripify_lead_id') if isinstance(hint, dict) else hint
                    if self.live and str(lid).isdigit() and not any(str(r['id']) == str(lid) for r in matches):
                        detail = self.dripify_collector.resource('leads/' + str(lid)).get('data', {})
                        if str(detail.get('id')) == str(lid) and linkedin(detail.get('linkedinProfileUrl')) == profile:
                            matches.append(detail)
                if not matches and self.live:
                    row['identifier_lookup'] = self.dripify_collector.lookup(person.get('linkedin_url'))
                    matches = row['identifier_lookup']['items']
                row['matches'] = matches
                row['reason'] = ('AMBIGUOUS_EXACT_PROFILE_MATCHES' if len(matches) > 1 else
                    'CONFIRMED_IDENTIFIER_MATCH' if matches else 'NO_MATCH_WITHIN_PARTIAL_COVERAGE')
                if not matches and row.get('identifier_lookup', {}).get('reason') in ('PROFILE_LOOKUP_FAILED', 'RUN_REQUEST_BUDGET_OR_RATE_LIMIT_REACHED'):
                    row['reason'] = row['identifier_lookup']['reason']
                if matches and self.live:
                    row['histories'] = [self.dripify_collector.lead_history(r['id']) for r in matches]
                    # Exact provider event names confirmed in the live validation.
                    events = [e for h in row['histories'] for e in h['activity']['items']]
                    row['previous_outreach_confirmed'] = any(e.get('sequenceEventType') in ('CONNECT_SENT', 'MESSAGE_SENT') for e in events)
                    row['invitation_conflict'] = any(e.get('sequenceEventType') == 'CONNECT_ERROR' and e.get('error') == 'INVITE_ALREADY_SENT' for e in events)
                    row['match_status'] = 'confirmed_exact_linkedin' if len(matches) == 1 else 'ambiguous_exact_linkedin'
                    if len(matches) == 1 and row['previous_outreach_confirmed']:
                        row['reason'] = 'CONFIRMED_PREVIOUS_OUTREACH'
                    elif len(matches) == 1 and row['invitation_conflict']:
                        row['reason'] = 'EXACT_MATCH_INVITATION_CONFLICT'
                    row['observed_at'] = row['histories'][0]['detail'].get('observed_at', '')
                elif row.get('identifier_lookup'):
                    row['observed_at'] = row['identifier_lookup'].get('observed_at', '')
            output.append(row)
        return {'state': 'pending', 'reason': 'ACCOUNT_OWNER_AND_HISTORY_COVERAGE_UNVERIFIED',
                'contacts': output, 'all_visible_index': {
                    'unique_leads_in_snapshot': len(index.get('items', [])),
                    'pagination_complete': index.get('pagination_complete', False),
                    'continuation_checkpoint_saved': bool(index.get('next_cursor')),
                    'coverage_complete': False, 'reason': index.get('reason')},
                'requests_this_run': self.dripify_collector.requests,
                'coverage_complete': False}


def seed_existing(state, checklist_path, hubspot_directory):
    """Explicit migration of local dated evidence; never mark it fresh or clear."""
    checklist = json.loads(Path(checklist_path).read_text())
    directory = Path(hubspot_directory)
    company_rows = state.companies()
    if len({c['data']['company'] for c in company_rows}) != len(company_rows):
        raise ValueError('Legacy seed needs unambiguous company-row linkage')
    companies = {c['data']['company']: c for c in company_rows}
    records = {}
    for filename in ('associated-contacts.json', 'bounce-and-quattrone-history.json', 'targeted-additional-sequence-fields.json'):
        p = directory / filename
        if p.exists():
            for record in json.loads(p.read_text()).get('results', []):
                rid = str(record['id'])
                previous = records.get(rid, {})
                records[rid] = {**previous, **record, 'properties': {**previous.get('properties', {}), **record.get('properties', {})}}
    prior_file = directory / 'dripify-identity-review.json'
    prior = {r['hubspot_contact_id']: r for r in json.loads(prior_file.read_text()).get('records', [])} if prior_file.exists() else {}
    for subject in checklist['contacts']:
        company = companies.get(subject['company'])
        if not company:
            raise ValueError('Seed company is not in this master')
        record = records.get(subject.get('hubspot_contact_id'))
        payload = contact_summary(record) if record else {'risks': [], 'blocked': False}
        payload.update({k: subject[k] for k in ('name', 'role', 'linkedin_url', 'email', 'hubspot_contact_id', 'hubspot_company_id', 'known_dripify_matches')})
        payload['blocked'] = payload.get('blocked') or 'Blocked' in subject.get('baseline_flags', [])
        if 'Ambiguous' in subject.get('baseline_flags', []):
            payload['risks'].append('AMBIGUOUS_BASELINE_IDENTITY')
        payload['prior_dripify_observation'] = prior.get(subject.get('hubspot_contact_id'))
        payload['hubspot_lookup_cached'] = True
        payload['source'] = str(checklist_path)
        payload['source_freshness'] = 'Retained dated evidence; expiry policy undefined'
        state.contact(subject['contact_key'], company['id'], payload)
    for company in companies.values():
        pos = company['position']
        p = directory / ('company-' + str(pos) + '.json')
        if p.exists():
            data = json.loads(p.read_text())
            if data.get('brand') == company['data']['company']:
                state.set_meta('seed_company:' + company['id'], {'source_ref': str(p), 'record': data})
