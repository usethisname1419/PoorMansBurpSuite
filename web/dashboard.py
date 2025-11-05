#!/usr/bin/env python3
"""
Simple server-side proxy + dashboard.

Usage:
  python web/dashboard.py

Then visit: http://<vps-ip>:6000/ to use the UI.

How it works:
- /            -> basic UI with an input for target URL and injection toggle
- /browse      -> returns HTML fetched from target URL (rewritten to route assets via /asset)
               -> if ?inject=1 is present, injects the callback payload (img tag) into the HTML
- /asset       -> fetches resources (css/js/img/etc) from original site and returns them
               -> proxied via the Flask server so iframe can load them
Notes:
- This is a simple approach. It rewrites src/href attributes and the <base> tag, but complex webapps (CSP, heavy JS, websockets, auth) may not behave perfectly.
- Keep this behind auth or VPN in production.
"""

from flask import Flask, request, Response, render_template_string, redirect, url_for
import requests, urllib.parse, time, json
from bs4 import BeautifulSoup
from pathlib import Path
import json, time


app = Flask(__name__)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "pentest-sim-proxy/1.0"})
LOG_DIR = Path("logs")
CALLBACK_LOG = LOG_DIR / "callbacks.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)
if not CALLBACK_LOG.exists():
    CALLBACK_LOG.write_text("[]")

# Set callback base to your public domain when deployed (or an interactsh URL).
# For local testing leave it as localhost:5000 (your Flask callback)
CALLBACK_BASE = "http://127.0.0.1:5000/callback"

# Simple UI template
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Pentest-sim Dashboard</title>
  <style>
    body{font-family:system-ui,Helvetica,Arial;margin:12px}
    label{display:inline-block;margin-right:8px}
    input[type=text]{width:60%}
    #frame{width:100%;height:80vh;border:1px solid #ccc}
    .controls{margin-bottom:8px}
  </style>
</head>
<body>
  <h2>PoorMansBurp — Server-side Proxy Dashboard</h2>
  <i>By: usethisname1419 - Derek Johnston<i>
  <form id="browse" action="/browse" method="get" target="preview">
    <div class="controls">
      <label>Target URL</label>
      <input type="text" name="url" placeholder="http://example.com" value="{{url|default('')}}" required />
      <label>Inject</label>
      <input type="checkbox" name="inject" value="1" {{ 'checked' if inject else '' }}>
      <button type="submit">Open</button>
      <button type="button" onclick="document.getElementById('browse').submit()">Open in iframe</button>
      <button type="button" onclick="window.open('/browse?url='+encodeURIComponent(document.querySelector('[name=url]').value),'_blank')">Open new tab</button>
    </div>
  </form>
  <iframe id="frame" name="preview" src="about:blank"></iframe>
  <p>Tip: use an HTTP target for quick tests. HTTPS targets will work but ensure the VPS can reach them.</p>
  <!-- Callbacks panel (paste into INDEX_HTML) -->
<div id="callbacks" style="margin-top:12px;max-height:25vh;overflow:auto;border:1px solid #ddd;padding:8px;background:#f9f9f9">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <strong>Callbacks</strong>
    <div>
      <button id="cb-clear">Clear</button>
      <button id="cb-refresh">Refresh</button>
    </div>
  </div>
  <div id="cb-list" style="margin-top:8px;font-size:13px">Loading…</div>
</div>

<script>
async function loadCallbacks(){
  const r = await fetch('/ui/callbacks');51.210.0.171

  const list = r.ok ? await r.json() : [];
  const el = document.getElementById('cb-list');
  if(!Array.isArray(list) || list.length===0){ el.innerHTML = '<div>No callbacks</div>'; return; }
  const items = list.slice().reverse().slice(0,200);
  el.innerHTML = items.map(it => {
    const t = it.time ? new Date(it.time*1000).toLocaleString() : '';
    const id = it.injection_id ? ` <code>${it.injection_id}</code>` : '';
    const ip = it.remote_addr ? ` from ${it.remote_addr}` : '';
    const args = it.args && Object.keys(it.args).length ? `<div style="font-size:12px;color:#555">args: ${JSON.stringify(it.args)}</div>` : '';
    return `<div style="border-bottom:1px solid #eee;padding:6px 0"><div style="color:#666;font-size:12px">${t}${id}${ip}</div>${args}</div>`;
  }).join('');
}
document.getElementById('cb-refresh').addEventListener('click', loadCallbacks);
document.getElementById('cb-clear').addEventListener('click', async ()=>{
  await fetch('/ui/callbacks/clear', {method:'POST'});
  loadCallbacks();
});
loadCallbacks(); // one-time load; no setInterval
</script>

