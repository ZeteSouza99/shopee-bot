"""
Canal WhatsApp via Evolution API.

Usa Evolution API (wrapper do WhatsApp Web via Baileys) para envio de
mensagens em grupos. Cada usuario tem sua propria instancia nomeada pelo
user_id, conectada via QR code.

Endpoints Evolution API utilizados:
  POST /instance/create
  GET  /instance/connect/{instance}
  GET  /instance/connectionState/{instance}
  GET  /group/fetchAllGroups/{instance}
  POST /message/sendText/{instance}
  POST /message/sendMedia/{instance}
  DELETE /instance/delete/{instance}
"""

import logging

import requests

from channels.base import SocialChannel, PublishResult, ChannelStatus

logger = logging.getLogger(__name__)


class WhatsAppChannel(SocialChannel):
    """Canal WhatsApp via Evolution API com suporte a grupos."""

    def __init__(
        self,
        api_url: str = "",
        api_key: str = "",
        instance_name: str = "",
        group_id: str = "",
    ):
        self.api_url = api_url.rstrip("/") if api_url else ""
        self.api_key = api_key
        self.instance_name = instance_name
        self.group_id = group_id

    @property
    def channel_name(self) -> str:
        return "whatsapp"

    @property
    def display_name(self) -> str:
        return "WhatsApp"

    def is_configured(self) -> bool:
        return bool(self.api_url and self.api_key and self.instance_name and self.group_id)

    def _headers(self) -> dict:
        return {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }

    # ─── Instance management (chamados pelas rotas de app.py) ────

    def create_instance(self) -> dict:
        """Cria uma instancia na Evolution API."""
        r = requests.post(
            f"{self.api_url}/instance/create",
            json={
                "instanceName": self.instance_name,
                "qrcode": True,
                "integration": "WHATSAPP-BAILEYS",
            },
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def connect_instance(self) -> dict:
        """Solicita QR code para conexao."""
        r = requests.get(
            f"{self.api_url}/instance/connect/{self.instance_name}",
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def get_connection_state(self) -> dict:
        """Retorna estado da conexao (open, close, connecting)."""
        r = requests.get(
            f"{self.api_url}/instance/connectionState/{self.instance_name}",
            headers=self._headers(),
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def fetch_groups(self) -> list:
        """Lista todos os grupos do WhatsApp conectado."""
        r = requests.get(
            f"{self.api_url}/group/fetchAllGroups/{self.instance_name}",
            params={"getParticipants": "false"},
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        # Evolution API retorna lista direta ou dentro de chave
        if isinstance(data, list):
            return data
        return data.get("groups", data.get("data", []))

    def delete_instance(self) -> dict:
        """Remove a instancia da Evolution API."""
        r = requests.delete(
            f"{self.api_url}/instance/delete/{self.instance_name}",
            headers=self._headers(),
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def logout_instance(self) -> dict:
        """Desconecta o WhatsApp sem remover a instancia."""
        r = requests.delete(
            f"{self.api_url}/instance/logout/{self.instance_name}",
            headers=self._headers(),
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

    # ─── SocialChannel interface ─────────────────────────────────

    def check_status(self) -> ChannelStatus:
        if not self.api_url or not self.api_key or not self.instance_name:
            return ChannelStatus("whatsapp", False, False)
        try:
            state = self.get_connection_state()
            # Evolution API v2: state.instance.state ou state.state
            instance_state = state.get("instance", {}).get("state", "")
            if not instance_state:
                instance_state = state.get("state", "close")

            connected = instance_state == "open"
            return ChannelStatus("whatsapp", True, connected, {
                "instanceName": self.instance_name,
                "state": instance_state,
                "groupId": self.group_id,
            })
        except Exception as e:
            logger.warning("Falha ao verificar WhatsApp Evolution: %s", e)
            return ChannelStatus("whatsapp", True, False)

    def publish_text(self, text: str) -> PublishResult:
        if not self.is_configured():
            return PublishResult(False, "whatsapp", "WhatsApp nao configurado")
        try:
            payload = {
                "number": self.group_id,
                "text": text,
            }
            r = requests.post(
                f"{self.api_url}/message/sendText/{self.instance_name}",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )
            r.raise_for_status()
            return PublishResult(True, "whatsapp", "Enviado com sucesso", r.json())
        except Exception as e:
            return PublishResult(False, "whatsapp", str(e))

    def publish_with_image(self, text: str, image_url: str) -> PublishResult:
        if not self.is_configured():
            return PublishResult(False, "whatsapp", "WhatsApp nao configurado")
        try:
            payload = {
                "number": self.group_id,
                "mediatype": "image",
                "media": image_url,
                "caption": text,
            }
            r = requests.post(
                f"{self.api_url}/message/sendMedia/{self.instance_name}",
                json=payload,
                headers=self._headers(),
                timeout=30,
            )
            r.raise_for_status()
            return PublishResult(True, "whatsapp", "Enviado com sucesso", r.json())
        except Exception as e:
            return PublishResult(False, "whatsapp", str(e))
