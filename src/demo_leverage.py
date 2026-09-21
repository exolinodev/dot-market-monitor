"""Normalize acquired leverage settings without inferring unreported margin rules."""
import re

from cycles import iso
from exchange_reconciliation import exchange_time, numeric, number
from kraken_execution import ExecutionError


def preference(payload, symbol='PF_DOTUSD'):
    if payload.get('result') != 'success' or not isinstance(payload.get('leveragePreferences'), list):
        raise ExecutionError('Invalid leverage preference response')
    settings = {}
    for row in payload['leveragePreferences']:
        if not isinstance(row, dict) or not isinstance(row.get('symbol'), str):
            raise ExecutionError('Leverage preference requires instrument identity')
        name = row['symbol'].upper()
        if not re.fullmatch(r'[A-Z0-9_]+', name) or name in settings:
            raise ExecutionError('Invalid or duplicate leverage preference instrument')
        value = row.get('maxLeverage')
        settings[name] = {'max_leverage_present': 'maxLeverage' in row,
                          'max_leverage': None if value is None else number(numeric(value, positive=True))}
    return {'symbol': symbol, 'asof_utc': iso(exchange_time(payload['serverTime'])),
            'configured': symbol in settings,
            **settings.get(symbol, {'max_leverage_present': False, 'max_leverage': None})}
