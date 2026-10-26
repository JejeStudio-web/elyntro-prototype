"""Optional Studio APIs with guarded integrations."""
from __future__ import annotations
import base64, copy, json, os, re, secrets, threading, time, uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from flask import Blueprint, jsonify, request
from search_providers import is_public_http_url

studio_api = Blueprint("studio_api", __name__, url_prefix="/api/studio")
ROOT = Path(__file__).resolve().parent
_sessions, _sessions_lock = {}, threading.RLock()
_proposals, _proposals_lock = {}, threading.RLock()

def _authorized():
    expected=os.environ.get("ELYNTRO_FOUNDER_TOKEN","").strip()
    header=request.headers.get("Authorization","")
    supplied=header[7:].strip() if header.lower().startswith("bearer ") else ""
    return bool(expected and supplied and secrets.compare_digest(expected,supplied))
def _denied():
    if not os.environ.get("ELYNTRO_FOUNDER_TOKEN","").strip():
        return jsonify({"error":"Founder Mode cerrado: configura ELYNTRO_FOUNDER_TOKEN como secreto del servidor."}),503
    if not _authorized(): return jsonify({"error":"Se requiere autenticación Founder Mode."}),401
    return None
def _body():
    value=request.get_json(silent=True)
    return value if isinstance(value,dict) else {}
def _text(value,limit=2000):
    return value.strip()[:limit] if isinstance(value,str) else ""

def validate_scene(scene):
    if not isinstance(scene,dict): raise ValueError("scene debe ser objeto JSON.")
    if len(json.dumps(scene,ensure_ascii=False).encode())>65536: raise ValueError("La escena supera 64 KiB.")
    if set(scene)-{"title","background","camera","objects"}: raise ValueError("Campos de escena no permitidos.")
    camera=scene.get("camera",{})
    if not isinstance(camera,dict) or set(camera)-{"position","lookAt","fov"}: raise ValueError("camera no válido.")
    def vec(value,default,lo=-1000,hi=1000):
        value=default if value is None else value
        if not isinstance(value,list) or len(value)!=3 or any(type(n) not in (int,float) or not lo<=n<=hi for n in value): raise ValueError("Se requiere un vector numérico de tres componentes.")
        return [float(n) for n in value]
    fov=camera.get("fov",50)
    if type(fov) not in (int,float) or not 20<=fov<=100: raise ValueError("FOV debe estar entre 20 y 100.")
    objects=scene.get("objects",[])
    if not isinstance(objects,list) or len(objects)>80: raise ValueError("Hasta 80 objetos permitidos.")
    clean=[]
    for obj in objects:
        if not isinstance(obj,dict) or set(obj)-{"type","name","position","rotation","scale","color","roughness","metalness"}: raise ValueError("Objeto no válido.")
        kind=obj.get("type","box")
        if kind not in {"box","sphere","cylinder","cone","torus","plane","icosahedron"}: raise ValueError("Geometría no permitida.")
        color=obj.get("color","#8295f5")
        if not isinstance(color,str) or not re.fullmatch(r"#[0-9a-fA-F]{6}",color): raise ValueError("Color hexadecimal no válido.")
        def prop(key,default,lo=-100,hi=100):
            return vec(obj.get(key,default),default,lo,hi)
        rough=obj.get("roughness",.55); metal=obj.get("metalness",.08)
        if any(type(n) not in (int,float) or not 0<=n<=1 for n in (rough,metal)): raise ValueError("Material fuera de rango.")
        clean.append({"type":kind,"name":_text(obj.get("name",kind),80) or kind,"position":prop("position",[0,0,0]),"rotation":prop("rotation",[0,0,0],-1000,1000),"scale":prop("scale",[1,1,1],.01,100),"color":color,"roughness":float(rough),"metalness":float(metal)})
    bg=scene.get("background","#101421")
    if not isinstance(bg,str) or not re.fullmatch(r"#[0-9a-fA-F]{6}",bg): raise ValueError("Fondo hexadecimal no válido.")
    return {"title":_text(scene.get("title","Escena Elyntro"),120) or "Escena Elyntro","background":bg,"camera":{"position":vec(camera.get("position"),[4,3,6]),"lookAt":vec(camera.get("lookAt"),[0,0,0]),"fov":float(fov)},"objects":clean}

