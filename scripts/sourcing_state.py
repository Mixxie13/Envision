"""Durable, local-only batch state. Never stores authorization to send."""
from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlparse


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


@contextmanager
def project_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another operation holds this project lock') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_master(path, mapping, sheet=None):
    """Explicit headers; preserve every original value and original row order."""
    path = Path(path)
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream)
            all_rows = list(reader)
    elif path.suffix.lower() == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=False)
        try:
            if sheet is None and len(book.sheetnames) != 1:
                raise ValueError('Choose the source worksheet explicitly')
            ws = book[sheet or book.sheetnames[0]]
            ws.reset_dimensions()
            all_rows = [list(row) for row in ws.values]
        finally:
            book.close()
    else:
        raise ValueError('Master must be CSV or XLSX')
    if not all_rows:
        raise ValueError('Master is empty')
    headers = [str(v) if v is not None else '' for v in all_rows[0]]
    special_headers = any(not h for h in headers) or len(headers) != len(set(headers))
    if not mapping.get('company') or any(headers.count(v) != 1 for v in mapping.values()):
        raise ValueError('Explicit column mapping does not match source headers')
    result = []
    for source_row, values in enumerate(all_rows[1:], 2):
        if not any(v is not None and v != '' for v in values):
            continue
        if path.suffix.lower() == '.xlsx' and len(values) < len(headers):
            values = values + [None] * (len(headers)-len(values))
        if len(values) != len(headers):
            raise ValueError('Malformed master row')
        if any(isinstance(v, str) and v.startswith('=') for v in values):
            raise ValueError('Formula cells require a reviewed values-only source')
        from datetime import date, time
        typed_values = []
        changed_types = False
        for value in values:
            if isinstance(value, (datetime, date, time)):
                typed_values.append(value.isoformat()); changed_types = True
            else:
                if isinstance(value, float):
                    import math
                    if not math.isfinite(value): raise ValueError('Non-finite source value')
                typed_values.append(value)
        original = {h: typed_values[i] for i,h in enumerate(headers) if h and headers.count(h)==1}
        name = original[mapping['company']]
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Every nonempty row needs a company name')
        result.append({'position': len(result) + 1, 'source_row': source_row,
                       'company': name, 'original': original,
                       'mapped': {k: original[v] for k, v in mapping.items()}})
        if special_headers or changed_types:
            result[-1]['original_columns'] = [{'column':i+1,'header':headers[i],'value':typed_values[i],
                'value_type':type(values[i]).__name__} for i in range(len(headers))]
    if not result:
        raise ValueError('No company rows')
    return result


class _Connection(sqlite3.Connection):
    """Allow offline intake to group existing helper writes in one transaction."""
    _managed = 0
    def __exit__(self, kind, value, traceback):
        if self._managed: return False
        return super().__exit__(kind, value, traceback)


