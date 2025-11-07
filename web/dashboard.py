#!/usr/bin/env python3
"""
PoorMansBurp — Dashboard (clean single-file)

Run:
  python web/dashboard.py
Visit:
  http://<vps-ip>:6000/
"""
from flask import Flask, request, Response, render_template_string, redirect, url_for, jsonify
import requests, urllib.parse, time, json, uuid
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urlparse
import os

app = Flask(__name__)

# -------------------- Globals / Storage --------------------
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "poormansburp/1.0"})
MITM_PROXY_URL = os.environ.get("PMB_MITM_PROXY", "http://127.0.0.1:8080")
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

CALLBACK_LOG = LOG_DIR / "callbacks.json"
if not CALLBACK_LOG.exists():
    CALLBACK_LOG.write_text("[]")

REQ_DB = LOG_DIR / "requests.json"
if not REQ_DB.exists():
    REQ_DB.write_text(json.dumps({}))

CLI_LOG = LOG_DIR / "cli.log"
if not CLI_LOG.exists():
    CLI_LOG.write_text("")

INTERCEPT_STATE = LOG_DIR / "intercept_state.json"
if not INTERCEPT_STATE.exists():
    INTERCEPT_STATE.write_text(json.dumps({"enabled": False}))


def session_proxies_for_iframe():
    """Return proxies dict when intercept is ON, else None."""
    if get_intercept_enabled():
        return {"http": MITM_PROXY_URL, "https": MITM_PROXY_URL}
    return None

def get_intercept_enabled() -> bool:
    try:
        return bool(json.loads(INTERCEPT_STATE.read_text()).get("enabled", False))
    except Exception:
        return False

def set_intercept_enabled(val: bool) -> None:
    INTERCEPT_STATE.write_text(json.dumps({"enabled": bool(val)}))


# Intercept state DB (proxy <-> dashboard)
INTERCEPT_DB = LOG_DIR / "intercept.json"
if not INTERCEPT_DB.exists():
    INTERCEPT_DB.write_text(json.dumps({}))

INTERNAL_PATH_PREFIXES = (
    "/cli/",
    "/ui/callbacks",
    "/ui/hit",
    "/ui/intercept",
    "/pac",
    "/asset",
    "/browse",
    "/favicon.ico",
    "/",
)

# -------------------- Helpers --------------------
def is_private_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    h = hostname.lower()
    if h in ("127.0.0.1", "localhost"):
        return True
    return h.startswith("10.") or h.startswith("192.168.") or h.startswith("172.")

def cli_log(msg: str, *, skip_internal: bool = True) -> None:
    """Append a line to logs/cli.log, skipping internal dashboard routes by default."""
    try:
        if skip_internal:
            p = request.path if request else ""
            for pref in INTERNAL_PATH_PREFIXES:
                if p.startswith(pref):
                    return
    except RuntimeError:
        pass
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        prev = CLI_LOG.read_text()
    except Exception:
        prev = ""
    CLI_LOG.write_text(prev + f"[{ts}] {msg}\n")

def make_abs_url(base: str, link: str) -> str:
    return urllib.parse.urljoin(base, link)

def load_req_db() -> dict:
    try:
        return json.loads(REQ_DB.read_text())
    except Exception:
        return {}

def save_req_db(db: dict) -> None:
    REQ_DB.write_text(json.dumps(db, indent=2))

def load_intercepts() -> dict:
    try:
        return json.loads(INTERCEPT_DB.read_text())
    except Exception:
        return {}

def save_intercepts(db: dict) -> None:
    INTERCEPT_DB.write_text(json.dumps(db, indent=2))

