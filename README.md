## poormansburp

i’m building a replacement for burpsuite.

this will be a long-term development project. it runs on a vps and does stuff (proxy + dashboard + callback + mitmdump integration). if you want to help with the project send me a telegram message: @usethisusername

![poormansburp dashboard](Screenshot%202025-11-07%20121118.png)
![poormansburp dashboard](Screenshot%202025-11-06%20165016.png)
![poormansburp dashboard](Screenshot%202025-11-06%20165130.png)


## what it is 

poormansburp is a server-first, backend-first pentest toolkit that combines:

an interception addon (mitmdump)

a server-side dashboard (iframe-based browsing + injection)

an integrated callback listener for blind/OOB confirmation

it’s intended to run on a remote VPS so you can leave it running and access the UI from anywhere. long term this will get more payload modules, headless-browser rendering, sqlite storage, and nicer reporting.

## to use

run everything from one launcher on the VPS:
```
python3 -m cli.main --proxy --dashboard --callback \
  --dashboard-port 5002 --callback-port 5005 --mitm-port 8080
```

open the dashboard in your browser:

http://<VPS-IP>:5002/


use the dashboard to browse proxied sites, toggle injection, and view callbacks.

dev notes / quick facts

the dashboard injects callbacks that hit the dashboard endpoint (no 127.0.0.1 loopback confusion for clients).

mitmdump runs as a subprocess for full mitmproxy capabilities — keep it local unless you intentionally proxy remote traffic.

To use callbacks (collab)

`http://yourvpsip:port/callback`

logs live in logs/ (requests.log, injected.json, callbacks.json).


## recommended deployment

small Ubuntu VPS (22.04 or similar)

nginx in front to terminate TLS and protect access (keep callback internal if possible)

run the launcher under systemd for reliability

firewall: allow SSH and only the ports you expose (dashboard/nginx). do not expose mitmdump publicly unless you mean to.


## testing tips

quick test endpoint: use webhook.site for a public one-off test

temporarily expose local callback with ngrok if you want to receive public callbacks to 127.0.0.1:5005

for blind DNS/HTTP testing use Interactsh or Burp Collaborator


## important

only use this tool against targets you own or are authorized to test. unauthorized testing is illegal.

This is in development, don't go test strange sites because there could be security risk!!


## help / contribute

this is a long-term project. help welcome (code, testing, docs). message me on Telegram: @usethisusername.


## quick troubleshooting

missing deps → pip install -r requirements.txt (use venv)

port in use → sudo ss -lntp | grep :5005 then kill conflicting process

iframe injections not triggering → mixed content (HTTPS target, HTTP callback) — run dashboard under HTTPS or test HTTP pages

If you don't have a VPS you can use tunnels like serveo, ngrok, zrok

## Issues

The interceptor is fairly buggy, sometimes it works

Security Issues

## Supoort

if you would like to see this grow into a full blown burp replacement thats free please donate BTC `bc1qtezfajhysn6dut07m60vtg0s33jy8tqcvjqqzk`
