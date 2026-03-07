"""
Factory de canais de publicacao.

Cria instancias de canais a partir de credenciais do usuario (per-user).
Nao usa singletons - cada chamada cria novas instancias.
"""

from channels.telegram import TelegramChannel
from channels.whatsapp import WhatsAppChannel
from channels.instagram import InstagramFeedChannel, InstagramStoriesChannel


def create_channels(credentials: dict, evolution_api_url: str = "", evolution_api_key: str = "") -> dict:
    """
    Cria instancias de todos os canais a partir das credenciais do usuario.

    Args:
        credentials: dict com chaves por servico, ex:
            {
                "telegram": {"bot_token": "...", "chat_id": "..."},
                "whatsapp": {"instance_name": "...", "group_id": "..."},
                "instagram": {"access_token": "...", "user_id": "..."},
            }
        evolution_api_url: URL da Evolution API (config global)
        evolution_api_key: Chave da Evolution API (config global)

    Returns:
        dict com instancias de canais: {"telegram": TelegramChannel(...), ...}
    """
    tg_creds = credentials.get("telegram", {})
    wa_creds = credentials.get("whatsapp", {})
    ig_creds = credentials.get("instagram", {})

    return {
        "telegram": TelegramChannel(
            bot_token=tg_creds.get("bot_token", ""),
            chat_id=tg_creds.get("chat_id", ""),
        ),
        "whatsapp": WhatsAppChannel(
            api_url=evolution_api_url,
            api_key=evolution_api_key,
            instance_name=wa_creds.get("instance_name", ""),
            group_id=wa_creds.get("group_id", ""),
        ),
        "instagram_feed": InstagramFeedChannel(
            access_token=ig_creds.get("access_token", ""),
            user_id=ig_creds.get("user_id", ""),
        ),
        "instagram_stories": InstagramStoriesChannel(
            access_token=ig_creds.get("access_token", ""),
            user_id=ig_creds.get("user_id", ""),
        ),
    }
