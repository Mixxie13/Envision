"""Offline original-row accounting. Progress is not verification or approval."""
import csv
import html
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from sourcing_state import digest
from eligibility import evaluate

STAGES = ('hubspot', 'research', 'identity', 'dripify', 'email', 'evaluation')


def supported_checks(person, contact_id, company, clock, required):
    """Validate only bound, current normalized internal checks.

    No raw provider field or boolean alone is accepted. A confirmed person's
    email can still be blocked; identity/role confirmation is not outreach use.
    """
    record = person.get('evidence_record', {})
    if not isinstance(record, dict):
        return False
    subject = record.get('subject', {})
    if not isinstance(subject, dict) or not isinstance(subject.get('full_name'), str) or not subject['full_name'].strip() or subject.get('contact_id') != contact_id or subject.get('company') != company or subject.get('full_name') != person.get('name'):
        return False
    checks = record.get('checks', {})
    if not isinstance(checks, dict):
        return False
    for name, expected in required.items():
        check = checks.get(name, {})
        if not isinstance(check, dict):
            return False
        if check.get('result') != expected or check.get('state') != 'ok' or check.get('complete') is not True or check.get('subject') != subject or not check.get('source') or not check.get('policy_ref'):
            return False
        if not all(isinstance(check.get(k), str) and check[k].strip() for k in ('source', 'policy_ref', 'observed_at', 'expires_at')):
            return False
        try:
            observed = datetime.fromisoformat(check['observed_at'].replace('Z', '+00:00'))
            expires = datetime.fromisoformat(check['expires_at'].replace('Z', '+00:00'))
            if not observed.tzinfo or not expires.tzinfo or not observed <= clock < expires:
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return True


def confirmed_person(person, contact_id, company, clock):
    if person.get('handoff'):
        from candidate_handoff import review
        return review(person, contact_id, company, clock)['confirmed_current_employment']
    if any(any(token in risk for token in ('IDENTITY', 'ALIAS', 'ATTRIBUTION', 'ROUTING_CONFLICT')) for risk in person.get('risks', [])):
        return False
    return supported_checks(person, contact_id, company, clock,
                            {'identity': 'confirmed', 'role_company': 'confirmed', 'company': 'confirmed'})


def verified_email(person, contact_id, company, clock):
    if person.get('handoff'):
        from candidate_handoff import review
        return review(person, contact_id, company, clock)['deliverability_verified_email']
    record = person.get('evidence_record')
    if not isinstance(record, dict) or record.get('unresolved_risks') != [] or person.get('blocked'):
        return False
    subject = record.get('subject')
    if not isinstance(subject, dict) or not subject.get('email') or subject['email'] != person.get('email'):
        return False
    return supported_checks(person, contact_id, company, clock,
                            {'professional_email': 'individual', 'verification': 'valid'})