@studio_api.get("/status")
def status():
    try:
        import playwright.sync_api
        pw=True
    except Exception: pw=False
    raw=os.environ.get("ELYNTRO_COMFYUI_URL","").strip()
    u=urlparse(raw) if raw else None
    comfy=bool(u and u.scheme in {"http","https"} and u.hostname and not u.username and not u.password)
    return jsonify({"ok":True,"features":{"threejs":{"available":True,"mode":"client-side declarative renderer"},"comfyui":{"configured":comfy,"workflow_required":True},"playwright":{"installed":pw,"founder_only":True,"chromium_required":True},"founder_mode":{"configured":bool(os.environ.get("ELYNTRO_FOUNDER_TOKEN","").strip()),"authenticated":_authorized()},"self_development":{"mode":"proposal ledger","automatic_publish":False}},"limitations":["Three.js module currently loads from CDN.","ComfyUI needs an operator-provided instance, workflow and models.","Free Render does not include Playwright Chromium or a GPU.","Proposal/session storage is in memory and resets on restart."]})

@studio_api.post("/scene/validate")
def scene():
    try: data=validate_scene(_body().get("scene"))
    except ValueError as exc: return jsonify({"error":str(exc)}),400
    return jsonify({"ok":True,"scene":data,"object_count":len(data["objects"]),"renderer":"three.js"})

@studio_api.post("/comfy/generate")
def comfy_generate():
    denied=_denied()
    if denied:return denied
    body=_body(); prompt=_text(body.get("prompt"))
    if not prompt:return jsonify({"error":"Prompt vacío."}),400
    base=os.environ.get("ELYNTRO_COMFYUI_URL","").strip().rstrip("/")
    wf=os.environ.get("ELYNTRO_COMFYUI_WORKFLOW","").strip()
    node_id=os.environ.get("ELYNTRO_COMFYUI_PROMPT_NODE","").strip()
    input_name=os.environ.get("ELYNTRO_COMFYUI_PROMPT_INPUT","text")
    if not base or not wf or not node_id:return jsonify({"error":"Configura ELYNTRO_COMFYUI_URL, ELYNTRO_COMFYUI_WORKFLOW y ELYNTRO_COMFYUI_PROMPT_NODE."}),503
    parsed=urlparse(base)
    if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password:return jsonify({"error":"URL de ComfyUI no válida."}),400
    path=(ROOT/wf).resolve()
    if ROOT not in path.parents or not path.is_file() or path.stat().st_size>1_000_000:return jsonify({"error":"Workflow debe ser JSON local dentro del repositorio y menor de 1 MB."}),400
    try:
        workflow=json.loads(path.read_text("utf-8")); node=workflow[node_id]; inputs=node["inputs"]
        if not isinstance(inputs,dict) or not isinstance(inputs.get(input_name),str):raise ValueError("Campo de texto del nodo no encontrado.")
        inputs[input_name]=prompt
        req=Request(base+"/prompt",data=json.dumps({"prompt":workflow,"client_id":"elyntro-"+uuid.uuid4().hex}).encode(),headers={"Content-Type":"application/json"},method="POST")
        with urlopen(req,timeout=8) as response: result=json.loads(response.read(256000).decode())
        if not isinstance(result,dict) or not result.get("prompt_id"):return jsonify({"error":"ComfyUI no devolvió prompt_id."}),502
        return jsonify({"ok":True,"provider":"comfyui","prompt_id":result["prompt_id"],"note":"Trabajo aceptado en cola; aún no confirma que la generación haya terminado."}),202
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError,TimeoutError) as exc:return jsonify({"error":"No se pudo enviar el workflow a ComfyUI.","detail":str(exc)[:240]}),502

def _url(value):
    if not isinstance(value,str) or len(value)>2048:raise ValueError("URL no válida.")
    parsed=urlparse(value)
    if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password or not is_public_http_url(value):raise ValueError("Solo se permiten destinos HTTP(S) públicos.")
    return value
def _session(sid):
    with _sessions_lock: s=_sessions.get(sid)
    if not s:raise KeyError("Sesión inexistente.")
    if time.monotonic()-s["created"]>1800:
        try:s["browser"].close();s["pw"].stop()
        except Exception:pass
        with _sessions_lock:_sessions.pop(sid,None)
        raise KeyError("Sesión caducada.")
    return s
def _guard(route):
    try:
        if not is_public_http_url(route.request.url):route.abort()
        else:route.continue_()
    except Exception:
        try:route.abort()
        except Exception:pass

