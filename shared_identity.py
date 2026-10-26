"""PostgreSQL-backed identity and server-side sessions for Elyntro.

Account endpoints are disabled until ELYNTRO_DATABASE_URL is configured. User IDs are
derived only from authenticated server-side sessions; never trust a client namespace.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timezone
from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import BadRequest

identity_api = Blueprint("identity_api", __name__, url_prefix="/api/identity")
EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")
MAX_PASSWORD_LENGTH = 1024
SESSION_COOKIE = "elyntro_session"


def _database_url() -> str:
    value = os.environ.get("ELYNTRO_DATABASE_URL", "").strip()
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    return value


def _connect():
    url = _database_url()
    if not url:
        raise RuntimeError("Shared identity database is not configured")
    import psycopg
    return psycopg.connect(url, connect_timeout=5, autocommit=False)


def _password_hash(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    return salt, hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


def _session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def init_identity_schema() -> None:
    with _connect() as db:
        with db.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS elyntro_users (
                    user_id UUID PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_salt BYTEA NOT NULL,
                    password_hash BYTEA NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS elyntro_sessions (
                    token_hash CHAR(64) PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES elyntro_users(user_id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_elyntro_sessions_expiry ON elyntro_sessions(expires_at)")
        db.commit()


def _db_ready() -> bool:
    return bool(_database_url())


def _issue_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with _connect() as db:
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO elyntro_sessions(token_hash,user_id,expires_at)
                VALUES (%s,%s,now() + interval '14 days')
            """, (_session_digest(token), user_id))
        db.commit()
    return token


def current_user() -> dict | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 200:
        return None
    with _connect() as db:
        with db.cursor() as cur:
            cur.execute("""
                SELECT u.user_id::text,u.email FROM elyntro_sessions s
                JOIN elyntro_users u ON u.user_id=s.user_id
                WHERE s.token_hash=%s AND s.expires_at>now()
            """, (_session_digest(token),))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE elyntro_sessions SET last_seen_at=now() WHERE token_hash=%s",
                            (_session_digest(token),))
        db.commit()
    return {"user_id": row[0], "email": row[1]} if row else None


def _unavailable():
    return jsonify({"error": "La identidad compartida aún no está configurada."}), 503


@identity_api.post("/register")
def register():
    if not _db_ready():
        return _unavailable()
    try:
        payload = request.get_json(force=True)
    except BadRequest:
        return jsonify({"error": "JSON no válido"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "Se esperaba un objeto JSON"}), 400
    email = payload.get("email")
    password = payload.get("password")
    if not isinstance(email, str) or not EMAIL_RE.fullmatch(email.strip()) or len(email) > 254:
        return jsonify({"error": "Correo no válido"}), 400
    if not isinstance(password, str) or not 12 <= len(password) <= MAX_PASSWORD_LENGTH:
        return jsonify({"error": "La contraseña debe tener entre 12 y 1024 caracteres"}), 400
    email = email.strip().casefold()
    salt, digest = _password_hash(password)
    import uuid
    user_id = str(uuid.uuid4())
    try:
        with _connect() as db:
            with db.cursor() as cur:
                cur.execute("""INSERT INTO elyntro_users(user_id,email,password_salt,password_hash)
                               VALUES (%s,%s,%s,%s)""", (user_id, email, salt, digest))
            db.commit()
    except Exception as exc:
        if exc.__class__.__name__ == "UniqueViolation":
            return jsonify({"error": "No se pudo crear la cuenta con esos datos"}), 409
        current_app.logger.exception("Identity registration failed")
        return jsonify({"error": "No se pudo crear la cuenta"}), 503
    token = _issue_session(user_id)
    response = jsonify({"user": {"user_id": user_id, "email": email}})
    response.set_cookie(SESSION_COOKIE, token, max_age=14 * 86400, httponly=True,
                        secure=request.is_secure or os.environ.get("ELYNTRO_COOKIE_SECURE", "true").lower() == "true",
                        samesite="Lax", path="/")
    return response, 201


@identity_api.post("/login")
def login():
    if not _db_ready():
        return _unavailable()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Se esperaba un objeto JSON"}), 400
    email, password = payload.get("email"), payload.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or len(password) > MAX_PASSWORD_LENGTH:
        return jsonify({"error": "Credenciales no válidas"}), 401
    with _connect() as db:
        with db.cursor() as cur:
            cur.execute("SELECT user_id::text,email,password_salt,password_hash FROM elyntro_users WHERE email=%s",
                        (email.strip().casefold(),))
            row = cur.fetchone()
    if not row:
        _password_hash(password, b"\0" * 16)
        return jsonify({"error": "Credenciales no válidas"}), 401
    _, candidate = _password_hash(password, bytes(row[2]))
    if not hmac.compare_digest(candidate, bytes(row[3])):
        return jsonify({"error": "Credenciales no válidas"}), 401
    token = _issue_session(row[0])
    response = jsonify({"user": {"user_id": row[0], "email": row[1]}})
    response.set_cookie(SESSION_COOKIE, token, max_age=14 * 86400, httponly=True,
                        secure=request.is_secure or os.environ.get("ELYNTRO_COOKIE_SECURE", "true").lower() == "true",
                        samesite="Lax", path="/")
    return response


@identity_api.get("/me")
def me():
    if not _db_ready():
        return _unavailable()
    user = current_user()
    if not user:
        response = jsonify({"error": "Se requiere iniciar sesión"})
        response.status_code = 401
        return response
    return jsonify({"user": user})


@identity_api.post("/logout")
def logout():
    if not _db_ready():
        return _unavailable()
    token = request.cookies.get(SESSION_COOKIE, "")
    if token and len(token) <= 200:
        with _connect() as db:
            with db.cursor() as cur:
                cur.execute("DELETE FROM elyntro_sessions WHERE token_hash=%s", (_session_digest(token),))
            db.commit()
    response = jsonify({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="Lax",
                           secure=request.is_secure or os.environ.get("ELYNTRO_COOKIE_SECURE", "true").lower() == "true")
    return response
