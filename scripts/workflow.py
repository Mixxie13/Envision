"""One-upload, reviewed-batch orchestration. Offline by default; never sends or writes CRM."""
import argparse
import csv
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from sourcing_state import State, digest, read_master, project_lock
from master_progress import snapshot, write_reports, atomic_text
from candidate_handoff import adopt_existing, ingest, refresh
from workbook import update, validate
from preflight import check
from email_workflow import EmailOperations
from eligibility import timestamp
from datetime import datetime, timezone

HEADERS = {
    'company': ('Brand', 'Company', 'Company Name'),
    'priority': ('Priority', 'Priority / Original Row Position', 'Upload Priority/Wave'),
    'category': ('Category',), 'price_tier': ('Price Tier',),
    'website': ('Website', 'Official Website'), 'hubspot_company_id': ('HubSpot Record',),
}


def infer_mapping(path, sheet=None):
    if Path(path).suffix.lower() == '.csv':
        with Path(path).open(encoding='utf-8-sig', newline='') as stream: headers = next(csv.reader(stream))
    else:
        from openpyxl import load_workbook
        b = load_workbook(path, read_only=True, data_only=False)
        try:
            if sheet is None and len(b.sheetnames) != 1: raise ValueError('Source worksheet required')
            ws = b[sheet or b.sheetnames[0]]; ws.reset_dimensions()
            headers = list(next(ws.values))
        finally: b.close()
    mapping = {}
    for field, options in HEADERS.items():
        matched = [name for name in options if name in headers]
        if len(matched) > 1: raise ValueError('Ambiguous headers require an explicit mapping')
        if matched: mapping[field] = matched[0]
    if 'company' not in mapping: raise ValueError('Company mapping required')
    return mapping


def intake(project, master, mapping=None, sheet=None, suppression_project=None):
    """Create a complete local project with a single directory publication."""
    project, master = Path(project).absolute(), Path(master)
    raw = master.read_bytes(); source_hash = hashlib.sha256(raw).hexdigest()
    project.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.envision-intake-', dir=project.parent))
    try:
        upload = stage / ('upload' + master.suffix.lower()); upload.write_bytes(raw); upload.chmod(0o600)
        mapping = mapping or infer_mapping(upload, sheet)
        rows = read_master(upload, mapping, sheet)
        if project.exists():
            s = State(project, readonly=True)
            try:
                if s.meta('source_hash') != source_hash or s.meta('master_signature') != digest(rows):
                    raise ValueError('Never replace an existing master or mapping')
                return {'imported': False, 'companies': len(rows), 'preserved': True, 'processing_started': False}
            finally: s.close()
        inherited = None
        suppression_project = suppression_project or os.environ.get('ENVISION_SUPPRESSION_PROJECT')
        if suppression_project:
            source_state = State(suppression_project, readonly=True)
            try: inherited = source_state.meta('offline-email-suppression-v1')
            finally: source_state.close()
            if not inherited or not inherited.get('entries'):raise ValueError('Approved private suppression registry required')
        prepared = stage/'project'; s = State(prepared)
        try:
            s.initialize(rows, source_hash); s.archive_source(upload)
            if inherited:
                s.set_meta('offline-email-suppression-v1',inherited)
                s.set_meta('suppression_inheritance_ref',str(Path(suppression_project).absolute()))
            archive_name = Path(s.meta('source_archive')).name
            s.directory = project  # Stable report/evidence references before publication.
            update(s, prepared/'research.xlsx')
            write_reports(s, output_directory=prepared)
            s.set_meta('source_archive', str(project/archive_name))
            s.set_meta('workflow_workbook', str(project/'research.xlsx'))
        finally: s.close()
        if project.exists(): raise ValueError('Project appeared concurrently; refusing replacement')
        # os.rename cannot overwrite a populated existing project; cooperating callers use distinct project paths.
        os.rename(prepared, project)
        return {'imported': True, 'companies': len(rows), 'batches': (len(rows)+9)//10,
                'workbook': str(project/'research.xlsx'), 'processing_started': False}
    finally: shutil.rmtree(stage)


