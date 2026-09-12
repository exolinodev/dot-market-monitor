import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest
import requests
from common import freshness, PublicHTTP
from kraken import KrakenClient
from coingecko import CoinGeckoClient

FIXTURES=Path(__file__).parent/'fixtures'


def fixture(name):
    return json.loads((FIXTURES/f'{name}.json').read_text())


@pytest.mark.parametrize('file,method',[('spot_ticker',lambda c:c.spot_ticker('DOTUSD')),
    ('spot_ohlc',lambda c:c.ohlc('DOTUSD',60)),('spot_depth',lambda c:c.order_book('DOTUSD')),
    ('spot_trades',lambda c:c.recent_trades('DOTUSD')),('spot_spread',lambda c:c.spread('DOTUSD')),
    ('futures_ticker',lambda c:c.futures_ticker()),('futures_book',lambda c:c.futures_orderbook()),
    ('futures_instruments',lambda c:c.futures_instrument())])
def test_real_public_response_fixtures(file,method):
    client=KrakenClient()
    client._get_json=lambda *args,**kwargs:fixture(file)
    value=method(client)
    assert value is not None
    assert len(value)>0


def test_real_futures_trade_side_and_stable_uid():
    payload=fixture('futures_trades')
    frame=KrakenClient.parse_futures_trades(payload)
    row=payload['history'][0]
    parsed=frame[frame.trade_id==row['uid']].iloc[0]
    assert parsed.volume==row['size']
    assert parsed.side=={'buy':'b','sell':'s'}[row['side']]
    assert parsed.price==row['price']


def test_optional_futures_field_missing_stays_null():
    data=fixture('futures_ticker')
    del data['ticker']['fundingRatePrediction']
    client=KrakenClient()
    client._get_json=lambda *a,**k:data
    out=client.futures_ticker()
    assert out['funding_rate_prediction'] is None
    assert out['field_status']['funding_rate_prediction']=='unavailable'
    data['ticker']['symbol']='WRONG'
    with pytest.raises(Exception): client.futures_ticker()


def test_malformed_ohlc_and_unknown_side_rejected():
    data=fixture('spot_ohlc')
    key=next(k for k in data['result'] if k!='last')
    data['result'][key][0][4]='NaN'
    client=KrakenClient()
    client._get_json=lambda *a,**k:data
    with pytest.raises(ValueError): client.ohlc('DOTUSD',60)
    trades=fixture('spot_trades')
    key=next(k for k in trades['result'] if k!='last')
    trades['result'][key][0][3]='unknown'
    with pytest.raises(ValueError): KrakenClient.parse_spot_trades(trades)


def test_coingecko_real_fields_and_total3():
    client=CoinGeckoClient()
    client._get=lambda path,*args:fixture('coingecko_global' if path=='/global' else 'coingecko_markets')
    value=client.global_market()
    assert value['total3_proxy_usd']==pytest.approx(value['total_market_cap_usd']-value['btc_market_cap_proxy_usd']-value['eth_market_cap_proxy_usd'])
    breadth=client.breadth()
    assert set(breadth['coins'])=={'ETH','BNB','XRP','SOL','DOGE','ADA','LINK','AVAX','DOT'}
    assert all(breadth['windows'][k]['sample_count']==8 for k in ['1h','24h','7d'])


def test_freshness_missing_stale_future_and_boundary():
    now=datetime(2026,9,12,tzinfo=timezone.utc)
    assert freshness(now-timedelta(seconds=120),now,120)['fresh']
    assert not freshness(now-timedelta(seconds=121),now,120)['fresh']
    assert freshness(now+timedelta(seconds=10),now,120)['status']=='invalid'
    assert not freshness(None,now,120)['fresh']
    assert not freshness('2026-09-12',now,120)['fresh']


class Response:
    def __init__(self,status,payload,headers=None):
        self.status_code,self.payload,self.headers=status,payload,headers or {}
    def raise_for_status(self):
        if self.status_code>=400: raise requests.HTTPError('failure',response=self)
    def json(self): return self.payload


class Session:
    def __init__(self,responses): self.responses=iter(responses); self.headers={}; self.calls=0
    def get(self,*args,**kwargs): self.calls+=1; return next(self.responses)


def test_http_rate_limit_retry_after_and_semantic_retry(monkeypatch):
    sleeps=[]
    monkeypatch.setattr('common.time.sleep',sleeps.append)
    session=Session([Response(429,{}, {'Retry-After':'2'}),Response(200,{'error':['EAPI:Rate limit exceeded']}),Response(200,{'error':[],'result':{}})])
    client=PublicHTTP(session=session,delay=0)
    assert client.get('https://example.test/api')['error']==[]
    assert session.calls==3
    assert sleeps==[2,3]
    assert list(client.records.values())[0]['attempts']==3


def test_nonretryable_404_is_bounded(monkeypatch):
    monkeypatch.setattr('common.time.sleep',lambda x:None)
    session=Session([Response(404,{})])
    client=PublicHTTP(session=session,delay=0)
    with pytest.raises(RuntimeError):client.get('https://example.test/api')
    assert session.calls==1
    assert list(client.records.values())[0]['status']=='unavailable'


def test_spot_pagination_deduplicates_and_marks_complete():
    payload=fixture('spot_trades')
    client=KrakenClient()
    calls=[]
    def get(url,params):
        calls.append(params)
        if len(calls)==1:return payload
        # Second page shares its boundary trade, then ends.
        reduced=json.loads(json.dumps(payload))
        key=next(k for k in reduced['result'] if k!='last')
        reduced['result'][key]=reduced['result'][key][-1:]
        return reduced
    client._get_json=get
    import pandas as pd
    now=pd.Timestamp('2026-09-12T18:00:00Z')
    tape=client.trade_tape('DOTUSD',now)
    assert not tape.trade_id.duplicated().any()
    assert tape.attrs['pagination_complete']
    assert calls[1]['since']==str(payload['result']['last'])


def test_futures_pagination_uses_last_time_and_marks_cap():
    client=KrakenClient()
    data=fixture('futures_trades')
    calls=[]
    def get(url,params): calls.append(params.copy());return data
    client._get_json=get
    import pandas as pd
    tape=client.futures_trade_tape(pd.Timestamp('2026-09-12T18:00:00Z'),max_pages=2)
    assert 'lastTime' in calls[1]
    assert not tape.attrs['pagination_complete']
    assert not tape.trade_id.duplicated().any()


def test_circuit_breaker_bounds_a_host_outage(monkeypatch):
    monkeypatch.setattr('common.time.sleep',lambda x:None)
    class TimeoutSession:
        headers={}
        calls=0
        def get(self,*a,**kw):
            self.calls+=1
            raise requests.Timeout('offline')
    session=TimeoutSession()
    client=PublicHTTP(session=session,delay=0)
    for i in range(4):
        with pytest.raises(RuntimeError):client.get('https://example.test/'+str(i))
    assert session.calls==9
    assert list(client.records.values())[-1]['attempts']==0
