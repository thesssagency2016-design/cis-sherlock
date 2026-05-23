import os
import re
import subprocess
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# On Render (Linux), sherlock is installed via pip and available on PATH
SHERLOCK_EXE = 'sherlock'


@app.route('/ping')
def ping():
    return jsonify({'status': 'ok'})


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
                '--timeout', '10',   # per-site timeout (seconds)
                '--no-color',
                '--print-found',     # only print found results → faster output
            ],
            capture_output=True,
            text=True,
            timeout=600             # 10-minute hard cap for the whole scan
        )

        output = result.stdout + result.stderr
        found = {}

        for line in output.splitlines():
            line = line.strip()
            # Strip any residual ANSI escape codes
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
    print(f"CIS Backend running on port {port}", flush=True)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