def company_reviews(state, records, clock=None):
    """Intake reviewed internal company evidence; never invent provider fields."""
    clock = clock or datetime.now(timezone.utc)
    from research import public_url
    from employment import normalized
    from urllib.parse import urlparse
    companies={c['id']:c for c in state.companies()}
    if not isinstance(records,list) or any(not isinstance(r,dict) for r in records):raise ValueError('Company evidence list required')
    if len({r.get('company_row_id') for r in records})!=len(records):raise ValueError('One reviewed record per original company row required')
    planned=[]
    for row in records:
        company=companies.get(row.get('company_row_id'))
        if not company or row.get('company') != company['data']['company']: raise ValueError('Exact company source row required')
        if row.get('schema') != 'envision.company-review.v1' or row.get('reviewed_original') is not True:
            raise ValueError('Reviewed internal company evidence required')
        if not public_url(row.get('source_url')) or not row.get('source_sha256') or not row.get('evidence_ref') or not row.get('policy_ref'):
            raise ValueError('Company source provenance incomplete')
        if not timestamp(row.get('observed_at')) <= clock < timestamp(row.get('expires_at')):
            raise ValueError('Current company evidence with approved expiry required')
        domain=row.get('employee_domain')
        if not isinstance(domain,str) or not domain or not public_url('https://'+domain) or urlparse('https://'+domain).hostname != domain:
            raise ValueError('Exact employee domain required')
        if not row.get('quote') or normalized(company['data']['company']) not in normalized(row['quote']):
            raise ValueError('Company attribution missing')
        supplied=company['data']['mapped'].get('website') or ''
        expected=(urlparse(supplied).hostname or '').removeprefix('www.')
        if expected and expected != domain and not row.get('parent_routing_evidence_ref'):
            raise ValueError('Parent/different employee domain requires routing evidence')
        previous=state.meta('reviewed_company:' + company['id'])
        conflicting=bool(state.meta('company_review_conflict:'+company['id'],False) or
            (previous and any(previous.get(k)!=row.get(k) for k in ('employee_domain','parent_operator'))))
        planned.append((company,row,conflicting))
    for company,row,conflicting in planned:
        state.set_meta('reviewed_company:' + company['id'],row)
        if conflicting:state.set_meta('company_review_conflict:'+company['id'],True)
        state.set_meta('verified_company_domain:' + company['id'],{'verified':row.get('employee_domain_confirmed') is True and not conflicting,
            'domain':row['employee_domain'],'evidence_ref':row['evidence_ref'],'observed_at':row['observed_at'],'expires_at':row['expires_at']})
        state.observe(company['id'],'','company-review','pending',{'reason':'COMPANY_REVIEW_CONFLICT_REQUIRES_REVIEW' if conflicting else 'REVIEWED_COMPANY_EVIDENCE_NOT_OUTREACH_PERMISSION',
            'source_url':row['source_url'],'observed_at':row['observed_at'],'evidence':row})
    return {'records':len(planned),'campaign_ready':False}


def summary(state, clock=None):
    progress = snapshot(state, clock)
    from screening import project as screen
    companies = {c['id']: c for c in state.companies()}
    people = state.contacts()
    statuses = [screen(c['data'], c['id'], companies[c['company_id']]['data']['company'], clock) for c in people]
    facts = [f for c in people for f in c['data'].get('handoff', {}).get('email_operations', [])]
    return {'companies_received': len(companies), 'original_rows_accounted_for': progress['all_original_rows_accounted_for'],
            'company_order': [c['data']['company'] for c in state.companies()],
            'candidate_evidence_counts': progress['candidate_evidence_counts'],
            'screening_counts': {value: sum(s['decision'] == value for s in statuses) for value in ('blocked','pending','eligible_for_review')},
            'provider_email_facts': {value: sum(f['result'] == value for f in facts) for value in ('candidate','valid','invalid','unknown','catch_all','failed')},
            'suppressed_addresses': len({a for e in state.meta('offline-email-suppression-v1', {}).get('entries', []) for a in [e.get('email'), *e.get('email_aliases', [])] if a}),
            'known_native_bounce_records': sum('HISTORICAL_HARD_BOUNCE' in c['data'].get('_suppression_projection', {}).get('native_risks', c['data'].get('risks', [])) for c in people),
            'campaign_ready': sum(v.get('campaign_ready') is True for v in statuses), 'authorized_to_send': False,
            'note': 'Provider facts, qualified people, deliverability and outreach permission are separate dimensions'}


