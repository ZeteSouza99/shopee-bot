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
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        """Lista grupos do WhatsApp via findChats + findGroupInfos.

        O endpoint fetchAllGroups do Evolution API v1.8.x retorna corpo vazio
        mesmo havendo grupos. Solucao: usar findChats para obter IDs de grupos
        (@g.us) e depois buscar nomes via findGroupInfos em paralelo.
        """
        # Passo 1: buscar todos os chats
        r = requests.get(
            f"{self.api_url}/chat/findChats/{self.instance_name}",
            headers=self._headers(),
            timeout=20,
        )
        r.raise_for_status()
        if not r.content:
            return []
        chats = r.json()
        if not isinstance(chats, list):
            return []

        # Passo 2: filtrar grupos (@g.us) e ordenar pelos mais recentes
        group_chats = [
            c for c in chats
            if isinstance(c.get("id"), str) and c["id"].endswith("@g.us")
        ]
        group_chats.sort(key=lambda c: c.get("lastMsgTimestamp", 0), reverse=True)
        top_groups = group_chats[:50]

        if not top_groups:
            return []

        # Passo 3: buscar nome de cada grupo em paralelo via findGroupInfos
        def _get_info(gid: str) -> dict:
            try:
                r2 = requests.get(
                    f"{self.api_url}/group/findGroupInfos/{self.instance_name}",
                    headers=self._headers(),
                    params={"groupJid": gid},
                    timeout=10,
                )
                if r2.status_code == 200 and r2.content:
                    info = r2.json()
                    participants = info.get("participants", [])
                    return {
                        "id": gid,
                        "subject": info.get("subject") or gid,
                        "size": len(participants) if isinstance(participants, list) else 0,
                    }
            except Exception:
                pass
            return {"id": gid, "subject": gid, "size": 0}

        groups = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_get_info, gc["id"]): gc["id"] for gc in top_groups}
            for future in as_completed(futures):
                groups.append(future.result())

        # Reordenar por nome para facilitar selecao
        groups.sort(key=lambda g: g["subject"].lower())
        return groups

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
            # Evolution API v1.8.x requer wrapper "textMessage"
            payload = {
                "number": self.group_id,
                "textMessage": {"text": text},
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
            # Evolution API v1.8.x requer wrapper "mediaMessage"
            payload = {
                "number": self.group_id,
                "mediaMessage": {
                    "mediatype": "image",
                    "media": image_url,
                    "fileName": "produto.jpg",
                    "caption": text,
                },
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
