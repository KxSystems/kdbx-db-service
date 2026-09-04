from rtpy import rt_helper
from datetime import datetime, timezone
from random import random, choice
import pykx as kx
import time
import os

print("Starting feed...", flush=True)

symlist = {'EURUSD':[1.16,4],'GBPUSD':[1.34,4],'USDJPY':[158,2]}

cfg_path = 'file://' + os.path.dirname(os.path.realpath(__file__)) + '/rtconfig.json'
params = rt_helper.RTParams(config_url=cfg_path, console_log_level='error')
h, status_code = rt_helper.start(params)
print("Feed started; publishing to fxquote. Press Ctrl+C to stop.", flush=True)

published = 0
while True:
    for sym in symlist:
        mid,dec = symlist[sym]
        newmid = round(mid + choice([-1,1]) * random()/10**(dec), dec)
        spread = round(random()/10**(dec-1),dec)
        now = datetime.now(timezone.utc)
        price = [{'trddate':now.date(),
                  'ts':now,
                  'sym': sym,
                  'bid': round(newmid-spread/2,dec+1),
                  'ask': round(newmid+spread/2,dec+1)}]
        symlist[sym][0] = newmid
        rt_helper.insert(h, 'fxquote', kx.toq(price))
        published += len(price)
    if published % 30 == 0:
        print(f"Published {published} rows to fxquote.", flush=True)
    time.sleep(1)
