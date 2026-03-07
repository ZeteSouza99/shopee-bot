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
        return f(*args, **kwargs)
    return decorated


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
