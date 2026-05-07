"""
Autenticacao de usuarios: registro, login, sessao.

Usa werkzeug.security para hashing de senhas (ja vem com Flask).
"""

import logging
from functools import wraps

from flask import session, request, redirect, url_for, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

import storage

logger = logging.getLogger(__name__)


def login_required(f):
    """Exige usuario autenticado. Extrai user_id da sessao."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Nao autenticado"}), 401
            return redirect(url_for("login"))
        # Verificar se usuario esta bloqueado
        user = storage.get_user_by_id(user_id)
        if user and user.get("is_blocked"):
            session.clear()
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Conta bloqueada. Entre em contato com o suporte."}), 403
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Exige usuario autenticado E admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Nao autenticado"}), 401
            return redirect(url_for("login"))
        user = storage.get_user_by_id(user_id)
        if not user or not user.get("is_admin"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Acesso negado"}), 403
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def subscription_required(channel: str = None):
    """Exige assinatura ativa com acesso ao canal especificado.
    Admin ignora verificacao. Trial libera todos os canais."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user_id = session.get("user_id")
            if not user_id:
                return jsonify({"error": "Nao autenticado"}), 401

            # Admin ignora restricao
            user = storage.get_user_by_id(user_id)
            if user and user.get("is_admin"):
                return f(*args, **kwargs)

            access = storage.check_subscription_access(user_id, channel)
            if not access["allowed"]:
                return jsonify({"error": access["reason"], "subscription_required": True}), 403

            return f(*args, **kwargs)
        return decorated
    return decorator


def get_current_user_id() -> int:
    """Retorna o user_id da sessao. Chamar apenas dentro de @login_required."""
    return session["user_id"]


def register_user(email: str, password: str) -> dict:
    """Registra um novo usuario. Retorna {"success": True, "user_id": N} ou {"error": "..."}."""
    email = email.strip().lower()
    if not email or "@" not in email:
        return {"error": "Email invalido"}
    if len(password) < 6:
        return {"error": "Senha deve ter no minimo 6 caracteres"}

    password_hash = generate_password_hash(password)
    user_id = storage.create_user(email, password_hash)
    if user_id is None:
        return {"error": "Email ja registrado"}

    logger.info("Novo usuario registrado: %s (id=%d)", email, user_id)
    return {"success": True, "user_id": user_id}


def authenticate_user(email: str, password: str) -> dict:
    """Verifica credenciais. Retorna {"success": True, "user_id": N} ou {"error": "..."}."""
    email = email.strip().lower()
    user = storage.get_user_by_email(email)
    if not user:
        return {"error": "Email ou senha incorretos"}
    if not check_password_hash(user["password_hash"], password):
        return {"error": "Email ou senha incorretos"}

    storage.update_last_login(user["id"])
    logger.info("Login bem-sucedido: %s", email)
    return {"success": True, "user_id": user["id"]}
