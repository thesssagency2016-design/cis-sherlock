import os
import re
import subprocess
import threading
import time
import urllib.request
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SHERLOCK_EXE = 'sherlock'

# ─────────────────────────────────────────────────────────
# KEEP-ALIVE: Render free tier spins down after 15 min of
# inactivity. This thread pings the service every 10 min
# to prevent cold-starts during an active investigation.
# ─────────────────────────────────────────────────────────
def _keep_alive():
    """Ping ourselves every 10 minutes so Render doesn't spin us down."""
    time.sleep(60)  # wait 60s for server to fully start first
    own_url = os.environ.get('RENDER_EXTERNAL_URL', '')
    if not own_url:
        print('[CIS] RENDER_EXTERNAL_URL not set — keep-alive disabled', flush=True)
        return
    ping_url = own_url.rstrip('/') + '/ping'
    while True:
        try:
            urllib.request.urlopen(ping_url, timeout=10)
            print(f'[CIS] keep-alive ping OK → {ping_url}', flush=True)
        except Exception as exc:
            print(f'[CIS] keep-alive ping failed: {exc}', flush=True)
        time.sleep(600)  # 10 minutes

threading.Thread(target=_keep_alive, daemon=True).start()


@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'service': 'cis-sherlock'})


@app.route('/search')
def search():
    username = request.args.get('username', '').strip()
    if not username:
        return jsonify({'error': 'No username provided'}), 400

    if username == '_ping_test_':
        return jsonify({'username': username, 'results': {}})

    try:
        result = subprocess.run(
            [
                SHERLOCK_EXE,
                username,
                '--timeout', '10',
                '--no-color',
                '--print-found',
            ],
            capture_output=True,
            text=True,
            timeout=600
        )

        output = result.stdout + result.stderr
        found = {}

        for line in output.splitlines():
            line = line.strip()
            line = re.sub(r'\x1b\[[0-9;]*m', '', line)
            if '[+]' in line:
                match = re.search(r'\[\+\]\s+(.+?):\s+(https?://\S+)', line)
                if match:
                    found[match.group(1).strip()] = match.group(2).strip()

        print(f"[CIS] username={username!r}  found={len(found)} platforms", flush=True)
        return jsonify({'username': username, 'results': found})

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Sherlock scan timed out (10 min limit). Try again.'}), 500
    except FileNotFoundError:
        return jsonify({'error': 'Sherlock is not installed on this server. Check requirements.txt.'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"[CIS] Backend running on port {port}", flush=True)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
