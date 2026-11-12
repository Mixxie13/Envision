"""Offline suppression reconciliation and reporting; never grants campaign access.

Identifiers link review records, not proven identities. Negative provider coverage
is never inferred from a cache, record absence, or a successful HTTP response.
"""
from copy import deepcopy
from datetime import datetime, timezone
from reconcile_exports import linkedin
from eligibility import evaluate, BLOCKS

RISK_CHECKS = {
    'HISTORICAL_HARD_BOUNCE': ('hard_bounce', 'bounced'),
    'EMAIL_OPT_OUT': ('opt_out', 'opted_out'),
    'DO_NOT_CONTACT': ('opt_out', 'opted_out'),
    'SUBSCRIPTION_PROHIBITED': ('subscriptions', 'prohibited'),
    'ACTIVE_SEQUENCE': ('hubspot_sequence', 'active'),
    'ACTIVE_DRIPIFY_CAMPAIGN': ('dripify_campaign', 'active'),
    'CROSS_CHANNEL_CONFLICT': ('cross_channel', 'conflict'),
}


def email(value):
    return value.strip().casefold() if isinstance(value, str) and value.count('@') == 1 else ''


def identifiers(person):
    values = set(person.get('retained_identifiers', []))
    aliases = person.get('email_aliases', [])
    if not isinstance(aliases, list): aliases = []
    confirmed = person.get('confirmed_email_aliases', [])
    if isinstance(confirmed, list): aliases = list(aliases) + confirmed
    raw_record = person.get('raw_record')
    raw = raw_record.get('properties', {}) if isinstance(raw_record, dict) else {}
    if not isinstance(raw, dict): raw = {}
    for value in [person.get('email'), *aliases, *str(raw.get('hs_additional_emails') or '').split(';')]:
        address = email(value)
        if address: values.add('email:' + address)
    profile = linkedin(person.get('linkedin_url'))
    if profile: values.add('linkedin:' + profile)
    if person.get('hubspot_contact_id'): values.add('hubspot:' + str(person['hubspot_contact_id']))
    return values


def hubspot_signals(properties):
    """Observed account properties only; never infer subscription clearance.

    Subscription type names/availability and the UI's opt-out action are not a
    person's status. No type-name-to-property-ID mapping is guessed here.
    """
    properties = properties if isinstance(properties, dict) else {}
    restrictions, history, risks = [], [], set()
    for field in ('hs_email_hard_bounce_reason_enum', 'hs_email_hard_bounce_reason'):
        if isinstance(properties.get(field), str) and properties[field].strip():
            risks.add('HISTORICAL_HARD_BOUNCE')
            restrictions.append({'property': field, 'result': properties[field]})
    for field, value in properties.items():
        if (field == 'hs_email_optout' or field.startswith('hs_email_optout_')) and str(value).strip().casefold() == 'true':
            risks.add('EMAIL_OPT_OUT')
            restrictions.append({'property': field, 'result': True})
    count = str(properties.get('hs_sequences_actively_enrolled_count') or '').strip()
    if str(properties.get('hs_sequences_is_enrolled')).strip().casefold() == 'true' or (count.isdigit() and int(count) > 0):
        risks.add('ACTIVE_SEQUENCE')
        for field in ('hs_sequences_is_enrolled', 'hs_sequences_actively_enrolled_count'):
            if properties.get(field) is not None:
                restrictions.append({'property': field, 'result': properties[field]})
    for field in ('hs_email_first_reply_date', 'hs_email_last_reply_date', 'notes_last_contacted'):
        if properties.get(field):
            history.append({'property': field, 'value': properties[field],
                'meaning': 'Recorded activity/reply indicator; event, channel and coverage need review'})
    enrolled = str(properties.get('hs_sequences_enrolled_count') or '').strip()
    if enrolled.isdigit() and int(enrolled) > 0:
        history.append({'property': 'hs_sequences_enrolled_count', 'value': properties['hs_sequences_enrolled_count'],
                        'meaning': 'Enrollment count snapshot; not complete sequence history'})
    if history: risks.add('PREVIOUS_CONTACT_REQUIRES_REVIEW')
    return {'risks': sorted(risks), 'positive_restrictions': restrictions,
            'activity_indicators': history,
            'suppression_status': 'blocked' if restrictions else 'pending',
            'subscription_clearance': 'pending', 'activity_coverage': 'pending',
            'reason_codes': ['SUBSCRIPTION_TYPE_STATUS_AND_APPLICABILITY_UNVERIFIED',
                             'COMPLETE_HISTORY_AND_FRESHNESS_UNVERIFIED'],
            'campaign_ready': False}


