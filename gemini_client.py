"""
Cliente para a API Google Gemini (LLM).

Gera textos de anuncio a partir de dados de produto para publicacao em redes sociais.
Suporta prompts especificos por canal (Telegram, WhatsApp, Instagram Feed/Stories).
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MODEL = "gemini-2.5-flash-lite"
MAX_RETRIES = 3

# Contexto base compartilhado por todos os canais
_BASE_SYSTEM = (
    "Voce e um especialista em criar anuncios de ofertas para afiliados da Shopee no Brasil. "
    "Cria anuncios limpos, diretos e profissionais no estilo vitrine de ofertas. "
    "O formato deve ser: emoji relacionado ao produto + nome do produto, "
    "preco original riscado, preco promocional em destaque, parcelas se aplicavel, "
    "link de compra e aviso de promocao sujeita a alteracao. "
    "NAO invente informacoes que nao foram fornecidas. "
    "NAO mencione numero de vendas nem avaliacao por estrelas. "
    "NAO escreva textos longos nem narrativas — seja direto e objetivo."
)

# Prompts por canal
CHANNEL_PROMPTS = {
    "telegram": {
        "system": _BASE_SYSTEM,
        "instructions": (
            "Formato EXATO (use este layout):\n"
            "[emoji relacionado] [Nome do Produto]\n"
            "\n"
            "~de R$ [preco original]~\n"
            "por R$ [preco promocional] [emoji fogo]\n"
            "\n"
            "Compre aqui: [link]\n"
            "\n"
            "_*Promocao sujeita a alteracao a qualquer momento_\n"
            "\n"
            "REGRAS:\n"
            "- Use 1 emoji relacionado ao produto no inicio (ex: panela=🍳, celular=📱, roupa=👕)\n"
            "- Preco original com ~til~ para riscar\n"
            "- Preco promocional em destaque\n"
            "- Se o desconto for grande (acima de 30%), adicione uma linha com parcelas estimadas\n"
            "- NAO use hashtags\n"
            "- NAO escreva textos narrativos, seja direto"
        ),
        "max_tokens": 300,
        "canal_label": "um canal de ofertas no Telegram",
    },
    "whatsapp": {
        "system": _BASE_SYSTEM,
        "instructions": (
            "Formato EXATO (use este layout com formatacao WhatsApp):\n"
            "[emoji relacionado] [Nome do Produto]\n"
            "\n"
            "~de R$ [preco original]~\n"
            "🏷️ por *R$ [preco promocional]* 🔥\n"
            "💳 ou [X]x de R$ [valor parcela] sem juros\n"
            "\n"
            "Compre aqui: [link]\n"
            "\n"
            "_*Promocao sujeita a alteracao a qualquer momento_\n"
            "\n"
            "REGRAS:\n"
            "- Use 1 emoji relacionado ao produto no inicio (ex: panela=🍳, celular=📱, fone=🎧)\n"
            "- Use ~til~ para riscar o preco original (formatacao WhatsApp)\n"
            "- Use *asterisco* para negrito no preco promocional\n"
            "- Calcule parcelas em 2x ou 3x sem juros (divida o preco por 2 ou 3)\n"
            "- Use _italico_ no aviso final\n"
            "- NAO use hashtags\n"
            "- NAO escreva textos narrativos, seja direto e limpo"
        ),
        "max_tokens": 300,
        "canal_label": "um grupo de ofertas no WhatsApp",
    },
    "instagram_feed": {
        "system": _BASE_SYSTEM,
        "instructions": (
            "Formato EXATO (use este layout para legenda de post):\n"
            "[emoji relacionado] [Nome do Produto]\n"
            "\n"
            "De R$ [preco original] ❌\n"
            "🏷️ Por R$ [preco promocional] 🔥\n"
            "💳 ou [X]x de R$ [valor parcela] sem juros\n"
            "\n"
            "🛒 Link na bio!\n"
            "\n"
            "⚠️ Promocao sujeita a alteracao a qualquer momento\n"
            "\n"
            "[8-12 hashtags relevantes como #shopee #oferta #desconto #promocao #acheinashopee]\n"
            "\n"
            "REGRAS:\n"
            "- Use 1 emoji relacionado ao produto no inicio\n"
            "- NAO inclua link direto (Instagram nao permite links clicaveis no caption)\n"
            "- Inclua hashtags relevantes ao final\n"
            "- Seja direto e limpo, sem textos narrativos"
        ),
        "max_tokens": 350,
        "canal_label": "um post no Instagram Feed",
    },
    "instagram_stories": {
        "system": _BASE_SYSTEM,
        "instructions": (
            "Formato EXATO (texto curto para Stories):\n"
            "[emoji relacionado] [Nome curto do Produto]\n"
            "\n"
            "De R$ [preco original] ❌\n"
            "Por R$ [preco promocional] 🔥\n"
            "\n"
            "🛒 Arraste para cima!\n"
            "\n"
            "REGRAS:\n"
            "- Texto MUITO curto (maximo 100 caracteres)\n"
            "- Use o nome resumido do produto\n"
            "- NAO inclua link, hashtags nem aviso\n"
            "- Seja extremamente direto"
        ),
        "max_tokens": 150,
        "canal_label": "um Story/Reels no Instagram",
    },
}


def gerar_anuncio(produto: dict, canal: str = "telegram", api_key: str = "", custom_template: str = None) -> str:
    """
    Gera texto de anuncio para publicacao em redes sociais.

    Se custom_template fornecido, faz substituicao de variaveis sem chamar a IA.
    Variaveis disponiveis: {{productName}}, {{priceMin}}, {{priceMax}},
    {{priceDiscountRate}}, {{commission}}, {{commissionRate}}, {{shopName}},
    {{shortLink}}, {{offerLink}}.

    Args:
        produto: Dicionario com dados do produto.
        canal: Nome do canal de destino.
        api_key: Chave de API do Gemini.
        custom_template: Template com variaveis {{campo}} para substituicao direta.

    Returns:
        Texto do anuncio pronto para publicacao.
    """
    nome = produto.get("productName") or produto.get("shopName") or produto.get("offerName") or "Produto"
    preco_min = produto.get("priceMin", "")
    preco_max = produto.get("priceMax", "")
    desconto = produto.get("priceDiscountRate", "")
    loja = produto.get("shopName", "")
    link = produto.get("shortLink") or produto.get("_shortLink") or produto.get("offerLink") or produto.get("productLink", "")

    # Se template customizado fornecido, substituir variaveis e retornar
    if custom_template:
        template_vars = {
            "productName": nome,
            "priceMin": f"R$ {preco_min}" if preco_min else "",
            "priceMax": f"R$ {preco_max}" if preco_max else "",
            "priceDiscountRate": f"{desconto}%" if desconto else "",
            "commission": produto.get("commission", ""),
            "commissionRate": f"{produto.get('commissionRate', '')}%" if produto.get("commissionRate") else "",
            "shopName": loja,
            "shortLink": link,
            "offerLink": produto.get("offerLink") or produto.get("productLink", ""),
        }
        try:
            result = custom_template
            for key, value in template_vars.items():
                result = result.replace("{{" + key + "}}", str(value) if value else "")
            logger.info("Anuncio gerado via template customizado (canal: %s)", canal)
            return result.strip()
        except Exception as e:
            logger.warning("Erro ao aplicar template customizado, usando IA: %s", e)

    if not api_key:
        raise ValueError("Chave da API Gemini nao configurada. Acesse Configuracoes.")

    channel_config = CHANNEL_PROMPTS.get(canal, CHANNEL_PROMPTS["telegram"])

    # Calcular preco original se houver desconto
    preco_info = ""
    try:
        preco = float(preco_min)
        desc = float(desconto) if desconto else 0
        if desc > 0:
            original = preco / (1 - desc / 100)
            preco_info = f"De R$ {original:.2f} por R$ {preco:.2f} ({int(desc)}% de desconto)"
        else:
            preco_info = f"R$ {preco:.2f}"
    except (ValueError, TypeError, ZeroDivisionError):
        preco_info = f"R$ {preco_min}" if preco_min else ""

    system_instruction = channel_config["system"]

    prompt = f"""Crie um anuncio de produto para {channel_config['canal_label']}.

