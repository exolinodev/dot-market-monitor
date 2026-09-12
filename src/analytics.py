"""Source-bound returns, breadth, volatility and rolling dependence."""
import numpy as np
import pandas as pd
from common import finite


def closed_returns(df, minutes, now, periods=None):
    periods=periods or {'1h':60,'4h':240,'24h':1440,'7d':10080}
    if df is None or df.empty: return {k:{'value_pct':None,'status':'unavailable'} for k in periods}
    closed=df[df.index+pd.Timedelta(minutes=minutes)<=pd.Timestamp(now)]
    if closed.empty: return {k:{'value_pct':None,'status':'unavailable'} for k in periods}
    end=closed.index[-1]+pd.Timedelta(minutes=minutes)
    result={}
    for name,duration in periods.items():
        target=closed.index[-1]-pd.Timedelta(minutes=duration)
        if duration%minutes or target not in closed.index:
            result[name]={'value_pct':None,'status':'insufficient_history','asof_utc':end.isoformat()}
        else:
            old=float(closed.loc[target,'close'])
            value=(float(closed.iloc[-1].close)/old-1)*100
            result[name]={'value_pct':value,'status':'ok','asof_utc':end.isoformat(),
                          'reference_utc':(target+pd.Timedelta(minutes=minutes)).isoformat(),'reference_price':old}
    return result


def log_returns(df, minutes, now):
    if df is None or df.empty: return pd.Series(dtype=float)
    closed=df[df.index+pd.Timedelta(minutes=minutes)<=pd.Timestamp(now)]
    r=np.log(closed.close/closed.close.shift(1))
    gap=closed.index.to_series().diff()!=pd.Timedelta(minutes=minutes)
    return r.where(~gap)


def realized_volatility(minute_df, hourly_df, now):
    result={}
    for name,df,minutes,n in [('1h',minute_df,1,60),('24h',hourly_df,60,24),('7d',hourly_df,60,168)]:
        r=log_returns(df,minutes,now).tail(n)
        good=len(r)==n and r.notna().all()
        result[name]={'value_pct':float(np.sqrt((r*r).sum())*100) if good else None,
                      'status':'ok' if good else 'insufficient_history', 'sampling_minutes':minutes,
                      'observations':int(r.notna().sum()),'annualized':False,
                      'asof_utc':(r.index[-1]+pd.Timedelta(minutes=minutes)).isoformat() if len(r) else None}
    return result


def correlation_beta(dot, btc, eth, now):
    returns={name:log_returns(df,60,now) for name,df in [('DOT',dot),('BTC',btc),('ETH',eth)]}
    result={}
    for name,peer in [('DOT_BTC','BTC'),('DOT_ETH','ETH')]:
        frame=pd.concat([returns['DOT'].rename('dot'),returns[peer].rename('peer')],axis=1).sort_index()
        result[name]={}
        for window,n in [('24h',24),('7d',168)]:
            tail=frame.tail(n)
            contiguous=len(tail)==n and tail.notna().all().all() and (tail.index.to_series().diff().iloc[1:]==pd.Timedelta(hours=1)).all()
            variance=float(tail.peer.var(ddof=1)) if contiguous else 0
            valid=bool(contiguous and variance>0 and tail['dot'].std(ddof=1)>0)
            result[name][window]={'correlation':finite(tail['dot'].corr(tail.peer)) if valid else None,
                                 'beta':finite(tail['dot'].cov(tail.peer)/variance) if contiguous and variance>0 else None,
                                 'status':'ok' if valid else 'insufficient_history_or_variance',
                                 'observations':int(tail.dropna().shape[0]),
                                 'asof_utc':(tail.index[-1]+pd.Timedelta(hours=1)).isoformat() if len(tail) else None}
    return result


def breadth_statistics(coins, dot_symbol='DOT'):
    results={}
    for period in ['1h','24h','7d']:
        returns={symbol:value['returns_pct'].get(period) for symbol,value in coins.items() if symbol!=dot_symbol}
        valid={k:v for k,v in returns.items() if v is not None and np.isfinite(v)}
        a=np.array(list(valid.values()))
        median=finite(np.median(a)) if len(a) else None
        mad=finite(np.median(np.abs(a-median))) if len(a) else None
        threshold=max(10,5*mad) if mad is not None else None
        outliers=[k for k,v in valid.items() if abs(v-median)>threshold] if len(a) else []
        dot=coins.get(dot_symbol,{}).get('returns_pct',{}).get(period)
        results[period]={'median_pct':median,'mean_pct':finite(a.mean()) if len(a) else None,
                         'fraction_positive':float((a>0).mean()) if len(a) else None,
                         'breadth_score':float(100*((a>0).sum()-(a<0).sum())/len(a)) if len(a) else None,
                         'dot_return_pct':dot,'dot_relative_return_vs_alt_median':None if dot is None or median is None else dot-median,
                         'outliers':outliers,'outlier_threshold_pp':threshold,'median_absolute_deviation_pp':mad,
                         'sample_count':len(a),'expected_peer_count':8,'status':'ok' if len(a)==8 else 'partial'}
    return {'coins':coins,'windows':results}
