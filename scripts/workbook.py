"""Atomic six-sheet output; never rewrites the master or clears prior evidence."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from openpyxl import Workbook, load_workbook
from master_progress import snapshot

SHEETS = {
 'Company Research': ['Company Name', 'Priority / Original Row Position', 'Category', 'Price Tier', 'Official Website', 'Parent / Operator', 'Instagram Handle', 'Instagram Profile URL', 'IG Followers Count', 'IG Followers Tier', 'Facebook Page URL', 'Facebook Followers Count', 'Facebook Followers Tier', 'Company Verification Status', 'Research Date', 'Source URLs', 'Research Notes'],
 'New Prospect Contacts': ['Batch', 'Company', 'Parent / Operator', 'Priority', 'Full Name', 'Current Role', 'Function', 'Professional Email Candidate', 'Email Confidence', 'LinkedIn', 'Role Source URL', 'Email Pattern Source URL', 'Ownership / Routing Notes', 'Research Date'],
 'Existing HubSpot Contacts': ['Batch', 'Company', 'Parent / Operator', 'Full Name', 'Current Job Title', 'LinkedIn Profile URL', 'Email Address', 'HubSpot Contact ID', 'HubSpot Company ID', 'Match Type', 'Existing Contact Status', 'Previous Outreach / Reply Information', 'Suppression / Opt-Out / Bounce Flags', 'Research Date', 'Notes / Evidence'],
 'Verification Evidence': ['Evidence ID', 'Company', 'Full Name', 'Claim / Check', 'Observed Result', 'Source URLs', 'Research Date', 'Verification Provider', 'Provider Result', 'Verification Date', 'Identity / Employment Status', 'HubSpot Status', 'Notes'],
 'Unresolved Issues': ['Campaign', 'Batch', 'Company', 'Full Name', 'Role', 'LinkedIn URL', 'Reason unresolved', 'Date', 'Case Type'],
 'Batch Summary': ['Scope', 'Company', 'Metric', 'Value', 'Status / Definition', 'Source / Notes'],
}
CONFIDENCE = ('Publicly confirmed company email; re-verify before sending',
              'Pattern-derived; verify before sending',
              'Low-confidence pattern; sources conflict, verify before sending',
              'Pattern-derived; post-acquisition domain must be verified',
              'No verified personal email found')


def safe(value):
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


def append(sheet, row):
    sheet.append([safe(v) for v in row])


def validate(book):
    if book.sheetnames != list(SHEETS):
        raise ValueError('Expected the established six sheets in order')
    for name, header in SHEETS.items():
        if list(next(book[name].values)) != header:
            raise ValueError('Workbook schema mismatch: ' + name)
    for row in list(book['New Prospect Contacts'].values)[1:]:
        if row[8] not in CONFIDENCE:
            raise ValueError('Unexpected Email Confidence label')


def populate_review_rows(state, book, clock=None):
    """Research-review rows only, after current identity and complete duplicate checks.

    No provisional, suppressed or provider-only email is silently promoted. Detailed
    eligibility remains in linked evidence; no row is permission to send.
    """
    from candidate_handoff import review
    from contact_research import role_rank
    links=state.meta('managed_prospect_rows', {})
    sheet=book['New Prospect Contacts']
    for company in state.companies():
        people=sorted(state.contacts(company['id']),key=lambda c:role_rank(c['data'].get('role','')))
        written=0
        for contact in people:
            person=contact['data']
            if not person.get('handoff') or person.get('blocked'): continue
            view=review(person,contact['id'],company['data']['company'],clock)
            if not (view['confirmed_current_employment'] and view['hubspot_status']=='absent' and view['dripify_status']=='absent'):continue
            address=person.get('email') or ''
            if address and not view['person_linked_email_candidate']:continue
            confidence=CONFIDENCE[0] if address else CONFIDENCE[-1]
            sources=view['employment_assessment']['sources']
            row=[company['batch'],company['data']['company'],'',company['data']['mapped'].get('priority',company['position']),
                person['name'],person['role'],person.get('function') or person['role'],address,confidence,
                person.get('linkedin_url'),sources[0]['url'] if sources else '',
                view['linked_email_evidence'][0]['source_url'] if address else '',
                'Research review only; see linked eligibility evidence; not campaign authorization',
                view['employment_assessment']['confirmed_as_of']]
            values=[None if v == '' else safe(v) for v in row]
            linked=links.get(contact['id'])
            if linked and linked['row'] <= sheet.max_row:
                current=list(next(sheet.iter_rows(min_row=linked['row'],max_row=linked['row'],values_only=True)))
                if current != linked['values']:
                    state.observe(company['id'],contact['id'],'prospect-review','pending',{'name':person['name'],'role':person['role'],
                        'linkedin_url':person.get('linkedin_url'),'reason':'MANUAL_PROSPECT_ROW_EDIT_REQUIRES_RECONCILIATION'})
                    continue
                for column,value in enumerate(values,1):sheet.cell(linked['row'],column).value=value
            else:
                # Do not duplicate an independently populated row with the same exact profile/company.
                existing=[r for r in list(sheet.values)[1:] if r[1]==values[1] and r[4]==values[4] and r[9]==values[9]]
                if existing:continue
                append(sheet,row)
                links[contact['id']]={'row':sheet.max_row,'values':values,'company_row_id':company['id']}
            if contact['id'] in links:links[contact['id']]['values']=values
            written+=1
            if written>=5:break
    state.set_meta('managed_prospect_rows',links)


def polish(book):
    """Readable review output; no sorting, filtering-out rows or schema changes."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    for sheet in book:
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='203864')
            cell.alignment = Alignment(wrap_text=True, vertical='center')
        sheet.row_dimensions[1].height = 32
        for column in sheet.columns:
            sample = [len(str(c.value or '')) for c in list(column)[:100]]
            width = min(65, max(14, max(sample, default=14) + 2))
            sheet.column_dimensions[get_column_letter(column[0].column)].width = width
            for cell in column[1:]: cell.alignment = Alignment(vertical='top', wrap_text=True)