class State:
    def __init__(self, directory, readonly=False):
        self.directory = Path(directory)
        if (self.directory / '.suppression-import-journal.json').exists():
            raise ValueError('Unfinished suppression import: recover before reading project state')
        if readonly:
            self.db = sqlite3.connect((self.directory / "state.sqlite").resolve().as_uri() + "?mode=ro", uri=True, factory=_Connection)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only=ON")
            return
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.directory / 'state.sqlite', factory=_Connection)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY, position INTEGER UNIQUE, batch INTEGER, payload TEXT);
        CREATE TABLE IF NOT EXISTS batches (id INTEGER PRIMARY KEY, reviewed INTEGER DEFAULT 0, review_ref TEXT);
        CREATE TABLE IF NOT EXISTS contacts (id TEXT PRIMARY KEY, company_id TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, company_id TEXT, contact_id TEXT, stage TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS jobs (company_id TEXT, stage TEXT, state TEXT, PRIMARY KEY(company_id,stage));
        CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, payload TEXT);
        ''')
        os.chmod(self.directory / 'state.sqlite', 0o600)

    @contextmanager
    def atomic_intake(self):
        """Offline writes only. Never wrap a provider dispatch/reservation in this."""
        name = 'offline_intake_' + str(self.db._managed)
        self.db.execute('SAVEPOINT ' + name)
        self.db._managed += 1
        try:
            yield
        except BaseException:
            self.db.execute('ROLLBACK TO ' + name)
            self.db.execute('RELEASE ' + name)
            raise
        else:
            self.db.execute('RELEASE ' + name)
        finally:
            self.db._managed -= 1

    def close(self):
        self.db.close()

    def meta(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def initialize(self, rows, source_hash):
        signature = digest(rows)
        if self.meta('master_signature'):
            if signature != self.meta('master_signature'):
                raise ValueError('Project already has a different master; never replace its order')
            return
        with self.db:
            self.db.execute('INSERT INTO meta VALUES (?,?)', ('master_signature', json.dumps(signature)))
            self.db.execute('INSERT INTO meta VALUES (?,?)', ('source_hash', json.dumps(source_hash)))
            self.db.execute('INSERT INTO meta VALUES (?,?)', ('source_manifest', json.dumps(rows, default=str)))
            self.db.execute('INSERT INTO meta VALUES (?,?)', ('project_id', json.dumps(signature[:16])))
            seen_names = {}
            for row in rows:
                cid = digest([signature, row['position']])[:24]
                batch = (row['position'] - 1) // 10 + 1
                self.db.execute('INSERT INTO companies VALUES (?,?,?,?)', (cid, row['position'], batch, json.dumps(row, default=str)))
                self.db.execute('INSERT OR IGNORE INTO batches(id) VALUES (?)', (batch,))
                name_key = row['company'].strip().casefold()
                if name_key in seen_names:
                    payload = {'state': 'pending', 'reason': 'REPEATED_MASTER_COMPANY_REVIEW', 'earlier_company_id': seen_names[name_key]}
                    oid = digest([cid, payload])
                    self.db.execute('INSERT INTO observations VALUES (?,?,?,?,?)', (oid, cid, '', 'identity', json.dumps(payload)))
                else:
                    seen_names[name_key] = cid

    def archive_source(self, path):
        """Retain the original upload locally; no research/API execution."""
        source = Path(path)
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.meta('source_hash'):
            raise ValueError('Original source bytes do not match import')
        saved = self.directory / ('original-source' + source.suffix.lower())
        if saved.exists():
            if saved.read_bytes() != raw:
                raise ValueError('Never overwrite original source archive')
        else:
            with saved.open('xb') as stream:
                stream.write(raw)
            saved.chmod(0o600)
        self.set_meta('source_archive', str(saved.resolve()))

    def companies(self, batch=None):
        sql = 'SELECT * FROM companies'
        values = ()
        if batch is not None:
            sql += ' WHERE batch=?'
            values = (batch,)
        return [dict(r, data=json.loads(r['payload'])) for r in self.db.execute(sql + ' ORDER BY position', values)]

    def contact(self, cid, company_id, payload):
        """Caller IDs are stable; repeated imports cannot erase suppression."""
        from screening import unproject
        payload = unproject(payload)
        old = self.db.execute('SELECT payload,company_id FROM contacts WHERE id=?', (cid,)).fetchone()
        if old and old['company_id'] != company_id:
            self.observe(company_id, cid, 'identity', 'pending', {'reason': 'CONTACT_COMPANY_CONFLICT'})
            return
        if old:
            before = json.loads(old['payload'])
            payload = {**before, **payload}
            payload['blocked'] = bool(before.get('blocked') or payload.get('blocked'))
            from screening import adverse, identifiers
            payload['retained_identifiers'] = sorted(identifiers(before) | identifiers(payload))
            payload['risks'] = sorted(adverse(before) | set(payload.get('risks', [])))
            from screening import email, _verified_aliases, RISK_CHECKS
            if before.get('blocked') or adverse(before).intersection(RISK_CHECKS):
                payload['retained_suppressed_emails'] = sorted(set(before.get('retained_suppressed_emails', [])) |
                    ({email(before.get('email'))} - {''}) | _verified_aliases(before))
        # Separate records may refer to one person. Flag collisions across brands,
        # never silently merge by name or discard adverse evidence.
        profile = payload.get('linkedin_url') or ''
        profile = urlparse(profile).path.rstrip('/').lower() if profile else ''
        for other in self.contacts():
            if other['id'] == cid:
                continue
            other_profile = other['data'].get('linkedin_url') or ''
            other_profile = urlparse(other_profile).path.rstrip('/').lower() if other_profile else ''
            name = str(payload.get('name') or '').casefold().split()
            other_name = str(other['data'].get('name') or '').casefold().split()
            if name and other_name and name[-1] == other_name[-1] and name != other_name and company_id == other['company_id']:
                payload['risks'] = sorted(set(payload.get('risks', []) + ['ALIAS_OR_SAME_SURNAME_REVIEW']))
                self.observe(company_id, cid, 'identity', 'pending', {'reason': 'ALIAS_OR_SAME_SURNAME_REVIEW', 'other_contact_id': other['id']})
            same_profile = bool(profile and profile.startswith('/in/') and profile == other_profile)
            same_email = bool(payload.get('email') and payload.get('email') == other['data'].get('email'))
            if same_profile or same_email:
                payload['risks'] = sorted(set(payload.get('risks', []) + ['CROSS_RECORD_IDENTITY_COLLISION']))
                self.observe(company_id, cid, 'identity', 'pending',
                             {'reason': 'CROSS_RECORD_IDENTITY_COLLISION', 'other_contact_id': other['id']})
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO contacts VALUES (?,?,?)', (cid, company_id, json.dumps(payload)))

    def contacts(self, company_id=None):
        query = 'SELECT * FROM contacts' + (' WHERE company_id=?' if company_id else '')
        from screening import reconcile
        # Reconcile account-local retained evidence before company filtering so
        # suppression cannot disappear across brands, candidate IDs or load order.
        rows = reconcile([dict(r, data=json.loads(r['payload'])) for r in self.db.execute('SELECT * FROM contacts')],
                         self.meta('offline-email-suppression-v1'))
        return [r for r in rows if company_id is None or r['company_id'] == company_id]

    def observe(self, company_id, contact_id, stage, state, evidence):
        payload = {'state': state, **evidence}
        oid = digest([company_id, contact_id, stage, payload])
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)',
                            (oid, company_id, contact_id, stage, json.dumps(payload)))
        return oid

    def observations(self):
        return [dict(r, data=json.loads(r['payload'])) for r in self.db.execute('SELECT * FROM observations ORDER BY rowid')]

    def cached(self, key, operation):
        key = digest([getattr(self, 'cache_revision', ''), key])
        row = self.db.execute('SELECT payload FROM cache WHERE key=?', (key,)).fetchone()
        if row:
            return json.loads(row[0])
        value = operation()
        with self.db:
            self.db.execute('INSERT INTO cache VALUES (?,?)', (key, json.dumps(value)))
        return value

    def job(self, company_id, stage):
        row = self.db.execute('SELECT state FROM jobs WHERE company_id=? AND stage=?', (company_id, stage)).fetchone()
        return row[0] if row else None

    def finish(self, company_id, stage, state):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?)', (company_id, stage, state))

    def recheck(self, position, stage, reference):
        if stage not in ('hubspot', 'research', 'identity', 'dripify', 'email', 'evaluation') or not reference.strip():
            raise ValueError('Stage and new-evidence reference required')
        company = next((c for c in self.companies() if c['position'] == position), None)
        if company is None:
            raise ValueError('Unknown company position')
        revision = digest([reference, now()])
        self.set_meta('revision:' + company['id'] + ':' + stage, revision)
        with self.db:
            self.db.execute('DELETE FROM jobs WHERE company_id=? AND stage=?', (company['id'], stage))
            self.db.execute('DELETE FROM jobs WHERE company_id=? AND stage=?', (company['id'], 'evaluation'))
            self.db.execute('UPDATE batches SET reviewed=0, review_ref=NULL WHERE id=?', (company['batch'],))
        self.observe(company['id'], '', 'recheck-request', 'pending',
                     {'reason': 'EXPLICIT_RECHECK_WITH_NEW_EVIDENCE', 'reference': reference, 'stage_requested': stage})

    def permit_batch(self, batch):
        if not self.companies(batch):
            raise ValueError('Unknown batch')
        previous = self.db.execute('SELECT id FROM batches WHERE id<? AND reviewed=0', (batch,)).fetchone()
        if previous:
            raise ValueError('Previous batch needs documented research review')

    def review_batch(self, batch, reference):
        companies = self.companies(batch)
        if not companies or not reference.strip():
            raise ValueError('Batch and actual review evidence required')
        stages = ('research', 'hubspot', 'identity', 'dripify', 'email', 'evaluation')
        if any(not self.job(c['id'], stage) for c in companies for stage in stages):
            raise ValueError('Run all research stages before review')
        with self.db:
            self.db.execute('UPDATE batches SET reviewed=1, review_ref=? WHERE id=?', (reference, batch))
