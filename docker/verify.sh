#!/usr/bin/env sh
# Post-deployment checks for the compose service:
#   sh verify.sh
# Exits non-zero when the AIPerf backend is unavailable, so it can gate a rollout.
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

printf '== containers ==\n'
docker compose ps

printf '\n== runtime versions ==\n'
docker compose exec -T evalscope evalscope --version
docker compose exec -T evalscope /opt/aiperf-venv/bin/aiperf --version

printf '\n== task queue ==\n'
docker compose exec -T redis redis-cli ping
docker compose exec -T worker python -c "from evalscope.service.utils import queue_config; print('worker backend:', queue_config.queue_backend(), 'queue:', queue_config.queue_name())"

printf '\n== external runner prerequisites ==\n'
docker compose exec -T evalscope python -c "import os; from evalscope.perf.executors.external import supports_external_runner; print('supports_external_runner:', supports_external_runner()); print('/bin/ps executable:', os.access('/bin/ps', os.X_OK))"

printf '\n== service endpoints ==\n'
docker compose exec -T evalscope python -c "import json, urllib.request; print('health:', json.load(urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=5))['status'])"
docker compose exec -T evalscope python -c "import json, urllib.request; print('ready:', json.load(urllib.request.urlopen('http://127.0.0.1:9000/ready', timeout=5)))"
docker compose exec -T evalscope python -c "
import json, sys, urllib.request
data = json.load(urllib.request.urlopen('http://127.0.0.1:9000/api/v1/perf/engines', timeout=10))
for item in data['engines']:
    print('engine %s: available=%s version=%s reason=%s' % (item['engine'], item['available'], item.get('version'), item.get('reason')))
if not any(item['engine'] == 'aiperf' and item['available'] for item in data['engines']):
    print('ERROR: AIPerf backend is unavailable', file=sys.stderr)
    sys.exit(1)
"

printf '\nOK: AIPerf backend is available\n'
