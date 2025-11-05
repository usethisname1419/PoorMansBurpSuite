# core/proxy.py
# Run with: mitmdump -s core/proxy.py -p 8080
"""
Simple mitmproxy addon:
- Logs requests/responses to logs/
- On requests with header `X-Inject-Payload: 1` or query ?inject=1:
    - Generates an injection id (UUID)
    - Stores mapping in logs/injected.json
    - When response is text/html, appends a small payload snippet containing the callback URL with the id
"""

from mitmproxy import http
from mitmproxy import ctx
import os, json, uuid, time, pathlib
from urllib.parse import urlencode

LOG_DIR = pathlib.Path("logs")
INJECTED_FILE = LOG_DIR / "injected.json"
REQUEST_LOG = LOG_DIR / "requests.log"

CALLBACK_BASE = "http://127.0.0.1:5000/callback"  # change to your public interactsh/host when ready

def ensure_dirs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not INJECTED_FILE.exists():
        INJECTED_FILE.write_text(json.dumps({}))

ensure_dirs()

def log_request_line(line: str):
    with open(REQUEST_LOG, "a") as f:
        f.write(line + "\n")

def save_injection(mapping):
    data = json.loads(INJECTED_FILE.read_text())
    data.update(mapping)
    INJECTED_FILE.write_text(json.dumps(data, indent=2))

class InjectorAddon:
    def __init__(self):
        ctx.log.info("InjectorAddon initialized")

    def request(self, flow: http.HTTPFlow):
        # Log basic request info
        req = flow.request
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] REQ {req.method} {req.pretty_url} UA:{req.headers.get('user-agent','')}"
        log_request_line(line)

        wants_inject = False
        # header trigger
        if req.headers.get("X-Inject-Payload", "").lower() in ("1", "true", "yes"):
            wants_inject = True
        # query param trigger
        if "inject=1" in req.query:
            wants_inject = True

        if wants_inject:
            inj_id = str(uuid.uuid4())
            # store basic mapping for later correlation
            mapping = {
                inj_id: {
                    "time": time.time(),
                    "method": req.method,
                    "url": req.pretty_url,
                    "client_ip": flow.client_conn.peername[0] if flow.client_conn and flow.client_conn.peername else None,
                    "user_agent": req.headers.get("user-agent", ""),
                    "injected": False
                }
            }
            save_injection(mapping)
            # record injection id on flow so we can modify response accordingly
            flow.request.headers["X-Injection-Id"] = inj_id
            # also add short note in the request (for debugging)
            ctx.log.info(f"Marked flow for injection id={inj_id}")

    def response(self, flow: http.HTTPFlow):
        # Log basic response info
        res = flow.response
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] RES {flow.request.method} {flow.request.pretty_url} -> {res.status_code} ({res.headers.get('content-type','')})"
        log_request_line(line)

        inj_id = flow.request.headers.get("X-Injection-Id")
        if not inj_id:
            return

        ct = res.headers.get("content-type","").lower()
        # Only attempt HTML injection for text/html responses
        if "text/html" in ct or "application/xhtml+xml" in ct:
            try:
                body = res.get_text()
                # Build a tiny callback payload. Keep it small and obviously non-destructive.
                cb_query = urlencode({"id": inj_id, "source": "proxy-inject"})
                cb_url = f"{CALLBACK_BASE}?{cb_query}"
                # Minimal payload: an image tag that will trigger a GET to your callback server when rendered.
                snippet = f'\n<!-- injected by pentest-sim id={inj_id} -->\n<img src="{cb_url}" alt="" style="display:none" />\n'
                # Append snippet before </body> if present, otherwise at end
                if "</body>" in body.lower():
                    # simple case-insensitive replace of last occurrence
                    idx = body.lower().rfind("</body>")
                    new_body = body[:idx] + snippet + body[idx:]
                else:
                    new_body = body + snippet
                res.set_text(new_body)
                # update injection mapping
                data = json.loads(INJECTED_FILE.read_text())
                if inj_id in data:
                    data[inj_id]["injected"] = True
                    data[inj_id]["injected_at"] = time.time()
                    INJECTED_FILE.write_text(json.dumps(data, indent=2))
                ctx.log.info(f"Injected payload id={inj_id} into response {flow.request.pretty_url}")
            except Exception as e:
                ctx.log.error(f"Injection failed for id={inj_id}: {e}")

addons = [
    InjectorAddon()
]
