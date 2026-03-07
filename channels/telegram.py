"""
Canal Telegram - Usa credenciais passadas no construtor.
"""

import logging

import requests

from channels.base import SocialChannel, PublishResult, ChannelStatus
from telegram_bot import _validate_url

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


class TelegramChannel(SocialChannel):

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def display_name(self) -> str:
        return "Telegram"

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def _api_url(self, method: str) -> str:
        return f"{TELEGRAM_API.format(token=self.bot_token)}/{method}"

    def check_status(self) -> ChannelStatus:
        if not self.is_configured():
            return ChannelStatus("telegram", False, False)
        try:
            r = requests.get(self._api_url("getMe"), timeout=10)
            r.raise_for_status()
            username = r.json().get("result", {}).get("username")
            return ChannelStatus("telegram", True, True, {
                "botUsername": username,
                "chatId": self.chat_id,
            })
        except Exception as e:
            logger.warning("Falha ao verificar bot Telegram: %s", e)
            return ChannelStatus("telegram", True, False)

    def publish_text(self, text: str) -> PublishResult:
        try:
            payload = {"chat_id": self.chat_id, "text": text}
            r = requests.post(self._api_url("sendMessage"), json=payload, timeout=15)
            r.raise_for_status()
            return PublishResult(True, "telegram", "Enviado com sucesso", r.json())
        except Exception as e:
            return PublishResult(False, "telegram", str(e))

    def publish_with_image(self, text: str, image_url: str) -> PublishResult:
        try:
            validated_url = _validate_url(image_url)
            img_response = requests.get(validated_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            img_response.raise_for_status()

            r = requests.post(
                self._api_url("sendPhoto"),
                data={"chat_id": self.chat_id, "caption": text},
                files={"photo": ("produto.jpg", img_response.content, "image/jpeg")},
                timeout=30,
            )
            r.raise_for_status()
            return PublishResult(True, "telegram", "Enviado com sucesso", r.json())
        except Exception as e:
            logger.warning("Falha ao enviar foto, fazendo fallback para texto: %s", e)
            return self.publish_text(text)
