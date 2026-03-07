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
from config import FLASK_SECRET_KEY, FLASK_DEBUG, EVOLUTION_API_URL, EVOLUTION_API_KEY
from auth import login_required, get_current_user_id, register_user, authenticate_user
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
    """Retorna a chave Gemini do usuario logado."""
    user_id = get_current_user_id()
    creds = storage.get_user_credentials(user_id, "gemini")
    return creds.get("api_key", "")


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
    """Retorna o historico. Opcional: ?itemId=123."""
    user_id = get_current_user_id()
    item_id = request.args.get("itemId")
    if item_id:
        item_id = int(item_id)
    return jsonify(storage.get_historico(user_id, item_id))


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
    result["gemini"] = bool(_get_gemini_key())
    return jsonify(result)


@app.route("/api/channels/preview", methods=["POST"])
@login_required
@limiter.limit("15 per minute")
def api_channels_preview():
    """Gera preview de anuncio via Gemini para canais selecionados."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo JSON obrigatorio"}), 400

    produto = data.get("produto", {})
    canais = data.get("canais", ["telegram"])
    api_key = _get_gemini_key()

    results = {}
    for canal in canais:
        try:
            texto = gemini_client.gerar_anuncio(produto, canal=canal, api_key=api_key)
            results[canal] = {"texto": texto}
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

        # Solicita QR code
        qr_data = wa.connect_instance()

        # Salva instance_name nas credenciais do usuario
        wa_creds = storage.get_user_credentials(user_id, "whatsapp")
        wa_creds["instance_name"] = wa.instance_name
        storage.save_user_credentials(user_id, "whatsapp", wa_creds)

        # Evolution API retorna QR em base64 ou como pairingCode
        qr_base64 = qr_data.get("base64", "")
        pairing_code = qr_data.get("pairingCode", "")

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


# ─── Startup ──────────────────────────────────────────────


if __name__ == "__main__":
    logger.info("Iniciando Shopee Afiliados SaaS")
    logger.info("Acesse: http://localhost:5000")
    app.run(debug=FLASK_DEBUG, port=5000)
