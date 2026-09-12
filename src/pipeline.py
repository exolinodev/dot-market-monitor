"""Fault-isolated collection and deterministic snapshot assembly."""
from __future__ import annotations
from datetime import timezone
from pathlib import Path
import time
import pandas as pd
from common import utcnow, freshness, finite, json_safe, write_json
from kraken import KrakenClient, SPOT_BASE, FUTURES_BASE
from coingecko import CoinGeckoClient
from indicators import IndicatorConfig, snapshot
from timeframes import DOT_TIMEFRAMES, DOTBTC_TIMEFRAMES, BTC_TIMEFRAMES, BASE, CandleCache, resample_candles, split_modes
from analytics import closed_returns, realized_volatility, correlation_beta, breadth_statistics
from orderflow import orderbook_metrics, tape_metrics, absorption, wall_persistence
from history import HistoryStore
from structure import fib_levels, level_distances


def unavailable(reason='source_unavailable'):
    return {'status':'unavailable','value':None,'reason':reason}


class Collector:
    def __init__(self, data_dir, kraken=None, cg=None):
        self.data_dir=Path(data_dir)
        self.kraken=kraken or KrakenClient(timeout=12)
        self.cg=cg or CoinGeckoClient()
        self.sources={}
        self.errors=[]
        self.frames={}
        self.started=utcnow()
        self.timer=time.monotonic()
        try:
            self.cache=CandleCache(self.data_dir/'raw'/'ohlc_cache.json.gz')
        except Exception as exc:
            self.errors.append({'source_id':'ohlc_cache','error':str(exc)})
            self.cache=CandleCache(self.data_dir/'raw'/'new_cache.json.gz')
            self.cache.path=self.data_dir/'raw'/'ohlc_cache.json.gz'
        try:
            self.history=HistoryStore(self.data_dir/'history.json')
        except Exception as exc:
            self.errors.append({'source_id':'history','error':str(exc)})
            self.history=HistoryStore(self.data_dir/'new_history.json')
            self.history.path=self.data_dir/'history.json'

    def source(self, key, url, function, timestamp=None, ttl=120, timestamp_kind='source'):
        try:
            value=function()
            received=utcnow()
            ts=timestamp(value) if timestamp else received
            meta=freshness(ts,received,ttl)
            meta.update({'source_id':key,'source_url':url,'received_at_utc':received.isoformat(),
                         'timestamp_kind':timestamp_kind if timestamp else 'received_at',
                         'max_age_seconds':ttl,'error':None})
            if not meta['fresh']:
                meta['error']='Source timestamp is stale, absent, or invalid'
                self.errors.append({'source_id':key,'error':meta['error']})
            self.sources[key]=meta
            return value if meta['fresh'] else None
        except Exception as exc:
            self.sources[key]={'source_id':key,'source_url':url,'received_at_utc':utcnow().isoformat(),
                               'source_timestamp_utc':None,'age_seconds':None,'fresh':False,'status':'unavailable',
                               'max_age_seconds':ttl,'timestamp_kind':timestamp_kind,'error':str(exc)}
            self.errors.append({'source_id':key,'error':str(exc)})
            return None

    def compute(self, name, function):
        try:
            return function()
        except Exception as exc:
            self.errors.append({'source_id':name,'error':str(exc)})
            return None

    def instrument_frames(self, name, pair, desired):
        output={}
        for minutes in sorted({BASE.get(v,v) for v in desired.values()}):
            key=f'{name}.ohlc.{minutes}'
            frame=self.source(key,f'{SPOT_BASE}/OHLC?pair={pair}&interval={minutes}',
                              lambda m=minutes:self.kraken.ohlc(pair,m),
                              timestamp=lambda df:df.index[-1],ttl=minutes*60+120,timestamp_kind='candle_open')
            if frame is not None:
                merged=self.compute(key+'.cache',lambda:self.cache.merge(key,frame))
                self.frames[(name,minutes)]=merged if merged is not None else frame
        for label,minutes in desired.items():
            base=BASE.get(minutes,minutes)
            key=f'{name}.ohlc.{base}'
            frame=self.frames.get((name,base))
            asof=self.sources[key]['received_at_utc']
            if frame is None:
                output[label]={'status':'unavailable','source_ids':[key],'live':None,'last_closed':None,
                               'interval_minutes':minutes,'fresh':False}
                continue
            item=self.compute(f'{name}.{label}.indicators',lambda:self.timeframe(frame,minutes,base,asof,key))
            output[label]=item or {'status':'unavailable','source_ids':[key],'live':None,'last_closed':None,'fresh':False,'interval_minutes':minutes}
        return output

    @staticmethod
    def timeframe(frame, minutes, base, asof, key):
        if minutes!=base: frame=resample_candles(frame,base,minutes,asof)
        live,closed=split_modes(frame,minutes,asof)
        cfg=IndicatorConfig()
        live_snapshot=snapshot(live,cfg,minutes<1440,closed_only=closed) if live is not None and not live.empty else None
        closed_snapshot=snapshot(closed,cfg,minutes<1440) if not closed.empty else None
        gaps=int((frame.index.to_series().diff().dropna()!=pd.Timedelta(minutes=minutes)).sum())
        return {'status':'ok' if live_snapshot and closed_snapshot else 'partial', 'fresh':bool(live_snapshot),
                'source_ids':[key],'interval_minutes':minutes,'native':minutes==base,'base_interval_minutes':base,
                'alignment':'Kraken native UTC boundaries' if minutes==base else 'UTC, origin=1970-01-01T00:00:00Z',
                'calculation_at_utc':asof,'source_rows':len(frame),'gap_count':gaps,
                'latest_candle_is_open':live_snapshot is not None,'live':live_snapshot,'last_closed':closed_snapshot}

    def closed_frame(self, name, minutes):
        key=f'{name}.ohlc.{minutes}'
        source=self.sources.get(key)
        frame=self.frames.get((name,minutes))
        if frame is None or source is None or not source['fresh']:
            return None
        # An open candle received before a boundary cannot become a confirmed
        # close just because another request finished after that boundary.
        cutoff=pd.Timestamp(source['received_at_utc'])
        return frame[frame.index+pd.Timedelta(minutes=minutes)<=cutoff]

    def spot(self,name,pair,full_tape=False):
        key=f'{name}.ticker'
        ticker=self.source(key,f'{SPOT_BASE}/Ticker?pair={pair}',lambda:self.kraken.spot_ticker(pair))
        trade_asof=utcnow()
        trades=self.source(f'{name}.trades',f'{SPOT_BASE}/Trades?pair={pair}',
                           lambda:self.kraken.trade_tape(pair,trade_asof) if full_tape else self.kraken.recent_trades(pair),
                           timestamp=None)
        spread=self.source(f'{name}.spread',f'{SPOT_BASE}/Spread?pair={pair}',lambda:self.kraken.spread(pair))
        price=None
        quote_mid=None
        quote_time=None
        method=None
        trade_timestamp=None
        if trades is not None and not trades.empty:
            latest=trades.iloc[-1]
            trade_timestamp=latest.time.isoformat()
            if freshness(trade_timestamp,utcnow(),120)['fresh']:
                price=float(latest.price)
                method='trade'
        if spread is not None and not spread.empty:
            latest_quote=spread.iloc[-1]
            if freshness(latest_quote.time,utcnow(),120)['fresh'] and 0<latest_quote.bid<=latest_quote.ask:
                quote_mid=float((latest_quote.bid+latest_quote.ask)/2)
                quote_time=latest_quote.time.isoformat()
        if price is None and ticker is not None and spread is not None and not spread.empty:
            latest=spread.iloc[-1]
            if freshness(latest.time,utcnow(),120)['fresh'] and latest.bid<=ticker['last']<=latest.ask:
                price=ticker['last']
                trade_timestamp=latest.time.isoformat()
                method='ticker_inside_fresh_spread'
        spot={'status':'ok' if price is not None or quote_mid is not None else 'unavailable','verified_price':price,
              'current_price':price if price is not None else quote_mid,'current_price_type':method if price is not None else ('spread_midpoint' if quote_mid is not None else None),
              'quote_midprice':quote_mid,'quote_timestamp_utc':quote_time,
              'verification':method,'source_timestamp_utc':trade_timestamp if price is not None else quote_time,
              'ticker':ticker,'source_ids':[key,f'{name}.trades',f'{name}.spread']}
        tape=None
        if full_tape and trades is not None:
            tape=self.compute(f'{name}.trade_flow',lambda:tape_metrics(trades,trade_asof))
        return spot,trades,tape,trade_asof

    def book(self,name,pair,futures=False):
        key=f'{name}.depth'
        url=f'{FUTURES_BASE}/orderbook?symbol=PF_DOTUSD' if futures else f'{SPOT_BASE}/Depth?pair={pair}&count=500'
        book=self.source(key,url,lambda:self.kraken.futures_orderbook() if futures else self.kraken.order_book(pair,500),
                         timestamp=(lambda b:b['server_time_utc']) if futures else None)
        metrics=self.compute(key+'.metrics',lambda:orderbook_metrics(book)) if book else None
        if metrics is not None:
            metrics['source_ids']=[key]
        return book,metrics

    def collect(self):
        markets={}
        # Fetch each native interval once. Resampling never performs another API call.
        for name,pair,desired in [('DOTUSD','DOTUSD',DOT_TIMEFRAMES),('BTCUSD','XBTUSD',BTC_TIMEFRAMES),
                                  ('DOTBTC','DOTXBT',DOTBTC_TIMEFRAMES),('ETHUSD','ETHUSD',{'1h':60}),
                                  ('ETHBTC','ETHXBT',{'1h':60})]:
            markets[name]={'timeframes':self.instrument_frames(name,pair,desired),'pair':pair}
        for name,pair in [('DOTUSD','DOTUSD'),('BTCUSD','XBTUSD'),('DOTBTC','DOTXBT'),('ETHBTC','ETHXBT')]:
            spot,trades,tape,asof=self.spot(name,pair,full_tape=name=='DOTUSD')
            markets[name]['spot']=spot
            if name=='DOTUSD':
                dot_trades,spot_tape_asof=trades,asof
                markets[name]['trade_flow']=tape
        dot_book,dot_book_metrics=self.book('DOTUSD','DOTUSD')
        markets['DOTUSD']['orderbook']=dot_book_metrics
        # Obtain contract definition before assigning DOT/USD units to derivatives.
        instrument=self.source('DOTPERP.instrument',f'{FUTURES_BASE}/instruments',self.kraken.futures_instrument,ttl=86400)
        perp=self.source('DOTPERP.ticker',f'{FUTURES_BASE}/tickers/PF_DOTUSD',self.kraken.futures_ticker,
                         timestamp=lambda x:x['server_time_utc'])
        perp_book,perp_book_metrics=self.book('DOTPERP','PF_DOTUSD',futures=True) if instrument else (None,None)
        perp_asof=utcnow()
        perp_trades=self.source('DOTPERP.trades',f'{FUTURES_BASE}/history?symbol=PF_DOTUSD',
                                lambda:self.kraken.futures_trade_tape(perp_asof)) if instrument else None
        if instrument is None:
            for part in ['depth','trades']:
                self.source('DOTPERP.'+part,FUTURES_BASE+('/orderbook' if part=='depth' else '/history'),
                            lambda:(_ for _ in ()).throw(ValueError('Validated contract units unavailable')))
        perp_tape=self.compute('DOTPERP.trade_flow',lambda:tape_metrics(perp_trades,perp_asof)) if perp_trades is not None else None
        markets['DOTUSD']['perp']={'status':'ok' if perp else 'unavailable','ticker':perp,
                                  'instrument':{k:instrument[k] for k in ['symbol','type','contractSize','base','quote','tickSize']} if instrument else None,
                                  'orderbook':perp_book_metrics,'trade_flow':perp_tape,
                                  'source_ids':['DOTPERP.ticker','DOTPERP.instrument','DOTPERP.depth','DOTPERP.trades']}
        global_market=self.source('coingecko.global',self.cg.base_url+'/global',self.cg.global_market,
                                  timestamp=lambda x:x['source_timestamp_utc'],ttl=1800)
        breadth=self.source('coingecko.breadth',self.cg.base_url+'/coins/markets',self.cg.breadth,ttl=1800)
        now=utcnow()
        if breadth:
            for symbol,coin in breadth['coins'].items():
                meta=freshness(coin['source_timestamp_utc'],now,1800)
                coin['freshness']=meta
                if not meta['fresh']:
                    coin['returns_pct']={k:None for k in ['1h','24h','7d']}
                    coin['price_usd']=None
                    coin['market_cap_usd']=None
                    coin['volume_24h_usd']=None
            breadth=breadth_statistics(breadth['coins'])
        # Explicit freshness refresh at end of collection. Never relabel an old
        # observation as new just because another source answered later.
        for source in self.sources.values():
            if source['status']=='ok':
                source.update(freshness(source['source_timestamp_utc'],now,source['max_age_seconds']))
                if not source['fresh']:
                    source['error']='Source aged beyond threshold during collection'
                    self.errors.append({'source_id':source['source_id'],'error':source['error']})
        for name in markets:
            spot=markets[name].get('spot')
            if spot:
                spot['freshness']=freshness(spot['source_timestamp_utc'],now,120)
                if not spot['freshness']['fresh']:
                    spot.update({'verified_price':None,'current_price':None,'quote_midprice':None,'status':'unavailable'})
            hourly=self.closed_frame(name,60)
            markets[name]['returns']=self.compute(name+'.returns',lambda:closed_returns(hourly,60,now))
            markets[name]['return_source_ids']=[name+'.ohlc.60']
        for name in ['DOTUSD','BTCUSD','DOTBTC']:
            for tf in markets[name]['timeframes'].values():
                if any(not self.sources[s]['fresh'] for s in tf['source_ids']):
                    tf.update({'live':None,'last_closed':None,'fresh':False,'status':'stale'})
        if not self.sources['coingecko.global']['fresh']: global_market=None
        if not self.sources['coingecko.breadth']['fresh']: breadth=None
        if global_market: global_market['source_ids']=['coingecko.global']
        if breadth: breadth['source_ids']=['coingecko.breadth']
        for key,name in [('DOTUSD.depth','DOTUSD'),('DOTPERP.depth','DOTPERP')]:
            if key in self.sources and not self.sources[key]['fresh']:
                if name=='DOTUSD':
                    dot_book=dot_book_metrics=None
                    markets['DOTUSD']['orderbook']=None
                else:
                    perp_book=perp_book_metrics=None
                    markets['DOTUSD']['perp']['orderbook']=None
        for key,tape in [('DOTUSD.trades',markets['DOTUSD']['trade_flow']),('DOTPERP.trades',markets['DOTUSD']['perp']['trade_flow'])]:
            if tape is not None:
                tape['source_ids']=[key]
                tape['freshness']=freshness(tape.get('asof_utc'),now,120)
                if not tape['freshness']['fresh']:
                    tape['status']='stale'
                    tape['windows']={k:None for k in tape['windows']}
                    tape['large_trades']=None
        if not self.sources['DOTUSD.trades']['fresh']: dot_trades=None
        if not self.sources.get('DOTPERP.trades',{}).get('fresh',False): perp_trades=None
        if perp and not self.sources['DOTPERP.ticker']['fresh']:
            perp=None
            markets['DOTUSD']['perp']['ticker']=None
            markets['DOTUSD']['perp']['status']='stale'
        spot_price=markets['DOTUSD']['spot']['current_price']
        btc_price=markets['BTCUSD']['spot']['current_price']
        mark=perp['mark_price'] if perp else None
        basis={'absolute':mark-spot_price if mark is not None and spot_price else None,
               'percent':(mark/spot_price-1)*100 if mark is not None and spot_price else None,
               'bps':(mark/spot_price-1)*10000 if mark is not None and spot_price else None,
               'source_ids':['DOTUSD.trades','DOTUSD.spread','DOTPERP.ticker']}
        markets['DOTUSD']['spot_perp_basis']=basis
        markets['DOTUSD']['level_distances_usd']=level_distances(spot_price,[1.03,1.14,1.27,1.2848,1.31,1.42])
        markets['BTCUSD']['level_distances_usd']=level_distances(btc_price,[80000,70000,60000])
        markets['DOTUSD']['fixed_anchor_fibs']=fib_levels('0.7324','1.2848')
        one_minute=markets['DOTUSD']['timeframes'].get('1m',{}).get('last_closed')
        atr_value=one_minute['indicators']['atr14'] if one_minute else None
        markets['DOTUSD']['absorption']=self.compute('DOTUSD.absorption',lambda:absorption(dot_trades,spot_tape_asof,atr_value))
        if markets['DOTUSD']['absorption']:
            markets['DOTUSD']['absorption']['source_ids']=['DOTUSD.trades','DOTUSD.ohlc.1']
            markets['DOTUSD']['absorption']['asof_utc']=spot_tape_asof.isoformat()
        markets['DOTUSD']['realized_volatility']=self.compute('DOTUSD.realized_volatility',lambda:realized_volatility(
            self.closed_frame('DOTUSD',1),self.closed_frame('DOTUSD',60),now))
        dependence=self.compute('correlations',lambda:correlation_beta(self.closed_frame('DOTUSD',60),self.closed_frame('BTCUSD',60),self.closed_frame('ETHUSD',60),now))
        if dependence is not None: dependence['source_ids']=['DOTUSD.ohlc.60','BTCUSD.ohlc.60','ETHUSD.ohlc.60']
        relative={}
        for period in ['1h','4h','24h','7d']:
            d=(markets['DOTUSD']['returns'] or {}).get(period,{}).get('value_pct')
            b=(markets['BTCUSD']['returns'] or {}).get(period,{}).get('value_pct')
            aligned=(markets['DOTUSD']['returns'] or {}).get(period,{}).get('asof_utc')==(markets['BTCUSD']['returns'] or {}).get(period,{}).get('asof_utc')
            if not aligned: d=b=None
            relative[period]={'status':'ok' if d is not None and b is not None else 'unavailable_or_unaligned','dot_return_pct':d,'btc_return_pct':b,'dot_minus_btc_pp':None if d is None or b is None else d-b,
                              'ratio_return_pct':None if d is None or b is None or b<=-100 else ((1+d/100)/(1+b/100)-1)*100}
        old=[x for x in self.history.history if x.get('schema_version')==2]
        previous=max(old,key=lambda x:x['unix']) if old else {}
        previous_time=previous.get('generated_at_utc')
        walls={}
        for name,book,metrics,trades,asof in [('spot',dot_book,dot_book_metrics,dot_trades,spot_tape_asof),
                                            ('perp',perp_book,perp_book_metrics,perp_trades,perp_asof)]:
            if metrics:
                tracking={**metrics,'visible_prices':{s:[x['price'] for x in book[s+'s']] for s in ['bid','ask']}}
                walls[name]=self.compute(name+'.wall_persistence',lambda:wall_persistence(tracking,previous.get('walls',{}).get(name),trades,previous_time,asof))
            else: walls[name]={'status':'unavailable','walls':None}
        output={'schema_version':2,'generated_at_utc':now.isoformat(),'generated_at_unix':now.timestamp(),
                'collection_started_at_utc':self.started.isoformat(),'run_duration_seconds':round(time.monotonic()-self.timer,3),
                'formula_version':'2.0.0','sources':self.sources,'markets':markets,'breadth':breadth,'global_market':global_market,
                'correlation_beta':dependence,'relative_strength_dot_btc':relative,'wall_persistence':walls,
                'errors':self.errors,'raw_data':'data/raw/latest.json.gz',
                'definitions':'docs/FORMULAS.md','status':'partial' if self.errors else 'ok'}
        current=hourly_record(output)
        output['history_changes']=self.compute('history.update',lambda:self.history.update(current))
        output['history_points']=len(self.history.history)
        self.compute('ohlc_cache.save',self.cache.save)
        raw={'schema_version':2,'generated_at_utc':now.isoformat(),'sources':self.sources,
             'http_requests':{**self.kraken.http.records,**self.cg.http.records},
             'responses':{**self.kraken.http.raw,**self.cg.http.raw},'previous_hourly_record':previous,
             'calculation_context':{'spot_tape_asof_utc':spot_tape_asof.isoformat(),'perp_tape_asof_utc':perp_asof.isoformat(),
                                    'timeframe_asof_utc':{k:v['received_at_utc'] for k,v in self.sources.items() if '.ohlc.' in k}}}
        self.compute('raw.save',lambda:write_json(self.data_dir/'raw'/'latest.json.gz',raw,compressed=True))
        output['status']='partial' if self.errors else 'ok'
        return json_safe(output)


