"""
Servidor Web Flask para a API Shopee Afiliados.

Multi-tenant SaaS: cada usuario tem suas proprias credenciais.
Inclui: autenticacao por email/senha, rate limiting, cache, logging e paralelismo.
"""

import csv
import hashlib
import io
import json
import logging
import os
import requests
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    Response, session, redirect, url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from shopee_client import ShopeeAffiliateClient
from config import FLASK_SECRET_KEY, FLASK_DEBUG, EVOLUTION_API_URL, EVOLUTION_API_KEY, GEMINI_API_KEY, IG_APP_ID, IG_APP_SECRET, MERCADO_PAGO_ACCESS_TOKEN
from auth import login_required, admin_required, subscription_required, get_current_user_id, register_user, authenticate_user
import storage
import gemini_client
import channels
from channels.whatsapp import WhatsAppChannel

# ─── Logging ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── App Flask ────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
app.config["SESSION_COOKIE_HTTPONLY"] = True

# ─── Rate Limiting ────────────────────────────────────────

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per minute"],
    storage_uri="memory://",
)

# ─── Devolver conexao ao pool apos cada request ──────────

@app.teardown_appcontext
def _teardown_db(exc):
    storage._release_conn()

# ─── Cache simples em memória ─────────────────────────────


class SimpleCache:
    """Cache TTL em memória para respostas de API."""

    def __init__(self, ttl: int = 300):
        self._cache: dict = {}
        self._ttl = ttl

    def get(self, key: str):
        if key in self._cache:
            value, expires = self._cache[key]
            if time.time() < expires:
                return value
            del self._cache[key]
        return None

    def set(self, key: str, value):
        self._cache[key] = (value, time.time() + self._ttl)

    def make_key(self, *args) -> str:
        return hashlib.md5(
            json.dumps(args, sort_keys=True, default=str).encode()
        ).hexdigest()


cache = SimpleCache(ttl=300)


# ─── Per-user service helpers ─────────────────────────────


def _get_shopee_client() -> ShopeeAffiliateClient:
    """Cria um ShopeeAffiliateClient com as credenciais do usuario logado."""
    user_id = get_current_user_id()
    creds = storage.get_user_credentials(user_id, "shopee")
    app_id = creds.get("app_id", "")
    secret = creds.get("secret", "")
    if not app_id or not secret:
        raise ValueError("Credenciais Shopee nao configuradas. Acesse Configuracoes.")
    return ShopeeAffiliateClient(app_id=app_id, secret=secret)


def _get_user_channels() -> dict:
    """Cria instancias de canais com as credenciais do usuario logado."""
    user_id = get_current_user_id()
    all_creds = storage.get_user_credentials(user_id)
    return channels.create_channels(
        all_creds,
        evolution_api_url=EVOLUTION_API_URL,
        evolution_api_key=EVOLUTION_API_KEY,
    )


def _get_whatsapp_channel(user_id: int = None) -> WhatsAppChannel:
    """Cria instancia de WhatsAppChannel para gerenciamento (QR, grupos, etc)."""
    if user_id is None:
        user_id = get_current_user_id()
    wa_creds = storage.get_user_credentials(user_id, "whatsapp")
    instance_name = wa_creds.get("instance_name", f"shopee_user_{user_id}")
    return WhatsAppChannel(
        api_url=EVOLUTION_API_URL,
        api_key=EVOLUTION_API_KEY,
        instance_name=instance_name,
        group_id=wa_creds.get("group_id", ""),
    )


def _get_gemini_key() -> str:
    """Retorna a chave Gemini centralizada do servidor."""
    return GEMINI_API_KEY


# ─── Autenticacao ─────────────────────────────────────────


