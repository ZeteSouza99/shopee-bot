"""
Cliente para a API Google Gemini.

Gera textos de anuncio a partir de dados de produto para publicacao em redes sociais.
Suporta prompts especificos por canal (Telegram, WhatsApp, Instagram Feed/Stories).
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
MAX_RETRIES = 3

# Prompts por canal
CHANNEL_PROMPTS = {
    "telegram": {
        "system": (
            "Voce e um Expert em Marketing Digital com mais de 10 anos de experiencia em "
            "vendas online, copywriting e gestao de canais no Telegram. "
            "Seu especialidade e criar anuncios altamente persuasivos que geram cliques e conversoes imediatas. "
            "Voce domina gatilhos mentais como escassez, prova social, urgencia e ancoragem de preco. "
            "Seus textos sao diretos, envolventes e adaptados ao publico brasileiro que busca ofertas e descontos."
        ),
        "instructions": (
            "- Use gatilhos mentais (urgencia, prova social, escassez, ancoragem de preco)\n"
            "- Emojis estrategicos para chamar atencao (maximo 4-5 emojis no total)\n"
            "- Destaque o desconto e o preco final com destaque visual\n"
            "- Texto direto, sem enrolacao — o leitor decide em segundos\n"
            "- O link de compra deve aparecer no final em linha separada\n"
            "- NAO mencione numero de vendas nem avaliacao por estrelas\n"
            "- NAO invente informacoes que nao foram fornecidas\n"
            "- NAO use hashtags\n"
            "- Maximo 300 caracteres no texto (sem contar o link)"
        ),
        "max_tokens": 300,
        "canal_label": "um canal de ofertas no Telegram",
    },
    "whatsapp": {
        "system": (
            "Voce e um Expert em Marketing Digital especializado em vendas por WhatsApp. "
            "Cria mensagens persuasivas e conversacionais, como se estivesse recomendando a um amigo proximo. "
            "Domina gatilhos mentais e usa a formatacao nativa do WhatsApp (*negrito*, _italico_) para impacto visual. "
            "Seus textos sao adaptados ao publico brasileiro que busca ofertas e descontos."
        ),
        "instructions": (
            "- Use *negrito* para destacar precos e descontos (formatacao WhatsApp)\n"
            "- Tom conversacional, como recomendar a um amigo\n"
            "- Emojis estrategicos (maximo 5-6 emojis)\n"
            "- Destaque o desconto e o preco final\n"
            "- O link de compra deve aparecer no final em linha separada\n"
            "- NAO mencione numero de vendas nem avaliacao por estrelas\n"
            "- NAO invente informacoes que nao foram fornecidas\n"
            "- NAO use hashtags\n"
            "- Maximo 350 caracteres no texto (sem contar o link)"
        ),
        "max_tokens": 350,
        "canal_label": "um grupo de ofertas no WhatsApp",
    },
    "instagram_feed": {
        "system": (
            "Voce e um Expert em Marketing Digital especializado em Instagram. "
            "Cria legendas envolventes que geram engajamento e salvamentos. "
            "Domina o uso de hashtags estrategicas e CTAs que direcionam ao link na bio. "
            "Seus textos sao adaptados ao publico brasileiro que busca ofertas e descontos."
        ),
        "instructions": (
            "- Comece com uma frase de impacto que pare o scroll\n"
            "- Use emojis estrategicamente (6-8 emojis)\n"
            "- Inclua 5-10 hashtags relevantes ao final (#ofertas #shopee #desconto etc.)\n"
            "- NAO inclua o link (Instagram nao permite links clicaveis no caption)\n"
            "- Termine com CTA: 'Link na bio!' ou 'Acesse o link nos stories!'\n"
            "- NAO mencione numero de vendas nem avaliacao por estrelas\n"
            "- NAO invente informacoes que nao foram fornecidas\n"
            "- Maximo 400 caracteres no texto principal (sem contar hashtags)"
        ),
        "max_tokens": 500,
        "canal_label": "um post no Instagram Feed",
    },
    "instagram_stories": {
        "system": (
            "Voce e um Expert em Marketing Digital especializado em Instagram Stories. "
            "Cria textos ultra-curtos e impactantes que geram urgencia imediata. "
            "Seus textos sao punchlines que fazem o usuario deslizar para cima ou clicar no link. "
            "Adaptado ao publico brasileiro que busca ofertas."
        ),
        "instructions": (
            "- Texto MUITO curto e impactante (maximo 100 caracteres)\n"
            "- Use 2-3 emojis no maximo\n"
            "- Gatilho de urgencia forte\n"
            "- NAO inclua link nem hashtags (o link vai no sticker)\n"
            "- Formato ideal: Frase de impacto + preco\n"
            "- NAO mencione numero de vendas nem avaliacao por estrelas\n"
            "- NAO invente informacoes que nao foram fornecidas"
        ),
        "max_tokens": 150,
        "canal_label": "um Story no Instagram",
    },
}


def gerar_anuncio(produto: dict, canal: str = "telegram", api_key: str = "") -> str:
    """
    Usa o Gemini para criar um texto de anuncio persuasivo.

    Args:
        produto: Dicionario com dados do produto (nome, preco, desconto, link, etc).
        canal: Nome do canal de destino ('telegram', 'whatsapp', 'instagram_feed', 'instagram_stories').
        api_key: Chave de API do Gemini do usuario.

    Returns:
        Texto do anuncio pronto para publicacao.
    """
    if not api_key:
        raise ValueError("Chave da API Gemini nao configurada. Acesse Configuracoes.")

    channel_config = CHANNEL_PROMPTS.get(canal, CHANNEL_PROMPTS["telegram"])

    nome = produto.get("productName") or produto.get("shopName") or produto.get("offerName") or "Produto"
    preco_min = produto.get("priceMin", "")
    preco_max = produto.get("priceMax", "")
    desconto = produto.get("priceDiscountRate", "")
    loja = produto.get("shopName", "")
    link = produto.get("shortLink") or produto.get("_shortLink") or produto.get("offerLink") or produto.get("productLink", "")

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
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": channel_config["max_tokens"],
        },
    }

    logger.info("Gerando anuncio via Gemini para canal '%s': %s", canal, nome[:50])

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                f"{API_URL}?key={api_key}",
                json=payload,
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