@studio_api.post("/browser/launch")
def browser_launch():
    denied=_denied()
    if denied:return denied
    try:
        from playwright.sync_api import sync_playwright
        with _sessions_lock:
            if len(_sessions)>=3:return jsonify({"error":"Límite de tres sesiones alcanzado."}),429
        pw=sync_playwright().start(); browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        context=browser.new_context(accept_downloads=False,service_workers="block",viewport={"width":1280,"height":800})
        context.route("**/*",_guard); page=context.new_page(); sid=secrets.token_urlsafe(24)
        with _sessions_lock:_sessions[sid]={"pw":pw,"browser":browser,"context":context,"page":page,"created":time.monotonic()}
        return jsonify({"ok":True,"session_id":sid,"message":"Navegador aislado; no comparte cookies con el navegador personal."}),201
    except Exception as exc:return jsonify({"error":"Playwright/Chromium no está instalado o no puede iniciarse.","detail":str(exc)[:200]}),503

@studio_api.post("/browser/navigate")
def browser_navigate():
    denied=_denied()
    if denied:return denied
    b=_body()
    try:
        s=_session(b.get("session_id","")); url=_url(b.get("url","")); p=s["page"]
        p.goto(url,wait_until="domcontentloaded",timeout=12000)
        return jsonify({"ok":True,"url":p.url,"title":p.title(),"text":p.locator("body").inner_text(timeout=3000)[:10000]})
    except KeyError as exc:return jsonify({"error":str(exc)}),404
    except Exception as exc:return jsonify({"error":"Navegación fallida.","detail":str(exc)[:200]}),502

@studio_api.post("/browser/inspect")
def browser_inspect():
    denied=_denied()
    if denied:return denied
    try:
        p=_session(_body().get("session_id",""))["page"]
        controls=p.locator("a,button,input,textarea,select,[role=button]").evaluate_all("""els=>els.slice(0,100).map((e,index)=>({index,tag:e.tagName.toLowerCase(),text:(e.innerText||e.getAttribute('aria-label')||e.getAttribute('placeholder')||'').trim().slice(0,140),type:e.getAttribute('type')||'',disabled:!!e.disabled}))""")
        return jsonify({"ok":True,"url":p.url,"title":p.title(),"text":p.locator("body").inner_text(timeout=3000)[:10000],"controls":controls})
    except KeyError as exc:return jsonify({"error":str(exc)}),404
    except Exception as exc:return jsonify({"error":"Inspección fallida.","detail":str(exc)[:200]}),502

@studio_api.post("/browser/action")
def browser_action():
    denied=_denied()
    if denied:return denied
    b=_body(); action=b.get("action")
    if action not in {"click","fill","scroll","back","forward"}:return jsonify({"error":"Acción no permitida."}),400
    try:
        p=_session(b.get("session_id",""))["page"]
        if action in {"back","forward"}:(p.go_back if action=="back" else p.go_forward)(wait_until="domcontentloaded",timeout=8000)
        elif action=="scroll":
            amount=b.get("amount",500)
            if type(amount) is not int or abs(amount)>2000:return jsonify({"error":"amount debe estar entre -2000 y 2000."}),400
            p.mouse.wheel(0,amount)
        else:
            selector=b.get("selector","")
            if not isinstance(selector,str) or not selector.strip() or len(selector)>300:return jsonify({"error":"Selector CSS requerido."}),400
            loc=p.locator(selector).first
            if action=="click":loc.click(timeout=4000)
            else:
                value=b.get("value","")
                if not isinstance(value,str) or len(value)>2000:return jsonify({"error":"Texto demasiado largo."}),400
                loc.fill(value,timeout=4000)
        return jsonify({"ok":True,"url":p.url,"title":p.title(),"text":p.locator("body").inner_text(timeout=3000)[:5000]})
    except KeyError as exc:return jsonify({"error":str(exc)}),404
    except Exception as exc:return jsonify({"error":"Acción fallida.","detail":str(exc)[:200]}),502

