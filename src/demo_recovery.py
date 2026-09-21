"""Select unique positive lifecycle proofs from acquired demo evidence only."""
from demo_journal import transition
from demo_position import acquired
from kraken_execution import ExecutionError
from ledger import digest


def recover_available(store):
    """Resolve supported lifecycles, with no HTTP, retries or absence inference.

    Candidate selection is not validation: every candidate must independently
    pass the existing journal validator and state transition before any append.
    Conflicting valid outcomes remain unresolved. Prepared intents require the
    explicit abandonment path; this function never consumes them automatically.
    """
    store._require_lock()
    ref = store.state['latest_capture']
    capture = acquired(store, ref)
    executions = store._read_artifact(capture['execution_history_sha256'])
    orders = store._read_artifact(capture['order_history_sha256'])
    triggers = store._read_artifact(capture['trigger_history_sha256'])
    resolved, issues = [], []
    for client_id in sorted(store.state['ownership']):
        state = store.state
        owner = state['ownership'][client_id]
        if owner['status'] == 'terminal':
            continue
        operations = {k: o for k, o in state['operations'].items()
                      if o['action']['params']['cliOrdId'] == client_id}
        if any(o['status'] == 'prepared' for o in operations.values()):
            issues.append({'client_id': client_id, 'reason': 'prepared_intent_requires_explicit_abandonment'})
            continue
        origin_id = owner['origin_operation_id']
        origin = operations[origin_id]
        exchange_id = owner.get('exchange_order_id')
        candidates = []

        def add(kind, **payload):
            candidates.append({'type': kind, 'payload': {'capture_sha256': ref, **payload}})

        if origin['status'] in ('unknown', 'acknowledged'):
            for row in capture['open_orders']:
                if row.get('cliOrdId') == client_id:
                    add('resolve_send', operation_id=origin_id, exchange_order_id=row['order_id'])
        identities = {f['order_id'] for f in executions['fills'] if f['cliOrdId'] == client_id}
        if exchange_id:
            identities.add(exchange_id)
        for ident in sorted(identities):
            add('order_filled', client_id=client_id, history_sha256=capture['execution_history_sha256'],
                exchange_order_id=ident)

        def linked(row):
            return row.get('client_id') == client_id or (exchange_id and row.get('order_id') == exchange_id)

        for event in orders['order_events']:
            if not linked(event.get('order', {})):
                continue
            if event['kind'] in ('OrderCancelled', 'OrderRejected'):
                add('order_terminal', client_id=client_id, history_sha256=capture['order_history_sha256'],
                    event_id=event['event_id'], exchange_order_id=event['order']['order_id'],
                    reason={'OrderCancelled':'cancelled', 'OrderRejected':'rejected'}[event['kind']])
            elif event['kind'] in ('OrderUpdated', 'OrderEditRejected'):
                for ident, operation in operations.items():
                    if operation['action']['endpoint'] == 'editorder' and operation['status'] in ('unknown', 'acknowledged'):
                        add('edit_resolved', operation_id=ident, history_sha256=capture['order_history_sha256'],
                            event_id=event['event_id'], outcome='edit_applied' if event['kind']=='OrderUpdated' else 'edit_rejected')
        for event in triggers['trigger_events']:
            if event['kind'] == 'OrderTriggerCancelled' and linked(event.get('order', {})):
                add('trigger_cancelled', client_id=client_id, history_sha256=capture['trigger_history_sha256'],
                    event_id=event['event_id'], exchange_order_id=event['order']['order_id'], reason='cancelled')
            elif event['kind'] in ('OrderTriggerUpdated', 'OrderTriggerEditRejected') and linked(event.get('order', {})):
                for ident, operation in operations.items():
                    if operation['action']['endpoint'] == 'editorder' and operation['status'] in ('unknown', 'acknowledged'):
                        add('edit_resolved', operation_id=ident, history_sha256=capture['trigger_history_sha256'],
                            event_id=event['event_id'], outcome='edit_applied' if event['kind']=='OrderTriggerUpdated' else 'edit_rejected')
        valid = {}
        for candidate in candidates:
            try:
                store._check_artifact(candidate)
                transition(state, candidate)
            except ExecutionError:
                continue
            valid[digest(candidate)] = candidate
        if len(valid) == 1:
            event = next(iter(valid.values()))
            saved = store.append(event['type'], event['payload'])
            resolved.append({'client_id': client_id, 'event_type': event['type'], 'event_sha256': saved['sha256']})
        elif len(valid) > 1:
            issues.append({'client_id': client_id, 'reason': 'conflicting_positive_proofs'})
        elif any(o['status'] != 'resolved' for o in operations.values()):
            issues.append({'client_id': client_id, 'reason': 'no_unique_positive_proof'})
    return {'capture_sha256': ref, 'resolved': resolved, 'issues': issues,
            'unresolved_operations': sorted(k for k, o in store.state['operations'].items() if o['status'] != 'resolved'),
            'authorizes_execution': False}
