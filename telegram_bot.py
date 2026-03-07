"""
Modulo para publicacao de anuncios no Telegram via Bot API.

Envia mensagens com foto e texto para canais/grupos.
Inclui validacao de URLs para prevenir SSRF.
Todas as funcoes aceitam bot_token e chat_id como parametros.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"

# Redes internas bloqueadas para prevencao de SSRF
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _validate_url(url: str) -> str:
    """Valida que a URL e externa (http/https) e nao resolve para IP interno."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Esquema de URL nao permitido: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL sem hostname")

    try:
        ip_str = socket.gethostbyname(hostname)
        ip_addr = ipaddress.ip_address(ip_str)
        for network in _BLOCKED_NETWORKS:
            if ip_addr in network:
                raise ValueError("URL resolve para rede interna bloqueada")
    except socket.gaierror:
        raise ValueError(f"Nao foi possivel resolver hostname: {hostname}")

    return url


def _api_url(method: str, bot_token: str) -> str:
    """Constroi a URL da API do Telegram."""
    if not bot_token:
        raise ValueError("Token do bot Telegram nao configurado")
    return f"{TELEGRAM_API.format(token=bot_token)}/{method}"


def enviar_com_foto(texto: str, image_url: str, bot_token: str = "", chat_id: str = "") -> dict:
    """Envia uma mensagem com foto para o Telegram."""
    if not chat_id:
        raise ValueError("Chat ID do Telegram nao configurado")

    try:
        validated_url = _validate_url(image_url)
        logger.info("Baixando imagem de %s para envio ao Telegram", validated_url[:80])

        img_response = requests.get(validated_url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0"
        })
        img_response.raise_for_status()
        image_bytes = img_response.content

        response = requests.post(
            _api_url("sendPhoto", bot_token),
            data={"chat_id": chat_id, "caption": texto},
            files={"photo": ("produto.jpg", image_bytes, "image/jpeg")},
            timeout=30,
        )
        response.raise_for_status()
        logger.info("Foto enviada ao Telegram com sucesso")
        return response.json()

    except Exception as e:
        logger.warning("Falha ao enviar foto, fazendo fallback para texto: %s", e)
        return enviar_texto(texto, bot_token=bot_token, chat_id=chat_id)


def enviar_texto(texto: str, bot_token: str = "", chat_id: str = "") -> dict:
    """Envia uma mensagem de texto simples para o Telegram."""
    if not chat_id:
        raise ValueError("Chat ID do Telegram nao configurado")

    payload = {"chat_id": chat_id, "text": texto}

    logger.info("Enviando texto ao Telegram (chat: %s)", chat_id)
    response = requests.post(_api_url("sendMessage", bot_token), json=payload, timeout=15)
    response.raise_for_status()
    logger.info("Texto enviado ao Telegram com sucesso")
    return response.json()


def verificar_bot(bot_token: str = "") -> dict:
    """Verifica se o bot esta configurado corretamente."""
    logger.info("Verificando configuracao do bot Telegram")
    response = requests.get(_api_url("getMe", bot_token), timeout=10)
    response.raise_for_status()
    return response.json()