# -------------------- UI template --------------------
INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>PoorMansBurp</title>
  <style>
    :root{
      --bg:#f6f7f9; --card:#ffffff; --border:#e6e8ee; --muted:#6b7280; --ink:#0b0b0b;
      --brand:#111827; --accent:#2563eb; --accent-weak:#dbeafe;
    }
    *{box-sizing:border-box}
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:16px;background:var(--bg);color:#111}
    h2{margin:0 0 12px 0}
    .bar{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
    .note{font-size:13px;background:var(--accent-weak);color:#0f172a;border:1px solid #bfdbfe;padding:10px;border-radius:10px;margin:10px 0}
    .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px}
    .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    label{font-size:13px;color:var(--muted)}
    input[type=text], input[type=number], select, textarea{
      width:100%;padding:10px;border:1px solid var(--border);border-radius:10px;background:#fff;font:inherit
    }
    textarea{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:13px}
    button{appearance:none;border:1px solid var(--border);background:#fff;border-radius:10px;padding:8px 12px;cursor:pointer;font:inherit}
    button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
    #frame{width:100%;height:42vh;border:1px solid var(--border);border-radius:12px;background:#fff}
    pre{background:var(--ink);color:#e6e6e6;padding:10px;border-radius:10px;overflow:auto;max-height:340px;white-space:pre-wrap;word-break:break-word}
    .small{font-size:12px;color:var(--muted)}
    .pill{font-size:12px;background:#eef2ff;color:#3730a3;border:1px solid #c7d2fe;border-radius:999px;padding:4px 8px}
    .spacer{height:8px}
    .table{width:100%;border-collapse:collapse}
    .table th,.table td{border-bottom:1px solid #eee;padding:6px;text-align:left;font-size:13px}
    details{border:1px solid #e5e7eb;border-radius:10px;padding:8px;background:#fff}
    details > summary{cursor:pointer;font-weight:600}

    /* HELP modal styles */
    .help-btn { margin-left:12px; }
/* modal sizing & scrolling */
.modal-backdrop {
  position:fixed; inset:0; background:rgba(11,11,11,0.45);
  display:none; align-items:center; justify-content:center; z-index:9999;
  padding:16px;                    /* keep a little breathing room on small screens */
}
.modal {
  width:820px; max-width:94%;
  max-height:92vh;                 /* <-- key: cap height to viewport */
  overflow:auto;                   /* <-- modal content scrolls inside */
  background:var(--card); border-radius:12px; padding:18px; border:1px solid var(--border);
  box-shadow:0 8px 30px rgba(0,0,0,.15);
}
.modal h3{ margin-top:0; }
.modal .cols{ display:grid; grid-template-columns:1fr 220px; gap:14px; }
.modal pre{ background:#f3f4f6; color:#111; padding:10px; border-radius:8px;
  max-height:220px; overflow:auto; white-space:pre-wrap; }
.modal-close{ position:absolute; right:18px; top:18px; cursor:pointer; font-weight:700; color:var(--muted);
  background:transparent; border:none; }

/* prevent background scroll while modal open */
body.modal-open { overflow:hidden; }

/* small screens */
@media (max-width:700px){
  .modal{ width:96%; max-height:94vh; }
  .modal .cols{ grid-template-columns:1fr; }
}
    
</style>
</head>
<body>

  <div class="bar">
    <div style="display:flex; align-items:center; gap:12px;">
      <h2>PoorMansBurp — Dashboard — By: Derek Johnston(TG:@usethisusername)</h2>
      <i>Support: BTC: <b>bc1qtezfajhysn6dut07m60vtg0s33jy8tqcvjqqzk</b></i>
      
    </div>
    <div class="row">
<button id="help-btn" class="help-btn">Help</button>
      <span class="pill">Proxy</span>
      <span class="pill">Request Engine</span>
      <span class="pill">Intercept</span>
      <span class="pill">Callbacks</span>
    </div>
  </div>

  <div class="note">
    <strong>Heads up:</strong> The <em>Request Engine / Repeater</em> sends requests directly from the server and is
    <u>separate</u> from the proxied page in the iframe below. Browse via the iframe; craft/repeat raw requests via the Request Engine.
  </div>

  <div class="card">
    <form id="browse" action="/browse" method="get" target="preview">
      <div class="row">
        <div style="flex:1;min-width:260px">
          <label>Target URL</label>
          <input type="text" name="url" placeholder="http://example.com" value="{{url|default('')}}" required>
        </div>
        <label><input type="checkbox" name="inject" value="1" {{ 'checked' if inject else '' }}> Inject callback snippet</label>
        <button class="primary" type="submit">Open</button>
        <button type="button" onclick="document.getElementById('browse').submit()">Open in iframe</button>
        <button type="button" onclick="window.open('/browse?url='+encodeURIComponent(document.querySelector('[name=url]').value),'_blank')">Open in new tab</button>
        <button id="pac-btn" type="button">PAC / Proxy setup</button>
      </div>
    </form>
    <div class="spacer"></div>
    <iframe id="frame" name="preview" src="about:blank"></iframe>
  </div>

  <div class="spacer"></div>

  <!-- Intercept Panel -->
  <div class="card">
    <div class="row" style="justify-content:space-between">
      <h3 style="margin:0">Intercept (Proxy → Dashboard queue)</h3>
<div class="row" style="justify-content:space-between">
  <h3 style="margin:0">Intercept (Proxy → Dashboard queue)</h3>
  <div class="row">
    <button id="intercept-toggle">Intercept: OFF</button>
    <div class="small" style="margin-left:8px">Requests appear here when intercept is ON</div>
  </div>
</div>

      <div class="small">Requests appear here when the proxy intercepts; choose Forward, Drop, or Modify.</div>
    </div>
    <div id="intercepts">Loading…</div>
  </div>

  <div class="spacer"></div>

  <div class="grid">
    <!-- Left column: Request Engine -->
    <div class="card">
      <h3 style="margin-top:0">Request Engine / Repeater</h3>
      <div style="margin-bottom:8px">
        <input id="req-url" type="text" placeholder="https://httpbin.org/get">
      </div>
      <div class="row" style="gap:10px">
        <div style="min-width:120px">
          <label>Method</label>
          <select id="req-method"><option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option><option>PATCH</option></select>
        </div>
        <div style="min-width:120px">
          <label>Timeout (s)</label>
          <input id="req-timeout" type="number" value="20">
        </div>
        <label><input id="req-follow" type="checkbox" checked> follow redirects</label>
        <label><input id="req-verify" type="checkbox" checked> verify SSL</label>
      </div>
      <div style="margin-top:8px">
        <label>Headers (JSON)</label>
        <textarea id="req-headers" rows="5">{}</textarea>
      </div>
      <div style="margin-top:8px">
        <label>Body</label>
        <textarea id="req-body" rows="6"></textarea>
      </div>
      <div class="row" style="margin-top:8px">
        <button id="send-req" class="primary">Send</button>
        <button id="repeat-req">Repeat</button>
        <span class="small">×</span>
        <input id="repeat-count" type="number" value="5" style="width:70px">
        <span class="small">every</span>
        <input id="repeat-delay" type="number" value="0.5" step="0.1" style="width:80px">
        <span class="small">sec</span>
        <button id="save-template">Save Template</button>
        <button id="load-templates">Load Template</button>
      </div>
    </div>

    <!-- Right column: Response + CLI -->
    <div class="card">
      <h3 style="margin-top:0">Response</h3>
      <div class="small">Status: <span id="resp-status">-</span> <span id="resp-time"></span></div>
      <div style="margin-top:6px">
        <strong>Headers</strong>
        <pre id="resp-headers">{}</pre>
      </div>
      <div style="margin-top:6px">
        <strong>Body</strong>
        <pre id="resp-body"></pre>
      </div>

      <div class="spacer"></div>
      <h3 style="margin-top:0">CLI output (server)</h3>
      <div class="row">
        <button id="cli-refresh">Refresh</button>
        <button id="cli-clear">Clear</button>
        <span class="small">Auto-refresh every 3s</span>
      </div>
      <pre id="cli-log">Loading CLI logs…</pre>
    </div>
  </div>

  <div class="spacer"></div>

  <div class="card">
    <div class="row" style="justify-content:space-between">
      <h3 style="margin:0">Callbacks</h3>
      <div>
        <button id="cb-refresh">Refresh</button>
        <button id="cb-clear">Clear</button>
      </div>
    </div>
    <div id="cb-list" class="small" style="margin-top:8px">Loading…</div>
  </div>

  <!-- HELP modal backdrop -->
  <div id="help-backdrop" class="modal-backdrop" aria-hidden="true">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="help-title">
      <button class="modal-close" id="help-close" title="Close">✕</button>
      <h3 id="help-title">Getting started & installing mitmproxy CA</h3>
      <div class="cols">
        <div>
          <p class="small">This short guide shows the minimum steps to get HTTPS interception working in the browser iframe. If you plan to intercept HTTPS traffic you must <strong>install mitmproxy's CA certificate</strong> into the browser or OS so the browser trusts the proxy's fake certificates.</p>

          <h4>Quick start</h4>
          <ol>
            <li>Start the launcher with proxy + dashboard + callback, using your chosen port:
              <pre>python -m cli.main --proxy --dashboard --callback --mitm-port 8080</pre>
            </li>
            <li>Open this dashboard in your browser and click <strong>Help</strong> (this dialog).</li>
            <li>Install the mitmproxy CA (instructions below) and enable the PAC / Proxy settings to point your browser to <code>&lt;this-host&gt;:8080</code> (use PAC helper to get a URL).</li>
            <li>Enable <strong>Intercept</strong> and browse in the iframe.</li>
          </ol>

          <h4>Install mitmproxy CA (common browsers)</h4>
          <p><strong>Step 1 — Download the cert</strong></p>
          <pre id="mitm-link">http://<span id="host-name">127.0.0.1</span>:8080/</pre>
          <p>Open the link above (replace port if you used a different port) and follow the "mitmproxy certificate" download link.</p>

          <p><strong>Step 2 — Install into your browser / OS</strong></p>
          <ul>
            <li><strong>Firefox (profile-level):</strong> Preferences → Privacy & Security → View Certificates → Authorities → Import → check “Trust this CA to identify websites”.</li>
            <li><strong>Chrome / Edge (Windows):</strong> double-click .crt → Install Certificate → Place in “Trusted Root Certification Authorities”. Then restart the browser.</li>
            <li><strong>macOS (Safari / Chrome):</strong> double-click .crt → Keychain Access → add to System → double-click cert → Trust → When using this certificate: Always Trust. Restart browser.</li>
            <li><strong>Linux (Debian/Ubuntu):</strong>
              <pre>sudo cp mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
sudo update-ca-certificates
# restart browser</pre>
            </li>
          </ul>

          <h4>Troubleshooting</h4>
          <ul>
            <li>If you see an SSL warning, the browser doesn't trust the CA — re-import and ensure you trusted it for websites.</li>
            <li>If iframe pages are not proxied, ensure Intercept is ON and that the PAC/Proxy points to the correct host:port.</li>
            <li>To test the proxy from shell: <code>curl --proxy http://127.0.0.1:8080 -I http://example.org</code></li>
            <li>If you changed mitm port when starting the launcher, use that port when opening the cert page (e.g. <code>http://127.0.0.1:8899/</code>).</li>
          </ul>

          <div class="note small">Security note: Installing a trusted root CA lets that CA mint certificates trusted by your browser. Only install mitmproxy's CA on machines you control and remove it when you're done.</div>
        </div>

        <div>
          <div style="border:1px solid #eee;padding:10px;border-radius:8px;background:#fff;">
            <strong>Actions</strong>
            <div class="actions" style="flex-direction:column;margin-top:8px">
              <button id="open-mitm" class="copy-btn">Open mitmproxy UI</button>
              <button id="copy-pac" class="copy-btn">Copy PAC URL</button>
              <button id="copy-proxy" class="copy-btn">Copy Proxy (host:port)</button>
              <button id="close-help" class="copy-btn">Close</button>
            </div>
            <div style="margin-top:12px"><small class="small">PAC helper uses /pac?proxy=HOST:PORT — you can use the PAC button on the dashboard too.</small></div>
          </div>

          <div style="margin-top:14px;border:1px solid #eee;padding:10px;border-radius:8px;background:#fff;">
            <strong>Quick commands</strong>
            <pre id="quick-cmds"># curl test (replace host:port as needed)
curl --proxy http://127.0.0.1:8080 -I http://example.org</pre>
          </div>
        </div>
      </div>
    </div>
  </div>

<script>
  /* ---------- small helpers for the modal ---------- */
  const helpBtn = document.getElementById('help-btn');
  const helpBackdrop = document.getElementById('help-backdrop');
  const helpClose = document.getElementById('help-close');
  const closeHelpBtn = document.getElementById('close-help');
/* ---- new edit -----*/
const interceptsEl = document.getElementById('intercepts');
let interceptsEditing = false;

// Pause auto-refresh while user is interacting inside the panel
interceptsEl.addEventListener('focusin', ()=>{ interceptsEditing = true; });
interceptsEl.addEventListener('focusout', ()=>{
  // only resume when focus has fully left the panel
  setTimeout(()=>{ 
    if (!interceptsEl.contains(document.activeElement)) interceptsEditing = false;
  }, 0);
});

// Optional: hovering into the edit area counts as “editing”
interceptsEl.addEventListener('mouseenter', ()=>{ interceptsEditing = true; });
interceptsEl.addEventListener('mouseleave', ()=>{
  // don’t resume if an input is still focused
  if (!interceptsEl.contains(document.activeElement)) interceptsEditing = false;
});
/* -------- */
  function showHelp() {
    // detect host and default port
    const host = location.hostname || '127.0.0.1';
    // best guess: default port 8080 (user may have chosen another).
    const port = 8080;
    document.getElementById('host-name').textContent = host + ':' + port;
    document.getElementById('mitm-link').textContent = 'http://' + host + ':' + port + '/';
    // update quick cmds
    document.getElementById('quick-cmds').textContent = `# curl test (replace host:port as needed)\ncurl --proxy http://${host}:${port} -I http://example.org`;
    helpBackdrop.style.display = 'flex';
    helpBackdrop.setAttribute('aria-hidden', 'false');
  }

  function hideHelp() {
    helpBackdrop.style.display = 'none';
    helpBackdrop.setAttribute('aria-hidden', 'true');
  }

  helpBtn.addEventListener('click', showHelp);
  helpClose.addEventListener('click', hideHelp);
  closeHelpBtn.addEventListener('click', hideHelp);

  // close on outside click
  helpBackdrop.addEventListener('click', (ev) => {
    if (ev.target === helpBackdrop) hideHelp();
  });

  // close on ESC
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') hideHelp(); });

  // action buttons
  document.getElementById('open-mitm').addEventListener('click', ()=>{
    const host = location.hostname || '127.0.0.1';
    const port = 8080;
    window.open(`http://${host}:${port}/`, '_blank');
  });

  document.getElementById('copy-pac').addEventListener('click', async ()=>{
    const host = location.hostname || '127.0.0.1';
    const port = 8080;
    const pacUrl = `${location.protocol}//${location.host}/pac?proxy=${encodeURIComponent(host+':'+port)}`;
    try { await navigator.clipboard.writeText(pacUrl); alert('PAC URL copied to clipboard:\\n' + pacUrl); } catch(e){ alert('Copied: ' + pacUrl); }
  });

  document.getElementById('copy-proxy').addEventListener('click', async ()=>{
    const host = location.hostname || '127.0.0.1';
    const port = 8080;
    const proxy = host + ':' + port;
    try { await navigator.clipboard.writeText(proxy); alert('Proxy copied to clipboard: ' + proxy); } catch(e){ alert('Copied: ' + proxy); }
  });

  // PAC helper (existing)
  document.getElementById('pac-btn').addEventListener('click', ()=>{
    const host = location.hostname;
    const port = prompt('mitmdump host:port', host + ':8080') || (host + ':8080');
    const pacUrl = location.protocol + '//' + location.host + '/pac?proxy=' + encodeURIComponent(port);
    alert('PAC URL:\\n' + pacUrl + '\\n\\nUse this in your browser (Automatic proxy configuration).\\nRemember to install mitmproxy CA for HTTPS interception.');
  });

  /* ---------- existing intercept toggle refresh logic ---------- */
  async function refreshInterceptToggle(){
    try{
      const r = await fetch('/ui/intercept/status');
      const j = await r.json();
      const btn = document.getElementById('intercept-toggle');
      if(j.enabled){
        btn.textContent = 'Intercept: ON';
        btn.classList.add('primary');
      }else{
        btn.textContent = 'Intercept: OFF';
        btn.classList.remove('primary');
      }
    }catch(e){}
  }
  document.getElementById('intercept-toggle').addEventListener('click', async ()=>{
    await fetch('/ui/intercept/toggle', {method:'POST', headers:{'Content-Type':'application/json'}});
    refreshInterceptToggle();
  });
  refreshInterceptToggle();
  setInterval(refreshInterceptToggle, 3000);
</script>

  <script>
    /* ---------- PAC helper ---------- */
    document.getElementById('pac-btn').addEventListener('click', ()=>{
      const host = location.hostname;
      const port = prompt('mitmdump host:port', host + ':8080') || (host + ':8080');
      const pacUrl = location.protocol + '//' + location.host + '/pac?proxy=' + encodeURIComponent(port);
      alert('PAC URL:\\n' + pacUrl + '\\n\\nUse this in your browser (Automatic proxy configuration).\\nRemember to install mitmproxy CA for HTTPS interception.');
    });

    /* ---------- Intercept UI ---------- */
   async function loadIntercepts(){
  if (interceptsEditing) return; // don’t clobber while user edits

  // remember which flows are open
  const previouslyOpen = Array.from(interceptsEl.querySelectorAll('details[open]'))
                              .map(d => d.getAttribute('data-flow'));

  try{
    const r = await fetch('/ui/intercept/list');
    const j = r.ok ? await r.json() : [];
    if(!Array.isArray(j) || j.length===0){
      interceptsEl.innerHTML = '<div class="small">No pending intercepted requests.</div>';
      return;
    }

    // render fresh list (with data-flow attributes as in step 1)
    interceptsEl.innerHTML = j.map(item => {
      const d = item.data || {};
      const flow = item.flow_id;
      const hdrs = d.headers ? JSON.stringify(d.headers, null, 2) : '{}';
      const body = (d.body||'');
      return `
        <details data-flow="${flow}" style="margin-bottom:8px">
          <summary>${d.method||''} <code>${(d.url||'').replace(/&/g,'&amp;')}</code> <span class="small">from ${d.client_addr||''}</span></summary>
          <div class="row" style="margin:8px 0;gap:8px">
            <button onclick="interceptDecision('${flow}','forward')">Forward</button>
            <button onclick="interceptDecision('${flow}','drop')">Drop</button>
            <button onclick="openModify('${flow}')">Modify…</button>
          </div>
          <div class="grid">
            <div class="card" style="padding:8px">
              <div class="small">Headers</div>
              <pre>${hdrs.replace(/</g,'&lt;')}</pre>
            </div>
            <div class="card" style="padding:8px">
              <div class="small">Body</div>
              <pre>${(body || '').replace(/</g,'&lt;')}</pre>
            </div>
          </div>
          <div style="margin-top:8px">
            <div class="small">Modify (optional):</div>
            <div class="grid" style="grid-template-columns:1fr 1fr;gap:8px">
              <div><label>Method</label><input id="m-${flow}-method" type="text" value="${d.method||''}"></div>
              <div><label>URL</label><input id="m-${flow}-url" type="text" value="${(d.url||'').replace(/"/g,'&quot;')}"></div>
            </div>
            <div style="margin-top:6px"><label>Headers (JSON)</label><textarea id="m-${flow}-headers" rows="5">${hdrs}</textarea></div>
            <div style="margin-top:6px"><label>Body</label><textarea id="m-${flow}-body" rows="5">${(body||'').replace(/</g,'&lt;')}</textarea></div>
          </div>
        </details>`;
    }).join('');

    // restore open state
    previouslyOpen.forEach(fid=>{
      const det = interceptsEl.querySelector(`details[data-flow="${fid}"]`);
      if (det) det.setAttribute('open', '');
    });

  }catch(e){
    interceptsEl.innerHTML = '<div class="small">Error loading intercepts</div>';
  }
}

    async function interceptDecision(flow_id, decision, modified){
      try{
        await fetch('/cli/intercept/decision', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({flow_id, decision, modified: modified||null})
        });
        loadIntercepts();
      }catch(e){}
    }
    function openModify(flow_id){
      const method = document.getElementById(`m-${flow_id}-method`).value.trim();
      const url = document.getElementById(`m-${flow_id}-url`).value.trim();
      let headers = {};
      try{ headers = JSON.parse(document.getElementById(`m-${flow_id}-headers`).value||'{}'); }catch(e){ alert('Invalid headers JSON'); return; }
      const body = document.getElementById(`m-${flow_id}-body`).value;
      interceptDecision(flow_id, 'modify', {method,url,headers,body});
    }
    setInterval(loadIntercepts, 1500);
    loadIntercepts();

    /* ---------- Callbacks UI ---------- */
    async function loadCallbacks(){
      try{
        const r = await fetch('/ui/callbacks');
        const j = r.ok ? await r.json() : [];
        const el = document.getElementById('cb-list');
        if(!Array.isArray(j) || j.length===0){ el.innerText = 'No callbacks yet'; return; }
        el.innerHTML = j.slice().reverse().slice(0,200).map(it=>{
          const t = it.time ? new Date(it.time*1000).toLocaleString() : '';
          const id = it.injection_id ? `<code>${it.injection_id}</code>` : '(no-id)';
          const ip = it.remote_addr || '';
          return `<div style="border-bottom:1px solid #eee;padding:6px 0">${id} from ${ip} @ ${t}</div>`;
        }).join('');
      }catch(e){
        document.getElementById('cb-list').innerText = 'Error loading callbacks';
      }
    }
    document.getElementById('cb-refresh').addEventListener('click', loadCallbacks);
    document.getElementById('cb-clear').addEventListener('click', async ()=>{
      await fetch('/ui/callbacks/clear', {method:'POST'});
      loadCallbacks();
    });
    loadCallbacks();

    /* ---------- CLI log viewer ---------- */
    async function loadCli(){
      try{
        const r = await fetch('/cli/logs');
        const text = r.ok ? await r.text() : 'error';
        document.getElementById('cli-log').innerText = text;
      }catch(e){
        document.getElementById('cli-log').innerText = 'Error loading logs';
      }
    }
    document.getElementById('cli-refresh').addEventListener('click', loadCli);
    document.getElementById('cli-clear').addEventListener('click', async ()=>{
      await fetch('/cli/logs/clear', {method:'POST'});
      loadCli();
    });
    loadCli();
    setInterval(loadCli, 3000);

    /* ---------- Request engine ---------- */
    function showResponse(j){
      const sEl = document.getElementById('resp-status');
      const tEl = document.getElementById('resp-time');
      const hEl = document.getElementById('resp-headers');
      const bEl = document.getElementById('resp-body');
      if (j.error){ sEl.textContent='ERROR'; tEl.textContent=''; hEl.textContent=''; bEl.textContent=j.error; return; }
      sEl.textContent = (j.status_code||'') + ' ' + (j.reason||'');
      tEl.textContent = ' (' + (j.elapsed||0).toFixed(3) + 's)';
      try { hEl.textContent = JSON.stringify(j.headers||{}, null, 2); } catch(e){ hEl.textContent = String(j.headers || ''); }
      let body = j.body || '';
      if (j.body_note){ bEl.textContent = j.body_note + (body ? ('\\n\\n' + body) : ''); }
      else { try { body = JSON.stringify(JSON.parse(body), null, 2); } catch(e){} bEl.textContent = body; }
    }
    async function sendRequest(payload){
      document.getElementById('resp-status').innerText='...';
      document.getElementById('resp-time').innerText='';
      document.getElementById('resp-headers').innerText='';
      document.getElementById('resp-body').innerText='';
      try{
        const r = await fetch('/reqs/send', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
        const j = await r.json();
        showResponse(j);
        await fetch('/cli/logs/append', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({msg:'Request sent: '+payload.method+' '+payload.url})});
      }catch(e){ showResponse({error:String(e)}); }
    }
    document.getElementById('send-req').addEventListener('click', async (ev)=>{
      ev.preventDefault();
      const url = document.getElementById('req-url').value.trim();
      if(!url) return alert('Missing URL');
      const payload = {
        url,
        method: document.getElementById('req-method').value,
        headers: (()=>{ try { return JSON.parse(document.getElementById('req-headers').value || '{}'); } catch(e){ alert('Invalid headers JSON'); return {}; }})(),
        body: document.getElementById('req-body').value,
        timeout: parseFloat(document.getElementById('req-timeout').value || 20),
        allow_redirects: document.getElementById('req-follow').checked,
        verify_ssl: document.getElementById('req-verify').checked
      };
      await sendRequest(payload);
    });
    document.getElementById('repeat-req').addEventListener('click', async (ev)=>{
      ev.preventDefault();
      const count = parseInt(document.getElementById('repeat-count').value || 1);
      const delay = parseFloat(document.getElementById('repeat-delay').value || 0.5);
      const url = document.getElementById('req-url').value.trim();
      if(!url) return alert('Missing URL');
      const payload = {
        url, method: document.getElementById('req-method').value,
        headers: (()=>{ try { return JSON.parse(document.getElementById('req-headers').value || '{}'); } catch(e){ return {}; }})(),
        body: document.getElementById('req-body').value,
        timeout: parseFloat(document.getElementById('req-timeout').value || 20),
        allow_redirects: document.getElementById('req-follow').checked,
        verify_ssl: document.getElementById('req-verify').checked
      };
      for(let i=0;i<count;i++){ await sendRequest(payload); await new Promise(r=>setTimeout(r, Math.max(0, delay*1000))); }
    });
  </script>

</body>
</html>
"""

# -------------------- Routes: Proxy / Browse / Asset --------------------
@app.route("/")
def index():
    return render_template_string(INDEX_HTML, url=request.args.get("url",""), inject=(request.args.get("inject")=="1"))

@app.route("/ui/intercept/status", methods=["GET"])
def ui_intercept_status():
    return jsonify({"enabled": get_intercept_enabled()})

@app.route("/ui/intercept/toggle", methods=["POST"])
def ui_intercept_toggle():
    j = request.get_json(silent=True) or {}
    if "enabled" in j:
        set_intercept_enabled(bool(j["enabled"]))
    else:
        set_intercept_enabled(not get_intercept_enabled())
    return jsonify({"enabled": get_intercept_enabled()})


@app.route("/browse")
def browse():
    target = request.args.get("url")
    inject = request.args.get("inject", "0") == "1"
    if not target:
        return redirect(url_for("index"))
    if not urllib.parse.urlparse(target).scheme:
        target = "http://" + target
    try:
        res = SESSION.get(target, timeout=15, allow_redirects=True, proxies=session_proxies_for_iframe(), verify=True)
    except Exception as e:
        return Response(f"Error fetching target: {e}", status=502)
    content_type = res.headers.get("content-type","")
    if "text/html" not in content_type.lower():
        return Response(res.content, headers={"Content-Type": content_type})
    soup = BeautifulSoup(res.text, "html.parser")
    base_tag = soup.find("base")
    base_url = make_abs_url(res.url, base_tag["href"]) if (base_tag and base_tag.get("href")) else res.url
    for tag in soup.find_all(src=True):
        tag["src"] = "/asset?url=" + urllib.parse.quote_plus(make_abs_url(base_url, tag["src"]))
    for tag in soup.find_all(href=True):
        href = tag["href"]
        if href.startswith("#"):
            continue
        tag["href"] = "/asset?url=" + urllib.parse.quote_plus(make_abs_url(base_url, href))
    if inject:
        inj_id = f"ui-{int(time.time()*1000)}"
        snippet = BeautifulSoup(f'<!-- injected id={inj_id} --><img src="/ui/hit?id={inj_id}" style="display:none">', "html.parser")
        if soup.body:
            soup.body.append(snippet)
        else:
            soup.append(snippet)
    return Response(str(soup), headers={"Content-Type": "text/html; charset=utf-8"})

@app.route("/asset")
def asset():
    u = request.args.get("url")
    if not u:
        return Response("Missing url", status=400)
    u = urllib.parse.unquote_plus(u)
    parsed = urllib.parse.urlparse(u)
    if is_private_host(parsed.hostname):
        return Response("Fetching local addresses is blocked by server policy.", status=403)
    try:
        r = SESSION.get(u, stream=True, timeout=15, proxies=session_proxies_for_iframe(), verify=True)
    except Exception as e:
        return Response(f"Error fetching asset: {e}", status=502)
    headers = {"Content-Type": r.headers.get("content-type","application/octet-stream")}
    if "cache-control" in r.headers:
        headers["Cache-Control"] = r.headers["cache-control"]
    return Response(r.content, headers=headers)

# -------------------- Callbacks --------------------
@app.route("/ui/hit", methods=["GET", "POST"])
def ui_hit():
    try:
        data = json.loads(CALLBACK_LOG.read_text())
    except Exception:
        data = []
    entry = {
        "time": time.time(),
        "remote_addr": request.remote_addr,
        "method": request.method,
        "args": request.args.to_dict(),
        "headers": dict(request.headers),
        "injection_id": request.args.get("id")
    }
    data.append(entry)
    CALLBACK_LOG.write_text(json.dumps(data, indent=2))
    cli_log(f"CALLBACK id={entry.get('injection_id')} from {entry.get('remote_addr')}")
    return ("", 204)

@app.route("/ui/callbacks", methods=["GET"])
def ui_callbacks():
    try:
        data = json.loads(CALLBACK_LOG.read_text())
    except Exception:
        data = []
    return Response(json.dumps(data), mimetype="application/json")

@app.route("/ui/callbacks/clear", methods=["POST"])
def ui_callbacks_clear():
    CALLBACK_LOG.write_text("[]")
    return jsonify({"status": "cleared"})

# -------------------- Intercept API (proxy <-> dashboard) --------------------
@app.route("/cli/intercept/new", methods=["POST"])
def intercept_new():
    """Proxy posts a new intercepted request here."""
    j = request.get_json() or {}
    flow_id = j.get("flow_id")
    data = j.get("data") or {}
    if not flow_id:
        return jsonify({"error": "missing flow_id"}), 400
    db = load_intercepts()
    db[flow_id] = {
        "flow_id": flow_id,
        "data": data,
        "decision": None,
        "modified": None,
        "created": time.time()
    }
    save_intercepts(db)
    cli_log(f"[INTERCEPT] new flow {flow_id} {data.get('method')} {data.get('url')}", skip_internal=False)
    return jsonify({"ok": True})

@app.route("/cli/intercept/decision", methods=["POST"])
def intercept_set_decision():
    """UI calls this to set a decision for a flow."""
    j = request.get_json() or {}
    flow_id = j.get("flow_id")
    decision = (j.get("decision") or "").lower()
    modified = j.get("modified")
    if decision not in ("forward", "drop", "modify"):
        return jsonify({"error": "invalid decision"}), 400
    db = load_intercepts()
    if flow_id not in db:
        return jsonify({"error": "unknown flow_id"}), 404
    db[flow_id]["decision"] = decision
    db[flow_id]["modified"] = modified if (decision == "modify") else None
    save_intercepts(db)
    return jsonify({"ok": True})

@app.route("/cli/intercept/decision", methods=["GET"])
def intercept_get_decision():
    """Proxy polls this for a decision; returns {} until a decision is set."""
    flow_id = request.args.get("flow_id", "")
    db = load_intercepts()
    item = db.get(flow_id)
    if not item or not item.get("decision"):
        # no decision yet -> empty object
        return jsonify({})
    # once returned to proxy, remove from queue
    out = {"decision": item["decision"], "modified": item.get("modified")}
    del db[flow_id]
    save_intercepts(db)
    return jsonify(out)

# UI helper to list pending intercepts
@app.route("/ui/intercept/list", methods=["GET"])
def intercept_list():
    db = load_intercepts()
    # only pending (no decision yet)
    pending = [v for v in db.values() if not v.get("decision")]
    # newest first
    pending.sort(key=lambda x: x.get("created", 0), reverse=True)
    return jsonify(pending)

# -------------------- Request Engine / Repeater --------------------
@app.route("/reqs/send", methods=["POST"])
def reqs_send():
    j = request.get_json() or {}
    url = j.get("url")
    method = (j.get("method","GET") or "GET").upper()
    headers = j.get("headers") or {}
    body = j.get("body") or None
    timeout = float(j.get("timeout", 20))
    allow_redirects = bool(j.get("allow_redirects", True))
    verify_ssl = bool(j.get("verify_ssl", True))
    proxy = j.get("proxy")
    proxies = {"http": proxy, "https": proxy} if proxy else None

    if not url:
        return jsonify({"error":"missing url"}), 400

    parsed = urlparse(url)
    if is_private_host(parsed.hostname):
        return jsonify({"error":"target blocked by server policy (local/private host)"}), 403

    start = time.time()
    try:
        resp = requests.request(
            method, url, headers=headers, data=body, timeout=timeout,
            allow_redirects=allow_redirects, verify=verify_ssl, proxies=proxies
        )
        elapsed = time.time() - start

        safe_headers = {str(k): str(v) for k, v in resp.headers.items()}
        ctype = safe_headers.get("Content-Type", "")
        body_note = None

        is_binary = any(kw in (ctype or "").lower() for kw in [
            "application/octet-stream", "application/pdf", "application/zip",
            "image/", "audio/", "video/", "font/"
        ])

        if is_binary:
            body_txt = ""
            body_note = f"[binary content: {ctype}, {len(resp.content)} bytes not displayed]"
        else:
            MAX = 200_000
            txt = resp.text
            if len(txt) > MAX:
                body_txt = txt[:MAX]
                body_note = f"[truncated to {MAX} chars from {len(txt)}]"
            else:
                body_txt = txt

        cli_log(f"SEND {method} {url} -> {resp.status_code} ({elapsed:.2f}s)")
        out = {
            "status_code": resp.status_code,
            "reason": resp.reason,
            "headers": safe_headers,
            "body": body_txt,
            "elapsed": elapsed,
            "url": resp.url
        }
        if body_note:
            out["body_note"] = body_note

        return jsonify(out)

    except Exception as e:
        elapsed = time.time() - start
        cli_log(f"ERR  {method} {url}: {e}")
        return jsonify({"error": str(e), "elapsed": elapsed}), 500

@app.route("/reqs/list", methods=["GET"])
def reqs_list():
    db = load_req_db()
    return jsonify({k: {"name": v.get("name"), "url": v.get("url"), "method": v.get("method"), "last": v.get("last_saved", None)} for k,v in db.items()})

@app.route("/reqs/rawdb", methods=["GET"])
def reqs_rawdb():
    return jsonify(load_req_db())

@app.route("/reqs/save", methods=["POST"])
def reqs_save():
    data = request.get_json() or {}
    db = load_req_db()
    rid = data.get("id") or str(uuid.uuid4())
    db[rid] = {
        "name": data.get("name","untitled"),
        "url": data.get("url",""),
        "method": data.get("method","GET"),
        "headers": data.get("headers", {}),
        "body": data.get("body", ""),
        "created": data.get("created", time.time()),
        "last_saved": time.time()
    }
    save_req_db(db)
    cli_log(f"TEMPLATE save {rid} {db[rid]['name']}")
    return jsonify({"id": rid})

# -------------------- CLI logs --------------------
@app.route("/cli/logs", methods=["GET"])
def cli_logs():
    try:
        txt = CLI_LOG.read_text()
    except Exception:
        txt = ""
    return Response(txt, mimetype="text/plain")

@app.route("/cli/logs/clear", methods=["POST"])
def cli_logs_clear():
    CLI_LOG.write_text("")
    return jsonify({"status":"cleared"})

@app.route("/cli/logs/append", methods=["POST"])
def cli_logs_append():
    try:
        data = request.get_json() or {}
        msg = data.get("msg","")
    except Exception:
        msg = ""
    if msg:
        cli_log(msg, skip_internal=False)
    return jsonify({"ok":True})

# -------------------- PAC endpoint --------------------
@app.route("/pac")
def pac():
    proxy = request.args.get("proxy")
    if not proxy:
        return Response("Missing proxy param, e.g. /pac?proxy=myvps:8080", status=400)
    pac_js = f"""function FindProxyForURL(url, host) {{
    return "PROXY {proxy}; DIRECT";
}}"""
    return Response(pac_js, mimetype="application/x-ns-proxy-autoconfig")

# -------------------- run --------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6000, debug=False)