def hourly_record(data):
    markets=data['markets']
    perp=markets['DOTUSD']['perp']['ticker'] or {}
    global_market=data['global_market'] or {}
    breadth=(data['breadth'] or {}).get('windows',{})
    values={'dot_spot':markets['DOTUSD']['spot']['current_price'],'perp_mark':perp.get('mark_price'),
            'btc_spot':markets['BTCUSD']['spot']['current_price'],'dot_btc':markets['DOTBTC']['spot']['current_price'],
            'eth_btc':markets['ETHBTC']['spot']['current_price'],
            'btc_dominance':global_market.get('btc_dominance'),'eth_dominance':global_market.get('eth_dominance'),
            'total_market_cap_usd':global_market.get('total_market_cap_usd'),'total_volume_usd':global_market.get('total_volume_usd'),
            'total3_proxy_usd':global_market.get('total3_proxy_usd'),'open_interest':perp.get('open_interest'),
            'funding_rate':perp.get('funding_rate'),'predicted_funding':perp.get('funding_rate_prediction'),
            'basis_bps':markets['DOTUSD']['spot_perp_basis']['bps']}
    for period in ['1h','24h','7d']:
        values[f'alt_median_{period}']=breadth.get(period,{}).get('median_pct')
        values[f'breadth_score_{period}']=breadth.get(period,{}).get('breadth_score')
    states={}
    for name in ['DOTUSD','BTCUSD','DOTBTC']:
        states[name]={}
        for tf,item in markets[name]['timeframes'].items():
            closed=item.get('last_closed')
            if closed:
                states[name][tf]={k:closed['indicators'][k] for k in ['rsi14','macd_hist','ema20','ema50','ema200','atr_pct','bb_bandwidth_pct','volume_z20']}
                if name=='DOTUSD' and tf in ['1h','4h','1d']:
                    values.update({f'dot_{tf}_{k}':v for k,v in states[name][tf].items()})
            else: states[name][tf]=None
    walls={name:[w for w in (result or {}).get('walls',[]) or [] if w['state'] in ['new','stable']]
           for name,result in data['wall_persistence'].items()}
    return {'schema_version':2,'unix':data['generated_at_unix'],'generated_at_utc':data['generated_at_utc'],
            'values':values,'indicator_states':states,'walls':walls}