def adverse(person):
    risks = set(person.get('risks', []))
    raw = person.get('raw_record')
    props = raw.get('properties', {}) if isinstance(raw, dict) else {}
    risks.update(hubspot_signals(props)['risks'])
    record = person.get('evidence_record') or {}
    checks = record.get('checks', {}) if isinstance(record, dict) else {}
    reverse = {value: key for key, value in RISK_CHECKS.items()}
    if not isinstance(checks, dict): checks = {}
    for name, item in checks.items():
        if isinstance(item, dict) and isinstance(item.get('result'), str) and item['result'] in BLOCKS.get(name, set()):
            risks.add(reverse.get((name, item['result']), 'KNOWN_SUPPRESSION_RESTRICTION'))
    if person.get('blocked'): risks.add('KNOWN_SUPPRESSION_RESTRICTION')
    return risks


def _stored_checks(person):
    record = person.get('evidence_record')
    checks = record.get('checks') if isinstance(record, dict) else None
    return checks if isinstance(checks, dict) else {}


def unproject(person):
    """Derived screening must never become a new authoritative suppression source."""
    person = deepcopy(person)
    projection = person.pop('_suppression_projection', None)
    if projection:
        added = set(person.get('risks', [])) - set(projection['projected_risks'])
        person['risks'] = sorted(set(projection['native_risks']) | added)
        person['blocked'] = bool(projection['native_blocked'] or (person.get('blocked') and not projection.get('derived_blocked')))
        person['suppression_sources'] = projection.get('native_sources', [])
        checks = _stored_checks(person)
        if isinstance(checks, dict):
            native_checks = projection.get('native_suppression_checks', {})
            for name in {v[0] for v in RISK_CHECKS.values()}:
                item = checks.get(name)
                if isinstance(item, dict) and item.get('source') in ('Retained suppression evidence', 'Retained known adverse evidence'):
                    if name in native_checks: checks[name] = deepcopy(native_checks[name])
                    else: checks.pop(name, None)
    return person


def _verified_aliases(person):
    aliases = set(email(v) for v in person.get('confirmed_email_aliases', []) if email(v))
    raw = person.get('raw_record')
    if isinstance(raw, dict) and str(raw.get('id')) == str(person.get('hubspot_contact_id')):
        props = raw.get('properties', {})
        if not isinstance(props, dict): props = {}
        aliases.update(email(v) for v in str(props.get('hs_additional_emails') or '').split(';') if email(v))
    return aliases


def _verified_crm(person):
    raw = person.get('raw_record')
    return bool(person.get('hubspot_contact_id') and isinstance(raw, dict) and
                str(raw.get('id')) == str(person['hubspot_contact_id']) and
                isinstance(raw.get('properties'), dict) and
                (not email(person.get('email')) or email(raw['properties'].get('email')) == email(person.get('email'))))


