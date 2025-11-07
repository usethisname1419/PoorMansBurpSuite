#!/usr/bin/env python3
"""
Master launcher (single Python process).

Starts:
 - core.callback.app (Flask)  -> default port 5000 (configurable)
 - web.dashboard.app (Flask)  -> default port 5002 (configurable)
 - mitmdump subprocess using core/proxy.py

Usage examples:
  # start everything (dashboard on 5002, callback on 5000)
  python -m cli.main --proxy --dashboard

  # start only dashboard + callback (no mitm)
  python -m cli.main --dashboard

  # specify mitmdump path/port and dashboard port
  python -m cli.main --proxy --mitm-path /usr/bin/mitmdump --mitm-port 8080 --dashboard --dashboard-port 5002 --callback-port 5000
"""
import argparse
import os
import signal
import shutil
import subprocess
import sys
import time
from threading import Thread
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def find_executable(name):
    return shutil.which(name)

def run_subprocess_stream(cmd, name_prefix=None):
    """
    Start subprocess and stream its stdout/stderr to this process stdout.
    Returns Popen object.
    """
    if name_prefix is None:
        name_prefix = Path(cmd[0]).name
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    def _stream():
        try:
            for line in p.stdout:
                print(f"[{name_prefix}] {line.rstrip()}")
        except Exception:
            pass
    t = Thread(target=_stream, daemon=True)
    t.start()
    return p

def run_mitm(mitm_path, addon_path, mitm_port, dashboard_port, callback_port, host="127.0.0.1"):
    mitm_bin = find_executable(mitm_path)
    if mitm_bin is None:
        print(f"[launcher] mitmdump not found at '{mitm_path}'.")
        return None
    if not Path(addon_path).exists():
        print(f"[launcher] expected addon '{addon_path}' not found.")
        return None
    cmd = [
        mitm_bin, "-s", str(addon_path), "-p", str(mitm_port),
        "--set", f"pmb_dashboard_url=http://{host}:{dashboard_port}",
        "--set", f"pmb_callback_base=http://{host}:{callback_port}/callback",
    ]
    print(f"[launcher] launching mitmdump: {' '.join(cmd)}")
    return run_subprocess_stream(cmd, name_prefix="mitmdump")


def run_flask_in_thread(module_path, attr_name="app", host="0.0.0.0", port=5000):
    """
    Import module_path (e.g. 'core.callback') and run the Flask app in a daemon thread.
    Expects the module to expose `app` (Flask instance).
    """
    sys.path.insert(0, str(ROOT))
    try:
        mod = __import__(module_path, fromlist=[attr_name])
    except Exception as e:
        print(f"[launcher] failed to import {module_path}: {e}")
        return None

    if not hasattr(mod, attr_name):
        print(f"[launcher] module {module_path} does not expose '{attr_name}'.")
        return None

    app = getattr(mod, attr_name)

    def _run_app():
        # note: use_reloader=False to avoid double-start
        print(f"[launcher] starting {module_path} on {host}:{port} (threaded)")
        try:
            app.run(host=host, port=port, debug=False, use_reloader=False)
        except Exception as e:
            print(f"[{module_path}] app.run exited: {e}")

    t = Thread(target=_run_app, daemon=True)
    t.start()
    return t

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--proxy", action="store_true", help="Start mitmdump proxy subprocess")
    p.add_argument("--mitm-path", default="mitmdump", help="mitmdump binary path")
    p.add_argument("--mitm-port", type=int, default=8080, help="mitmdump listen port")
    p.add_argument("--dashboard", action="store_true", help="Start dashboard (web.dashboard) in-process")
    p.add_argument("--dashboard-port", type=int, default=5002, help="Dashboard port (defaults to 5002)")
    p.add_argument("--callback", action="store_true", help="Start callback (core.callback) in-process")
    p.add_argument("--callback-port", type=int, default=5000, help="Callback port (defaults to 5000)")
    return p.parse_args()

def main():
    args = parse_args()
    if args.proxy:
        os.environ["PMB_MITM_PROXY"] = f"http://127.0.0.1:{args.mitm_port}"
    children = []   # subprocesses (mitmdump)
    threads = []    # in-process threads (Flask apps)

    def shutdown_handler(signum, frame):
        print("\n[launcher] shutdown signal received, terminating children...")
        # terminate subprocesses
        for p in children:
            if p and getattr(p, "poll", None) and p.poll() is None:
                try:
                    print(f"[launcher] terminating pid {p.pid}")
                    p.terminate()
                except Exception as e:
                    print(f"[launcher] error terminating pid {p.pid}: {e}")
        # give them a second
        time.sleep(1)
        for p in children:
            if p and getattr(p, "poll", None) and p.poll() is None:
                try:
                    print(f"[launcher] killing pid {p.pid}")
                    p.kill()
                except Exception:
                    pass
        print("[launcher] exiting.")
        # exit entire process
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    if not (args.proxy or args.dashboard or args.callback):
        print("[launcher] nothing requested. Use --proxy, --dashboard and/or --callback. See --help.")
        sys.exit(1)

    # Start callback app in-process (if requested)
    if args.callback:
        t = run_flask_in_thread("core.callback", attr_name="app", host="0.0.0.0", port=args.callback_port)
        if t:
            threads.append(("core.callback", t))

    # Start dashboard app in-process (if requested)
    if args.dashboard:
        # ensure web.dashboard module exists
        t = run_flask_in_thread("web.dashboard", attr_name="app", host="0.0.0.0", port=args.dashboard_port)
        if t:
            threads.append(("web.dashboard", t))

    # Start mitmdump as a subprocess (if requested)
    if args.proxy:
        addon = ROOT / "core" / "proxy.py"
        p = run_mitm(args.mitm_path, addon, args.mitm_port, args.dashboard_port, args.callback_port, host="127.0.0.1")
        if p: children.append(p)


    # main loop: keep process alive while threads/subprocesses run
    try:
        while True:
            alive = False
            # check subprocesses
            for p in children:
                if p and getattr(p, "poll", None) and p.poll() is None:
                    alive = True
            # check threads
            for name, t in threads:
                if t.is_alive():
                    alive = True
                else:
                    print(f"[launcher] thread {name} not alive")
            if not alive:
                print("[launcher] no active components left, exiting.")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown_handler(None, None)

if __name__ == "__main__":
    main()
