"""Displayed liquidity, taker-side tape statistics and measured heuristics."""
import numpy as np
import pandas as pd
from common import finite

WINDOWS = {'1m':1,'5m':5,'15m':15,'30m':30,'1h':60,'4h':240}


def validate_book(book):
    for side in ('bids','asks'):
        if not book.get(side): raise ValueError('Missing book side')
        for level in book[side]:
            if finite(level['price']) is None or finite(level['volume']) is None or level['price']<=0 or level['volume']<0:
                raise ValueError('Invalid orderbook level')
    if max(x['price'] for x in book['bids']) >= min(x['price'] for x in book['asks']):
        raise ValueError('Crossed or locked orderbook')
    return book


def slippage(levels, mid, side, amount, denomination):
    remaining, quantity, quote = float(amount),0.0,0.0
    for level in sorted(levels,key=lambda x:x['price'],reverse=side=='sell'):
        price,available=level['price'],level['volume']
        take=min(available,remaining/price if denomination=='USD' else remaining)
        quantity+=take
        quote+=take*price
        remaining-=take*price if denomination=='USD' else take
        if remaining<=1e-8: break
    complete=remaining<=1e-8
    vwap=quote/quantity if quantity else None
    return {'requested':amount,'denomination':denomination,'fill_complete':complete,
            'filled_base':quantity,'filled_quote':quote,'unfilled':max(0,remaining),
            'vwap':vwap if complete else None,'partial_vwap':vwap if not complete else None,
            'slippage_bps':None if not complete or vwap is None else (vwap/mid-1)*10000*(1 if side=='buy' else -1)}


def orderbook_metrics(book):
    validate_book(book)
    bids=sorted(book['bids'],key=lambda x:x['price'],reverse=True)
    asks=sorted(book['asks'],key=lambda x:x['price'])
    bid,ask=bids[0],asks[0]
    mid=(bid['price']+ask['price'])/2
    total=bid['volume']+ask['volume']
    out={'best_bid':bid['price'],'best_ask':ask['price'],'best_bid_size':bid['volume'],'best_ask_size':ask['volume'],
         'spread_absolute':ask['price']-bid['price'],'spread_bps':(ask['price']-bid['price'])/mid*10000,
         'midprice':mid,'microprice':None if not total else (ask['price']*bid['volume']+bid['price']*ask['volume'])/total,
         'top_imbalance':None if not total else (bid['volume']-ask['volume'])/total,'depth':{},'walls':{},'slippage':{}}
    for bps in [5,10,25,50,100,200]:
        near_bids=[x for x in bids if x['price']>=mid*(1-bps/10000)]
        near_asks=[x for x in asks if x['price']<=mid*(1+bps/10000)]
        bv,av=sum(x['volume'] for x in near_bids),sum(x['volume'] for x in near_asks)
        bn,an=sum(x['volume']*x['price'] for x in near_bids),sum(x['volume']*x['price'] for x in near_asks)
        out['depth'][str(bps)]={'bid_volume':bv,'ask_volume':av,'bid_notional':bn,'ask_notional':an,
                              'imbalance':None if bv+av==0 else (bv-av)/(bv+av),
                              'notional_imbalance':None if bn+an==0 else (bn-an)/(bn+an),
                              'visible_range_complete':bids[-1]['price']<=mid*(1-bps/10000) and asks[-1]['price']>=mid*(1+bps/10000)}
    out['imbalance']=out['depth']['200']['imbalance']
    for pct in [0.5,1,2]:
        out['walls'][str(pct)]={}
        for side,levels in [('bid',bids),('ask',asks)]:
            near=[x for x in levels if abs(x['price']/mid-1)<=pct/100]
            top=sorted(near,key=lambda x:(-x['price']*x['volume'],x['price']))[:3]
            out['walls'][str(pct)][side]=[{'side':side,'price':x['price'],'volume':x['volume'],
                                          'notional':x['price']*x['volume'],'distance_bps':abs(x['price']/mid-1)*10000} for x in top]
    for side,levels in [('buy',asks),('sell',bids)]:
        out['slippage'][side]={f'{amount}_{denomination}':slippage(levels,mid,side,amount,denomination)
                               for amount,denomination in [(1000,'USD'),(5000,'USD'),(10000,'USD'),(5000,'DOT')]}
    return out


def validate_trades(df):
    required={'price','volume','time','side'}
    if not required.issubset(df.columns): raise ValueError('Trade schema missing fields')
    if not df.empty:
        if not df.side.isin(['b','s']).all(): raise ValueError('Unknown taker side')
        if (df.price<=0).any() or (df.volume<0).any() or not np.isfinite(df[['price','volume']]).all().all():
            raise ValueError('Invalid trade price/size')
    return df