def reconcile(rows, registry=None):
    """Direct address matches block. Ambiguous links never form suppression chains."""
    result = deepcopy(rows)
    native = [{**r, 'data': unproject(r['data'])} for r in rows]
    sources = []
    for row in native:
        risks = adverse(row['data'])
        if risks.intersection(RISK_CHECKS) or 'KNOWN_SUPPRESSION_RESTRICTION' in risks:
            sources.append(row)
    sources += [{'id': e['id'], 'data': {**e, 'blocked': True,
        'risks': ['KNOWN_SUPPRESSION_RESTRICTION', 'HARD_BOUNCE_SUPPRESSION_LIST_MEMBERSHIP']},
        'registry': True} for e in (registry or {}).get('entries', [])]
    def review_keys(person):
        keys = ({'hubspot:' + str(person['hubspot_contact_id'])} if person.get('hubspot_contact_id') else set())
        if linkedin(person.get('linkedin_url')): keys.add('linkedin:' + linkedin(person['linkedin_url']))
        keys |= {'alias:' + value for value in _verified_aliases(person)}
        keys |= {'alias:' + email(value) for value in person.get('email_aliases', []) if email(value)}
        return keys
    claims = {}
    for row in native:
        person = row['data']
        keys = review_keys(person)
        for key in keys: claims.setdefault(key, []).append(person)
    ambiguous = set()
    for key, people in claims.items():
        if any(len({str(p.get(field)).strip().casefold() for p in people if p.get(field)}) > 1
               for field in ('name', 'email', 'linkedin_url')):
            ambiguous.add(key)
    for row, original in zip(result, native):
        person = row['data']; base = original['data']; risks = adverse(base)
        native_risks = sorted(risks); native_blocked = bool(base.get('blocked'))
        blocked = native_blocked or bool(risks.intersection(RISK_CHECKS))
        if review_keys(base) & ambiguous: risks.add('AMBIGUOUS_IDENTITY_LINK_REQUIRES_REVIEW')
        retained_sources = set(base.get('suppression_sources', []))
        address = email(base.get('email'))
        for source in sources:
            if source['id'] == row['id']: continue
            other = source['data']
            addresses = {email(other.get('email'))} | _verified_aliases(other)
            if source.get('registry'): addresses.update(email(v) for v in other.get('email_aliases', []))
            addresses.update(other.get('retained_suppressed_emails', []))
            addresses.discard('')
            shared = identifiers(base) & identifiers(other)
            if not shared and not (address and address in addresses): continue
            id_conflict = bool(base.get('hubspot_contact_id') and other.get('hubspot_contact_id') and
                               str(base['hubspot_contact_id']) != str(other['hubspot_contact_id']))
            profile_conflict = bool(linkedin(base.get('linkedin_url')) and linkedin(other.get('linkedin_url')) and
                                    linkedin(base['linkedin_url']) != linkedin(other['linkedin_url']))
            name_conflict = bool(base.get('name') and other.get('name') and
                                 base['name'].strip().casefold() != other['name'].strip().casefold())
            exact_address = bool(address and address in addresses)
            verified_alias = bool(_verified_aliases(base) & addresses)
            verified_id = (_verified_crm(base) and base.get('hubspot_contact_id') == other.get('hubspot_contact_id'))
            disputed_link = ('hubspot:' + str(base.get('hubspot_contact_id')) in ambiguous or
                any('alias:' + value in ambiguous for value in _verified_aliases(base) & addresses))
            consistent = not (id_conflict or profile_conflict or name_conflict or disputed_link)
            if exact_address or (consistent and (verified_alias or verified_id)):
                blocked = True; retained_sources.add(source['id'])
                risks.update(adverse(other).intersection(RISK_CHECKS))
            else:
                risks.add('AMBIGUOUS_SUPPRESSION_LINK_REQUIRES_REVIEW')
            if id_conflict or profile_conflict or name_conflict:
                risks.add('IDENTITY_CONFLICT_SUPPRESSION_LIST_IDENTIFIERS')
        person['blocked'] = blocked
        person['risks'] = sorted(risks)
        person['suppression_sources'] = sorted(retained_sources)
        person['retained_bounce_sources'] = sorted(retained_sources)
        person['_suppression_projection'] = {'native_blocked': native_blocked,
            'native_risks': native_risks, 'derived_blocked': blocked and not native_blocked, 'native_sources': base.get('suppression_sources', []),
            'projected_risks': person['risks'],
            'native_suppression_checks': {k:v for k,v in _stored_checks(base).items() if k in {v[0] for v in RISK_CHECKS.values()}}}
    return result