def snapshot(state, clock=None):
    clock = clock or datetime.now(timezone.utc)
    from candidate_handoff import review
    companies = state.companies()
    if not state.meta('master_signature'):
        raise ValueError('Import a source before reporting progress')
    manifest = state.meta('source_manifest')
    if manifest is None:
        # Safe legacy migration: cannot reconstruct a lost row from company names.
        manifest = [c['data'] for c in companies]
        if digest(manifest) != state.meta('master_signature'):
            raise ValueError('Legacy source integrity mismatch; original import needed')
        state.set_meta('source_manifest', manifest)
    if digest(manifest) != state.meta('master_signature'):
        raise ValueError('Original source manifest integrity mismatch')
    current = {c['id']: c for c in companies}
    contacts = state.contacts()
    observations = state.observations()
    people_by_company, observations_by_company = {}, {}
    for contact in contacts:
        people_by_company.setdefault(contact['company_id'], []).append(contact)
    for observation in observations:
        observations_by_company.setdefault(observation['company_id'], []).append(observation)
    batches = {r['id']: dict(r) for r in state.db.execute('SELECT * FROM batches ORDER BY id')}
    name_positions = {}
    for row in manifest:
        name_positions.setdefault(row['company'].strip().casefold(), []).append(row['position'])
    outcomes, issues, seen_ids = [], [], set()
    for original in manifest:
        position = original['position']
        cid = digest([state.meta('master_signature'), position])[:24]
        seen_ids.add(cid)
        row = current.get(cid)
        intact = bool(row and row['data'] == original and row['position'] == position and row['batch'] == (position - 1) // 10 + 1)
        if not intact:
            issues.append({'position': position, 'issue': 'SOURCE_ROW_MISSING_OR_CHANGED'})
        jobs = {stage: state.job(cid, stage) for stage in STAGES}
        attempted = any(jobs.values())
        company_observations = observations_by_company.get(cid, [])
        research_observations = [o['data'] for o in company_observations if o['stage'] == 'research']
        research_attempted = bool(research_observations and research_observations[-1].get('reason') != 'PUBLIC_RESEARCH_NOT_ENABLED')
        processed = all(jobs.values()) and intact and research_attempted
        researched = any(p.get('state') == 'observed' for o in company_observations if o['stage'] == 'research' for p in o['data'].get('pages', []))
        people = [c for c in people_by_company.get(cid, []) if not c['data'].get('research_excluded')]
        confirmed = sum(confirmed_person(c['data'], c['id'], original['company'], clock) for c in people)
        emails = sum(verified_email(c['data'], c['id'], original['company'], clock) for c in people)
        existing = 0
        for contact in people:
            data = contact['data']; raw = data.get('raw_record')
            if isinstance(raw, dict) and data.get('hubspot_contact_id') and str(data['hubspot_contact_id']) == str(raw.get('id')) and isinstance(raw.get('properties'), dict):
                existing += 1
        duplicate_rows = name_positions[original['company'].strip().casefold()]
        from screening import project
        screens = [project(c['data'], c['id'], original['company'], clock) for c in people]
        technical_pending = any(v['technical_eligibility'] != 'eligible_for_review' for v in screens)
        unresolved = attempted and (technical_pending or any(value != 'complete' for value in jobs.values()) or not confirmed or any(c['data'].get('blocked') or c['data'].get('risks') for c in people))
        unresolved = bool(unresolved or len(duplicate_rows) > 1 or not intact or (bool(people) and technical_pending))
        outcome = 'not_yet_processed'
        if attempted and not processed:
            outcome = 'in_progress_or_missing_stage'
        elif processed:
            outcome = 'processed_with_confirmed_contacts' if confirmed else 'processed_no_confirmed_contacts'
        reasons = [stage.upper() + '_' + str(status or 'NOT_CHECKED').upper() for stage, status in jobs.items() if status != 'complete']
        if processed and not researched:
            reasons.append('NO_ACCESSIBLE_RESEARCH_SOURCE_OBSERVED')
        if processed and not confirmed:
            reasons.append('NO_CURRENT_QUALIFIED_CONTACT_CONFIRMED')
        if technical_pending:
            reasons.append('VERIFICATION_OR_SUPPRESSION_UNRESOLVED')
        if any(c['data'].get('blocked') for c in people):
            reasons.append('KNOWN_CONTACT_BLOCK_RETAINED')
        if len(duplicate_rows) > 1:
            reasons.append('POTENTIAL_DUPLICATE_SOURCE_ROWS_PRESERVED')
        outcomes.append({'company_id': cid, 'original_position': position, 'source_row': original['source_row'],
                         'batch': (position - 1) // 10 + 1, 'company': original['company'],
                         'original_values': original['original'], 'original_priority': original['mapped'].get('priority'),
                         'original_category': original['mapped'].get('category'), 'source_row_intact': intact,
                         'outcome': outcome, 'stage_statuses': jobs, 'research_sources_observed': researched,
                         'confirmed_contacts': confirmed, 'existing_hubspot_contacts': existing,
                         'verified_professional_emails': emails,
                         'email_outcome': 'verification_evidence_present_not_outreach_authorization' if emails else ('no_current_verified_individual_email_established' if processed else 'not_checked_or_in_progress'),
                         'unresolved': unresolved, 'reason_codes': reasons,
                         'screening_counts': {status: sum(v['decision'] == status for v in screens) for status in ('blocked', 'pending', 'eligible_for_review')},
                         'potential_duplicate_positions': duplicate_rows if len(duplicate_rows) > 1 else [],
                         'lead_sourcing_owner_reviewed': bool(batches.get((position - 1) // 10 + 1, {}).get('reviewed')),
                         'bridget_final_approval': 'not established by this tracker',
                         'outreach_ready': False})
    if set(current) - seen_ids:
        issues.append({'issue': 'STATE_HAS_ROWS_NOT_IN_ORIGINAL_SOURCE', 'company_ids': sorted(set(current) - seen_ids)})
    archive = state.meta('source_archive')
    source_bytes_verified = bool(archive and Path(archive).is_file() and hashlib.sha256(Path(archive).read_bytes()).hexdigest() == state.meta('source_hash'))
    processed_count = sum(o['outcome'].startswith('processed_') for o in outcomes)
    not_started = sum(o['outcome'] == 'not_yet_processed' for o in outcomes)
    reviewed = all(o['lead_sourcing_owner_reviewed'] for o in outcomes)
    complete = bool(outcomes and not issues and processed_count == len(manifest) and reviewed and source_bytes_verified)
    from candidate_handoff import review
    views = [review(c['data'], c['id'], current[c['company_id']]['data']['company'], clock) for c in contacts if c['data'].get('handoff')]
    candidate_counts = {key: sum(v[key] for v in views) for key in ('discovered_candidate','provisional_role_evidence','confirmed_current_employment','person_linked_email_candidate','deliverability_verified_email')}
    candidate_counts['outreach_eligibility'] = {decision: sum(v['outreach_eligibility'] == decision for v in views) for decision in ('blocked','pending','eligible_for_review')}
    return {'scope': 'Only the imported source rows; do not equate a pilot with Bridget\'s entire master list',
            'source_signature': state.meta('master_signature'), 'source_bytes_verified': source_bytes_verified,
            'candidate_evidence_counts': candidate_counts,
            'total_companies_received': len(manifest), 'companies_researched': sum(o['research_sources_observed'] for o in outcomes),
            'companies_with_confirmed_contacts': sum(o['confirmed_contacts'] > 0 for o in outcomes),
            'companies_with_existing_hubspot_contacts': sum(o['existing_hubspot_contacts'] > 0 for o in outcomes),
            'companies_with_unresolved_issues': sum(o['unresolved'] for o in outcomes),
            'companies_not_yet_processed': not_started, 'companies_in_progress': len(manifest) - processed_count - not_started,
            'companies_with_documented_processed_outcomes': processed_count,
            'all_original_rows_accounted_for': not issues and len(outcomes) == len(manifest),
            'integrity_issues': issues, 'all_batches_research_reviewed': reviewed,
            'research_coverage_complete': complete, 'completion_status': 'RESEARCH_COVERAGE_COMPLETE_APPROVAL_PENDING' if complete else 'INCOMPLETE',
            'bridget_final_approval': 'not established; separate approval required', 'outreach_ready': False,
            'companies': outcomes}


def atomic_text(path, text):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_reports(state, completion=False, output_directory=None):
    report = snapshot(state)
    folder = Path(output_directory) if output_directory is not None else state.directory
    atomic_text(folder / 'master-progress.json', json.dumps(report, indent=2, ensure_ascii=False))
    fields = ['original_position', 'source_row', 'batch', 'company', 'original_priority', 'original_category', 'outcome', 'research_sources_observed', 'confirmed_contacts', 'existing_hubspot_contacts', 'verified_professional_emails', 'email_outcome', 'unresolved', 'reason_codes', 'potential_duplicate_positions', 'lead_sourcing_owner_reviewed', 'bridget_final_approval']
    import io
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
    for row in report['companies']:
        selected = {k: row[k] for k in fields}
        for k, value in selected.items():
            if isinstance(value, list):
                selected[k] = ' | '.join(map(str, value))
            if isinstance(selected[k], str) and selected[k].startswith(('=', '+', '-', '@', '\t', '\r')):
                selected[k] = "'" + selected[k]
        writer.writerow(selected)
    atomic_text(folder / 'master-progress.csv', stream.getvalue())
    if completion:
        lines = ['# Original-list completion reconciliation', '', '**' + report['completion_status'] + '**', '',
                 'Research coverage does not establish verification, Bridget approval or outreach permission.', '',
                 'Every original company row is retained below, including duplicates and unresolved/no-contact cases.', '',
                 '|Original position|Source row|Company|Outcome|Confirmed contacts|Verified emails|Unresolved|', '|---|---|---|---|---|---|---|']
        lines += ['|'+str(o['original_position'])+'|'+str(o['source_row'])+'|'+html.escape(o['company']).replace('|','\\|').replace('\n',' ')+'|'+o['outcome']+'|'+str(o['confirmed_contacts'])+'|'+str(o['verified_professional_emails'])+'|'+str(o['unresolved'])+'|' for o in report['companies']]
        lines += ['', '## Counts (source rows; categories may overlap)', '']
        lines += [key + ': ' + str(report[key]) for key in ('total_companies_received', 'companies_researched', 'companies_with_confirmed_contacts', 'companies_with_existing_hubspot_contacts', 'companies_with_unresolved_issues', 'companies_not_yet_processed')]
        lines += ['', 'Integrity issues: '+json.dumps(report['integrity_issues']), 'Source bytes verified: '+str(report['source_bytes_verified']), 'All original rows accounted for: '+str(report['all_original_rows_accounted_for']), 'Bridget approval remains separate; no outreach authorized.']
        atomic_text(folder / 'master-completion-report.md', '\n'.join(lines)+'\n')
    return report