class ReadBudget:
    """Durable actual-dispatch reservations across pages and process restarts."""
    def __init__(self, state, authorization):
        self.state, self.auth = state, authorization
        self.key = 'workflow-read-ledger:' + digest(authorization['evidence_ref'])

    def reserve(self, provider):
        if timestamp(self.auth.get('expires_at')) <= datetime.now(timezone.utc):raise ValueError('Read authorization expired')
        limit = self.auth.get('max_requests', {}).get(provider)
        if type(limit) is not int or limit < 1: raise ValueError('Explicit read-request cap required')
        ledger = self.state.meta(self.key, {})
        used = ledger.get(provider, 0)
        if used >= limit: raise ValueError('Authorized read budget exhausted')
        ledger[provider] = used+1; self.state.set_meta(self.key, ledger)


def _authorized_readers(state, batch, requested):
    auth = state.meta('workflow_operation_authorization', {})
    try:
        valid = (auth.get('approved') is True and auth.get('evidence_ref') and batch in auth.get('batches', []) and
                 set(requested) <= set(auth.get('operations', [])) and timestamp(auth.get('expires_at')) > datetime.now(timezone.utc))
    except (ValueError,TypeError): valid=False
    if not valid: raise ValueError('Separate scoped live authorization required')
    for name in requested - {'email'}:
        if type(auth.get('max_requests', {}).get(name)) is not int or auth['max_requests'][name]<1:
            raise ValueError('Explicit per-provider request caps required')
    from provider_evidence import Providers
    from hubspot import Client, ReadError
    from dripify_evidence import ReadFailure
    from apollo import ApolloError
    budget=ReadBudget(state,auth)
    def hubspot_budget():
        try:budget.reserve('hubspot')
        except ValueError:raise ReadError('AUTHORIZED_READ_BUDGET_EXHAUSTED') from None
    def dripify_budget():
        try:budget.reserve('dripify')
        except ValueError:raise ReadFailure('AUTHORIZED_READ_BUDGET_EXHAUSTED') from None
    def apollo_budget():
        try:budget.reserve('apollo')
        except ValueError:raise ApolloError('AUTHORIZED_READ_BUDGET_EXHAUSTED') from None
    if 'apollo' in requested:
        capability=next(p for p in check(state,batch)['providers'] if p['provider']=='apollo')
        if not capability['capability_currently_verified']: raise ValueError('Resolve Apollo access before batch discovery')
    providers=Providers(state,live=('hubspot' in requested or 'dripify' in requested),apollo_enabled='apollo' in requested)
    if 'hubspot' in requested:providers._client=Client(before_request=hubspot_budget)
    else:
        # Do not enable unrelated service reads from a broad live flag.
        providers.hubspot=lambda c:{'state':'pending','reason':'HUBSPOT_NOT_AUTHORIZED'}
        providers.identity=lambda c:{'state':'pending','reason':'HUBSPOT_NOT_AUTHORIZED','contacts':[]}
    if 'dripify' in requested:providers.dripify_collector.before_request=dripify_budget
    else:providers.dripify=lambda c:{'state':'pending','reason':'DRIPIFY_NOT_AUTHORIZED','contacts':[]}
    providers.apollo_client.before_request=apollo_budget
    if 'apollo' in requested:
        providers.apollo_client.remaining=min(auth['max_requests']['apollo'],10)
        providers.apollo_client.max_pages=1
    def public_fetch(url):
        try:budget.reserve('public_web')
        except ValueError:return {'state':'pending','reason':'AUTHORIZED_PUBLIC_REQUEST_BUDGET_EXHAUSTED'}
        from research import PublicResearch
        return PublicResearch._fetch(url)
    return providers,public_fetch


