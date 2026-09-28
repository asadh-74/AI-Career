import os
import httpx
host = os.environ['API_HOST']
secret = os.environ['SCAN_SECRET']
r = httpx.post(f'http://{host}/api/scan/scheduled', headers={'X-Scan-Secret': secret}, timeout=120)
r.raise_for_status()
print(r.json())
