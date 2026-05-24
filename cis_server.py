import gc
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SHERLOCK_EXE = 'sherlock'
BATCH_SIZE   = 75   # sites per subprocess — keeps each batch well under 512MB

# ─────────────────────────────────────────────────────────
# SITE LIST  (loaded once at startup, reused across requests)
# ─────────────────────────────────────────────────────────
def _load_sites():
    try:
        import sherlock_project, pathlib
        p = pathlib.Path(sherlock_project.__file__).parent / 'resources' / 'data.json'
        data = json.loads(p.read_text())
        return [k for k in data.keys() if k != '$schema']
    except Exception as e:
        print(f'[CIS] Could not load sherlock site list: {e}', flush=True)
        return []

ALL_SITES = _load_sites()
print(f'[CIS] Loaded {len(ALL_SITES)} sherlock sites', flush=True)

# Pre-compute batches so we don't do it per-request
SITE_BATCHES = [ALL_SITES[i:i+BATCH_SIZE] for i in range(0, len(ALL_SITES), BATCH_SIZE)]
print(f'[CIS] {len(SITE_BATCHES)} batches of ~{BATCH_SIZE} sites each', flush=True)


# ─────────────────────────────────────────────────────────
# KEEP-ALIVE
# ─────────────────────────────────────────────────────────
def _keep_alive():
    time.sleep(90)
    own_url = os.environ.get('RENDER_EXTERNAL_URL', '')
    if not own_url:
        print('[CIS] RENDER_EXTERNAL_URL not set — keep-alive disabled', flush=True)
        return
    ping_url = own_url.rstrip('/') + '/ping'
    while True:
        try:
            urllib.request.urlopen(ping_url, timeout=10)
            print(f'[CIS] keep-alive ping OK', flush=True)
        except Exception as exc:
            print(f'[CIS] keep-alive ping failed: {exc}', flush=True)
        time.sleep(600)

threading.Thread(target=_keep_alive, daemon=True).start()


# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────
ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
FOUND_RE = re.compile(r'\[\+\]\s+(.+?):\s+(https?://\S+)')

def _run_batch(username: str, sites: list[str]) -> dict:
    """Run sherlock on a single batch of sites. Returns {platform: url}."""
    cmd = [
        SHERLOCK_EXE, username,
        '--timeout', '8',
        '--no-color',
        '--print-found',
    ]
    for s in sites:
        cmd += ['--site', s]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180   # 3-minute cap per batch
        )
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        return {}

    found = {}
    for line in output.splitlines():
        line = ANSI_RE.sub('', line.strip())
        if '[+]' in line:
            m = FOUND_RE.search(line)
            if m:
                found[m.group(1).strip()] = m.group(2).strip()
    return found


# ─────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────
@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'service': 'cis-sherlock', 'sites': len(ALL_SITES)})


@app.route('/search/stream')
def search_stream():
    """
    Server-Sent Events endpoint.
    Sends JSON lines as each batch completes:
      data: {"type":"progress","batch":1,"of":6,"found":{...}}
      data: {"type":"done","total_found":42}
      data: {"type":"error","message":"..."}
    """
    username = request.args.get('username', '').strip()
    if not username:
        return jsonify({'error': 'No username provided'}), 400

    def generate():
        all_found = {}
        total_batches = len(SITE_BATCHES)

        for i, batch in enumerate(SITE_BATCHES, 1):
            try:
                batch_found = _run_batch(username, batch)
                all_found.update(batch_found)
                gc.collect()   # free memory after each batch

                payload = json.dumps({
                    'type':    'progress',
                    'batch':   i,
                    'of':      total_batches,
                    'found':   batch_found,
                })
                yield f'data: {payload}\n\n'
                print(f'[CIS] batch {i}/{total_batches} done — '
                      f'{len(batch_found)} found this batch, {len(all_found)} total', flush=True)

            except Exception as e:
                err = json.dumps({'type': 'error', 'message': str(e)})
                yield f'data: {err}\n\n'
                return

        done = json.dumps({'type': 'done', 'total_found': len(all_found)})
        yield f'data: {done}\n\n'

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # disable Nginx/Render proxy buffering
        }
    )


@app.route('/search')
def search():
    """
    Legacy single-shot endpoint (kept for compatibility).
    Runs all batches sequentially and returns combined results.
    """
    username = request.args.get('username', '').strip()
    if not username:
        return jsonify({'error': 'No username provided'}), 400

    if username == '_ping_test_':
        return jsonify({'username': username, 'results': {}})

    all_found = {}
    try:
        for i, batch in enumerate(SITE_BATCHES, 1):
            batch_found = _run_batch(username, batch)
            all_found.update(batch_found)
            gc.collect()
            print(f'[CIS] /search batch {i}/{len(SITE_BATCHES)} — {len(batch_found)} found', flush=True)

        return jsonify({'username': username, 'results': all_found})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'[CIS] Backend starting on port {port}', flush=True)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