@studio_api.post("/browser/guide")
def browser_guide():
    denied=_denied()
    if denied:return denied
    body=_body(); selector=body.get("selector",""); note=_text(body.get("note","Sigue este elemento."),300)
    if not isinstance(selector,str) or not selector.strip() or len(selector)>300:return jsonify({"error":"Selector CSS requerido."}),400
    try:
        page=_session(body.get("session_id",""))["page"]
        locator=page.locator(selector).first
        locator.wait_for(state="visible",timeout=4000)
        locator.evaluate("""e=>{e.scrollIntoView({behavior:'instant',block:'center',inline:'center'});e.dataset.elyntroGuideOriginalOutline=e.style.outline||'';e.dataset.elyntroGuideOriginalOutlineOffset=e.style.outlineOffset||'';e.style.outline='3px solid #b8c5ff';e.style.outlineOffset='4px';e.style.transition='outline-color 150ms ease';}""")
        label=locator.inner_text(timeout=1000)[:200] if locator.evaluate("(e)=>e.tagName.toLowerCase()") not in {"input","textarea","select"} else locator.get_attribute("aria-label") or locator.get_attribute("placeholder") or "campo"
        return jsonify({"ok":True,"guided_element":label,"instruction":note,"url":page.url,"message":"Elemento resaltado en el navegador aislado; la superposición solo existe en esa sesión."})
    except KeyError as exc:return jsonify({"error":str(exc)}),404
    except Exception as exc:return jsonify({"error":"No se pudo resaltar el elemento.","detail":str(exc)[:200]}),502

@studio_api.get("/browser/screenshot/<sid>")
def browser_screenshot(sid):
    denied=_denied()
    if denied:return denied
    try:
        p=_session(sid)["page"]; data=p.screenshot(type="png",full_page=False,timeout=5000)
        return jsonify({"ok":True,"url":p.url,"image":"data:image/png;base64,"+base64.b64encode(data).decode("ascii")})
    except KeyError as exc:return jsonify({"error":str(exc)}),404
    except Exception as exc:return jsonify({"error":"Captura fallida.","detail":str(exc)[:200]}),502

@studio_api.post("/browser/close")
def browser_close():
    denied=_denied()
    if denied:return denied
    sid=_body().get("session_id","")
    with _sessions_lock:s=_sessions.pop(sid,None)
    if not s:return jsonify({"error":"Sesión no encontrada."}),404
    for key in ("context","browser"):
        try:s[key].close()
        except Exception:pass
    try:s["pw"].stop()
    except Exception:pass
    return jsonify({"ok":True})

@studio_api.post("/founder/proposals")
def proposal_create():
    denied=_denied()
    if denied:return denied
    b=_body(); title=_text(b.get("title"),120); why=_text(b.get("rationale"),1500); patch=b.get("patch",""); tests=b.get("tests",[])
    if not title or not why or not isinstance(patch,str) or len(patch.encode())>20000:return jsonify({"error":"Título, justificación y cambio de hasta 20 KB obligatorios."}),400
    if not isinstance(tests,list) or len(tests)>20 or any(not isinstance(x,str) or len(x)>300 for x in tests):return jsonify({"error":"Lista tests no válida."}),400
    item={"id":uuid.uuid4().hex,"title":title,"rationale":why,"patch":patch,"tests":tests,"status":"proposed","created_at":int(time.time()),"published":False,"note":"Registrada en memoria; no se modifica el repositorio ni se ejecuta código."}
    with _proposals_lock:_proposals[item["id"]]=item
    return jsonify({"ok":True,"proposal":item}),201
@studio_api.get("/founder/proposals")
def proposal_list():
    denied=_denied()
    if denied:return denied
    with _proposals_lock:items=copy.deepcopy(list(_proposals.values()))
    return jsonify({"ok":True,"proposals":sorted(items,key=lambda x:x["created_at"],reverse=True)[:100]})
def _proposal_decision(pid,status,note):
    with _proposals_lock:
        item=_proposals.get(pid)
        if not item:return jsonify({"error":"Propuesta no encontrada."}),404
        if item["status"]!="proposed":return jsonify({"error":"La propuesta ya tiene una decisión."}),409
        item["status"]=status;item["note"]=note
        return jsonify({"ok":True,"proposal":copy.deepcopy(item)})
@studio_api.post("/founder/proposals/<pid>/approve")
def proposal_approve(pid):
    denied=_denied()
    if denied:return denied
    return _proposal_decision(pid,"approved_for_manual_review","Aprobada para revisión manual; no se ha aplicado ni publicado.")
@studio_api.post("/founder/proposals/<pid>/reject")
def proposal_reject(pid):
    denied=_denied()
    if denied:return denied
    return _proposal_decision(pid,"rejected","Rechazada; no se ha aplicado ningún cambio.")