</body>
</html>
"""

def build_callback_snippet(inj_id: str):
    # Relative to dashboard host, so it works from any client browser
    cb = f"/ui/hit?id={inj_id}&source=ui"
    return f'<!-- injected by pentest-sim id={inj_id} -->\n<img src="{cb}" alt="" style="display:none" />\n'

def make_abs_url(base: str, link: str):
    return urllib.parse.urljoin(base, link)

@app.route("/")
def index():
    return render_template_string(INDEX_HTML, url=request.args.get("url",""), inject=(request.args.get("inject")=="1"))

@app.route("/browse")
def browse():
    target = request.args.get("url")
    inject = request.args.get("inject", "0") == "1"
    if not target:
        return redirect(url_for("index"))

    # Normalize
    if not urllib.parse.urlparse(target).scheme:
        target = "http://" + target

    # Fetch the page server-side
    try:
        res = SESSION.get(target, timeout=15, allow_redirects=True)
    except Exception as e:
        return Response(f"Error fetching target: {e}", status=502)

    content_type = res.headers.get("content-type","")
    # If not HTML, just stream the resource back (no iframe)
    if "text/html" not in content_type.lower():
        # return as direct response
        return Response(res.content, headers={"Content-Type": content_type})

    body = res.text
    # Parse and rewrite resource links
    soup = BeautifulSoup(body, "html.parser")

    # Rewrite <base> or create one to help with relative URLs
    base_tag = soup.find("base")
    if base_tag and base_tag.get("href"):
        base_url = make_abs_url(res.url, base_tag["href"])
    else:
        base_url = res.url

    # Rewrite all src, href attributes to route through /asset
    for tag in soup.find_all(src=True):
        orig = tag["src"]
        new = "/asset?url=" + urllib.parse.quote_plus(make_abs_url(base_url, orig))
        tag["src"] = new
    for tag in soup.find_all(href=True):
        orig = tag["href"]
        # skip anchor links
        if orig.startswith("#"):
            continue
        new = "/asset?url=" + urllib.parse.quote_plus(make_abs_url(base_url, orig))
        tag["href"] = new

    # Optional: inject snippet before </body>
    if inject:
        inj_id = f"ui-{int(time.time()*1000)}"
        snippet = BeautifulSoup(build_callback_snippet(inj_id), "html.parser")
        if soup.body:
            soup.body.append(snippet)
        else:
            soup.append(snippet)

    # Return rewritten HTML
    out_html = str(soup)
    return Response(out_html, headers={"Content-Type": "text/html; charset=utf-8"})

@app.route("/asset")
def asset():
    # Fetch and return raw asset. Use `url` param which should be absolute.
    u = request.args.get("url")
    if not u:
        return Response("Missing url", status=400)
    u = urllib.parse.unquote_plus(u)

    # Basic protections: disallow internal addresses unless explicitly allowed
    parsed = urllib.parse.urlparse(u)
    if parsed.hostname in ("127.0.0.1","localhost"):
        # Prevent SSRF to local loopback by default (comment out if you trust your environment)
        return Response("Fetching local addresses is blocked by server policy.", status=403)

    try:
        r = SESSION.get(u, stream=True, timeout=15)
    except Exception as e:
        return Response(f"Error fetching asset: {e}", status=502)

    headers = {"Content-Type": r.headers.get("content-type","application/octet-stream")}
    # pass-through some caching headers (optional)
    if "cache-control" in r.headers:
        headers["Cache-Control"] = r.headers["cache-control"]
    return Response(r.content, headers=headers)

from flask import jsonify, request, Response

@app.route("/ui/hit", methods=["GET", "POST"])
def ui_hit():
    """Record a callback hit directly from the browser -> dashboard."""
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


if __name__ == "__main__":
    # Run on port 6000 by default
    app.run(host="0.0.0.0", port=6000, debug=False)
