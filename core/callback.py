# core/callback.py
# Run: python core/callback.py
from flask import Flask, request, jsonify
import pathlib, json, time

LOG_DIR = pathlib.Path("logs")
CALLBACK_LOG = LOG_DIR / "callbacks.json"
INJECTED_FILE = LOG_DIR / "injected.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)
if not CALLBACK_LOG.exists():
    CALLBACK_LOG.write_text(json.dumps([]))
if not INJECTED_FILE.exists():
    INJECTED_FILE.write_text(json.dumps({}))

app = Flask(__name__)

def append_callback(entry):
    data = json.loads(CALLBACK_LOG.read_text())
    data.append(entry)
    CALLBACK_LOG.write_text(json.dumps(data, indent=2))

@app.route("/callback", methods=["GET", "POST"])
def callback():
    # Accept either POST JSON or GET query
    payload = {
        "time": time.time(),
        "remote_addr": request.remote_addr,
        "method": request.method,
        "args": request.args.to_dict(),
        "headers": {k: v for k, v in request.headers.items()},
    }
    # If body JSON, include it
    if request.is_json:
        payload["json"] = request.get_json()

    # correlate with injected.json if id present
    inj_id = request.args.get("id") or (request.json and request.json.get("id") if request.is_json else None)
    if inj_id:
        payload["injection_id"] = inj_id
        try:
            injected = json.loads(INJECTED_FILE.read_text())
            if inj_id in injected:
                injected[inj_id].setdefault("callbacks", []).append({
                    "time": time.time(),
                    "remote_addr": request.remote_addr,
                    "args": request.args.to_dict()
                })
                INJECTED_FILE.write_text(json.dumps(injected, indent=2))
        except Exception:
            pass

    append_callback(payload)
    # Simple response so that an image GET won't have big payload
    return ("", 204)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
