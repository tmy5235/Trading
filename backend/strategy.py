"""Explicit, conservative long-only SMA paper strategy."""
STRATEGY_ID = 'sma-crossover-flat-v1'
RULES = ('For this symbol: when flat, buy the configured size on an upward SMA crossover; '
         'when holding shares, close the entire position on a downward SMA crossover; '
         'otherwise wait while flat or hold while invested. No other action is allowed.')
MAX_AGE_SECONDS = 60


def expected_action(snapshot):
    account=snapshot.get('account') or {}
    shares=account.get('shares')
    signal=(snapshot.get('sma') or {}).get('signal')
    if isinstance(shares,bool) or not isinstance(shares,int) or shares < 0 or signal not in (None,'up','down'):
        return None
    if shares == 0:
        return 'buy' if signal == 'up' else 'wait'
    return 'close' if signal == 'down' else 'hold'
