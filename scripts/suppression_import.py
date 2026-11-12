"""Offline XLSX suppression-list intake. No provider operations or clearance.

Default validates only. Source rows are data; no formulas or instructions execute.
Only explicitly designated suppression lists may add local precautionary blocks.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import io
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

from openpyxl import load_workbook
from screening import email, identifiers, reconcile, unproject
from sourcing_state import State, digest, project_lock

REGISTRY = 'offline-email-suppression-v1'


def _contact_id(cell):
    value = cell.value
    if value is None or value == '': return ''
    if isinstance(value, str) and value.strip().isdigit(): return value.strip()
    if type(value) is int and 0 <= value < 10**15: return str(value)
    raise ValueError('Contact IDs must be exact decimal strings or safely represented integers')


def _email(value):
    original = value
    value = email(value)
    if original is not None and str(original).strip() and not value:
        raise ValueError('Email cell is not an exact individual address')
    if value and any(c.isspace() or c in '<>,;' for c in value):
        raise ValueError('Ambiguous email cell; explicit normalization required')
    return value


def inspect_export(path, sheet=None, aliases_column=None):
    path = Path(path)
    raw = path.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    try:
        if sheet is None and len(book.sheetnames) != 1: raise ValueError('Explicit worksheet required')
        ws = book[sheet] if sheet else book.active
        try: declared_dimension = ws.calculate_dimension()
        except ValueError: declared_dimension = 'unknown'
        # Some actual HubSpot exports declare A1 while containing many XML rows.
        ws.reset_dimensions()
        iterator = ws.iter_rows()
        header_cells = next(iterator)
        headers = [c.value for c in header_cells]
        if any(c.data_type == 'f' for c in header_cells): raise ValueError('Formula header unsupported')
        selected = ['Email', 'Record ID - Contact'] + ([aliases_column] if aliases_column else [])
        columns = {}
        for label in selected:
            found = [i for i, value in enumerate(headers) if value == label]
            if len(found) > 1: raise ValueError('Ambiguous identifier column')
            if found: columns[label] = found[0]
        if not ({'Email', 'Record ID - Contact'} & columns.keys()): raise ValueError('Email or contact ID column required')
        if aliases_column and aliases_column not in columns: raise ValueError('Alias column unavailable')
        entries = []
        for position, cells in enumerate(iterator, 2):
            if not any(c.value is not None for c in cells): continue
            if any(c.data_type == 'f' for c in cells): raise ValueError('Formula cells unsupported')
            def cell(label):
                i = columns.get(label)
                return cells[i] if i is not None and i < len(cells) else None
            address = _email(cell('Email').value) if cell('Email') is not None else ''
            cid = _contact_id(cell('Record ID - Contact')) if cell('Record ID - Contact') is not None else ''
            alias_cell = cell(aliases_column) if aliases_column else None
            aliases = []
            if alias_cell is not None and alias_cell.value:
                if not isinstance(alias_cell.value, str): raise ValueError('Alias column must contain text')
                aliases = sorted({_email(v) for v in alias_cell.value.split(';') if v.strip()})
            if not address and not cid and not aliases: raise ValueError('Suppression row lacks an identifier')
            entries.append({'id': 'suppression:' + digest([source_hash, ws.title, position])[:24],
                'email': address, 'email_aliases': aliases, 'hubspot_contact_id': cid,
                'source_hash': source_hash, 'sheet': ws.title, 'source_row': position,
                'evidence_basis': 'User-designated suppression-list membership; bounce reason/date not exported',
                'source_ref': str(path.resolve())})
        return {'source_hash': source_hash, 'sheet': ws.title, 'headers': headers,
                'declared_dimension': declared_dimension, 'data_rows': len(entries),
                'unique_emails': len({a for e in entries for a in [e['email'], *e['email_aliases']] if a}),
                'unique_contact_ids': len({e['hubspot_contact_id'] for e in entries if e['hubspot_contact_id']}),
                'entries': entries, 'coverage_complete': False, 'subscription_clearance': 'pending',
                'warnings': ['No absence/clearance inferred from list membership or non-membership',
                             'Bounce reason/date and suppression-filter definition require source confirmation']}
    finally:
        book.close()


def import_export(state, inspection, confirmed_list=False):
    if not confirmed_list: raise ValueError('Explicit suppression-list designation required')
    _validate_inspection(inspection)
    registry = state.meta(REGISTRY, {'entries': [], 'sources': {}})
    entries = {e['id']: e for e in registry['entries']}
    before_emails = {a for e in entries.values() for a in [e['email'], *e.get('email_aliases', [])] if a}
    before = {c['id']: c['data'] for c in state.contacts()}
    for entry in inspection['entries']:
        previous = entries.get(entry['id'])
        if previous and any(previous.get(k) != entry.get(k) for k in ('email', 'email_aliases', 'hubspot_contact_id', 'source_hash', 'sheet', 'source_row')):
            raise ValueError('Conflicting evidence for an existing source row')
        entries.setdefault(entry['id'], entry)
    inspected_entries = [entries[e['id']] for e in inspection['entries']]
    registry['entries'] = list(entries.values())
    source_hash = inspection['source_hash']
    if source_hash not in registry['sources']:
        registry['sources'][source_hash] = {k: v for k, v in inspection.items() if k != 'entries'}
        registry['sources'][source_hash]['imported_at'] = datetime.now(timezone.utc).isoformat()
    matches, observations = [], []
    raw_contacts = [dict(r, data=json.loads(r['payload'])) for r in state.db.execute('SELECT * FROM contacts')]
    for contact in reconcile(raw_contacts, registry):
        person = contact['data']; keys = identifiers(person)
        matched = [e for e in inspected_entries if keys & identifiers(e)]
        if not matched: continue
        direct = any(e['id'] in person.get('suppression_sources', []) for e in matched)
        bases = sorted({key.split(':', 1)[0] for e in matched for key in keys & identifiers(e)})
        matches.append({'contact_id': contact['id'], 'basis': bases,
                        'previously_blocked': bool(before[contact['id']].get('blocked')),
                        'decision': 'blocked' if direct else 'pending'})
        payload = {'state': 'blocked' if direct else 'pending',
            'name': person.get('name'), 'role': person.get('role'), 'linkedin_url': person.get('linkedin_url'),
            'reason': 'DESIGNATED_HARD_BOUNCE_SUPPRESSION_LIST_MATCH' if direct else 'AMBIGUOUS_SUPPRESSION_LINK_REQUIRES_REVIEW',
            'evidence_record_id': contact['id'], 'source_url': matched[0]['source_ref'],
            'source_hash': source_hash, 'source_rows': [e['source_row'] for e in matched],
            'pending_reasons': ['EXPORT_BOUNCE_REASON_DATE_AND_FILTER_NOT_INCLUDED',
                                'SUBSCRIPTION_AND_ACTIVITY_COVERAGE_PENDING']}
        oid = digest([contact['company_id'], contact['id'], 'suppression-export', payload])
        observations.append((oid, contact['company_id'], contact['id'], 'suppression-export', json.dumps(payload)))
    with state.db:
        state.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (REGISTRY, json.dumps(registry)))
        state.db.executemany('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)', observations)
    all_emails = {a for e in entries.values() for a in [e['email'], *e.get('email_aliases', [])] if a}
    return {'data_rows': inspection['data_rows'], 'unique_emails_in_export': inspection['unique_emails'],
            'registry_unique_emails': len(all_emails), 'new_registry_emails': len(all_emails-before_emails),
            'matched_contacts': matches, 'newly_blocked_existing_records': sum(not r['previously_blocked'] and r['decision'] == 'blocked' for r in matches),
            'coverage_complete': False, 'campaign_ready': False}


JOURNAL = '.suppression-import-journal.json'


def _validate_inspection(inspection):
    if not isinstance(inspection, dict) or not re.fullmatch('[0-9a-f]{64}', str(inspection.get('source_hash', ''))):
        raise ValueError('Validated source inspection required')
    entries = inspection.get('entries')
    if not isinstance(entries, list) or inspection.get('data_rows') != len(entries): raise ValueError('Invalid source rows')
    ids = set()
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get('id'), str) or e['id'] in ids:
            raise ValueError('Duplicate or invalid source row')
        ids.add(e['id'])
        if e.get('source_hash') != inspection['source_hash'] or not isinstance(e.get('source_ref'), str):
            raise ValueError('Source provenance mismatch')
        if type(e.get('source_row')) is not int or e['source_row'] < 2: raise ValueError('Invalid row reference')
        if not isinstance(e.get('email'), str) or _email(e['email']) != e['email']: raise ValueError('Email normalization required')
        if not isinstance(e.get('email_aliases'), list) or any(not isinstance(v, str) or not v or _email(v) != v for v in e['email_aliases']):
            raise ValueError('Invalid alias list')
        cid = e.get('hubspot_contact_id')
        if not isinstance(cid, str) or (cid and not cid.isdigit()): raise ValueError('String contact ID required')
        if not e['email'] and not cid and not e['email_aliases']: raise ValueError('Unbound row')
    actual = {a for e in entries for a in [e['email'], *e['email_aliases']] if a}
    if inspection.get('unique_emails') != len(actual): raise ValueError('Address count mismatch')


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _replace_bytes(path, content):
    fd, name = tempfile.mkstemp(prefix='.suppression-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path); _sync_directory(path.parent)
    finally:
        if os.path.exists(name): os.unlink(name)


def _publish(staged, destination):
    _replace_bytes(destination, staged.read_bytes())


def recover_import(project, workbook):
    """Caller holds project lock. Resolve a durable interrupted import journal."""
    project, workbook = Path(project).resolve(), Path(workbook).resolve()
    journal = project / JOURNAL
    if not journal.exists(): return
    data = json.loads(journal.read_text())
    stage = Path(data['stage'])
    if stage.parent != project or not stage.name.startswith('.suppression-stage-') or stage.is_symlink():
        raise ValueError('Invalid recovery directory')
    expected = {project/'state.sqlite', project/'master-progress.json', project/'master-progress.csv', workbook}
    if {Path(r['path']) for r in data['targets']} != expected: raise ValueError('Recovery target mismatch')
    if data.get('status') not in ('prepared', 'committed'): raise ValueError('Invalid recovery state')
    if data['status'] != 'committed':
        conflicts = []
        for item in data['targets']:
            target = Path(item['path']); current = _hash(target)
            if current == item['before']:
                if current is not None:
                    os.chmod(target, item['mode'])
                    os.utime(target, ns=(item['atime_ns'], item['mtime_ns']))
                continue
            if current != item['after']:
                conflicts.append(str(target)); continue  # Preserve concurrent external edits.
            if item['before'] is None:
                target.unlink(); _sync_directory(target.parent)
            else:
                backup = stage / item['backup']
                if _hash(backup) != item['before']: raise ValueError('Recovery backup mismatch')
                _replace_bytes(target, backup.read_bytes())
                os.chmod(target, item['mode'])
                os.utime(target, ns=(item['atime_ns'], item['mtime_ns']))
        if conflicts: raise ValueError('Concurrent change prevents automatic rollback; journal retained')
    journal.unlink(); _sync_directory(project)
    shutil.rmtree(stage)


def apply_atomic(project, workbook, inspection):
    """Stage all data first; publish with durable rollback/recovery and read guard."""
    _validate_inspection(inspection)
    project, workbook = Path(project).resolve(), Path(workbook).absolute()
    db_path = project/'state.sqlite'
    if not db_path.is_file() or db_path.is_symlink() or not workbook.is_file() or workbook.is_symlink():
        raise ValueError('Existing project and regular six-sheet workbook required')
    if workbook.resolve() in {db_path, project/'master-progress.json', project/'master-progress.csv'}:
        raise ValueError('Output paths overlap')
    workbook = workbook.resolve()
    with project_lock(project):
        recover_import(project, workbook)
        # Validate inputs before staging any persistent data changes.
        from workbook import validate, update
        from master_progress import write_reports
        book = load_workbook(workbook)
        state, stage = None, None
        try:
            state = State(project, readonly=True)
            validate(book)
            companies = state.companies()
            if not state.meta('master_signature'): raise ValueError('Initialized source project required')
            for company in companies:
                if book['Company Research'].cell(company['position']+1, 1).value != company['data']['company']:
                    raise ValueError('Workbook source order mismatch')
            if state.db.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                raise ValueError('Exclusive rollback-journal SQLite project required')
            stage = Path(tempfile.mkdtemp(prefix='.suppression-stage-', dir=project))
            staged_project = stage/'project'; staged_project.mkdir(mode=0o700)
            with sqlite3.connect(staged_project/'state.sqlite') as destination: state.db.backup(destination)
        except BaseException:
            if stage is not None and stage.exists(): shutil.rmtree(stage)
            raise
        finally:
            book.close()
            if state is not None: state.close()
        journal = project/JOURNAL
        targets = [db_path, workbook, project/'master-progress.json', project/'master-progress.csv']
        try:
            before = {str(p): _hash(p) for p in targets}
            staged = State(staged_project)
            staged.directory = project  # Evidence references remain real; DB connection stays staged.
            try:
                result = import_export(staged, inspection, True)
                staged_book = stage/'workbook.xlsx'; shutil.copy2(workbook, staged_book)
                update(staged, staged_book)
                write_reports(staged, output_directory=stage)
            finally: staged.close()
            published = [staged_project/'state.sqlite', staged_book, stage/'master-progress.json', stage/'master-progress.csv']
            items = []
            for i, (target, prepared) in enumerate(zip(targets, published)):
                if target.is_symlink() or _hash(target) != before[str(target)]: raise ValueError('Concurrent output change')
                backup = stage/('before-'+str(i))
                if target.exists():
                    with backup.open('wb') as stream:
                        stream.write(target.read_bytes()); stream.flush(); os.fsync(stream.fileno())
                    if _hash(backup) != before[str(target)]: raise ValueError('Concurrent backup change')
                # Flush staged artifacts before creating the durable intent journal.
                with prepared.open('rb') as stream: os.fsync(stream.fileno())
                items.append({'path': str(target), 'before': before[str(target)], 'after': _hash(prepared), 'backup': backup.name,
                    'mode': target.stat().st_mode & 0o777 if target.exists() else None,
                    'atime_ns': target.stat().st_atime_ns if target.exists() else None,
                    'mtime_ns': target.stat().st_mtime_ns if target.exists() else None})
            data = {'status': 'prepared', 'stage': str(stage), 'targets': items}
            _sync_directory(stage); _replace_bytes(journal, json.dumps(data).encode())
            for item, prepared in zip(items, published):
                target = Path(item['path'])
                if _hash(target) != item['before']: raise ValueError('Concurrent publication change')
                _publish(prepared, target)
            data['status'] = 'committed'
            _replace_bytes(journal, json.dumps(data).encode())
        except BaseException:
            if journal.exists(): recover_import(project, workbook)
            elif stage.exists(): shutil.rmtree(stage)
            raise
        # Once the durable commit marker exists, all outputs are committed.
        recover_import(project, workbook)
        return result



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True); parser.add_argument('--input', required=True)
    parser.add_argument('--sheet'); parser.add_argument('--aliases-column')
    parser.add_argument('--apply', action='store_true'); parser.add_argument('--confirm-suppression-list', action='store_true')
    parser.add_argument('--workbook')
    args = parser.parse_args()
    try:
        inspection = inspect_export(args.input, args.sheet, args.aliases_column)
        if not args.apply:
            print(json.dumps({k:v for k,v in inspection.items() if k != 'entries'})); return
        if not args.workbook or not args.confirm_suppression_list: raise ValueError('Workbook and list confirmation required')
        result = apply_atomic(args.project, args.workbook, inspection)
        print(json.dumps(result))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        parser.exit(2, 'Suppression import validation failed; review source identifiers and workbook schema.\n')


if __name__ == '__main__': main()
