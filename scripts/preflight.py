"""Offline capability/precondition inventory. Does not read credential values or probe APIs."""
from datetime import datetime, timezone
from eligibility import timestamp

PROVIDERS = {
    'hubspot': ('HUBSPOT_ACCESS_TOKEN', 'Read client; contact/company/association reads previously observed; full suppression/history unknown'),
    'dripify': ('RELAY_TOKEN', 'PR #7 read routes; prior bounded history retrieval; all-owner/retention coverage unknown'),
    'apollo': ('APOLLO_API_KEY', 'People Search adapter disabled; prior HTTP 403; successful account schema not verified'),
    'tavily': ('TAVILY_API_KEY', 'Basic adapter paused; prior authorized searches did not establish qualified contact yield'),
    'findymail': ('RELAY_TOKEN', 'Relay helpers exist; person discovery/verification response contract and real-data yield unverified'),
    'neverbounce': ('RELAY_TOKEN', 'Relay helper exists; verification contract and current account capability unverified'),
}


def check(state, batch=None, inspect_environment=False, clock=None):
    clock = clock or datetime.now(timezone.utc)
    providers = []
    for name, (variable, note) in PROVIDERS.items():
        capability = state.meta('verified_provider_capability:' + name, {})
        valid = False
        try:
            valid = (capability.get('verified') is True and bool(capability.get('evidence_ref')) and
                     timestamp(capability.get('observed_at')) <= clock < timestamp(capability.get('expires_at')))
        except (ValueError, TypeError): pass
        configured = 'not_inspected'
        if inspect_environment:
            import os
            configured = bool(os.environ.get(variable))  # Presence only; never returned/logged.
        providers.append({'provider': name, 'implementation': note, 'capability_currently_verified': valid,
                          'credential_presence': configured, 'enabled_by_default': False,
                          'scope_and_coverage': 'pending', 'live_probe_performed': False})
    rows = state.companies(batch)
    issues = []
    if not rows: issues.append('COMPANY_ROWS_REQUIRED')
    if not state.meta('offline-email-suppression-v1', {}).get('entries'): issues.append('PRIVATE_SUPPRESSION_REGISTRY_NOT_LOADED')
    if not state.meta('email_operation_authorization', {}).get('approved'): issues.append('PAID_EMAIL_OPERATIONS_NOT_AUTHORIZED')
    if not state.meta('email_response_contracts'): issues.append('EMAIL_RESPONSE_CONTRACTS_UNVERIFIED')
    for provider in ('apollo', 'hubspot', 'dripify'):
        if not next(p['capability_currently_verified'] for p in providers if p['provider'] == provider):
            issues.append(provider.upper() + '_CURRENT_CAPABILITY_OR_COVERAGE_UNVERIFIED')
    issues += ['SUBSCRIPTION_SEQUENCE_AND_OWNER_HISTORY_PENDING', 'FRESHNESS_RECONTACT_AND_CROSS_CHANNEL_POLICY_PENDING']
    return {'schema': 'envision.offline-preflight.v1', 'providers': providers,
            'batch': batch, 'company_rows': len(rows), 'blockers': sorted(set(issues)),
            'external_calls': 0, 'campaign_ready': False,
            'live_readiness': 'blocked' if issues else 'requires_separate_authorization'}