def tape_metrics(df, now, windows=None):
    validate_trades(df)
    now=pd.Timestamp(now)
    windows=windows or WINDOWS
    observed=df[df.time<=now].sort_values(['time','trade_id'] if 'trade_id' in df else ['time']).copy()
    if observed.empty:
        return {'status':'unavailable','windows':{k:None for k in windows},'large_trades':None}
    excluded_types = {}
    if 'type' in observed:
        excluded_types = observed.loc[~observed['type'].isin(['fill','liquidation']), 'type'].value_counts().to_dict()
        observed = observed[observed['type'].isin(['fill','liquidation'])].copy()
        if observed.empty:
            return {'status':'unavailable','windows':{k:None for k in windows},'large_trades':None,'excluded_trade_types':excluded_types}
    observed['notional']=observed.price*observed.volume
    observed['signed_volume']=np.where(observed.side=='b',observed.volume,-observed.volume)
    coverage_start=pd.Timestamp(df.attrs.get('coverage_start',observed.time.min()))
    coverage_end=pd.Timestamp(df.attrs.get('coverage_end',observed.time.max()))
    out={'status':'ok','asof_utc':now.isoformat(),'earliest_trade_utc':observed.time.min().isoformat(),
         'latest_trade_utc':observed.time.max().isoformat(),'coverage_start_utc':coverage_start.isoformat(),
         'coverage_end_utc':coverage_end.isoformat(),'windows':{},'large_trades':{},'excluded_trade_types':excluded_types}
    p95,p99=np.quantile(observed.volume,[.95,.99],method='linear')
    large=observed[observed.volume>=p95].tail(12)
    out['large_trades']={'sample_count':len(observed),'size_p95':p95,'size_p99':p99,
                         'count_ge_p95':int((observed.volume>=p95).sum()),'count_ge_p99':int((observed.volume>=p99).sum()),
                         'recent':[{'time':x.time.isoformat(),'price':x.price,'volume':x.volume,'side':x.side,
                                    'ge_p99':bool(x.volume>=p99)} for x in large.itertuples()]}
    for name,minutes in windows.items():
        start=now-pd.Timedelta(minutes=minutes)
        w=observed[(observed.time>start)&(observed.time<=now)]
        buys,sells=w[w.side=='b'],w[w.side=='s']
        bv,sv=buys.volume.sum(),sells.volume.sum()
        bn,sn=buys.notional.sum(),sells.notional.sum()
        cumulative=w.signed_volume.cumsum()
        complete=bool(coverage_start<=start and coverage_end>=now)
        out['windows'][name]={'buy_volume':float(bv),'sell_volume':float(sv),'buy_notional':float(bn),'sell_notional':float(sn),
                              'buy_count':len(buys),'sell_count':len(sells),'trade_count':len(w),
                              'average_trade_size':finite(w.volume.mean()),'average_trade_notional':finite(w.notional.mean()),
                              'delta':float(bv-sv),'notional_delta':float(bn-sn),
                              'delta_ratio':None if bv+sv==0 else float((bv-sv)/(bv+sv)),
                              'cvd':float(cumulative.iloc[-1]) if len(cumulative) else 0.0,
                              'cvd_min':float(min(0,cumulative.min())) if len(cumulative) else 0.0,
                              'cvd_max':float(max(0,cumulative.max())) if len(cumulative) else 0.0,
                              'cvd_reset_utc':start.isoformat(),'window_complete':complete,
                              'status':'ok' if complete else 'partial',
                              'first_price':finite(w.price.iloc[0]) if len(w) else None,
                              'last_price':finite(w.price.iloc[-1]) if len(w) else None}
    return out


