"""Offline person-specific employment assessment; never outreach permission.

Reviewed claims come from permitted original documents, not search snippets.
No freshness window, alias equivalence or provider schema is inferred.
"""
from datetime import datetime, timezone
import unicodedata
from reconcile_exports import linkedin
from research import relevant_role, public_url


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).replace('’', "'").casefold().split())


def assess(person, claims, as_of):
    """A current finding needs a same-date explicit review of an original profile.

    `current_review_date` records actual current-role review, not page fetch time.
    Confirmation is an as-of research finding, never a reusable freshness grant.
    """
    assessment_time = datetime.fromisoformat(as_of.replace('Z', '+00:00'))
    if assessment_time.tzinfo is None:
        raise ValueError('Timezone-aware assessment timestamp required')
    date = assessment_time.date()
    profile = linkedin(person.get('linkedin_url'))
    reasons, sources, positives, adverse = [], [], [], []
    if person.get('identity_conflict'):
        reasons.append('IDENTITY_CONFLICT')
    for claim in claims:
        if normalized(claim.get('name')) != normalized(person.get('name')):
            reasons.append('UNBOUND_PERSON_NAME');continue
        if normalized(claim.get('company')) != normalized(person.get('company')):
            reasons.append('UNBOUND_COMPANY');continue
        url = claim.get('source_url')
        if not public_url(url) or claim.get('access_status') != 'observed' or not claim.get('source_sha256') or not claim.get('quote'):
            reasons.append('ORIGINAL_SOURCE_EVIDENCE_INCOMPLETE');continue
        try:
            observed = datetime.fromisoformat(claim['observed_at'].replace('Z', '+00:00'))
            if observed.tzinfo is None or observed > assessment_time: raise ValueError()
        except (ValueError, KeyError, TypeError):
            reasons.append('OBSERVATION_DATE_INVALID');continue
        sources.append({'url': url, 'observed_at': claim['observed_at'],
                        'source_sha256': claim['source_sha256'],
                        'publication_date': claim.get('publication_date')})
        state = claim.get('claim_state', 'unknown')
        if state in ('departed', 'role_changed'):
            adverse.append(state);continue
        if state != 'current':
            reasons.append('HISTORICAL_OR_UNRESOLVED_PUBLICATION');continue
        if normalized(claim.get('role')) != normalized(person.get('role')) or not relevant_role(claim.get('role', '')):
            adverse.append('conflicting_role');continue
        if claim.get('stale') is True:
            reasons.append('STALE_EVIDENCE');continue
        if not profile or linkedin(url) != profile or linkedin(claim.get('subject_profile_url')) != profile:
            reasons.append('PERSONAL_PROFILE_CONFIRMATION_MISSING');continue
        # Fetch dates and copyright/publication years cannot substitute for review.
        if observed.astimezone(assessment_time.tzinfo).date() != date or claim.get('current_review_date') != date.isoformat() or claim.get('current_role_explicit') is not True or claim.get('reviewed_original') is not True:
            reasons.append('CURRENT_ROLE_REVIEW_MISSING_OR_STALE');continue
        # The original person-specific quotation must support all attribution fields.
        quote = normalized(claim['quote'])
        if not all(normalized(claim.get(k)) in quote for k in ('name', 'company', 'role')):
            reasons.append('QUOTATION_ATTRIBUTION_INCOMPLETE');continue
        positives.append(claim)
    conflict = bool(adverse or person.get('identity_conflict'))
    confirmed = bool(positives) and not conflict and not any(r in reasons for r in ('STALE_EVIDENCE', 'UNBOUND_COMPANY', 'UNBOUND_PERSON_NAME'))
    if not claims: reasons.append('NO_PERSON_SPECIFIC_EVIDENCE')
    return {'name': person.get('name'), 'company': person.get('company'),
            'status': 'conflicting' if conflict else 'confirmed_current' if confirmed else 'pending',
            'current_employment_confirmed': confirmed,
            'confirmed_as_of': date.isoformat() if confirmed else None,
            'reason_codes': sorted(set(reasons + (['CONFLICTING_EMPLOYMENT_CLAIMS'] if adverse else []))),
            'sources': sources, 'outreach_ready': False,
            'retained_risks': person.get('risks', []),
            'limit': 'Research finding only; suppression, verification, freshness and approvals remain separate'}
