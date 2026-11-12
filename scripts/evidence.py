"""Offline intake of internal evidence envelopes; no provider schemas or IO."""
from copy import deepcopy

import eligibility

SCHEMA = 'envision.internal-evidence.v1'


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _bound(subject, envelope, item):
    """Exact identity checks only; never infer a person from a company/name."""
    if not isinstance(subject, dict) or item.get('subject') != subject:
        return False
    identity = envelope.get('identity')
    if not isinstance(identity, dict):
        return False
    if identity.get('contact_id') != subject.get('contact_id') or not _text(subject.get('contact_id')):
        return False
    basis, value = identity.get('basis'), identity.get('value')
    if basis == 'email':
        return _text(value) and value == subject.get('email')
    if basis == 'linkedin':
        return (_text(value) and value == subject.get('linkedin_url')
                and identity.get('confirmed_personal') is True)
    return False


def assemble(record, envelopes):
    """Return a Phase 1 record and intake trace from synthetic/internal evidence.

    record supplies subject, channel, approvals and unresolved_risks. Its checks
    are deliberately ignored: every check must pass through envelope intake.
    No raw provider response format is recognized.
    """
    record = deepcopy(record) if isinstance(record, dict) else {}
    subject = record.get('subject')
    output = {key: record.get(key) for key in ('subject', 'channel', 'approvals')}
    risks = record.get('unresolved_risks')
    risks = list(risks) if isinstance(risks, list) else ['RISK_REGISTER_MISSING']
    output['checks'] = {}
    trace, groups = [], {}
    if not isinstance(envelopes, list):
        envelopes = []
        risks.append('ENVELOPES_INVALID')
    for index, raw in enumerate(envelopes):
        entry = {'index': index, 'issues': []}
        trace.append(entry)
        if not isinstance(raw, dict):
            entry['issues'].append('ENVELOPE_INVALID')
            risks.append('ENVELOPE_INVALID')
            continue
        name = raw.get('check')
        provenance = raw.get('provenance')
        entry.update({'check': name, 'schema': raw.get('schema'),
                      'identity': deepcopy(raw.get('identity')),
                      'provenance': deepcopy(provenance)})
        if raw.get('schema') != SCHEMA:
            entry['issues'].append('SCHEMA_UNSUPPORTED')
        if not isinstance(name, str) or name not in eligibility.CHECKS:
            entry['issues'].append('CHECK_UNSUPPORTED')
        item = raw.get('evidence')
        if not isinstance(item, dict):
            item = {}
            entry['issues'].append('EVIDENCE_MISSING')
        entry['evidence'] = deepcopy(item)
        bound = _bound(subject, raw, item)
        if not bound:
            entry['issues'].append('IDENTITY_UNRESOLVED')
        if not isinstance(provenance, dict):
            provenance = {}
        if not all(_text(provenance.get(key)) for key in ('source_ref', 'coverage_ref')):
            entry['issues'].append('PROVENANCE_UNVERIFIED')
        if item.get('source') != provenance.get('source_ref') or not _text(item.get('source')):
            entry['issues'].append('SOURCE_REFERENCE_MISMATCH')
        if provenance.get('capability_verified') is not True:
            entry['issues'].append('CAPABILITY_UNVERIFIED')
        if provenance.get('coverage_verified') is not True:
            entry['issues'].append('COVERAGE_UNVERIFIED')
        if provenance.get('pagination_complete') is not True:
            entry['issues'].append('PAGINATION_INCOMPLETE')
        if provenance.get('lookup_succeeded') is not True:
            entry['issues'].append('LOOKUP_FAILED')
        if item.get('complete') is not True or item.get('state') != 'ok':
            entry['issues'].append('EVIDENCE_UNRESOLVED')
        # Only recognized, identity-bound internal assertions can be merged.
        if raw.get('schema') == SCHEMA and isinstance(name, str) and name in eligibility.CHECKS and bound:
            groups.setdefault(name, []).append((deepcopy(item), entry['issues']))
        risks.extend(entry['issues'])
    for name, candidates in groups.items():
        adverse = set(eligibility.BLOCKS.get(name, set()))
        if name == 'verification':
            adverse.add('invalid')
        # A later clear assertion cannot erase any known bound suppression.
        harmful = [item for item, _ in candidates
                   if isinstance(item.get('result'), str) and item['result'] in adverse]
        if harmful:
            chosen = harmful[0]
        elif len(candidates) == 1:
            chosen = candidates[0][0]
        else:
            chosen = candidates[0][0]
            risks.append(name.upper() + '_MULTIPLE_OBSERVATIONS_REQUIRE_REVIEW')
            chosen['result'] = 'unresolved'
        if any(issues for _, issues in candidates):
            chosen['state'] = 'unresolved'
            chosen['complete'] = False
        output['checks'][name] = chosen
    # Phase 2A must not silently bypass the SOP's email-selected Dripify skip.
    if output.get('channel') == 'email':
        risks.append('EMAIL_CROSS_CHANNEL_POLICY_UNRESOLVED')
        item = output['checks'].get('cross_channel')
        if item is not None:
            item['state'] = 'unresolved'
            item['complete'] = False
    output['unresolved_risks'] = risks
    return {'record': output, 'intake': trace}


def evaluate_evidence(record, envelopes, now=None):
    """Safe handoff to the eligibility evaluator, retaining provenance separately."""
    assembled = assemble(record, envelopes)
    return {**assembled, 'evaluation': eligibility.evaluate(assembled['record'], now=now)}