def update(state, path, clock=None):
    """Project lock must be held. IDs in evidence/notes make repeat runs idempotent."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    if old_hash:
        book = load_workbook(path)
    else:
        book = Workbook()
        book.remove(book.active)
        for name, header in SHEETS.items():
            book.create_sheet(name).append(header)
    validate(book)
    company_rows = {c['id']: c for c in state.companies()}
    from screening import project
    # Assessment time is not provider observation time. This records the same
    # fail-closed projection used by the runner and whole-list tracker.
    from datetime import datetime, timezone
    screening_clock = clock or datetime.now(timezone.utc)
    from candidate_handoff import refresh
    refresh(state, clock=screening_clock)
    for contact in state.contacts():
        person = contact['data']; company = company_rows[contact['company_id']]
        if person.get('handoff'): continue  # Already has the shared named review observation.
        screen = project(person, contact['id'], company['data']['company'], screening_clock)
        stable = {k: screen[k] for k in ('decision', 'technical_eligibility', 'reason_codes',
                  'hubspot_status', 'dripify_status', 'campaign_ready', 'suppression_sources')}
        state.observe(contact['company_id'], contact['id'], 'screening', screen['decision'], {
            'name': person.get('name'), 'role': person.get('role'), 'linkedin_url': person.get('linkedin_url'),
            'reason': 'SUPPRESSION_SCREENING_' + screen['decision'].upper(),
            'evidence_record_id': contact['id'], 'source_url': person.get('source'),
            'pending_reasons': screen['reason_codes'], 'screening': stable,
            'provider_evidence_dates': screen['evidence_dates'],
            'provider_reviews': person.get('handoff', person).get('provider_reviews', {})})
    registry = state.meta('offline-email-suppression-v1', {})
    for source_hash, source in registry.get('sources', {}).items():
        marker = 'AUTO-SUPPRESSION-' + source_hash
        if not any(row[0] == marker for row in list(book['Batch Summary'].values)[1:]):
            append(book['Batch Summary'], [marker, 'Offline suppression list', 'Source rows / unique blocked emails',
                str(source['data_rows']) + ' / ' + str(source['unique_emails']),
                'Designated-list evidence only; reasons/dates/filter missing; no absence or subscription clearance',
                'Source SHA256 ' + source_hash + '; imported_at is not a bounce date'])
    # Preserve existing company rows and values. New workbooks preserve all original categories.
    for company in state.companies():
        p = company['position']
        data = company['data']
        if book['Company Research'].max_row >= p + 1:
            if book['Company Research'].cell(p + 1, 1).value != safe(data['company']):
                raise ValueError('Workbook company order differs from master')
            continue
        m = data['mapped']
        append(book['Company Research'], [data['company'], m.get('priority', p), m.get('category', ''), m.get('price_tier', ''), m.get('website', ''), '', '', '', None, 'N/A', '', None, 'N/A', 'Pending evidence', '', '', 'Source row ' + str(data['source_row']) + '; original fields retained in project state'])
    populate_review_rows(state,book,clock)
    # Repair earlier AUTO issue rows without deleting historical reasons or changing schema.
    observations = {'AUTO-' + o['id'][:24]: o for o in state.observations()}
    contacts = {c['id']: c['data'] for c in state.contacts()}
    for cells in book['Unresolved Issues'].iter_rows(min_row=2):
        reason = str(cells[6].value or '')
        marker = reason.split(']')[0].lstrip('[')
        obs = observations.get(marker)
        if not obs: continue
        payload = obs['data']; person = contacts.get(obs['contact_id'], {})
        for column, field in ((3, 'name'), (4, 'role'), (5, 'linkedin_url')):
            if not cells[column].value:
                cells[column].value = safe(payload.get(field) or person.get(field, ''))
        if '"record_id"' not in reason:
            cells[6].value = safe(reason + '; ' + json.dumps({
                'record_id': payload.get('evidence_record_id') or obs['contact_id'],
                'source_url': payload.get('source_url') or person.get('source'),
                'pending_reasons': payload.get('pending_reasons', [])}, ensure_ascii=False))
    evidence_ids = {r[0] for r in list(book['Verification Evidence'].values)[1:]}
    issue_markers = {str(r[6]).split(']')[0] + ']' for r in list(book['Unresolved Issues'].values)[1:]}
    for obs in state.observations():
        evidence_id = 'AUTO-' + obs['id'][:24]
        if evidence_id in evidence_ids:
            continue
        company = company_rows[obs['company_id']]
        payload = obs['data']
        result = payload.get('reason') or payload.get('state') or 'Pending'
        append(book['Verification Evidence'], [evidence_id, company['data']['company'], payload.get('name', ''), obs['stage'], result,
            payload.get('source_url') or str(state.directory / 'state.sqlite'), payload.get('observed_at', ''), 'Reusable read-only workflow', payload.get('state'), '',
            payload.get('employment_status', 'Current employment not established by automation'), 'No absence/permission inferred',
            json.dumps({'record_id': payload.get('evidence_record_id'), 'review_states': payload.get('review_states'), 'provenance': payload.get('provenance'), 'email_evidence': payload.get('email_evidence'), 'pending_reasons': payload.get('pending_reasons'), 'screening': payload.get('screening'), 'provider_evidence_dates': payload.get('provider_evidence_dates'), 'provider_reviews': payload.get('provider_reviews')}, ensure_ascii=False) if payload.get('review_states') or payload.get('screening') else 'Retained immutable observation ' + obs['id']])
        evidence_ids.add(evidence_id)
        if payload.get('state') in ('pending', 'blocked'):
            marker = '[' + evidence_id + '] ' + str(result)
            if '[' + evidence_id + ']' not in issue_markers:
                contact = contacts.get(obs['contact_id'], {})
                details = json.dumps({'record_id': payload.get('evidence_record_id') or obs['contact_id'],
                                      'source_url': payload.get('source_url') or contact.get('source'),
                                      'pending_reasons': payload.get('pending_reasons', [])}, ensure_ascii=False)
                append(book['Unresolved Issues'], ['Not supplied', company['batch'], company['data']['company'],
                    payload.get('name') or contact.get('name', ''), payload.get('role') or contact.get('role', ''),
                    payload.get('linkedin_url') or contact.get('linkedin_url', ''), marker + '; ' + details,
                    payload.get('observed_at', ''), 'Other'])
                issue_markers.add('[' + evidence_id + ']')
    existing = {str(row[7]): i for i, row in enumerate(list(book['Existing HubSpot Contacts'].values)[1:], 2)}
    for contact in state.contacts():
        data = contact['data']
        cid = str(data.get('hubspot_contact_id') or '')
        company = company_rows[contact['company_id']]
        if cid:
            if cid not in existing:
                append(book['Existing HubSpot Contacts'], [company['batch'], company['data']['company'], '', data.get('name'), data.get('role'), data.get('linkedin_url'), data.get('email'), cid, data.get('hubspot_company_id'), 'Existing CRM ID; identity/role review pending', 'Existing; not new', 'History coverage pending', 'BLOCKED: ' + ','.join(data.get('risks', [])) if data.get('blocked') else 'Unknown; no consent inferred', '', 'AUTO-CONTACT-' + contact['id']])
                existing[cid] = book['Existing HubSpot Contacts'].max_row
            elif data.get('blocked'):
                cell = book['Existing HubSpot Contacts'].cell(existing[cid], 13)
                if 'AUTO: retained block' not in str(cell.value):
                    cell.value = str(cell.value or '') + '; AUTO: retained block; no clearance'
        # Provisional and incomplete-coverage candidates remain supporting evidence.
    marker = 'AUTO-PROJECT-' + state.meta('project_id')
    summary = book['Batch Summary']
    values = [marker, 'Original company list', 'Companies / contacts / blocked',
              f"{len(company_rows)} / {len(state.contacts())} / {sum(bool(c['data'].get('blocked')) for c in state.contacts())}",
              'No outreach-cleared contacts; complete provider coverage unavailable', 'Saved progress in ' + str(state.directory)]
    summary_row = next((i for i, row in enumerate(list(summary.values)[1:], 2) if row[0] == marker), None)
    if summary_row:
        for i, value in enumerate(values, 1):
            summary.cell(summary_row, i).value = safe(value)
    else:
        append(summary, values)
    progress = snapshot(state, clock)
    for metric in ('total_companies_received', 'companies_researched', 'companies_with_confirmed_contacts', 'companies_with_existing_hubspot_contacts', 'companies_with_unresolved_issues', 'companies_not_yet_processed'):
        key = 'AUTO-MASTER-' + state.meta('project_id') + '-' + metric
        row = [key, 'All original imported rows', metric, progress[metric],
               'Row-based counts; categories overlap; not outreach clearance', str(state.directory / 'master-progress.json')]
        index = next((i for i, value in enumerate(list(summary.values)[1:], 2) if value[0] == key), None)
        if index:
            for column, value in enumerate(row, 1):
                summary.cell(index, column).value = safe(value)
        else:
            append(summary, row)
    for metric, value in progress.get('candidate_evidence_counts', {}).items():
        key = 'AUTO-CANDIDATE-' + state.meta('project_id') + '-' + metric
        row = [key, 'Per-person evidence records', metric, value, 'Evidence-derived; no outreach authorization', str(state.directory)]
        index = next((i for i, old in enumerate(list(summary.values)[1:], 2) if old[0] == key), None)
        if index:
            for column, item in enumerate(row, 1): summary.cell(index, column).value = safe(item)
        else: append(summary, row)
    validate(book)
    polish(book)
    fd, temporary = tempfile.mkstemp(suffix='.xlsx', dir=path.parent)
    os.close(fd)
    try:
        book.save(temporary)
        check = load_workbook(temporary)
        validate(check)
        check.close()
        if old_hash and hashlib.sha256(path.read_bytes()).hexdigest() != old_hash:
            raise ValueError('Workbook changed concurrently; refusing overwrite')
        if old_hash:
            backup = path.with_name(path.name + '.' + old_hash[:16] + '.bak')
            if not backup.exists():
                shutil.copy2(path, backup)
                backup.chmod(0o600)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        book.close()
        if os.path.exists(temporary):
            os.unlink(temporary)
