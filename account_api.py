"""Password-based accounts and PostgreSQL-backed conversation sync for Elyntro.

Requires DATABASE_URL (PostgreSQL) and Flask's ELYNTRO_SESSION_SECRET.
No credentials are logged; every data query is scoped to the signed-in user.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

account_api = Blueprint("account_api", __name__, url_prefix="/api/account")
MAX_SYNC_BYTES = 400_000
MAX_ITEMS = 100
MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 12_000
_EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _connect():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("PostgreSQL driver is not installed") from exc
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return psycopg.connect(url, connect_timeout=5)


def _ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS elyntro_users (
                id UUID PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS elyntro_user_sync (
                user_id UUID PRIMARY KEY REFERENCES elyntro_users(id) ON DELETE CASCADE,
                payload JSONB NOT NULL DEFAULT '{"activeId":null,"items":[],"deleted":{}}'::jsonb,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    conn.commit()


def _db_error():
    return jsonify({"ok": False, "error": "El almacenamiento de cuentas no está disponible. Configura DATABASE_URL con la conexión PostgreSQL de Render."}), 503


def _safe_email(value):
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    return value if len(value) <= 254 and _EMAIL_RE.fullmatch(value) else ""


def _validate_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("items", []), list):
        raise ValueError("El historial debe contener una lista de conversaciones.")
    items_in = payload.get("items", [])
    if len(items_in) > MAX_ITEMS:
        raise ValueError("Se permiten como máximo 100 conversaciones sincronizadas.")
    items = []
    seen = set()
    for item in items_in:
        if not isinstance(item, dict):
            raise ValueError("Formato de conversación no válido.")
        cid = item.get("id")
        if not isinstance(cid, str) or not _ID_RE.fullmatch(cid) or cid in seen:
            raise ValueError("Identificador de conversación no válido o duplicado.")
        seen.add(cid)
        title = item.get("title", "Nueva conversación")
        if not isinstance(title, str):
            title = "Nueva conversación"
        messages_in = item.get("messages", [])
        if not isinstance(messages_in, list):
            raise ValueError("Formato de mensajes no válido.")
        messages = []
        for message in messages_in[-MAX_MESSAGES:]:
            if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
                continue
            content = message.get("content")
            if not isinstance(content, str) or len(content) > MAX_MESSAGE_CHARS:
                raise ValueError("Un mensaje supera el límite de 12.000 caracteres.")
            messages.append({"role": message["role"], "content": content})
        try:
            updated = int(item.get("updatedAt", 0))
        except (TypeError, ValueError):
            updated = 0
        items.append({"id": cid, "title": title[:120], "updatedAt": max(0, updated), "messages": messages})
    deleted_in = payload.get("deleted", {})
    if not isinstance(deleted_in, dict):
        raise ValueError("Formato de eliminaciones no válido.")
    deleted = {}
    for cid, stamp in list(deleted_in.items())[-500:]:
        if not isinstance(cid, str) or not _ID_RE.fullmatch(cid):
            continue
        try:
            deleted[cid] = max(0, int(stamp))
        except (TypeError, ValueError):
            continue
    active = payload.get("activeId")
    if not isinstance(active, str) or active not in seen:
        active = items[0]["id"] if items else None
    normalized = {"activeId": active, "items": items, "deleted": deleted}
    if len(json.dumps(normalized, ensure_ascii=False).encode("utf-8")) > MAX_SYNC_BYTES:
        raise OverflowError("El historial sincronizado supera 400 KB.")
    return normalized


def _merge_payload(old, new):
    old = _validate_payload(old if isinstance(old, dict) else {})
    new = _validate_payload(new if isinstance(new, dict) else {})
    deleted = {**old["deleted"]}
    for cid, stamp in new["deleted"].items():
        deleted[cid] = max(stamp, deleted.get(cid, 0))
    by_id = {item["id"]: item for item in old["items"]}
    for item in new["items"]:
        current = by_id.get(item["id"])
        if current is None or item["updatedAt"] >= current["updatedAt"]:
            by_id[item["id"]] = item
    merged_items = []
    for cid, item in by_id.items():
        if deleted.get(cid, -1) >= item["updatedAt"]:
            continue
        merged_items.append(item)
    merged_items.sort(key=lambda item: item["updatedAt"], reverse=True)
    merged_items = merged_items[:MAX_ITEMS]
    valid_ids = {item["id"] for item in merged_items}
    active = new.get("activeId") if new.get("activeId") in valid_ids else old.get("activeId")
    if active not in valid_ids:
        active = merged_items[0]["id"] if merged_items else None
    return {"activeId": active, "items": merged_items, "deleted": dict(list(deleted.items())[-500:])}


def _current_user():
    user_id = session.get("elyntro_user_id")
    if not user_id:
        return None
    try:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT id::text, email FROM elyntro_users WHERE id = %s", (user_id,))
                row = cur.fetchone()
            if not row:
                session.clear()
                return None
            return {"id": row[0], "email": row[1]}
    except Exception:
        return None


@account_api.get("/me")
def account_me():
    user = _current_user()
    return jsonify({"ok": True, "authenticated": bool(user), "account": user})


@account_api.post("/register")
def account_register():
    if not current_app.secret_key or not os.getenv("DATABASE_URL", "").strip():
        return _db_error()
    data = request.get_json(silent=True) or {}
    email = _safe_email(data.get("email"))
    password = data.get("password")
    if not email:
        return jsonify({"ok": False, "error": "Introduce un correo electrónico válido."}), 400
    if not isinstance(password, str) or len(password) < 12 or len(password) > 128:
        return jsonify({"ok": False, "error": "La contraseña debe tener entre 12 y 128 caracteres."}), 400
    try:
        user_id = str(uuid.uuid4())
        password_hash = generate_password_hash(password, method="scrypt")
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("INSERT INTO elyntro_users (id,email,password_hash) VALUES (%s,%s,%s)", (user_id, email, password_hash))
                cur.execute("INSERT INTO elyntro_user_sync (user_id,payload) VALUES (%s,%s::jsonb)", (user_id, json.dumps({"activeId": None, "items": [], "deleted": {}})))
            conn.commit()
        session.clear()
        session["elyntro_user_id"] = user_id
        session.permanent = True
        return jsonify({"ok": True, "account": {"id": user_id, "email": email}, "note": "Cuenta creada. El correo no se ha verificado porque aún no hay servicio de correo configurado."}), 201
    except Exception as exc:
        if type(exc).__name__ == "UniqueViolation":
            return jsonify({"ok": False, "error": "Ya existe una cuenta con ese correo."}), 409
        return _db_error()


@account_api.post("/login")
def account_login():
    if not current_app.secret_key or not os.getenv("DATABASE_URL", "").strip():
        return _db_error()
    data = request.get_json(silent=True) or {}
    email = _safe_email(data.get("email"))
    password = data.get("password")
    if not email or not isinstance(password, str) or len(password) > 128:
        return jsonify({"ok": False, "error": "Correo o contraseña incorrectos."}), 401
    try:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT id::text,email,password_hash FROM elyntro_users WHERE email=%s", (email,))
                row = cur.fetchone()
        if not row or not check_password_hash(row[2], password):
            return jsonify({"ok": False, "error": "Correo o contraseña incorrectos."}), 401
        session.clear()
        session["elyntro_user_id"] = row[0]
        session.permanent = True
        return jsonify({"ok": True, "account": {"id": row[0], "email": row[1]}})
    except Exception:
        return _db_error()


@account_api.post("/logout")
def account_logout():
    session.clear()
    return jsonify({"ok": True})


@account_api.route("/sync", methods=["GET", "PUT"])
def account_sync():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "Inicia sesión para sincronizar conversaciones."}), 401
    try:
        incoming = _validate_payload(request.get_json(silent=True) or {}) if request.method == "PUT" else None
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT payload FROM elyntro_user_sync WHERE user_id=%s FOR UPDATE", (user["id"],))
                row = cur.fetchone()
                existing = row[0] if row and isinstance(row[0], dict) else (json.loads(row[0]) if row else {"activeId": None, "items": [], "deleted": {}})
                merged = _merge_payload(existing, incoming) if incoming is not None else _validate_payload(existing)
                cur.execute(
                    "INSERT INTO elyntro_user_sync (user_id,payload,updated_at) VALUES (%s,%s::jsonb,NOW()) "
                    "ON CONFLICT (user_id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=NOW()",
                    (user["id"], json.dumps(merged, ensure_ascii=False)),
                )
            conn.commit()
        return jsonify({"ok": True, "payload": merged, "synced_at": int(time.time())})
    except (ValueError, OverflowError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        return _db_error()
