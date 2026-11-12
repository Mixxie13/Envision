"""Offline normalized manual-export reconciliation. No provider IO or clearance."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urlparse

FIELDS = ('source', 'contact_key', 'hubspot_contact_id', 'linkedin_url', 'email',
          'identity_confirmed', 'existing', 'hard_bounce', 'opt_out',
          'subscription_prohibited', 'active_sequence', 'active_campaign',
          'prior_outreach', 'reply_received', 'coverage_complete',
          'observed_at', 'evidence_ref', 'coverage_ref')
BOOL_FIELDS = ('identity_confirmed', 'existing', 'hard_bounce', 'opt_out',
               'subscription_prohibited', 'active_sequence', 'active_campaign',
               'prior_outreach', 'reply_received', 'coverage_complete')
ADVERSE = ('hard_bounce', 'opt_out', 'subscription_prohibited', 'active_sequence', 'active_campaign')
LABELS = ('Confirmed existing', 'Confirmed cross-channel match', 'Blocked',
          'Ambiguous', 'Not checked', 'Checked with incomplete coverage')


def linkedin(value):
    """Conservative personal profile normalization; not proof of ownership."""
    p = urlparse(value or '')
    host = (p.hostname or '').lower()
    if p.scheme not in ('https', 'http') or host not in ('linkedin.com', 'www.linkedin.com'):
        return None
    try:
        port = p.port
    except ValueError:
        return None
    if p.username or p.password or port or p.query or p.fragment:
        return None
    path = p.path.rstrip('/')
    if not path.startswith('/in/') or len(path.split('/')) != 3 or not path[4:]:
        return None
    return 'linkedin.com' + path.lower()


def read_csv(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)) or set(headers) != set(FIELDS):
            raise ValueError('Use the documented normalized CSV headers exactly')
        rows = list(reader)
    for row in rows:
        if None in row or any(v is None for v in row.values()):
            raise ValueError('Malformed CSV row')
    return rows


def reconcile(checklist, rows):
    """Return review classifications, never evaluator permission/clearance.

    All export fields are human-normalized assertions, not provider schemas.
    Unmatched rows are retained for review. Negative evidence cannot clear
    adverse baseline evidence. Names and company names are never join keys.
    """
    from screening import email as normalize_email
    contacts = checklist['contacts']
    keys = [c['contact_key'] for c in contacts]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate checklist keys')
    grouped = {k: [] for k in keys}
    rejected = []
    for index, row in enumerate(rows):
        key = row.get('contact_key')
        if key not in grouped:
            rejected.append({'row': index + 1, 'reason': 'Unknown contact_key', 'evidence': row})
        else:
            grouped[key].append(row)
    output = []
    for c in contacts:
        flags, reasons, accepted, bound_rows = set(c.get('baseline_flags', [])), [], [], []
        complete = set()
        for row in grouped[c['contact_key']]:
            cid, lid, email = row.get('hubspot_contact_id', ''), row.get('linkedin_url', ''), normalize_email(row.get('email', ''))
            known_id = c.get('hubspot_contact_id', '')
            known_li = linkedin(c.get('linkedin_url', ''))
            known_email = normalize_email(c.get('email', ''))
            id_match = bool(cid and known_id and cid == known_id)
            li_match = bool(known_li and linkedin(lid) == known_li)
            email_match = bool(email and known_email and email == known_email)
            conflict = bool((cid and known_id and cid != known_id) or
                            (lid and known_li and linkedin(lid) != known_li) or
                            (email and known_email and email != known_email))
            if conflict or not (id_match or li_match or email_match):
                # An exact shared identifier plus adverse evidence cannot be
                # discarded merely because another identifier disagrees. Keep
                # the conflict for review; never merge or grant clearance.
                if (id_match or li_match or email_match) and any(row.get(k) == 'true' for k in ADVERSE):
                    flags.add('Blocked')
                    reasons.append('CONFLICTING_IDENTIFIER_WITH_ADVERSE_EVIDENCE')
                    reasons.extend(k.upper() for k in ADVERSE if row.get(k) == 'true')
                flags.add('Ambiguous')
                reasons.append('IDENTITY_CONFLICT_OR_UNBOUND_ROW')
                rejected.append({'contact_key': c['contact_key'], 'reason': 'Identity conflict/unbound', 'evidence': row})
                continue
            bound_rows.append(row)
            # Conservatively retain adverse evidence even when provenance/coverage fails.
            if any(row.get(k) == 'true' for k in ADVERSE):
                flags.add('Blocked')
                reasons.extend(k.upper() for k in ADVERSE if row.get(k) == 'true')
            valid = row.get('source') in ('hubspot', 'dripify')
            valid = valid and all(row.get(k) in ('true', 'false', 'unknown') for k in BOOL_FIELDS)
            try:
                observed = datetime.fromisoformat(row.get('observed_at', '').replace('Z', '+00:00'))
                valid = valid and observed.tzinfo is not None and observed <= datetime.now(observed.tzinfo)
            except (ValueError, TypeError):
                valid = False
            valid = valid and bool(row.get('evidence_ref')) and bool(row.get('coverage_ref'))
            if not valid or row.get('identity_confirmed') != 'true':
                flags.add('Checked with incomplete coverage')
                reasons.append('PROVENANCE_OR_IDENTITY_UNVERIFIED')
                continue
            accepted.append(row)
            if row.get('existing') == 'true':
                flags.add('Confirmed existing')
                if row['source'] == 'dripify' and li_match:
                    flags.add('Confirmed cross-channel match')
            if row.get('coverage_complete') == 'true' and all(row.get(k) != 'unknown' for k in BOOL_FIELDS):
                complete.add(row['source'])
            else:
                flags.add('Checked with incomplete coverage')
            if row.get('prior_outreach') == 'true' or row.get('reply_received') == 'true':
                reasons.append('PREVIOUS_CONTACT_REQUIRES_REVIEW')
        if not grouped[c['contact_key']]:
            flags.add('Not checked')
        if complete != {'hubspot', 'dripify'}:
            flags.add('Checked with incomplete coverage')
        # Conflicting assertions for a bound identity cannot be silently resolved.
        for source in ('hubspot', 'dripify'):
            source_rows = [r for r in bound_rows if r.get('source') == source]
            if any({r.get(k) for r in source_rows} >= {'true', 'false'} for k in BOOL_FIELDS if k != 'coverage_complete'):
                flags.add('Ambiguous')
                reasons.append('CONFLICTING_ASSERTIONS')
        ordered = [s for s in ('Blocked', 'Ambiguous', 'Confirmed cross-channel match', 'Confirmed existing', 'Not checked', 'Checked with incomplete coverage') if s in flags]
        output.append({'contact_key': c['contact_key'], 'classification': ordered[0],
                       'flags': ordered, 'reason_codes': sorted(set(reasons)),
                       'complete_sources_claimed': sorted(complete), 'evidence': grouped[c['contact_key']],
                       'outreach_ready': False,
                       'suppression_status': 'blocked' if 'Blocked' in flags else 'pending',
                       'subscription_clearance': 'pending', 'activity_coverage': 'pending',
                       'pending': ['Freshness policy and source coverage require review',
                                   'Employment, deliverability, approvals and cross-channel policy remain separate',
                                   'No confirmed-new or outreach authorization is issued']})
    return {'schema': 'envision.manual-reconciliation.v1', 'contacts': output, 'unmatched_or_conflicting_rows': rejected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checklist', required=True)
    parser.add_argument('--csv', action='append', default=[])
    parser.add_argument('--output', required=True, help='New JSON report; existing files are never overwritten')
    args = parser.parse_args()
    try:
        result = reconcile(json.loads(Path(args.checklist).read_text()),
                           [r for p in args.csv for r in read_csv(p)])
        with Path(args.output).open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
    except (ValueError, KeyError, TypeError, OSError, csv.Error):
        parser.exit(2, 'Input/output validation failed; no source files or CRM records modified.\n')


if __name__ == '__main__':
    main()