def advance(state, workbook, candidates=None, providers=None, researcher=None, public_web=False, clock=None, live_operations=(), email_operations=None):
    """Run/resume the next reviewed batch. Never advances past missing batch review."""
    batches = [dict(r) for r in state.db.execute('SELECT * FROM batches ORDER BY id')]
    batch = next((b['id'] for b in batches if not b['reviewed']), None)
    if batch is None: return {'state': 'all_batches_research_reviewed', 'summary': summary(state, clock), 'campaign_ready': False}
    state.permit_batch(batch)
    public_fetch=None
    if live_operations:
        providers,public_fetch=_authorized_readers(state,batch,set(live_operations))
        public_web='public_web' in live_operations
    email_operations=email_operations or EmailOperations(state, enabled='email' in live_operations)
    companies = state.companies(batch)
    if candidates is not None:
        from candidate_handoff import validate_rows
        validate_rows(state,candidates,batch)
    for company in companies: adopt_existing(state, company['id'], clock)
    if candidates: ingest(state, candidates, batch=batch, clock=clock)
    from source_batch import run
    if providers is None:
        from provider_evidence import Providers
        providers=Providers(state,live=False)
    report = run(state, batch, workbook, providers=providers, researcher=researcher, public_web=public_web, email_runner=email_operations, public_fetch=public_fetch,clock=clock)
    # Cached checkpoints never prevent a fresh, offline evidence/eligibility assessment.
    for company in companies:
        adopt_existing(state, company['id'], clock)
        from candidate_handoff import provider_review
        for name in ('identity','dripify'):
            evidence=getattr(providers,name)(company)
            provider_review(state,company['id'],evidence,'hubspot' if name=='identity' else 'dripify',clock)
        refresh(state, clock, company['id'])
        disabled = email_operations.collect(company)
        state.observe(company['id'], '', 'email-workflow', 'pending', disabled)
    update(state, workbook, clock); write_reports(state)
    result = {'state': 'batch_review_required', 'batch': batch, 'batch_report': report,
              'preflight': check(state, batch, clock=clock), 'summary': summary(state, clock),
              'next_action': 'Lead Sourcing Owner reviews this batch; Bridget final approval remains separate',
              'live_operations_enabled': list(live_operations), 'email_attempts': email_operations.attempts}
    atomic_text(state.directory/'workflow-status.json', json.dumps(result, indent=2))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--project', required=True)
    sub = p.add_subparsers(dest='command', required=True)
    take=sub.add_parser('intake'); take.add_argument('--master', required=True);take.add_argument('--mapping');take.add_argument('--sheet');take.add_argument('--suppression-project')
    run=sub.add_parser('next');run.add_argument('--workbook');run.add_argument('--candidates');run.add_argument('--live', action='append', choices=('hubspot','dripify','apollo','public_web','email'), default=[])
    sub.add_parser('preflight');sub.add_parser('status')
    comp=sub.add_parser('company-evidence');comp.add_argument('--input',required=True)
    review=sub.add_parser('review');review.add_argument('--batch',type=int,required=True);review.add_argument('--evidence-ref',required=True)
    args=p.parse_args()
    try:
        if args.command=='intake':
            mapping=json.loads(Path(args.mapping).read_text()) if args.mapping else None
            print(json.dumps(intake(args.project,args.master,mapping,args.sheet,args.suppression_project)));return
        if args.command in ('preflight','status'):
            s=State(args.project,readonly=True)
            try:result=check(s) if args.command=='preflight' else summary(s)
            finally:s.close()
        else:
            with project_lock(args.project):
                s=State(args.project)
                try:
                    if args.command=='company-evidence':
                        result=company_reviews(s,json.loads(Path(args.input).read_text()))
                        workbook=s.meta('workflow_workbook')
                        if workbook:update(s,workbook)
                        write_reports(s)
                    elif args.command=='review':
                        s.review_batch(args.batch,args.evidence_ref);write_reports(s)
                        result={'review_recorded':args.batch,'bridget_approval_granted':False,'outreach_ready':False}
                    else:
                        workbook=args.workbook or s.meta('workflow_workbook')
                        if not workbook:raise ValueError('Existing workbook path required')
                        candidates=json.loads(Path(args.candidates).read_text()) if args.candidates else None
                        result=advance(s,workbook,candidates,live_operations=args.live)
                finally:s.close()
        print(json.dumps(result))
    except (OSError,ValueError,KeyError,TypeError):
        p.exit(2,'Workflow validation failed; inspect source mapping, project integrity and recorded review. No provider action authorized.\n')


if __name__=='__main__':main()
