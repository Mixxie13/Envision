"""Offline per-person provenance intake and shared review projection.

Discovery groups are not identity confirmations. No provider calls or CRM writes.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

from sourcing_state import digest
from research import public_url, relevant_role
from reconcile_exports import linkedin
from employment import assess as assess_employment, normalized
from evidence import assemble
from eligibility import evaluate, timestamp

SCHEMA = 'envision.candidate-handoff.v1'


def resolve_company(state, row, batch=None):
    if not isinstance(row, dict): raise ValueError('Candidate object required')
    for field in ('employment_claims','email_evidence','envelopes'):
        if field in row and (not isinstance(row[field], list) or any(not isinstance(v, dict) for v in row[field])):
            raise ValueError('Structured evidence lists required')
    if 'approvals' in row and not isinstance(row['approvals'], dict): raise ValueError('Approval object required')
    choices = [c for c in state.companies(batch) if c['data']['company'] == row.get('company')]
    if row.get('company_row_id'):
        choices = [c for c in choices if c['id'] == row['company_row_id']]
    if len(choices) != 1:
        raise ValueError('Exact original company row required; duplicate names need company_row_id')
    if not all(isinstance(row.get(k), str) and row[k].strip() for k in ('name', 'job_title', 'employer_attribution')):
        raise ValueError('Named candidate, job title and employer attribution required')
    return choices[0]


def candidate_id(company_id, row):
    profile = linkedin(row.get('linkedin_url'))
    if profile:
        # Link exact profile/name observations, without claiming verified identity.
        return 'candidate:' + digest([company_id, normalized(row['name']), profile])[:24]
    # Without a profile, only repeated observations from the same source group.
    return 'candidate:' + digest([company_id, normalized(row['name']), normalized(row['employer_attribution']),
                                linkedin(row.get('linkedin_url')), row.get('original_source_url'), row.get('source_reference')])[:24]


def _source_key(row):
    return (normalized(row.get('name')), normalized(row.get('employer_attribution')),
            row.get('original_source_url'), row.get('source_reference'))


def reconcile_id(state, company, row):
    """Stable source observations or explicit IDs, never name-only identity merges."""
    contacts = [c for c in state.contacts(company['id']) if c['data'].get('handoff')]
    explicit = row.get('candidate_record_id')
    if explicit:
        target = next((c for c in contacts if c['id'] == explicit), None)
        if not target or normalized(target['data']['name']) != normalized(row['name']):
            raise ValueError('Explicit candidate identity requires manual review')
        return explicit
    matches = [c['id'] for c in contacts if
               ((row.get('original_source_url') or row.get('source_reference')) and
                any(_source_key(f) == _source_key(row) for f in c['data']['handoff']['facts']))
               or (linkedin(row.get('linkedin_url')) and
                   linkedin(c['data'].get('linkedin_url')) == linkedin(row.get('linkedin_url')) and
                   normalized(c['data']['name']) == normalized(row['name']))]
    if len(set(matches)) > 1:
        raise ValueError('Ambiguous candidate identity: supply reviewed candidate_record_id')
    if not matches and not row.get('original_source_url') and not row.get('source_reference') and any(
            normalized(c['data']['name']) == normalized(row['name']) for c in contacts):
        raise ValueError('Unbound candidate update requires reviewed candidate_record_id')
    return matches[0] if matches else candidate_id(company['id'], row)


def validate_rows(state, rows, batch=None):
    if not isinstance(rows, list): raise ValueError('Candidate list required')
    planned = [(resolve_company(state, row, batch), row) for row in rows]
    for company, row in planned: reconcile_id(state, company, row)
    return planned


def _retain_bounces(state, person, cid):
    address = str(person.get('email') or '').strip().casefold()
    for other in state.contacts():
        data = other['data']
        bounced = ('HISTORICAL_HARD_BOUNCE' in data.get('risks', []) or
                   data.get('evidence_record', {}).get('checks', {}).get('hard_bounce', {}).get('result') == 'bounced')
        if address and str(data.get('email') or '').strip().casefold() == address and bounced:
            person['blocked'] = True
            person['risks'] = sorted(set(person.get('risks', []) + ['HISTORICAL_HARD_BOUNCE']))
            refs = person.setdefault('retained_bounce_sources', [])
            person['retained_bounce_sources'] = sorted(set(refs + [other['id']]))


def _date_valid(value):
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _build(person, contact_id, company, clock):
    packet = person['handoff']
    facts = packet['facts']
    risks = set(person.get('risks', []))
    subject = {'contact_id': contact_id, 'company': company, 'full_name': person['name'],
               'linkedin_url': person.get('linkedin_url') or '', 'email': person.get('email') or ''}
    for fact in facts:
        if normalized(fact['employer_attribution']) != normalized(company): risks.add('PARENT_OR_COMPANY_ATTRIBUTION_UNRESOLVED')
        if not public_url(fact.get('original_source_url')): risks.add('ORIGINAL_SOURCE_URL_MISSING_OR_UNSAFE')
        if not _date_valid(fact.get('evidence_date')): risks.add('EVIDENCE_DATE_MISSING_OR_INVALID')
        if not _date_valid(fact.get('research_date')): risks.add('RESEARCH_DATE_MISSING_OR_INVALID')
        for field in ('evidence_date','research_date'):
            if _date_valid(fact.get(field)) and datetime.fromisoformat(fact[field].replace('Z','+00:00')).date() > clock.date():
                risks.add('FUTURE_DISCOVERY_DATE')
        if fact.get('stale') is True: risks.add('STALE_DISCOVERY_EVIDENCE')
    if len({normalized(f['job_title']) for f in facts}) > 1: risks.add('CONFLICTING_ROLE_EVIDENCE')
    if not linkedin(subject['linkedin_url']): risks.add('PERSONAL_LINKEDIN_IDENTIFIER_MISSING')
    identity_conflict = any(any(token in risk for token in ('AMBIGUOUS', 'ALIAS', 'IDENTITY_CONFLICT', 'IDENTITY_COLLISION')) for risk in risks)
    employment = assess_employment({**person, 'company': company, 'identity_conflict': identity_conflict}, packet['employment_claims'], clock.isoformat())
    if employment['status'] == 'conflicting': risks.add('CONFLICTING_EMPLOYMENT_EVIDENCE')
    base = {'subject': subject, 'channel': packet.get('channel'), 'approvals': packet.get('approvals', {}), 'unresolved_risks': sorted(risks)}
    assembled = assemble(base, packet['envelopes'])
    record = assembled['record']
    # An internal role check cannot bypass the original-profile employment path.
    if not employment['current_employment_confirmed'] and 'role_company' in record['checks']:
        record['checks']['role_company'].update(state='unresolved', complete=False)
    if any('CONFLICT' in r or 'ATTRIBUTION' in r or 'IDENTITY_' in r for r in risks):
        for name in ('identity', 'company', 'role_company'):
            if name in record['checks']: record['checks'][name].update(state='unresolved', complete=False)
    # Retain known suppression even if fresh checks, identity or dates are incomplete.
    for risk, (check, result) in {'HISTORICAL_HARD_BOUNCE': ('hard_bounce', 'bounced'), 'EMAIL_OPT_OUT': ('opt_out', 'opted_out'), 'ACTIVE_SEQUENCE': ('hubspot_sequence', 'active')}.items():
        if risk in risks:
            record['checks'][check] = {'result': result, 'state': 'unresolved', 'complete': False,
                                      'subject': subject, 'source': 'Retained known adverse evidence'}
    return record, employment, assembled['intake']


def review(person, contact_id, company, clock=None):
    """One fresh projection consumed by runner, workbook and progress reporting."""
    clock = clock or datetime.now(timezone.utc)
    record, employment, intake = _build(person, contact_id, company, clock)
    effective = {**person, 'evidence_record': record}
    from master_progress import supported_checks
    critical = {'PARENT_OR_COMPANY_ATTRIBUTION_UNRESOLVED','ORIGINAL_SOURCE_URL_MISSING_OR_UNSAFE','EVIDENCE_DATE_MISSING_OR_INVALID','RESEARCH_DATE_MISSING_OR_INVALID','STALE_DISCOVERY_EVIDENCE','CONFLICTING_ROLE_EVIDENCE','PERSONAL_LINKEDIN_IDENTIFIER_MISSING','CONFLICTING_EMPLOYMENT_EVIDENCE','FUTURE_DISCOVERY_DATE'}
    identity_risk = any(any(token in r for token in ('AMBIGUOUS','ALIAS','IDENTITY_CONFLICT','IDENTITY_COLLISION')) for r in record['unresolved_risks'])
    current = not identity_risk and not critical.intersection(record['unresolved_risks']) and employment['current_employment_confirmed'] and supported_checks(effective, contact_id, company, clock,
                            {'identity': 'confirmed', 'role_company': 'confirmed', 'company': 'confirmed'})
    linked = []
    for email in person['handoff']['email_evidence']:
        if (email.get('explicitly_published') is True and normalized(email.get('person_name')) == normalized(person['name'])
                and normalized(email.get('company')) == normalized(company) and public_url(email.get('source_url'))
                and _date_valid(email.get('evidence_date')) and datetime.fromisoformat(email['evidence_date'].replace('Z','+00:00')).date() <= clock.date() and email.get('shared') is False
                and isinstance(email.get('email'), str) and '@' in email['email']):
            linked.append(email)
    # Deliverability is a dated provider fact, independent of suppression/permission.
    verified = bool(linked and
                    any(e['email'] == subject_email(record) for e in linked) and supported_checks(effective, contact_id, company, clock,
                            {'professional_email': 'individual', 'verification': 'valid'}))
    result = evaluate(record, now=clock)
    if person.get('blocked') and result['decision'] != 'blocked':
        result['decision'] = result['technical_eligibility'] = 'blocked'
        result['reason_codes'].append('RETAINED_BASELINE_BLOCK')
    role_supported = relevant_role(person.get('role', '')) and not any(r in record['unresolved_risks'] for r in ('PARENT_OR_COMPANY_ATTRIBUTION_UNRESOLVED', 'CONFLICTING_ROLE_EVIDENCE'))
    def provider_status(check):
        result = record['checks'].get(check, {}).get('result')
        return result if result and supported_checks(effective, contact_id, company, clock, {check: result}) else 'pending'
    return {'discovered_candidate': relevant_role(person.get('role', '')),
            'provisional_role_evidence': bool(role_supported and not current),
            'confirmed_current_employment': bool(current), 'person_linked_email_candidate': bool(linked),
            'deliverability_verified_email': verified, 'outreach_eligibility': result['decision'],
            'employment_assessment': employment, 'eligibility_assessment': result,
            'record': record, 'intake': intake, 'linked_email_evidence': linked,
            'hubspot_status': provider_status('hubspot_duplicate'),
            'dripify_status': provider_status('dripify_duplicate'),
            'provider_reviews': person['handoff'].get('provider_reviews', {}), 'outreach_ready': False}


def subject_email(record):
    return record['subject'].get('email')


def _unique(values):
    return list({digest(value): value for value in values}.values())


def ingest(state, rows, batch=None, clock=None):
    clock = clock or datetime.now(timezone.utc)
    planned = validate_rows(state, rows, batch)
    identifiers = []
    for company, row in planned:
        cid = reconcile_id(state, company, row)
        old = next((c['data'] for c in state.contacts(company['id']) if c['id'] == cid), {})
        packet = deepcopy(old.get('handoff', {'schema': SCHEMA, 'facts': [], 'employment_claims': [], 'envelopes': [], 'email_evidence': []}))
        fact = {k: row.get(k) for k in ('company', 'name', 'job_title', 'employer_attribution', 'linkedin_url', 'original_source_url', 'source_reference', 'evidence_date', 'research_date', 'stale')}
        packet['facts'] = _unique(packet['facts'] + [fact])
        for field in ('employment_claims', 'envelopes', 'email_evidence'):
            packet[field] = _unique(packet[field] + deepcopy(row.get(field, [])))
        packet['channel'] = row.get('channel', packet.get('channel'))
        packet['approvals'] = {**packet.get('approvals', {}), **row.get('approvals', {})}
        profile = linkedin(row.get('linkedin_url') or old.get('linkedin_url'))
        known = [c['data'] for c in state.contacts() if c['id'] != cid and profile and linkedin(c['data'].get('linkedin_url')) == profile]
        risks = set(old.get('risks', []))
        for other in known:
            risks.update(other.get('risks', []))
            if normalized(other.get('name')) != normalized(row['name']): risks.add('IDENTITY_CONFLICT')
        person = {**old, 'name': row['name'].strip(), 'role': row['job_title'].strip(),
                  'linkedin_url': 'https://' + profile if profile else '',
                  'employer_attribution': row['employer_attribution'], 'email': row.get('email', old.get('email', '')),
                  'source': row.get('original_source_url') or row.get('source_reference') or 'Manual candidate; original source pending',
                  'blocked': bool(old.get('blocked') or any(k.get('blocked') for k in known)),
                  'risks': sorted(risks), 'handoff': packet, 'current_employment_confirmed': False, 'outreach_ready': False}
        if old.get('linkedin_url') and profile and linkedin(old['linkedin_url']) != profile:
            person['risks'] = sorted(set(person['risks'] + ['IDENTITY_CONFLICT_PROFILE_CORRECTION_REQUIRES_REVIEW']))
        _retain_bounces(state, person, cid)
        state.contact(cid, company['id'], person)
        # State may add identity-collision risks; assess the stored record, not a stale copy.
        person = next(c['data'] for c in state.contacts(company['id']) if c['id'] == cid)
        view = review(person, cid, company['data']['company'], clock)
        person['evidence_record'] = view['record']
        person['current_employment_confirmed'] = view['confirmed_current_employment']
        state.contact(cid, company['id'], person)
        states = {k: view[k] for k in ('discovered_candidate','provisional_role_evidence','confirmed_current_employment','person_linked_email_candidate','deliverability_verified_email','outreach_eligibility','hubspot_status','dripify_status')}
        state.observe(company['id'], cid, 'candidate-handoff', view['outreach_eligibility'],
            {'name': person['name'], 'role': person['role'], 'linkedin_url': person['linkedin_url'],
             'reason': 'PER_PERSON_EVIDENCE_REVIEW', 'source_url': row.get('original_source_url'),
             'observed_at': row.get('research_date'), 'employment_status': view['employment_assessment']['status'],
             'review_states': states, 'email_evidence': view['linked_email_evidence'],
             'evidence_record_id': cid, 'provenance': packet['facts'], 'pending_reasons': view['eligibility_assessment']['reason_codes']})
        identifiers.append(cid)
    return sorted(set(identifiers))


def provider_review(state, company_id, result, provider, clock=None):
    """Retain existing helper warnings; raw provider success never becomes a pass."""
    clock = clock or datetime.now(timezone.utc)
    contacts = {c['id']: c for c in state.contacts(company_id)}
    for item in result.get('contacts', []):
        contact = contacts.get(item.get('contact_id'))
        if not contact or not contact['data'].get('handoff'): continue
        person = contact['data']
        packet = person['handoff']
        notes = packet.setdefault('provider_reviews', {}).setdefault(provider, [])
        note = {'reviewed_at': clock.isoformat(), 'reason': item.get('reason') or result.get('reason'),
                'positive_match': bool(item.get('matches') or item.get('retained_exact_matches')),
                'previous_outreach_confirmed': item.get('previous_outreach_confirmed') is True,
                'coverage_complete': False}
        packet['provider_reviews'][provider] = _unique(notes + [note])
        if note['positive_match'] or note['previous_outreach_confirmed']:
            person['risks'] = sorted(set(person.get('risks', []) + ['EXISTING_OR_PREVIOUS_OUTREACH_REQUIRES_REVIEW']))
        state.contact(contact['id'], company_id, person)


def refresh(state, clock=None, company_id=None):
    """Reconcile retained adverse identifiers and append named review projections."""
    clock = clock or datetime.now(timezone.utc)
    for contact in state.contacts(company_id):
        person = contact['data']
        if not person.get('handoff'): continue
        profile = linkedin(person.get('linkedin_url'))
        others = [c['data'] for c in state.contacts() if c['id'] != contact['id'] and profile and linkedin(c['data'].get('linkedin_url')) == profile]
        for other in others:
            person['blocked'] = bool(person.get('blocked') or other.get('blocked'))
            person['risks'] = sorted(set(person.get('risks', []) + other.get('risks', [])))
            if normalized(other.get('name')) != normalized(person['name']):
                person['risks'] = sorted(set(person['risks'] + ['IDENTITY_CONFLICT']))
        _retain_bounces(state, person, contact['id'])
        company = next(c for c in state.companies() if c['id'] == contact['company_id'])
        view = review(person, contact['id'], company['data']['company'], clock)
        person['evidence_record'] = view['record']
        person['current_employment_confirmed'] = view['confirmed_current_employment']
        state.contact(contact['id'], contact['company_id'], person)
        states = {k: view[k] for k in ('discovered_candidate','provisional_role_evidence','confirmed_current_employment','person_linked_email_candidate','deliverability_verified_email','outreach_eligibility','hubspot_status','dripify_status')}
        fact = person['handoff']['facts'][-1]
        state.observe(contact['company_id'], contact['id'], 'candidate-handoff', view['outreach_eligibility'],
            {'name':person['name'],'role':person['role'],'linkedin_url':person['linkedin_url'],
             'reason':'PER_PERSON_EVIDENCE_REVIEW','source_url':fact.get('original_source_url'),
             'observed_at':fact.get('research_date'),'employment_status':view['employment_assessment']['status'],
             'review_states':states,'email_evidence':view['linked_email_evidence'],
             'evidence_record_id':contact['id'],'provenance':person['handoff']['facts'],'pending_reasons':view['eligibility_assessment']['reason_codes']})