def enforce(person, record):
    record = deepcopy(record) if isinstance(record, dict) else {}
    if not isinstance(record.get('checks'), dict): record['checks'] = {}
    risks = adverse(person)
    if person.get('hubspot_contact_id'): risks.add('HUBSPOT_RECORD_BINDING_OR_REUSE_REQUIRES_REVIEW')
    if person.get('known_dripify_matches'): risks.add('EXISTING_DRIPIFY_EVIDENCE_REQUIRES_REVIEW')
    record['unresolved_risks'] = sorted(set(record.get('unresolved_risks') or []) | risks)
    for risk, (check, value) in RISK_CHECKS.items():
        if risk in risks:
            record['checks'][check] = {'result': value, 'subject': record.get('subject'),
                'source': 'Retained suppression evidence', 'complete': False, 'state': 'unresolved'}
    return record


def decision(person, record, clock=None):
    result = evaluate(enforce(person, record), now=clock)
    if person.get('blocked') or 'KNOWN_SUPPRESSION_RESTRICTION' in adverse(person):
        result.update(decision='blocked', technical_eligibility='blocked', expires_at=None)
        result['reason_codes'] = sorted(set(result['reason_codes'] + ['RETAINED_BASELINE_BLOCK']))
    return result


def project(person, cid, company, clock=None):
    clock = clock or datetime.now(timezone.utc)
    if person.get('handoff'):
        from candidate_handoff import review
        view = review(person, cid, company, clock)
        result = view['eligibility_assessment']; record = view['record']
    else:
        record = deepcopy(person.get('evidence_record') or {'subject': {
            'contact_id': cid, 'company': company, 'full_name': person.get('name') or '',
            'email': person.get('email') or '', 'linkedin_url': person.get('linkedin_url') or ''},
            'channel': person.get('channel'), 'checks': {}, 'approvals': {},
            'unresolved_risks': ['SOURCE_COVERAGE_AND_POLICY_UNRESOLVED']})
        if not isinstance(record, dict):
            record = {'unresolved_risks': ['STORED_RECORD_MALFORMED']}
        if not isinstance(record.get('checks'), dict): record['checks'] = {}
        expected = {'contact_id': cid, 'company': company, 'full_name': person.get('name') or '',
                    'email': person.get('email') or '', 'linkedin_url': person.get('linkedin_url') or ''}
        if record.get('subject') != expected:
            record['unresolved_risks'] = list(record.get('unresolved_risks') or []) + ['STORED_SUBJECT_IDENTITY_CONFLICT']
        record = enforce(person, record)
        result = decision(person, record, clock)
    checks = record.get('checks') or {}
    from master_progress import supported_checks
    statuses = {}
    for provider in ('hubspot', 'dripify'):
        check = provider + '_duplicate'; item = checks.get(check)
        value = item.get('result') if isinstance(item, dict) else None
        statuses[provider + '_status'] = value if value == 'absent' and not record.get('unresolved_risks') and supported_checks(
            {**person, 'evidence_record': record}, cid, company, clock, {check: value}) else 'pending'
    return {**result, **statuses, 'contact_id': cid, 'campaign_ready': False,
            'ownership_routing_status': 'pending', 'coverage_status': 'pending',
            'hubspot_suppression': hubspot_signals((person.get('raw_record') or {}).get('properties', {}) if isinstance(person.get('raw_record'), dict) else {}),
            'suppression_sources': person.get('suppression_sources', []),
            'evidence_dates': [{'source': e.get('source'), 'observed_at': e.get('observed_at'),
                                'expires_at': e.get('expires_at')} for e in result['evidence']]}