@app.route("/login", methods=["GET", "POST"])
def login():
    """Pagina de login com email e senha."""
    if session.get("user_id"):
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        result = authenticate_user(email, password)

        if result.get("success"):
            session["user_id"] = result["user_id"]
            logger.info("Login bem-sucedido de %s", request.remote_addr)
            return redirect(url_for("index"))

        logger.warning("Tentativa de login falhou de %s", request.remote_addr)
        return render_template("login.html", error=result.get("error", "Erro desconhecido"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """Pagina de registro de novo usuario."""
    if session.get("user_id"):
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if password != confirm:
            return render_template("register.html", error="As senhas nao coincidem")

        result = register_user(email, password)
        if result.get("success"):
            # Criar trial automatico de 7 dias
            storage.create_trial_subscription(result["user_id"])
            session["user_id"] = result["user_id"]
            return redirect(url_for("index"))

        return render_template("register.html", error=result.get("error", "Erro desconhecido"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    """Encerra a sessao."""
    session.clear()
    return redirect(url_for("login"))


# ─── Health Check ─────────────────────────────────────────


@app.route("/health")
def health():
    """Endpoint de health check para monitoramento."""
    return jsonify({
        "status": "ok",
        "timestamp": int(time.time()),
    })


# ─── Pagina Principal ────────────────────────────────────


@app.route("/")
@login_required
def index():
    """Serve a pagina principal."""
    user_id = get_current_user_id()
    user = storage.get_user_by_id(user_id)
    user_email = user["email"] if user else "?"
    return render_template("index.html", user_email=user_email)


# ─── Credenciais do Usuario (Settings) ───────────────────


@app.route("/api/settings/credentials", methods=["GET"])
@login_required
def api_get_credentials():
    """Retorna credenciais do usuario com valores mascarados."""
    user_id = get_current_user_id()
    all_creds = storage.get_user_credentials(user_id)

    masked = {}
    for service, creds in all_creds.items():
        masked[service] = {}
        for key, value in creds.items():
            if value and len(value) > 8:
                masked[service][key] = value[:4] + "****" + value[-4:]
            elif value:
                masked[service][key] = "****"
            else:
                masked[service][key] = ""

    return jsonify(masked)


@app.route("/api/settings/credentials/<service>", methods=["PUT"])
@login_required
def api_save_credentials(service):
    """Salva credenciais para um servico."""
    VALID_SERVICES = {"shopee", "telegram", "whatsapp", "instagram", "gemini"}
    if service not in VALID_SERVICES:
        return jsonify({"error": f"Servico invalido: {service}"}), 400

    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    user_id = get_current_user_id()
    storage.save_user_credentials(user_id, service, data)
    logger.info("Credenciais salvas: user=%d service=%s", user_id, service)
    return jsonify({"saved": True})


@app.route("/api/settings/credentials/<service>", methods=["DELETE"])
@login_required
def api_delete_credentials(service):
    """Remove credenciais de um servico."""
    user_id = get_current_user_id()
    storage.delete_user_credentials(user_id, service)
    logger.info("Credenciais removidas: user=%d service=%s", user_id, service)
    return jsonify({"deleted": True})


# ─── Endpoints de API ────────────────────────────────────


@app.route("/api/ofertas-shopee", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def api_ofertas_shopee():
    """Busca ofertas Shopee (shopeeOfferV2)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    try:
        client = _get_shopee_client()
        user_id = get_current_user_id()
        keyword = data.get("keyword", "")
        sort_type = int(data.get("sortType", 1))
        page = int(data.get("page", 1))
        limit = min(int(data.get("limit", 10)), 500)

        cache_key = cache.make_key("ofertas_shopee", user_id, keyword, sort_type, page, limit)
        cached = cache.get(cache_key)
        if cached:
            logger.info("Cache hit: ofertas_shopee")
            return jsonify(cached)

        result = client.buscar_ofertas_shopee(
            keyword=keyword,
            sort_type=sort_type,
            page=page,
            limit=limit,
        )
        cache.set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        logger.exception("Erro em api_ofertas_shopee")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ofertas-loja", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def api_ofertas_loja():
    """Busca ofertas por loja (shopOfferV2)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    try:
        client = _get_shopee_client()
        user_id = get_current_user_id()
        shop_type = data.get("shopType")
        if shop_type and isinstance(shop_type, list):
            shop_type = [int(t) for t in shop_type]

        keyword = data.get("keyword", "")
        sort_type = int(data.get("sortType", 2))
        page = int(data.get("page", 1))
        limit = min(int(data.get("limit", 10)), 500)

        cache_key = cache.make_key(
            "ofertas_loja", user_id, keyword, data.get("shopId"), shop_type,
            data.get("isKeySeller"), sort_type, page, limit,
        )
        cached = cache.get(cache_key)
        if cached:
            logger.info("Cache hit: ofertas_loja")
            return jsonify(cached)

        result = client.buscar_ofertas_loja(
            keyword=keyword,
            shop_id=int(data["shopId"]) if data.get("shopId") else None,
            shop_type=shop_type if shop_type else None,
            is_key_seller=data.get("isKeySeller"),
            sort_type=sort_type,
            seller_comm_cove_ratio=data.get("sellerCommCoveRatio"),
            page=page,
            limit=limit,
        )
        cache.set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        logger.exception("Erro em api_ofertas_loja")
        return jsonify({"error": str(e)}), 500


@app.route("/api/produtos", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def api_produtos():
    """Busca produtos (productOfferV2)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    try:
        client = _get_shopee_client()
        user_id = get_current_user_id()
        keyword = data.get("keyword", "")
        list_type = int(data.get("listType", 1))
        sort_type = int(data.get("sortType", 5))
        page = int(data.get("page", 1))
        limit = min(int(data.get("limit", 20)), 500)

        cache_key = cache.make_key(
            "produtos", user_id, keyword, data.get("shopId"), data.get("itemId"),
            list_type, sort_type, data.get("isAMSOffer"), data.get("isKeySeller"),
            page, limit,
        )
        cached = cache.get(cache_key)
        if cached:
            logger.info("Cache hit: produtos")
            return jsonify(cached)

        result = client.buscar_produtos(
            keyword=keyword,
            shop_id=int(data["shopId"]) if data.get("shopId") else None,
            item_id=int(data["itemId"]) if data.get("itemId") else None,
            list_type=list_type,
            sort_type=sort_type,
            is_ams_offer=data.get("isAMSOffer"),
            is_key_seller=data.get("isKeySeller"),
            page=page,
            limit=limit,
        )
        cache.set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        logger.exception("Erro em api_produtos")
        return jsonify({"error": str(e)}), 500


@app.route("/api/short-link", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def api_short_link():
    """Gera short link (generateShortLink)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    origin_url = data.get("originUrl", "")
    if not origin_url:
        return jsonify({"error": "URL e obrigatoria"}), 400

    try:
        client = _get_shopee_client()
        sub_ids = data.get("subIds")
        if sub_ids and isinstance(sub_ids, str):
            sub_ids = [s.strip() for s in sub_ids.split(",") if s.strip()][:5]

        result = client.gerar_short_link(origin_url, sub_ids if sub_ids else None)
        return jsonify(result)
    except Exception as e:
        logger.exception("Erro em api_short_link")
        return jsonify({"error": str(e)}), 500


@app.route("/api/short-links-lote", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def api_short_links_lote():
    """Gera short links em lote para uma lista de URLs (max 50)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    try:
        client = _get_shopee_client()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    urls = data.get("urls", [])[:50]
    sub_ids = data.get("subIds")
    if sub_ids and isinstance(sub_ids, str):
        sub_ids = [s.strip() for s in sub_ids.split(",") if s.strip()][:5]

    results = {}
    for url in urls:
        if not url:
            continue
        try:
            resp = client.gerar_short_link(url, sub_ids if sub_ids else None)
            short = resp.get("data", {}).get("generateShortLink", {}).get("shortLink")
            if short:
                results[url] = short
        except Exception:
            logger.warning("Falha ao gerar short link para: %s", url[:80])
            results[url] = None

    return jsonify({"shortLinks": results})


@app.route("/api/relatorio-conversao", methods=["POST"])
@login_required
@limiter.limit("15 per minute")
def api_relatorio_conversao():
    """Relatorio de conversao (conversionReport)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    try:
        client = _get_shopee_client()
        result = client.relatorio_conversao(
            purchase_time_start=int(data["purchaseTimeStart"]),
            purchase_time_end=int(data["purchaseTimeEnd"]),
            order_status=data.get("orderStatus") or None,
            buyer_type=data.get("buyerType") or None,
            device=data.get("device") or None,
            scroll_id=data.get("scrollId") or None,
            limit=min(int(data.get("limit", 50)), 200),
        )
        return jsonify(result)
    except Exception as e:
        logger.exception("Erro em api_relatorio_conversao")
        return jsonify({"error": str(e)}), 500


@app.route("/api/relatorio-validado", methods=["POST"])
@login_required
@limiter.limit("15 per minute")
def api_relatorio_validado():
    """Relatorio validado (validatedReport)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    try:
        client = _get_shopee_client()
        result = client.relatorio_validado(
            validation_id=int(data["validationId"]),
            scroll_id=data.get("scrollId") or None,
            limit=min(int(data.get("limit", 100)), 500),
        )
        return jsonify(result)
    except Exception as e:
        logger.exception("Erro em api_relatorio_validado")
        return jsonify({"error": str(e)}), 500


# ─── Ranking de Produtos (paralelo) ──────────────────────


@app.route("/api/ranking", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def api_ranking():
    """Busca produtos em multiplos criterios em paralelo."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    try:
        client = _get_shopee_client()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    user_id = get_current_user_id()
    keyword = data.get("keyword", "")
    limit = min(int(data.get("limit", 10)), 50)

    categories = [
        {"label": "recomendados",   "list_type": 0, "sort_type": 1},
        {"label": "maiorComissao",  "list_type": 0, "sort_type": 5},
        {"label": "maisVendidos",   "list_type": 0, "sort_type": 3},
        {"label": "topPerformance", "list_type": 2, "sort_type": 5},
    ]

    def fetch_category(cat):
        cache_key = cache.make_key("ranking", user_id, keyword, cat["list_type"], cat["sort_type"], limit)
        cached = cache.get(cache_key)
        if cached is not None:
            return cat["label"], cached
        try:
            result = client.buscar_produtos(
                keyword=keyword,
                list_type=cat["list_type"],
                sort_type=cat["sort_type"],
                page=1,
                limit=limit,
            )
            nodes = result.get("data", {}).get("productOfferV2", {}).get("nodes", [])
            cache.set(cache_key, nodes)
            return cat["label"], nodes
        except Exception:
            logger.exception("Erro ao buscar ranking: %s", cat["label"])
            return cat["label"], []

    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_category, cat) for cat in categories]
        for future in futures:
            label, nodes = future.result()
            results[label] = nodes

    return jsonify(results)


# ─── Export CSV ──────────────────────────────────────────────


@app.route("/api/export-csv", methods=["POST"])
@login_required
def api_export_csv():
    """Exporta produtos para CSV."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    produtos = data.get("produtos", [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Produto", "Preco Min", "Preco Max", "Desconto %",
        "Comissao %", "Comissao R$", "Vendas", "Nota",
        "Loja", "Tipo Loja", "Link Afiliado", "Short Link",
    ])

    for p in produtos:
        comm_rate = p.get("commissionRate", "0")
        try:
            comm_pct = f"{float(comm_rate) * 100:.1f}%"
        except (ValueError, TypeError):
            comm_pct = comm_rate

        writer.writerow([
            p.get("productName", ""),
            p.get("priceMin", ""),
            p.get("priceMax", ""),
            p.get("priceDiscountRate", ""),
            comm_pct,
            p.get("commission", ""),
            p.get("sales", ""),
            p.get("ratingStar", ""),
            p.get("shopName", ""),
            p.get("shopType", ""),
            p.get("offerLink", ""),
            p.get("shortLink", ""),
        ])

    csv_content = output.getvalue()
    output.close()

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=produtos_shopee.csv"},
    )


# ─── Lojas Favoritas ────────────────────────────────────────


@app.route("/api/favoritas", methods=["GET"])
@login_required
def api_get_favoritas():
    """Retorna a lista de lojas favoritas."""
    user_id = get_current_user_id()
    return jsonify(storage.get_favoritas(user_id))


@app.route("/api/favoritas", methods=["POST"])
@login_required
def api_add_favorita():
    """Adiciona uma loja aos favoritos."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400
    user_id = get_current_user_id()
    added = storage.add_favorita(user_id, data)
    return jsonify({"added": added})


@app.route("/api/favoritas/<int:shop_id>", methods=["DELETE"])
@login_required
def api_remove_favorita(shop_id):
    """Remove uma loja dos favoritos."""
    user_id = get_current_user_id()
    storage.remove_favorita(user_id, shop_id)
    return jsonify({"removed": True})


@app.route("/api/favoritas/atualizar", methods=["POST"])
@login_required
@limiter.limit("5 per minute")
def api_atualizar_favoritas():
    """Atualiza dados de todas as lojas favoritas via API."""
    user_id = get_current_user_id()

    try:
        client = _get_shopee_client()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    favoritas = storage.get_favoritas(user_id)
    updated = 0

    for fav in favoritas:
        shop_id = fav.get("shopId")
        if not shop_id:
            continue
        try:
            result = client.buscar_ofertas_loja(shop_id=shop_id, limit=1)
            nodes = result.get("data", {}).get("shopOfferV2", {}).get("nodes", [])
            if nodes:
                shop = nodes[0]
                storage.update_favorita(user_id, shop_id, {
                    "commissionRate": shop.get("commissionRate"),
                    "remainingBudget": shop.get("remainingBudget"),
                    "ratingStar": shop.get("ratingStar"),
                })
                updated += 1
        except Exception:
            logger.warning("Falha ao atualizar favorita shop_id=%s", shop_id)
            continue

    return jsonify({"updated": updated})


# ─── Historico ──────────────────────────────────────────────


@app.route("/api/historico", methods=["GET"])
@login_required
def api_get_historico():
    """Retorna o historico com paginacao. Opcional: ?itemId=123&limit=200&offset=0."""
    user_id = get_current_user_id()
    item_id = request.args.get("itemId")
    limit = min(int(request.args.get("limit", 200)), 500)
    offset = int(request.args.get("offset", 0))
    if item_id:
        item_id = int(item_id)
    return jsonify(storage.get_historico(user_id, item_id, limit=limit, offset=offset))


@app.route("/api/historico", methods=["POST"])
@login_required
def api_save_historico():
    """Salva produtos no historico."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400
    user_id = get_current_user_id()
    produtos = data.get("produtos", [])
    count = storage.save_historico(user_id, produtos)
    return jsonify({"saved": count})


@app.route("/api/historico", methods=["DELETE"])
@login_required
def api_clear_historico():
    """Limpa o historico."""
    user_id = get_current_user_id()
    storage.clear_historico(user_id)
    return jsonify({"cleared": True})


# ─── Canais de Publicacao (Multi-canal) ───────────────────


@app.route("/api/channels/status", methods=["GET"])
@login_required
def api_channels_status():
    """Retorna status de todos os canais do usuario."""
    user_channels = _get_user_channels()
    result = {}
    for name, ch in user_channels.items():
        status = ch.check_status()
        result[name] = {
            "configured": status.configured,
            "connected": status.connected,
            "displayName": ch.display_name,
            "details": status.details,
        }
    result["gemini"] = True
    return jsonify(result)


@app.route("/api/channels/preview", methods=["POST"])
@login_required
@limiter.limit("15 per minute")
def api_channels_preview():
    """Gera preview de anuncio via Gemini (ou template customizado) para canais selecionados."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    produto = data.get("produto", {})
    canais = data.get("canais", ["telegram"])
    template_id = data.get("templateId")
    api_key = _get_gemini_key()
    user_id = get_current_user_id()

    results = {}
    for canal in canais:
        custom_template = None
        if template_id:
            tmpl = storage.get_template_by_id(user_id, int(template_id))
            if tmpl and tmpl["canal"] == canal:
                custom_template = tmpl["templateText"]

        try:
            texto = gemini_client.gerar_anuncio(
                produto, canal=canal, api_key=api_key, custom_template=custom_template
            )
            results[canal] = {"texto": texto, "usedTemplate": bool(custom_template)}
        except Exception as e:
            logger.warning("Erro ao gerar preview para canal '%s': %s", canal, e)
            results[canal] = {"error": str(e)}

    return jsonify(results)


@app.route("/api/channels/publish", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def api_channels_publish():
    """Publica em um ou mais canais selecionados."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    produto = data.get("produto", {})
    publicacoes = data.get("publicacoes", [])

    # Verificar assinatura para cada canal
    user_id = get_current_user_id()
    user = storage.get_user_by_id(user_id)
    is_admin = user and user.get("is_admin")

    if not is_admin:
        for pub in publicacoes:
            canal_name = pub.get("canal", "")
            access = storage.check_subscription_access(user_id, canal_name)
            if not access["allowed"]:
                return jsonify({"error": access["reason"], "subscription_required": True}), 403

    user_channels = _get_user_channels()

    results = {}
    for pub in publicacoes:
        canal_name = pub.get("canal")
        texto = pub.get("texto", "")
        ch = user_channels.get(canal_name)
        if not ch:
            results[canal_name] = {"success": False, "error": f"Canal '{canal_name}' nao encontrado"}
            continue
        if not texto.strip():
            results[canal_name] = {"success": False, "error": "Texto vazio"}
            continue
        try:
            image_url = produto.get("imageUrl", "")
            # URLs Shopee com _tn sao miniaturas pequenas — usar versao completa
            if image_url and image_url.endswith("_tn"):
                image_url = image_url[:-3]
            if image_url:
                result = ch.publish_with_image(texto, image_url)
            else:
                result = ch.publish_text(texto)
            results[canal_name] = {
                "success": result.success,
                "message": result.message,
            }
        except Exception as e:
            logger.exception("Erro ao publicar no canal '%s'", canal_name)
            results[canal_name] = {"success": False, "error": str(e)}

    return jsonify(results)


# ─── Telegram: Deteccao Automatica de Grupos ─────────────


@app.route("/api/telegram/detect-groups", methods=["POST"])
@login_required
@limiter.limit("5 per minute")
def api_telegram_detect_groups():
    """Detecta grupos/canais onde o bot esta presente."""
    data = request.get_json()
    bot_token = data.get("bot_token", "").strip() if data else ""
    group_link = data.get("group_link", "").strip() if data else ""

    if not bot_token:
        return jsonify({"error": "Bot Token obrigatorio"}), 400

    try:
        # Verificar se o token e valido
        me_resp = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
        if not me_resp.ok:
            return jsonify({"error": "Bot Token invalido. Verifique e tente novamente."}), 400
        bot_info = me_resp.json().get("result", {})

        chats = {}

        # Metodo 1: Se o usuario informou link/username do grupo, tentar getChat direto
        if group_link:
            # Aceitar @username, t.me/username, ou chat_id numerico
            chat_identifier = group_link.strip()
            if "t.me/" in chat_identifier:
                # Extrair username do link
                chat_identifier = "@" + chat_identifier.split("t.me/")[-1].split("/")[0].split("?")[0]
            elif not chat_identifier.startswith("@") and not chat_identifier.startswith("-"):
                chat_identifier = "@" + chat_identifier

            chat_resp = requests.get(
                f"https://api.telegram.org/bot{bot_token}/getChat",
                params={"chat_id": chat_identifier},
                timeout=10,
            )
            if chat_resp.ok:
                chat_data = chat_resp.json().get("result", {})
                if chat_data.get("type") in ("group", "supergroup", "channel"):
                    chats[chat_data["id"]] = {
                        "id": chat_data["id"],
                        "title": chat_data.get("title", "Sem nome"),
                        "type": chat_data.get("type"),
                    }
            else:
                err = chat_resp.json().get("description", "")
                if "not found" in err.lower() or "chat not found" in err.lower():
                    return jsonify({
                        "error": "Grupo nao encontrado. Verifique se o bot e admin do grupo e se o link esta correto.",
                        "bot": {"username": bot_info.get("username", ""), "first_name": bot_info.get("first_name", "")},
                        "groups": [],
                    })

        # Metodo 2: Buscar via getUpdates (pega grupos com atividade recente)
        updates_resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            params={"limit": 100, "allowed_updates": '["message","my_chat_member","chat_member"]'},
            timeout=15,
        )
        if updates_resp.ok:
            updates = updates_resp.json().get("result", [])
            for update in updates:
                chat = None
                if "message" in update:
                    chat = update["message"].get("chat")
                elif "my_chat_member" in update:
                    chat = update["my_chat_member"].get("chat")
                elif "chat_member" in update:
                    chat = update["chat_member"].get("chat")

                if chat and chat.get("type") in ("group", "supergroup", "channel"):
                    chat_id = chat["id"]
                    if chat_id not in chats:
                        chats[chat_id] = {
                            "id": chat_id,
                            "title": chat.get("title", "Sem nome"),
                            "type": chat.get("type"),
                        }

        return jsonify({
            "bot": {
                "username": bot_info.get("username", ""),
                "first_name": bot_info.get("first_name", ""),
            },
            "groups": list(chats.values()),
        })

    except requests.exceptions.RequestException as e:
        logger.error("Erro ao detectar grupos Telegram: %s", e)
        return jsonify({"error": "Erro de conexao com a API do Telegram."}), 500


@app.route("/api/telegram/save", methods=["POST"])
@login_required
def api_telegram_save():
    """Salva bot_token e chat_id selecionado."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Dados obrigatorios"}), 400

    bot_token = data.get("bot_token", "").strip()
    chat_id = data.get("chat_id", "").strip()
    chat_title = data.get("chat_title", "")

    if not bot_token or not chat_id:
        return jsonify({"error": "Bot Token e Chat ID obrigatorios"}), 400

    user_id = get_current_user_id()
    storage.save_user_credentials(user_id, "telegram", {
        "bot_token": bot_token,
        "chat_id": chat_id,
        "chat_title": chat_title,
    })
    logger.info("Telegram salvo: user=%d chat=%s title=%s", user_id, chat_id, chat_title)
    return jsonify({"saved": True})


# ─── Instagram OAuth (Instagram API with Instagram Login) ─

@app.route("/api/instagram/auth", methods=["GET"])
@login_required
def api_instagram_auth():
    """Inicia fluxo OAuth do Instagram. Redireciona para tela de autorizacao."""
    if not IG_APP_ID:
        return jsonify({"error": "Instagram App ID nao configurado no servidor"}), 500

    redirect_uri = request.host_url.rstrip("/") + "/api/instagram/callback"
    scope = "instagram_business_basic,instagram_business_content_publish"
    auth_url = (
        f"https://www.instagram.com/oauth/authorize"
        f"?enable_fb_login=0&force_authentication=1"
        f"&client_id={IG_APP_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={scope}"
    )
    return redirect(auth_url)


@app.route("/api/instagram/callback", methods=["GET"])
@login_required
def api_instagram_callback():
    """Callback do OAuth Instagram. Troca code por token e salva credenciais."""
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        logger.warning("Instagram OAuth negado: %s", request.args.get("error_description", error))
        return redirect("/?ig_error=denied")

    if not code:
        return redirect("/?ig_error=no_code")

    redirect_uri = request.host_url.rstrip("/") + "/api/instagram/callback"

    try:
        # 1. Trocar code por short-lived token
        token_resp = requests.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": IG_APP_ID,
                "client_secret": IG_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=15,
        )
        if not token_resp.ok:
            logger.error("Instagram token exchange falhou: %s", token_resp.text[:300])
            return redirect("/?ig_error=token_failed")

        token_data = token_resp.json()
        short_token = token_data.get("access_token")
        ig_user_id = str(token_data.get("user_id", ""))

        # 2. Trocar por long-lived token (60 dias)
        ll_resp = requests.get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": IG_APP_SECRET,
                "access_token": short_token,
            },
            timeout=15,
        )
        if ll_resp.ok:
            ll_data = ll_resp.json()
            access_token = ll_data.get("access_token", short_token)
            logger.info("Instagram long-lived token obtido com sucesso")
        else:
            access_token = short_token
            logger.warning("Falha ao obter long-lived token, usando short-lived")

        # 3. Buscar username
        me_resp = requests.get(
            f"https://graph.instagram.com/v21.0/me",
            params={"fields": "user_id,username", "access_token": access_token},
            timeout=10,
        )
        username = ""
        if me_resp.ok:
            username = me_resp.json().get("username", "")

        # 4. Salvar credenciais do usuario
        user_id = get_current_user_id()
        storage.save_user_credentials(user_id, "instagram", {
            "access_token": access_token,
            "user_id": ig_user_id,
            "username": username,
        })
        logger.info("Instagram OAuth concluido: user=%d ig_user=%s username=%s", user_id, ig_user_id, username)

        return redirect("/?ig_success=1")

    except Exception as e:
        logger.exception("Erro no callback Instagram OAuth")
        return redirect(f"/?ig_error=exception")


@app.route("/api/instagram/status", methods=["GET"])
@login_required
def api_instagram_status():
    """Retorna status da conexao Instagram do usuario."""
    user_id = get_current_user_id()
    creds = storage.get_user_credentials(user_id, "instagram")
    if not creds or not creds.get("access_token"):
        return jsonify({"connected": False})
    return jsonify({
        "connected": True,
        "username": creds.get("username", ""),
        "user_id": creds.get("user_id", ""),
    })


# ─── WhatsApp Evolution API Management ───────────────────


@app.route("/api/whatsapp/connect", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def api_whatsapp_connect():
    """Cria instancia (se necessario) e retorna QR code para conexao."""
    user_id = get_current_user_id()
    wa = _get_whatsapp_channel(user_id)

    try:
        # Tenta obter estado atual
        try:
            state = wa.get_connection_state()
            instance_state = state.get("instance", {}).get("state", "")
            if not instance_state:
                instance_state = state.get("state", "close")

            if instance_state == "open":
                return jsonify({
                    "status": "connected",
                    "message": "WhatsApp ja esta conectado",
                    "instanceName": wa.instance_name,
                })
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                # Instancia nao existe, criar
                wa.create_instance()
                logger.info("Instancia Evolution criada: %s", wa.instance_name)
            else:
                raise

        # Solicita QR code com retry (Evolution API gera QR de forma assincrona)
        qr_base64 = ""
        pairing_code = ""
        for attempt in range(8):
            time.sleep(2)
            qr_data = wa.connect_instance()
            qr_base64 = (qr_data.get("base64", "")
                         or qr_data.get("qrcode", {}).get("base64", ""))
            pairing_code = qr_data.get("pairingCode", "")
            if qr_base64 or pairing_code:
                break
            logger.info("QR ainda nao pronto, aguardando... (tentativa %d/8)", attempt + 1)

        # Salva instance_name nas credenciais do usuario
        wa_creds = storage.get_user_credentials(user_id, "whatsapp")
        wa_creds["instance_name"] = wa.instance_name
        storage.save_user_credentials(user_id, "whatsapp", wa_creds)

        return jsonify({
            "status": "qr_code",
            "qrCode": qr_base64,
            "pairingCode": pairing_code,
            "instanceName": wa.instance_name,
        })
    except Exception as e:
        logger.exception("Erro ao conectar WhatsApp")
        return jsonify({"error": str(e)}), 500


@app.route("/api/whatsapp/status", methods=["GET"])
@login_required
def api_whatsapp_status():
    """Retorna estado da conexao WhatsApp do usuario."""
    user_id = get_current_user_id()
    wa = _get_whatsapp_channel(user_id)

    if not wa.api_url or not wa.api_key:
        return jsonify({
            "status": "not_configured",
            "message": "Evolution API nao configurada no servidor",
        })

    try:
        state = wa.get_connection_state()
        instance_state = state.get("instance", {}).get("state", "")
        if not instance_state:
            instance_state = state.get("state", "close")

        wa_creds = storage.get_user_credentials(user_id, "whatsapp")

        return jsonify({
            "status": instance_state,
            "instanceName": wa.instance_name,
            "groupId": wa_creds.get("group_id", ""),
            "groupName": wa_creds.get("group_name", ""),
        })
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return jsonify({"status": "not_created", "instanceName": wa.instance_name})
        return jsonify({"status": "error", "error": str(e)}), 500
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/whatsapp/groups", methods=["GET"])
@login_required
def api_whatsapp_groups():
    """Lista os grupos do WhatsApp conectado."""
    user_id = get_current_user_id()
    wa = _get_whatsapp_channel(user_id)

    try:
        groups = wa.fetch_groups()
        result = []
        for g in groups:
            result.append({
                "id": g.get("id", ""),
                "subject": g.get("subject", "Sem nome"),
                "size": g.get("size", 0),
            })
        return jsonify({"groups": result})
    except Exception as e:
        logger.exception("Erro ao listar grupos WhatsApp")
        return jsonify({"error": str(e)}), 500


@app.route("/api/whatsapp/select-group", methods=["POST"])
@login_required
def api_whatsapp_select_group():
    """Salva o grupo selecionado pelo usuario."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    group_id = data.get("groupId", "")
    group_name = data.get("groupName", "")

    if not group_id:
        return jsonify({"error": "groupId obrigatorio"}), 400

    user_id = get_current_user_id()
    wa_creds = storage.get_user_credentials(user_id, "whatsapp")
    wa_creds["group_id"] = group_id
    wa_creds["group_name"] = group_name
    storage.save_user_credentials(user_id, "whatsapp", wa_creds)

    logger.info("Grupo WhatsApp selecionado: user=%d group=%s", user_id, group_id)
    return jsonify({"saved": True, "groupId": group_id, "groupName": group_name})


@app.route("/api/whatsapp/disconnect", methods=["POST"])
@login_required
def api_whatsapp_disconnect():
    """Desconecta o WhatsApp do usuario."""
    user_id = get_current_user_id()
    wa = _get_whatsapp_channel(user_id)

    try:
        wa.logout_instance()
        logger.info("WhatsApp desconectado: user=%d instance=%s", user_id, wa.instance_name)
        return jsonify({"disconnected": True})
    except Exception as e:
        logger.exception("Erro ao desconectar WhatsApp")
        return jsonify({"error": str(e)}), 500


# ─── Templates de Anuncio ───────────────────────────────────


@app.route("/api/templates", methods=["GET"])
@login_required
def api_get_templates():
    """Lista templates do usuario. Opcional: ?canal=telegram"""
    user_id = get_current_user_id()
    canal = request.args.get("canal")
    return jsonify(storage.get_templates(user_id, canal=canal))


@app.route("/api/templates", methods=["POST"])
@login_required
def api_create_template():
    """Cria um novo template de anuncio."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    name = data.get("name", "").strip()
    canal = data.get("canal", "").strip()
    template_text = data.get("templateText", "").strip()

    VALID_CANAIS = {"telegram", "whatsapp", "instagram_feed", "instagram_stories"}
    if not name:
        return jsonify({"error": "Nome obrigatorio"}), 400
    if canal not in VALID_CANAIS:
        return jsonify({"error": f"Canal invalido: {canal}"}), 400
    if not template_text:
        return jsonify({"error": "Texto do template obrigatorio"}), 400

    user_id = get_current_user_id()
    template_id = storage.save_template(user_id, name, canal, template_text)
    logger.info("Template criado: user=%d canal=%s id=%d", user_id, canal, template_id)
    return jsonify({"created": True, "id": template_id})


@app.route("/api/templates/<int:template_id>", methods=["PUT"])
@login_required
def api_update_template(template_id):
    """Atualiza um template existente."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    name = data.get("name", "").strip()
    template_text = data.get("templateText", "").strip()

    if not name or not template_text:
        return jsonify({"error": "Nome e texto obrigatorios"}), 400

    user_id = get_current_user_id()
    storage.update_template(user_id, template_id, name, template_text)
    return jsonify({"updated": True})


@app.route("/api/templates/<int:template_id>", methods=["DELETE"])
@login_required
def api_delete_template(template_id):
    """Remove um template."""
    user_id = get_current_user_id()
    storage.delete_template(user_id, template_id)
    return jsonify({"deleted": True})


@app.route("/api/templates/<int:template_id>/set-default", methods=["POST"])
@login_required
def api_set_default_template(template_id):
    """Define um template como padrao para o seu canal."""
    user_id = get_current_user_id()
    tmpl = storage.get_template_by_id(user_id, template_id)
    if not tmpl:
        return jsonify({"error": "Template nao encontrado"}), 404
    storage.set_default_template(user_id, template_id, tmpl["canal"])
    return jsonify({"default": True, "canal": tmpl["canal"]})


# ─── Storefront Publico ─────────────────────────────────────


@app.route("/api/storefront/settings", methods=["GET"])
@login_required
def api_get_storefront_settings():
    """Retorna configuracoes do site publico do usuario."""
    user_id = get_current_user_id()
    return jsonify(storage.get_storefront_settings(user_id))


@app.route("/api/storefront/settings", methods=["PUT"])
@login_required
def api_update_storefront_settings():
    """Salva slug e display_name do site publico."""
    import re
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    slug = data.get("slug", "").strip().lower()
    display_name = data.get("displayName", "").strip()

    if slug and not re.match(r"^[a-z0-9][a-z0-9\-]{1,48}[a-z0-9]$", slug):
        return jsonify({"error": "Slug invalido. Use apenas letras minusculas, numeros e hifens (3-50 chars)"}), 400

    user_id = get_current_user_id()
    try:
        storage.update_storefront_settings(user_id, slug or None, display_name or None)
        logger.info("Storefront atualizado: user=%d slug=%s", user_id, slug)
        return jsonify({"saved": True, "slug": slug, "displayName": display_name})
    except Exception as e:
        if "UNIQUE" in str(e):
            return jsonify({"error": "Este slug ja esta em uso. Escolha outro."}), 409
        logger.exception("Erro ao salvar storefront settings")
        return jsonify({"error": str(e)}), 500


@app.route("/api/promotions", methods=["GET"])
@login_required
def api_get_promotions():
    """Lista promocoes do usuario com paginacao. ?limit=100&offset=0."""
    user_id = get_current_user_id()
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))
    return jsonify(storage.get_user_promotions(user_id, limit=limit, offset=offset))


@app.route("/api/promotions", methods=["POST"])
@login_required
def api_add_promotion():
    """Adiciona um produto ao site publico."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    if not data.get("shortLink"):
        return jsonify({"error": "shortLink obrigatorio"}), 400
    if not data.get("title"):
        return jsonify({"error": "title obrigatorio"}), 400

    user_id = get_current_user_id()
    promotion_id = storage.add_promotion(user_id, data)
    logger.info("Promocao adicionada: user=%d id=%d", user_id, promotion_id)
    return jsonify({"added": True, "id": promotion_id})


@app.route("/api/promotions/<int:promotion_id>", methods=["DELETE"])
@login_required
def api_remove_promotion(promotion_id):
    """Remove uma promocao do site publico."""
    user_id = get_current_user_id()
    storage.remove_promotion(user_id, promotion_id)
    return jsonify({"removed": True})


@app.route("/api/promotions/<int:promotion_id>/toggle", methods=["PATCH"])
@login_required
def api_toggle_promotion(promotion_id):
    """Ativa ou desativa uma promocao."""
    user_id = get_current_user_id()
    new_state = storage.toggle_promotion(user_id, promotion_id)
    return jsonify({"isActive": new_state})


# ─── Planos & Assinaturas ────────────────────────────────


@app.route("/api/plans")
def api_get_plans():
    """Retorna planos disponiveis (publico)."""
    plans = storage.get_plans(active_only=True)
    return jsonify(plans)


@app.route("/api/subscription")
@login_required
def api_get_subscription():
    """Retorna assinatura atual do usuario."""
    user_id = get_current_user_id()
    sub = storage.get_user_subscription(user_id)
    if not sub:
        return jsonify({"status": "none", "message": "Sem assinatura"})
    return jsonify(sub)


@app.route("/api/subscription/checkout", methods=["POST"])
@login_required
@limiter.limit("5 per minute")
def api_checkout():
    """Cria pagamento PIX via Mercado Pago para um plano."""
    if not MERCADO_PAGO_ACCESS_TOKEN:
        return jsonify({"error": "Gateway de pagamento nao configurado"}), 500

    data = request.get_json()
    if not data or not data.get("plan_slug"):
        return jsonify({"error": "plan_slug obrigatorio"}), 400

    plan = storage.get_plan_by_slug(data["plan_slug"])
    if not plan or not plan["is_active"]:
        return jsonify({"error": "Plano nao encontrado"}), 404

    user_id = get_current_user_id()
    user = storage.get_user_by_email_or_id(user_id)

    # Obter ou criar assinatura
    sub = storage.get_user_subscription(user_id)
    if not sub:
        sub_id = storage.create_trial_subscription(user_id)
        sub = storage.get_user_subscription(user_id)
    else:
        sub_id = sub["id"]

    amount = plan["price_cents"] / 100.0
    idempotency_key = hashlib.md5(f"{user_id}-{plan['slug']}-{int(time.time())}".encode()).hexdigest()

    try:
        mp_response = requests.post(
            "https://api.mercadopago.com/v1/payments",
            headers={
                "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
                "Content-Type": "application/json",
                "X-Idempotency-Key": idempotency_key,
            },
            json={
                "transaction_amount": amount,
                "description": f"AffiliateHub - Plano {plan['name']}",
                "payment_method_id": "pix",
                "payer": {"email": user["email"]},
            },
            timeout=30,
        )
        mp_data = mp_response.json()

        if mp_response.status_code not in (200, 201):
            logger.error("Erro Mercado Pago: %s", mp_data)
            return jsonify({"error": "Erro ao criar pagamento", "detail": mp_data.get("message", "")}), 500

        # Extrair dados PIX
        pix_data = mp_data.get("point_of_interaction", {}).get("transaction_data", {})
        qr_code = pix_data.get("qr_code", "")
        qr_code_base64 = pix_data.get("qr_code_base64", "")
        gateway_id = str(mp_data.get("id", ""))

        # Salvar pagamento no banco
        payment_id = storage.create_payment(
            user_id=user_id,
            subscription_id=sub_id,
            plan_id=plan["id"],
            amount_cents=plan["price_cents"],
            gateway="mercadopago",
            gateway_payment_id=gateway_id,
            payment_method="pix",
            pix_qr_code=qr_code,
            pix_qr_code_base64=qr_code_base64,
        )

        logger.info("Pagamento PIX criado: user=%d plan=%s mp_id=%s", user_id, plan["slug"], gateway_id)

        return jsonify({
            "payment_id": payment_id,
            "gateway_payment_id": gateway_id,
            "status": "pending",
            "pix_qr_code": qr_code,
            "pix_qr_code_base64": qr_code_base64,
            "ticket_url": pix_data.get("ticket_url", ""),
            "amount": amount,
            "plan_name": plan["name"],
        })

    except requests.exceptions.RequestException as e:
        logger.exception("Erro de conexao com Mercado Pago")
        return jsonify({"error": "Erro de conexao com gateway de pagamento"}), 502


@app.route("/api/subscription/payments")
@login_required
def api_get_payments():
    """Retorna historico de pagamentos do usuario."""
    user_id = get_current_user_id()
    payments = storage.get_user_payments(user_id)
    return jsonify(payments)


@app.route("/api/subscription/check-payment/<gateway_payment_id>")
@login_required
def api_check_payment(gateway_payment_id):
    """Verifica status de um pagamento no Mercado Pago."""
    if not MERCADO_PAGO_ACCESS_TOKEN:
        return jsonify({"error": "Gateway nao configurado"}), 500

    try:
        mp_response = requests.get(
            f"https://api.mercadopago.com/v1/payments/{gateway_payment_id}",
            headers={"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"},
            timeout=15,
        )
        mp_data = mp_response.json()
        status = mp_data.get("status", "unknown")

        # Se aprovado, ativar assinatura
        if status == "approved":
            payment = storage.get_payment_by_gateway_id(gateway_payment_id)
            if payment and payment["status"] != "approved":
                storage.update_payment_status(payment["id"], "approved", paid_at=int(time.time()))
                storage.activate_subscription(payment["subscription_id"], payment["plan_id"], gateway="mercadopago")
                logger.info("Pagamento aprovado via polling: %s", gateway_payment_id)

        return jsonify({"status": status})
    except Exception as e:
        logger.exception("Erro ao verificar pagamento")
        return jsonify({"error": str(e)}), 500


# ─── Webhook Mercado Pago ────────────────────────────────


@app.route("/api/webhooks/mercadopago", methods=["POST"])
def api_mercadopago_webhook():
    """Recebe notificacoes de pagamento do Mercado Pago."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"received": True}), 200

    action = data.get("action", "")
    payment_id = None

    if data.get("type") == "payment":
        payment_id = str(data.get("data", {}).get("id", ""))
    elif "payment" in action:
        payment_id = str(data.get("data", {}).get("id", ""))

    if not payment_id or not MERCADO_PAGO_ACCESS_TOKEN:
        return jsonify({"received": True}), 200

    try:
        # Buscar detalhes do pagamento no Mercado Pago
        mp_response = requests.get(
            f"https://api.mercadopago.com/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"},
            timeout=15,
        )
        mp_data = mp_response.json()
        status = mp_data.get("status", "")

        payment = storage.get_payment_by_gateway_id(payment_id)
        if not payment:
            logger.warning("Webhook: pagamento %s nao encontrado no banco", payment_id)
            return jsonify({"received": True}), 200

        if status == "approved" and payment["status"] != "approved":
            storage.update_payment_status(payment["id"], "approved", paid_at=int(time.time()))
            storage.activate_subscription(payment["subscription_id"], payment["plan_id"], gateway="mercadopago")
            logger.info("Webhook: pagamento %s aprovado, assinatura ativada (user=%d)", payment_id, payment["user_id"])

        elif status == "rejected" and payment["status"] == "pending":
            storage.update_payment_status(payment["id"], "rejected")
            logger.info("Webhook: pagamento %s rejeitado", payment_id)

        elif status == "cancelled" and payment["status"] == "pending":
            storage.update_payment_status(payment["id"], "cancelled")
            logger.info("Webhook: pagamento %s cancelado", payment_id)

    except Exception as e:
        logger.exception("Erro ao processar webhook Mercado Pago")

    return jsonify({"received": True}), 200


# ─── Site Publico (sem autenticacao) ─────────────────────


@app.route("/s/<slug>")
def public_storefront(slug):
    """Pagina publica de promocoes de um usuario."""
    user = storage.get_user_by_slug(slug)
    if not user:
        return render_template("storefront.html", user=None, promotions=[], slug=slug)

    promotions = storage.get_public_promotions(slug)
    return render_template("storefront.html", user=user, promotions=promotions, slug=slug)


# ─── Admin Panel ──────────────────────────────────────────


@app.route("/admin")
@admin_required
def admin_dashboard():
    """Painel administrativo - dashboard."""
    return render_template("admin.html")


@app.route("/api/admin/stats")
@admin_required
def api_admin_stats():
    """Retorna estatisticas para o dashboard admin."""
    stats = storage.admin_get_stats()
    fin_stats = storage.admin_get_subscription_stats()
    stats["subscriptions"] = fin_stats["subscriptions"]
    stats["financial"] = fin_stats["financial"]
    stats["plan_distribution"] = fin_stats["plan_distribution"]
    return jsonify(stats)


@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    """Lista usuarios com pesquisa e paginacao."""
    search = request.args.get("search", "").strip()
    limit = min(int(request.args.get("limit", 50)), 100)
    offset = int(request.args.get("offset", 0))
    users = storage.admin_list_users(search=search, limit=limit, offset=offset)
    total = storage.admin_count_users(search=search)
    return jsonify({"users": users, "total": total})


@app.route("/api/admin/users/<int:uid>")
@admin_required
def api_admin_user_detail(uid):
    """Detalhes de um usuario."""
    user = storage.admin_get_user_detail(uid)
    if not user:
        return jsonify({"error": "Usuario nao encontrado"}), 404
    return jsonify(user)


@app.route("/api/admin/users/<int:uid>/toggle-block", methods=["POST"])
@admin_required
def api_admin_toggle_block(uid):
    """Bloqueia/desbloqueia usuario."""
    current_user_id = get_current_user_id()
    if uid == current_user_id:
        return jsonify({"error": "Voce nao pode bloquear a si mesmo"}), 400
    new_state = storage.admin_toggle_block(uid)
    return jsonify({"blocked": new_state})


@app.route("/api/admin/users/<int:uid>/set-admin", methods=["POST"])
@admin_required
def api_admin_set_admin(uid):
    """Define/remove admin de um usuario."""
    data = request.get_json()
    is_admin = data.get("is_admin", False) if data else False
    current_user_id = get_current_user_id()
    if uid == current_user_id:
        return jsonify({"error": "Voce nao pode alterar seu proprio admin"}), 400
    storage.admin_set_admin(uid, is_admin)
    return jsonify({"is_admin": is_admin})


# ─── Startup ──────────────────────────────────────────────


if __name__ == "__main__":
    logger.info("Iniciando AffiliateHub SaaS")
    port = int(os.environ.get("PORT", 5000))
    logger.info("Acesse: http://localhost:%d", port)
    app.run(debug=FLASK_DEBUG, host="0.0.0.0", port=port)
