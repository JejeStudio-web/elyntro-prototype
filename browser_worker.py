"""Isolated, allowlisted Playwright worker. Run only as a private service/container."""
from __future__ import annotations
import base64, hmac, ipaddress, os, re, socket, urllib.parse
from flask import Flask, jsonify, request
from playwright.sync_api import sync_playwright

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
MAX_SCREENSHOT_BYTES = 1_500_000

def _domains():
    return [x.strip().lower().rstrip(".") for x in os.getenv("ELYNTRO_BROWSER_ALLOWLIST", "").split(",") if x.strip()]

def _safe_url(url, domains):
    if not isinstance(url, str) or not url or len(url) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in url): return False
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password: return False
        if p.port not in (None, 80, 443) or (p.scheme == "https" and p.port == 80) or (p.scheme == "http" and p.port == 443): return False
        host = p.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if not any(host == d or host.endswith("." + d) for d in domains): return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")): return False
        try: addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = [ipaddress.ip_address(x[4][0].split("%", 1)[0]) for x in socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)]
        return bool(addresses) and all(a.is_global for a in addresses)
    except (OSError, ValueError, UnicodeError): return False

def _authorized():
    expected = os.getenv("ELYNTRO_BROWSER_WORKER_TOKEN", "")
    value = request.headers.get("Authorization", "")
    supplied = value[7:].strip() if value.lower().startswith("bearer ") else ""
    return bool(expected and supplied) and hmac.compare_digest(expected, supplied)

def _snapshot(page):
    return page.evaluate("""() => ({title:(document.title||'').slice(0,300),url:location.href,text:(document.body?.innerText||'').slice(0,12000),links:Array.from(document.querySelectorAll('a[href]')).slice(0,80).map(a=>({text:(a.innerText||a.getAttribute('aria-label')||'').trim().slice(0,160),href:a.href})).filter(a=>/^https?:/.test(a.href))})""")

def _run(payload, domains, with_actions=False):
    url = payload.get("url")
    if not _safe_url(url, domains): return {"ok":False,"error":"URL pública no permitida o dominio fuera de la allowlist."},400
    actions = payload.get("actions", [])
    if with_actions:
        if not isinstance(actions,list) or len(actions)>8: return {"ok":False,"error":"actions debe contener como máximo 8 acciones."},400
        for a in actions:
            if not isinstance(a,dict) or a.get("type") not in {"click_link","click_button","hover_text","scroll"}: return {"ok":False,"error":"Tipo de acción no admitido."},400
            if a["type"] in {"click_link","click_button","hover_text"} and (not isinstance(a.get("text"),str) or not a["text"].strip() or len(a["text"])>160): return {"ok":False,"error":"Texto exacto de acción no válido."},400
            if a["type"]=="click_button" and re.search(r"delete|remove|purchase|buy|pay|submit|send|confirm|logout|log out|eliminar|borrar|comprar|pagar|enviar|confirmar|cerrar sesión",a["text"],re.I): return {"ok":False,"error":"Botón transaccional o destructivo bloqueado."},400
            if a["type"]=="scroll" and a.get("direction") not in {"up","down"}: return {"ok":False,"error":"Dirección de scroll no válida."},400
    completed=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=["--disable-background-networking","--disable-sync"])
            context=browser.new_context(viewport={"width":1280,"height":800},accept_downloads=False,service_workers="block")
            page=context.new_page()
            page.set_default_timeout(3000)
            page.route("**/*",lambda route: route.continue_() if _safe_url(route.request.url,domains) else route.abort())
            page.goto(url,wait_until="domcontentloaded",timeout=12000)
            if not _safe_url(page.url,domains):
                browser.close()
                return {"ok":False,"error":"Redirección a destino no permitido."},403
            if with_actions:
                for a in actions:
                    kind=a["type"]
                    if kind=="scroll":
                        page.evaluate("(d)=>window.scrollBy(0,d==='down'?Math.min(innerHeight*.8,640):-Math.min(innerHeight*.8,640))",a["direction"])
                        completed.append({"type":kind,"direction":a["direction"],"ok":True})
                    elif kind=="hover_text":
                        page.get_by_text(a["text"].strip(),exact=True).first.hover()
                        completed.append({"type":kind,"text":a["text"].strip(),"ok":True})
                    elif kind=="click_button":
                        loc=page.get_by_role("button",name=a["text"].strip(),exact=True).first
                        if not loc.evaluate("(el)=>!el.disabled&&!el.closest('form')&&!el.hasAttribute('download')"): raise ValueError("Botón deshabilitado o vinculado a formulario.")
                        loc.click()
                        completed.append({"type":kind,"text":a["text"].strip(),"ok":True})
                    else:
                        page.get_by_role("link",name=a["text"].strip(),exact=True).first.click()
                        page.wait_for_load_state("domcontentloaded",timeout=5000)
                        if not _safe_url(page.url,domains): raise ValueError("La navegación salió de la allowlist.")
                        completed.append({"type":kind,"text":a["text"].strip(),"ok":True,"url":page.url})
            snap=_snapshot(page)
            shot=page.screenshot(type="jpeg",quality=40,full_page=False)
            if len(shot)>MAX_SCREENSHOT_BYTES: raise ValueError("Captura excede el límite.")
            encoded=base64.b64encode(shot).decode("ascii")
            browser.close()
        out={"ok":True,"mode":"isolated_ephemeral","snapshot":snap,"screenshot_data_url":"data:image/jpeg;base64,"+encoded}
        out["actions_completed" if with_actions else "actions_taken"]=completed if with_actions else []
        return out,200
    except Exception as exc:
        return {"ok":False,"error":"La sesión de navegador no pudo completar la petición.","detail":type(exc).__name__,"actions_completed":completed},502

@app.get("/healthz")
def healthz(): return jsonify({"ok":True,"service":"elyntro-browser-worker","browser":"chromium"})

@app.post("/v1/inspect")
def inspect():
    if not _authorized(): return jsonify({"ok":False,"error":"Unauthorized"}),401
    domains=_domains()
    if not domains: return jsonify({"ok":False,"error":"Allowlist vacía"}),503
    result,status=_run(request.get_json(silent=True) or {},domains)
    return jsonify(result),status

@app.post("/v1/act")
def act():
    if not _authorized(): return jsonify({"ok":False,"error":"Unauthorized"}),401
    domains=_domains()
    if not domains: return jsonify({"ok":False,"error":"Allowlist vacía"}),503
    result,status=_run(request.get_json(silent=True) or {},domains,True)
    return jsonify(result),status

if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
