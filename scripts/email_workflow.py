"""Opt-in, durable relay email operations. No request without verified contracts/budget.

Provider facts are not campaign permission. Uncertain dispatches are never retried.
"""
from datetime import datetime, timezone
from copy import deepcopy

from sourcing_state import digest
from eligibility import timestamp
from screening import email

OPERATIONS = {'findymail.find', 'findymail.verify', 'neverbounce.verify'}


def _value(data, path):
    for key in path:
        if not isinstance(key, str) or not isinstance(data, dict) or key not in data:
            raise ValueError('Response contract mismatch')
        data = data[key]
    return data


class EmailOperations:
    def __init__(self, state, enabled=False, transport=None, clock=None):
        self.state, self.enabled = state, enabled
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.attempts = 0

    def operation(self, company, contact, operation, fields):
        pending = {'state': 'pending', 'operation': operation, 'campaign_ready': False}
        if not self.enabled: return {**pending, 'reason': 'EMAIL_OPERATIONS_DISABLED'}
        if operation not in OPERATIONS: return {**pending, 'reason': 'UNSUPPORTED_OPERATION'}
        auth = self.state.meta('email_operation_authorization', {})
        contracts = self.state.meta('email_response_contracts', {})
        contract = contracts.get(operation, {})
        try:
            valid_auth = (auth.get('approved') is True and auth.get('evidence_ref') and auth.get('pricing_ref') and
                          auth.get('maximum_cost_verified') is True and timestamp(auth.get('expires_at')) > self.clock() and
                          contact['id'] in auth.get('subject_ids', []) and company['batch'] in auth.get('batches', []))
        except (ValueError, TypeError, AttributeError): valid_auth = False
        if not valid_auth: return {**pending, 'reason': 'EMAIL_BUDGET_OR_SCOPE_NOT_AUTHORIZED'}
        if not self.state.meta('offline-email-suppression-v1', {}).get('entries'):
            return {**pending, 'reason': 'PRIVATE_SUPPRESSION_REGISTRY_REQUIRED'}
        if not (contract.get('verified') is True and contract.get('review_ref') and
                isinstance(contract.get('result_path'), list) and bool(contract['result_path']) and all(isinstance(v,str) for v in contract['result_path']) and isinstance(contract.get('result_map'), dict) and
                (operation != 'findymail.find' or (isinstance(contract.get('email_path'), list) and bool(contract['email_path']) and all(isinstance(v,str) for v in contract['email_path'])))):
            return {**pending, 'reason': 'PROVIDER_RESPONSE_CONTRACT_UNVERIFIED'}
        from candidate_handoff import review
        current = next((c for c in self.state.contacts(company['id']) if c['id'] == contact['id']), None)
        if current is None: return {**pending, 'reason': 'CURRENT_SUBJECT_RECORD_UNAVAILABLE'}
        contact = current
        person = contact['data']
        if not person.get('handoff'): return {**pending, 'reason': 'PER_PERSON_HANDOFF_REQUIRED'}
        view = review(person, contact['id'], company['data']['company'], self.clock())
        if person.get('blocked') or not view['confirmed_current_employment']:
            return {**pending, 'reason': 'PERSON_SUPPRESSED_OR_CURRENT_EMPLOYMENT_UNCONFIRMED'}
        binding = self.state.meta('verified_company_domain:' + company['id'], {})
        try:
            valid_binding = (binding.get('verified') is True and binding.get('domain') and binding.get('evidence_ref') and
                timestamp(binding.get('observed_at')) <= self.clock() < timestamp(binding.get('expires_at')))
        except (ValueError,TypeError): valid_binding=False
        if not valid_binding:
            return {**pending, 'reason': 'EMPLOYEE_DOMAIN_EVIDENCE_REQUIRED'}
        if operation == 'findymail.find':
            expected = {'name': person['name'], 'domain': binding['domain']}
        else:
            expected = {'email': email(person.get('email'))}
            if not expected['email'] or not view['person_linked_email_candidate']:
                return {**pending, 'reason': 'PERSON_LINKED_SELECTED_EMAIL_REQUIRED'}
        if fields != expected: return {**pending, 'reason': 'REQUEST_SUBJECT_OR_DOMAIN_MISMATCH'}
        cost = auth.get('credits_per_operation', {}).get(operation)
        caps = (auth.get('max_requests'), auth.get('max_credits'), cost)
        if any(type(n) is not int or n < 0 for n in caps) or not caps[0] or not cost:
            return {**pending, 'reason': 'EXPLICIT_VERIFIED_CREDIT_CAP_REQUIRED'}
        ledger_key = 'email-operation-ledger:' + digest(auth['evidence_ref'])
        ledger = self.state.meta(ledger_key, {'requests_reserved': 0, 'credits_reserved': 0, 'entries': {}})
        key = digest([operation, contact['id'], fields])
        if key in ledger['entries']:
            entry = ledger['entries'][key]
            if entry.get('result', {}).get('fact'): self._record(company, contact, entry['result']['fact'])
            return {**pending, **entry.get('result', {}), 'reused': True,
                    'reason': entry.get('result', {}).get('reason', 'UNCERTAIN_DISPATCH_NO_AUTOMATIC_RETRY')}
        if ledger.get('stopped') or ledger['requests_reserved'] >= caps[0] or ledger['credits_reserved'] + cost > caps[1]:
            return {**pending, 'reason': 'AUTHORIZED_BUDGET_EXHAUSTED_OR_PROVIDER_PAUSED'}
        if self.transport is None:
            import os
            if not (os.environ.get('RELAY_TOKEN') or '').strip():
                return {**pending, 'reason': 'RELAY_CREDENTIAL_NOT_CONFIGURED'}
        # Commit the reservation BEFORE any dispatch. Crashes cannot repeat a paid call.
        ledger['requests_reserved'] += 1; ledger['credits_reserved'] += cost
        ledger['entries'][key] = {'state': 'reserved', 'operation': operation, 'subject_id': contact['id'],
                                  'reserved_at': self.clock().isoformat(), 'credits_reserved': cost}
        self.state.set_meta(ledger_key, ledger)
        self.attempts += 1
        try:
            transport = self.transport
            if transport is None:
                from email_api import request
                transport = request
            service, action = operation.split('.')
            body = transport(service, action, **fields)['data']
            raw = _value(body, contract['result_path'])
            if not isinstance(raw, str): raise ValueError('Provider result is not a string')
            result = contract['result_map'].get(raw)
            if result not in ('candidate', 'valid', 'invalid', 'unknown', 'catch_all', 'failed'):
                raise ValueError('Unknown provider result')
            # Known risk words cannot be mapped to a positive result accidentally.
            lowered = raw.strip().casefold().replace('-', '_').replace(' ', '_')
            if lowered in ('invalid', 'unknown', 'catch_all', 'catchall', 'failed', 'error') and result in ('valid', 'candidate'):
                raise ValueError('Unsafe status mapping')
            address = _value(body, contract['email_path']) if operation == 'findymail.find' else fields['email']
            address = email(address)
            if not address or any(c.isspace() for c in address): raise ValueError('Missing exact address')
            fact = {'operation': operation, 'provider_result': raw, 'result': result, 'email': address,
                    'subject_id': contact['id'], 'company_row_id': company['id'], 'person_name': person['name'],
                    'observed_at': self.clock().isoformat(), 'source_ref': ledger_key + ':' + key,
                    'contract_ref': contract['review_ref'], 'policy_ref': contract.get('policy_ref'),
                    'expires_at': contract.get('expires_at'), 'outreach_ready': False}
            output = {**pending, 'reason': 'PROVIDER_FACT_RECORDED_NOT_CLEARANCE', 'fact': fact}
            ledger['entries'][key].update(state='completed', result=output)
        except Exception:
            # Deliberately no retries, arbitrary exception text or raw response persistence.
            ledger['stopped'] = True
            output = {**pending, 'reason': 'PROVIDER_ERROR_OR_UNVERIFIED_RESPONSE_NO_RETRY'}
            ledger['entries'][key].update(state='uncertain', result=output)
        self.state.set_meta(ledger_key, ledger)
        if 'fact' in output:
            self._record(company, contact, output['fact'])
        return output

    def _record(self, company, contact, fact):
        current = next(c['data'] for c in self.state.contacts(company['id']) if c['id'] == contact['id'])
        if (fact.get('subject_id') != contact['id'] or fact.get('company_row_id') != company['id'] or
                fact.get('person_name') != current['name']):
            self.state.observe(company['id'], contact['id'], 'email-operation', 'pending',
                {'reason':'HISTORICAL_EMAIL_FACT_IDENTITY_REVIEW_REQUIRED','evidence_record_id':contact['id']})
            return
        packet = current['handoff']; facts = packet.setdefault('email_operations', [])
        if not any(f['source_ref'] == fact['source_ref'] for f in facts): facts.append(deepcopy(fact))
        if fact['result'] in ('unknown', 'catch_all', 'failed'):
            current['risks'] = sorted(set(current.get('risks', [])) | {'UNRESOLVED_EMAIL_PROVIDER_RISK'})
        if fact['result'] == 'invalid' and fact['email'] == email(current.get('email')):
            subject = {'contact_id': contact['id'], 'company': company['data']['company'], 'full_name': current['name'],
                       'linkedin_url': current.get('linkedin_url') or '', 'email': current.get('email') or ''}
            import evidence
            envelope = {'schema': evidence.SCHEMA, 'check': 'verification',
                'identity': {'contact_id': contact['id'], 'basis': 'email', 'value': subject['email']},
                'provenance': {'source_ref': fact['source_ref'], 'capability_verified': True,
                               'coverage_ref': fact['contract_ref'], 'coverage_verified': False,
                               'pagination_complete': True, 'lookup_succeeded': True},
                'evidence': {'subject': subject, 'result': 'invalid', 'state': 'unresolved', 'complete': False,
                             'source': fact['source_ref'], 'observed_at': fact['observed_at'],
                             'expires_at': fact.get('expires_at'), 'policy_ref': fact.get('policy_ref')}}
            if not any(e.get('provenance', {}).get('source_ref') == fact['source_ref'] for e in packet['envelopes']):
                packet['envelopes'].append(envelope)
        self.state.contact(contact['id'], company['id'], current)
        self.state.observe(company['id'], contact['id'], 'email-operation', 'pending', {
            'name': current['name'], 'role': current['role'], 'linkedin_url': current.get('linkedin_url'),
            'reason': 'PROVIDER_FACT_REQUIRES_FULL_VERIFICATION_AND_ELIGIBILITY', 'evidence_record_id': contact['id'],
            'observed_at': fact['observed_at'], 'provider_fact': fact, 'source_url': fact['source_ref']})

    def replay(self, company):
        contacts = {c['id']: c for c in self.state.contacts(company['id']) if c['data'].get('handoff')}
        for row in self.state.db.execute("SELECT value FROM meta WHERE key LIKE 'email-operation-ledger:%'").fetchall():
            import json
            for entry in json.loads(row[0]).get('entries', {}).values():
                fact=entry.get('result', {}).get('fact')
                if fact and fact.get('subject_id') in contacts:
                    self._record(company,contacts[fact['subject_id']],fact)

    def collect(self, company):
        self.replay(company)
        outputs = []
        for contact in self.state.contacts(company['id']):
            if not contact['data'].get('handoff'): continue
            person = contact['data']; operation = 'neverbounce.verify' if person.get('email') else 'findymail.find'
            binding = self.state.meta('verified_company_domain:' + company['id'], {})
            fields = {'email': email(person.get('email'))} if person.get('email') else {'name': person['name'], 'domain': binding.get('domain')}
            outputs.append({'contact_id': contact['id'], **self.operation(company, contact, operation, fields)})
        return {'state': 'pending', 'reason': 'EMAIL_FACTS_NOT_OUTREACH_PERMISSION', 'contacts': outputs,
                'attempts': self.attempts, 'campaign_ready': False}