def absorption(df, now, atr_1m, minutes=5):
    out={'absorption_candidate':False,'buy_side_candidate':False,'sell_side_candidate':False,
         'status':'unavailable','window_minutes':minutes,'atr_1m':finite(atr_1m),'delta_z_score':None,
         'price_change':None,'price_change_atr':None,'delta':None,
         'thresholds':{'delta_z_abs':2.0,'max_price_progress_atr':0.25,'min_baseline_windows':24}}
    if df is None or df.empty or atr_1m is None or atr_1m<=0: return out
    now=pd.Timestamp(now)
    start=now-pd.Timedelta(minutes=minutes)
    coverage_start=pd.Timestamp(df.attrs.get('coverage_start',df.time.min()))
    coverage_end=pd.Timestamp(df.attrs.get('coverage_end',df.time.max()))
    if coverage_start>now-pd.Timedelta(hours=4) or coverage_end<now: return out
    work=df[(df.time<=now)&(df.time>now-pd.Timedelta(hours=4))].copy()
    work['signed']=np.where(work.side=='b',work.volume,-work.volume)
    current=work[work.time>start]
    baseline=[]
    for i in range(1,48):
        right=start-pd.Timedelta(minutes=(i-1)*minutes)
        left=right-pd.Timedelta(minutes=minutes)
        baseline.append(work[(work.time>left)&(work.time<=right)].signed.sum())
    if len(current)<2: return out
    sd=float(np.std(baseline,ddof=0))
    if not sd:
        out['status']='zero_baseline_variance'
        return out
    delta=float(current.signed.sum())
    z=(delta-float(np.mean(baseline)))/sd
    change=float(current.iloc[-1].price-current.iloc[0].price)
    progress=change/atr_1m
    buy=delta<0 and z<=-2 and progress>=-.25
    sell=delta>0 and z>=2 and progress<=.25
    out.update({'status':'ok','absorption_candidate':bool(buy or sell),'buy_side_candidate':bool(buy),
                'sell_side_candidate':bool(sell),'delta_z_score':z,'price_change':change,'price_change_atr':progress,
                'delta':delta,'baseline_mean':float(np.mean(baseline)),'baseline_std':sd,'baseline_windows':len(baseline)})
    return out


def wall_persistence(current, previous, trades, previous_time, now):
    if current is None: return {'status':'unavailable','walls':None}
    walls=[w for side in current['walls']['2'].values() for w in side]
    old=previous or []
    result=[]
    def match(w, pool):
        return next((p for p in pool if p['side']==w['side'] and abs(p['price']-w['price'])<1e-10),None)
    for w in walls:
        p=match(w,old)
        result.append({**w,'state':'stable' if p else 'new','snapshots':int(p.get('snapshots',1))+1 if p else 1,
                       'first_seen_utc':p.get('first_seen_utc',previous_time) if p else pd.Timestamp(now).isoformat(),
                       'closer_to_market':bool(p and w['distance_bps']<p['distance_bps']),
                       'wall_removed_without_observed_trade':False,'execution_observed_at_price':False})
    for p in old:
        if match(p,walls): continue
        complete=False
        matching=None
        if trades is not None and not trades.empty and previous_time:
            start=pd.Timestamp(previous_time)
            complete=pd.Timestamp(trades.attrs.get('coverage_start',trades.time.min()))<=start and pd.Timestamp(trades.attrs.get('coverage_end',trades.time.max()))>=pd.Timestamp(now)
            matching=trades[(trades.time>start)&(trades.time<=pd.Timestamp(now)) &
                            ((trades.price-p['price']).abs()<1e-10)&(trades.side==('s' if p['side']=='bid' else 'b'))]
        observed=matching is not None and not matching.empty
        # Falling out of the top-three list is not proof of removal. Caller
        # supplies all visible levels to distinguish still-visible levels.
        visible_prices=current.get('visible_prices',{}).get(p['side'])
        still_visible=visible_prices is not None and any(abs(v-p['price'])<1e-10 for v in visible_prices)
        in_range = visible_prices is None or (min(visible_prices)<=p['price']<=max(visible_prices))
        state='still_visible_below_wall_rank' if still_visible else ('not_visible_in_snapshot' if in_range else 'outside_visible_range')
        observed_volume=float(matching.volume.sum()) if observed else 0.0
        result.append({**p,'state':state,'execution_observed_at_price':bool(observed),
                       'observed_execution_volume':observed_volume,
                       'execution_volume_vs_previous_wall':observed_volume/p['volume'] if p['volume'] else None,
                       'wall_removed_without_observed_trade':False if still_visible else (not observed if complete and in_range else None),
                       'trade_coverage_complete':complete,'identity_proven':False})
    relocations=[]
    removed=[w for w in result if w['state']=='not_visible_in_snapshot']
    appeared=[w for w in result if w['state']=='new']
    for before in removed:
        candidates=[after for after in appeared if after['side']==before['side'] and before['volume']>0
                    and abs(after['volume']/before['volume']-1)<=.1
                    and abs(after['price']/before['price']-1)<=.0025]
        if len(candidates)==1:
            after=candidates[0]
            relocations.append({'side':before['side'],'previous_price':before['price'],'current_price':after['price'],
                                'price_shift_bps':(after['price']/before['price']-1)*10000,
                                'volume_change_pct':(after['volume']/before['volume']-1)*100,
                                'wall_moved_closer_candidate':after['distance_bps']<before['distance_bps'],'identity_proven':False})
    return {'status':'ok','walls':result,'relocation_candidates':relocations,
            'matching':'same side and exact displayed price; order identity unavailable'}