PRODUTO:
- Nome: {nome}
- Preco: {preco_info}
- Loja: {loja}
- Link de compra: {link}

INSTRUCOES:
{channel_config['instructions']}

Responda APENAS com o texto do anuncio pronto para publicar, sem explicacoes ou comentarios."""

    payload = {
        "system_instruction": {
            "parts": [{"text": system_instruction}]
        },
        "contents": [
            {"parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": channel_config["max_tokens"],
        },
    }

    url = API_URL.format(model=MODEL)

    logger.info("Gerando anuncio via Gemini (%s) para canal '%s': %s", MODEL, canal, nome[:50])

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                url,
                json=payload,
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            if response.status_code == 429:
                wait = 5 * (2 ** attempt)
                logger.warning("Gemini rate limit (429), aguardando %ds (tentativa %d/%d)", wait, attempt + 1, MAX_RETRIES)
                last_error = response.text
                time.sleep(wait)
                continue
            response.raise_for_status()
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            logger.info("Anuncio gerado com sucesso via Gemini (canal: %s)", canal)
            return text.strip()
        except requests.exceptions.RequestException as e:
            logger.error("Erro na requisicao ao Gemini (tentativa %d/%d): %s", attempt + 1, MAX_RETRIES, e)
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(2)

    raise Exception(f"Gemini API: falha apos {MAX_RETRIES} tentativas. Detalhes: {last_error}")
