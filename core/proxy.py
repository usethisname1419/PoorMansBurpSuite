# core/proxy.py  – mitmdump -s core/proxy.py -p 8080
from mitmproxy import http, ctx
import pathlib, time, uuid, json, requests, os
from urllib.parse import urlencode, urlparse

LOG_DIR       = pathlib.Path("logs")
INJECTED_FILE = LOG_DIR / "injected.json"
REQUEST_LOG   = LOG_DIR / "requests.log"
CFG_FILE      = LOG_DIR / "pmb_config.json"   # optional: written by dashboard

def ensure_dirs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not INJECTED_FILE.exists():
        INJECTED_FILE.write_text(json.dumps({}))
ensure_dirs()

# ---------- config helpers ----------
def _load_cfg_file():
    try:
        return json.loads(CFG_FILE.read_text())
    except Exception:
        return {}

def _get_config_value(cli_value, env_name, file_key, default):
    # precedence: mitm --set > env > logs/pmb_config.json > default
    if cli_value:
        return cli_value
    env_v = os.environ.get(env_name)
    if env_v:
        return env_v
    file_v = _load_cfg_file().get(file_key)
    if file_v:
        return file_v
    return default

class InjectorAddon:
    def __init__(self):
        # populated in load()
        self.dashboard_url = None
        self.callback_base = None
        self._last_toggle = {"t": 0.0, "enabled": False}
        ctx.log.info("InjectorAddon initialized")

    def load(self, loader):
        # Allow --set pmb_dashboard_url=... --set pmb_callback_base=...
        loader.add_option(
            "pmb_dashboard_url",
            str,
            "",  # empty means "not set on CLI"
            "PoorMansBurp dashboard base URL, e.g. http://10.0.0.5:6000",
        )
        loader.add_option(
            "pmb_callback_base",
            str,
            "",
            "Callback base for injected beacons, e.g. http://10.0.0.5:5000/callback",
        )

    def configure(self, updates):
        # Recompute effective endpoints whenever options change
        self.dashboard_url = _get_config_value(
            ctx.options.pmb_dashboard_url,
            "PMB_DASHBOARD_URL",
            "dashboard_url",
            "http://127.0.0.1:6000",
        ).rstrip("/")
        self.callback_base = _get_config_value(
            ctx.options.pmb_callback_base,
            "PMB_CALLBACK_BASE",
            "callback_base",
            "http://127.0.0.1:5000/callback",
        ).rstrip("/")
        ctx.log.info(f"[PMB] dashboard_url={self.dashboard_url}  callback_base={self.callback_base}")

    # -------- basic log
    def _log_request_line(self, line: str):
        with open(REQUEST_LOG, "a") as f:
            f.write(line + "\n")

    # -------- HTTP to dashboard
    def _post_to_dashboard(self, path: str, payload: dict, timeout=4):
        url = self.dashboard_url + path
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            if not r.ok:
                ctx.log.error(f"[proxy->dashboard] POST {url} -> {r.status_code} {r.text[:200]}")
            return r
        except Exception as e:
            ctx.log.error(f"[proxy->dashboard] POST {url} failed: {e}")
            return None

    def _get_from_dashboard(self, path: str, params: dict | None = None, timeout=4):
        url = self.dashboard_url + path
        try:
            r = requests.get(url, params=params or {}, timeout=timeout)
            if not r.ok:
                ctx.log.error(f"[proxy->dashboard] GET  {r.url} -> {r.status_code} {r.text[:200]}")
            return r
        except Exception as e:
            ctx.log.error(f"[proxy->dashboard] GET  {url} failed: {e}")
            return None

    # -------- intercept toggle (cached)
    def _intercept_enabled_globally(self) -> bool:
        now = time.time()
        if now - self._last_toggle["t"] < 1.0:
            return self._last_toggle["enabled"]
        try:
            r = self._get_from_dashboard("/ui/intercept/status", timeout=1.5)
            if r and r.ok:
                self._last_toggle["enabled"] = bool(r.json().get("enabled", False))
        except Exception:
            pass
        self._last_toggle["t"] = now
        return self._last_toggle["enabled"]

    def _hostname(self, url: str) -> str | None:
        try:
            return urlparse(url).hostname
        except Exception:
            return None

    def _is_internal(self, url: str) -> bool:
        h = (self._hostname(url) or "").lower()
        if not h:
            return False
        dash_h = (self._hostname(self.dashboard_url) or "").lower()
        cb_h   = (self._hostname(self.callback_base) or "").lower()
        return h in {dash_h, cb_h, "localhost", "127.0.0.1"}

    # ---------- intercept helpers ----------
    def _send_intercept(self, flow_id: str, flow: http.HTTPFlow, client_ip: str | None):
        req = flow.request
        try:
            try:
                body_text = req.get_text(strict=False)
            except TypeError:
                try:
                    body_text = req.get_text()
                except Exception:
                    body_text = None
            payload = {
                "flow_id": flow_id,
                "data": {
                    "method": req.method,
                    "url": req.url,
                    "path": req.path,
                    "http_version": req.http_version,
                    "headers": dict(req.headers),
                    "body": body_text,
                    "client_addr": client_ip
                }
            }
            self._post_to_dashboard("/cli/intercept/new", payload, timeout=3)
            ctx.log.debug(f"[intercept] posted {flow_id}")
        except Exception as e:
            ctx.log.debug(f"[intercept] post exception: {e}")

    def _poll_decision(self, flow_id: str) -> dict:
        start = time.time()
        params = {"flow_id": flow_id}
        while (time.time() - start) < 30:
            r = self._get_from_dashboard("/cli/intercept/decision", params=params, timeout=2.0)
            if r and r.ok:
                try:
                    j = r.json() or {}
                    if j.get("decision"):
                        return j
                except Exception:
                    pass
            time.sleep(0.5)
        return {"decision": "forward"}

    # ---------- mitm hooks ----------
    def request(self, flow: http.HTTPFlow):
        req = flow.request
        # client ip
        try:
            client_ip = flow.client_conn.peername[0]
        except Exception:
            try:
                client_ip = str(flow.client_conn.address)
            except Exception:
                client_ip = None

        self._log_request_line(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] REQ {req.method} {req.pretty_url} UA:{req.headers.get('user-agent','')} from:{client_ip}")

        # injection trigger (unchanged)
        wants_inject = False
        if req.headers.get("X-Inject-Payload", "").lower() in ("1", "true", "yes"):
            wants_inject = True
        try:
            if "inject" in req.query and req.query.get("inject") in ("1", "true", "yes"):
                wants_inject = True
        except Exception:
            if "inject=1" in req.pretty_url:
                wants_inject = True
        if wants_inject:
            inj_id = str(uuid.uuid4())
            try:
                data = json.loads(INJECTED_FILE.read_text())
            except Exception:
                data = {}
            data[inj_id] = {
                "time": time.time(),
                "method": req.method,
                "url": req.pretty_url,
                "client_ip": client_ip,
                "user_agent": req.headers.get("user-agent", ""),
                "injected": False
            }
            INJECTED_FILE.write_text(json.dumps(data, indent=2))
            req.headers["X-Injection-Id"] = inj_id
            ctx.log.info(f"Marked flow for injection id={inj_id}")

        # skip internal dashboard/callback traffic
        if self._is_internal(req.url):
            return

        global_on = self._intercept_enabled_globally()
        per_req   = req.headers.get("X-Intercept", "").lower() in ("1", "true", "yes")
        try:
            if "intercept" in req.query and req.query.get("intercept") in ("1", "true", "yes"):
                per_req = True
        except Exception:
            if "intercept=1" in req.pretty_url:
                per_req = True

        if not (global_on or per_req):
            return  # forward

        # intercept
        try:
            flow_id = str(uuid.uuid4())
            self._send_intercept(flow_id, flow, client_ip)
            decision = self._poll_decision(flow_id)
            dec = decision.get("decision", "forward")

            if dec == "drop":
                flow.response = http.HTTPResponse.make(
                    418, b"Intercepted and dropped by operator", {"Content-Type": "text/plain"}
                )
                ctx.log.info(f"[intercept] drop {flow_id} {req.url}")
                return

            if dec == "modify":
                mod = decision.get("modified") or {}
                if mod.get("method"): req.method = str(mod["method"])
                if mod.get("url"):
                    try:
                        req.url = str(mod["url"])
                    except Exception:
                        try:
                            p = urlparse(str(mod["url"]))
                            if p.hostname: req.host = p.hostname
                            if p.path:     req.path = p.path
                        except Exception:
                            pass
                if isinstance(mod.get("headers"), dict):
                    try:
                        req.headers.clear()
                        for k, v in mod["headers"].items():
                            req.headers[k] = str(v)
                    except Exception:
                        pass
                if "body" in mod:
                    try:
                        req.set_text("" if mod["body"] is None else str(mod["body"]))
                    except Exception:
                        try:
                            req.content = ("" if mod["body"] is None else str(mod["body"])).encode("utf-8")
                        except Exception:
                            pass
                ctx.log.info(f"[intercept] modify+forward {flow_id} {req.url}")
                return

            ctx.log.debug(f"[intercept] forward {flow_id} {req.url}")

        except Exception as e:
            ctx.log.debug(f"[intercept] exception (forwarding): {e}")
            return

    def response(self, flow: http.HTTPFlow):
        res = flow.response
        self._log_request_line(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] RES {flow.request.method} {flow.request.pretty_url} -> {res.status_code} ({res.headers.get('content-type','')})")

        inj_id = flow.request.headers.get("X-Injection-Id")
        if not inj_id:
            return
        ct = (res.headers.get("content-type", "") or "").lower()
        if ("text/html" in ct) or ("application/xhtml+xml" in ct):
            try:
                body = res.get_text()
                cb_url = f"{self.callback_base}?{urlencode({'id': inj_id, 'source': 'proxy-inject'})}"
                snippet = f'\n<!-- injected by pentest-sim id={inj_id} -->\n<img src="{cb_url}" alt="" style="display:none" />\n'
                lower = body.lower()
                idx = lower.rfind("</body>") if "</body>" in lower else -1
                body = (body[:idx] + snippet + body[idx:]) if idx >= 0 else (body + snippet)
                res.set_text(body)

                try:
                    data = json.loads(INJECTED_FILE.read_text())
                except Exception:
                    data = {}
                if inj_id in data:
                    data[inj_id]["injected"] = True
                    data[inj_id]["injected_at"] = time.time()
                    INJECTED_FILE.write_text(json.dumps(data, indent=2))

                ctx.log.info(f"Injected payload id={inj_id} into response {flow.request.pretty_url}")
            except Exception as e:
                ctx.log.error(f"Injection failed for id={inj_id}: {e}")

addons = [InjectorAddon()]
