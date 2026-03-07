"""
Classe base abstrata para canais de publicacao social.

Todos os canais (Telegram, WhatsApp, Instagram) devem herdar de SocialChannel.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PublishResult:
    """Resultado de uma operacao de publicacao."""
    success: bool
    channel: str
    message: str
    response_data: Optional[dict] = field(default=None)


@dataclass
class ChannelStatus:
    """Status de configuracao e conexao de um canal."""
    channel: str
    configured: bool
    connected: bool
    details: Optional[dict] = field(default=None)


class SocialChannel(ABC):
    """Interface abstrata para canais de publicacao."""

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Identificador unico: 'telegram', 'whatsapp', etc."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Nome para exibicao: 'Telegram', 'WhatsApp', etc."""
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Se as credenciais estao presentes no .env."""
        ...

    @abstractmethod
    def check_status(self) -> ChannelStatus:
        """Verifica conectividade e autenticacao."""
        ...

    @abstractmethod
    def publish_text(self, text: str) -> PublishResult:
        """Envia mensagem somente texto."""
        ...

    @abstractmethod
    def publish_with_image(self, text: str, image_url: str) -> PublishResult:
        """Envia mensagem com imagem."""
        ...
