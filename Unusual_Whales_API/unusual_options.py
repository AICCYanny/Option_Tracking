# %%
import os
import httpx
import pathlib as Path
from dotenv import load_dotenv, find_dotenv
import polars as pl
import pandas as pd
import requests

# Interact with API

load_dotenv(find_dotenv(filename=".env", usecwd=True), override=True)

# %%
uw_token = os.getenv('UW_TOKEN')
headers = {
    'Authorization': f"Bearer {uw_token}",
    'Accept': 'application/json, text/plain',
}

id_url = 'https://api.unusualwhales.com/api/alerts/configuration'
id_rsp = httpx.get(id_url, headers=headers)
configs = {d['name']: d['id'] for d in id_rsp.json()['data']}
config_ids = list(configs.values())
print(config_ids)

# %%
url = 'https://api.unusualwhales.com/api/alerts'
params = {
    #'config_ids[]': config_ids,
    'limit': 500,
    'intraday_only': True,
}
rsp = httpx.get(url, headers=headers, params=params)
rsp.status_code
rsp.json()['data']

alerts = []
for alert in rsp.json()['data']:
    d = {}
    d['id'] = alert['id']
    d['name'] = alert['name']
    d['symbol'] = alert['symbol']
    d['underlying_symbol'] = alert['meta']['underlying_symbol']
    d['created_at'] = alert['created_at']
    d['tape_time'] = alert['tape_time']
    d['noti_type'] = alert['noti_type']
    d['symbol_type'] = alert['symbol_type']
    d['ask_volume'] = alert['meta']['ask_volume']
    d['avg_fill'] = alert['meta']['avg_fill']
    d['bid_volume'] = alert['meta']['bid_volume']
    d['close'] = alert['meta']['close']
    d['diff'] = alert['meta']['diff']
    d['iv_change'] = alert['meta']['iv_change']
    d['minute'] = alert['meta']['minute']
    d['multi_leg_vol_ratio'] = alert['meta']['multi_leg_vol_ratio']
    d['open_interest'] = alert['meta']['open_interest']
    d['rounded_tape_time'] = alert['meta']['rounded_tape_time']
    d['total_premium'] = alert['meta']['total_premium']
    d['vol_oi_ratio'] = alert['meta']['vol_oi_ratio']
    d['volume'] = alert['meta']['volume']
    alerts.append(d)

# %%
df = pd.DataFrame(alerts)
# %%
print(rsp.json()['data'])
# %%

url = "https://api.unusualwhales.com/api/option-contract/GOOG251017C00245000/intraday"

headers = {
    "Accept": "application/json, text/plain",
    "Authorization": f"Bearer {uw_token}"
}

response = requests.get(url, headers=headers)


df = pd.DataFrame(response.json()['data'])
# %%
df
# %%


url = "https://api.unusualwhales.com/api/stock/QQQ/greeks"

querystring = {"expiry":"2025-10-03"}

headers = {
    "Accept": "application/json, text/plain",
    "Authorization": f"Bearer {uw_token}"
}

response = requests.get(url, headers=headers, params=querystring)
df = pd.DataFrame(response.json()['data'])
df
type(response.json()['data'])
# %%
url = "https://api.unusualwhales.com/api/stock/QQQ/stock-state"

headers = {
    "Accept": "application/json, text/plain",
    "Authorization": f"Bearer {uw_token}"
}

response = requests.get(url, headers=headers)

df = pd.DataFrame([response.json()['data']]) 
df
# %%
