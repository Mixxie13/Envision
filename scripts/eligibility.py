"""Offline, read-only pre-outreach review gate. No provider or credential access."""
import argparse
from datetime import datetime, timezone
import json
import sys

# Internal evidence vocabulary, not provider properties or response values.
CHECKS = {
    'identity': 'confirmed',
    'company': 'confirmed',
    'role_company': 'confirmed',
    'professional_email': 'individual',
    'verification': 'valid',
    'hard_bounce': 'clear',
    'opt_out': 'clear',
    'subscriptions': 'permitted',
    'hubspot_duplicate': 'absent',
    'hubspot_sequence': 'inactive',
    'outreach_history': 'none',
    'dripify_duplicate': 'absent',
    'dripify_campaign': 'inactive',
    'cross_channel': 'clear',
}
BLOCKS = {
    'hard_bounce': {'bounced'},
    'opt_out': {'opted_out'},
    'subscriptions': {'prohibited'},
    'hubspot_sequence': {'active'},
    'dripify_campaign': {'active'},
    'cross_channel': {'conflict'},
    'professional_email': {'shared'},
}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def evaluate(record, now=None):
    """Evaluate explicitly supplied evidence; never fetch or infer missing facts."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Evaluation time must have a timezone')
    pending, blocked, evidence, expiries = [], [], [], []
    if not isinstance(record, dict):
        record = {}
    subject = record.get('subject')
    subject_ok = (isinstance(subject, dict)
                  and all(isinstance(subject.get(k), str) and subject[k].strip()
                          for k in ('contact_id', 'company', 'full_name')))
    if not subject_ok:
        pending.append('SUBJECT_MISSING')
    channel = record.get('channel')
    if channel not in ('email', 'linkedin'):
        pending.append('CHANNEL_UNDEFINED')
        channel = None
    if channel == 'email' and (not isinstance(subject, dict)
                              or not isinstance(subject.get('email'), str)
                              or not subject['email'].strip()):
        pending.append('EMAIL_MISSING')
    if channel == 'linkedin' and (not isinstance(subject, dict)
                                 or not isinstance(subject.get('linkedin_url'), str)
                                 or not subject['linkedin_url'].strip()):
        pending.append('LINKEDIN_MISSING')
    checks = record.get('checks', {})
    if not isinstance(checks, dict):
        checks = {}
    for name, acceptable in CHECKS.items():
        if channel != 'email' and name in ('verification', 'professional_email'):
            continue
        item = checks.get(name)
        code = name.upper()
        if not isinstance(item, dict):
            pending.append(code + '_MISSING')
            continue
        result = item.get('result')
        evidence.append({'check': name, 'result': result, 'source': item.get('source'),
                         'observed_at': item.get('observed_at'),
                         'expires_at': item.get('expires_at'),
                         'policy_ref': item.get('policy_ref')})
        # Positive suppression/conflict evidence remains a block even when stale.
        # A malformed/unbound assertion can deny eligibility, never grant it.
        if isinstance(result, str) and result in BLOCKS.get(name, set()):
            blocked.append(code + '_BLOCKED')
        if name == 'verification' and result == 'invalid':
            blocked.append('VERIFICATION_INVALID')
        if result != acceptable:
            pending.append(code + '_UNRESOLVED')
        if item.get('subject') != subject or not subject_ok:
            pending.append(code + '_SUBJECT_MISMATCH')
        if item.get('complete') is not True:
            pending.append(code + '_INCOMPLETE')
        if item.get('state') != 'ok':
            pending.append(code + '_SOURCE_UNRESOLVED')
        if not isinstance(item.get('source'), str) or not item['source'].strip():
            pending.append(code + '_EVIDENCE_MISSING')
        if not isinstance(item.get('policy_ref'), str) or not item['policy_ref'].strip():
            pending.append(code + '_POLICY_UNDEFINED')
        try:
            observed, expiry = timestamp(item.get('observed_at')), timestamp(item.get('expires_at'))
            if observed > now or expiry <= observed:
                pending.append(code + '_DATES_INVALID')
            elif expiry <= now:
                pending.append(code + '_STALE')
            else:
                expiries.append(expiry)
        except (ValueError, TypeError, OverflowError):
            pending.append(code + '_FRESHNESS_UNDEFINED')
    risks = record.get('unresolved_risks')
    if not isinstance(risks, list) or risks:
        pending.append('RISKS_UNRESOLVED')
    technical = 'blocked' if blocked else 'pending' if pending else 'eligible_for_review'
    approvals = record.get('approvals', {})
    if not isinstance(approvals, dict):
        approvals = {}
    approval_output = {}
    for person in ('roxy', 'bridget'):
        item = approvals.get(person)
        approval_output[person] = item if isinstance(item, dict) else {'state': 'missing'}
        if not isinstance(item, dict) or item.get('state') != 'approved':
            pending.append(person.upper() + '_APPROVAL_REQUIRED')
        elif item.get('subject') != subject or not item.get('source'):
            pending.append(person.upper() + '_APPROVAL_EVIDENCE_MISSING')
        else:
            try:
                if timestamp(item.get('observed_at')) > now:
                    raise ValueError('Future approval')
            except (ValueError, TypeError, OverflowError):
                pending.append(person.upper() + '_APPROVAL_DATE_INVALID')
    return {
        'decision': 'blocked' if blocked else 'pending' if pending else 'eligible_for_review',
        'technical_eligibility': technical,
        'reason_codes': sorted(set(blocked + pending)),
        'channel': channel,
        'evaluated_at': now.isoformat(),
        'expires_at': min(expiries).isoformat() if technical == 'eligible_for_review' else None,
        'evidence': evidence,
        'approvals': approval_output,
        'authorized_to_send': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record', help='Linked evidence JSON file, or - for stdin')
    args = parser.parse_args()
    try:
        if args.record == '-':
            record = json.load(sys.stdin)
        else:
            with open(args.record, encoding='utf-8') as stream:
                record = json.load(stream)
        result = evaluate(record)
    except (OSError, ValueError, TypeError, OverflowError):
        result = evaluate({})
        result['reason_codes'].append('INPUT_UNREADABLE_OR_INVALID')
    print(json.dumps(result))
    return 0 if result['decision'] == 'eligible_for_review' else 1


if __name__ == '__main__':
    raise SystemExit(main())
