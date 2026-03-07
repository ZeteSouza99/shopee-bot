"""
Canais Instagram via Graph API v18.0.

Suporta publicacao no Feed e nos Stories.
Credenciais passadas via construtor (per-user).
"""

import logging
import time

import requests

from channels.base import SocialChannel, PublishResult, ChannelStatus

logger = logging.getLogger(__name__)

GRAPH_API = "https://graph.facebook.com/v18.0"


class InstagramFeedChannel(SocialChannel):

    def __init__(self, access_token: str = "", user_id: str = ""):
        self.access_token = access_token
        self.ig_user_id = user_id

    @property
    def channel_name(self) -> str:
        return "instagram_feed"

    @property
    def display_name(self) -> str:
        return "Instagram Feed"

    def is_configured(self) -> bool:
        return bool(self.access_token and self.ig_user_id)

    def check_status(self) -> ChannelStatus:
        if not self.is_configured():
            return ChannelStatus("instagram_feed", False, False)
        try:
            r = requests.get(
                f"{GRAPH_API}/{self.ig_user_id}",
                params={"fields": "username", "access_token": self.access_token},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            return ChannelStatus("instagram_feed", True, True, {
                "username": data.get("username"),
            })
        except Exception as e:
            logger.warning("Falha ao verificar Instagram Feed: %s", e)
            return ChannelStatus("instagram_feed", True, False)

    def publish_text(self, text: str) -> PublishResult:
        return PublishResult(False, "instagram_feed", "Instagram Feed requer uma imagem")

    def publish_with_image(self, text: str, image_url: str) -> PublishResult:
        try:
            r1 = requests.post(
                f"{GRAPH_API}/{self.ig_user_id}/media",
                data={
                    "image_url": image_url,
                    "caption": text,
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            r1.raise_for_status()
            creation_id = r1.json()["id"]

            time.sleep(3)
            r2 = requests.post(
                f"{GRAPH_API}/{self.ig_user_id}/media_publish",
                data={
                    "creation_id": creation_id,
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            r2.raise_for_status()
            return PublishResult(True, "instagram_feed", "Publicado no Feed", r2.json())
        except Exception as e:
            return PublishResult(False, "instagram_feed", str(e))


class InstagramStoriesChannel(SocialChannel):

    def __init__(self, access_token: str = "", user_id: str = ""):
        self.access_token = access_token
        self.ig_user_id = user_id

    @property
    def channel_name(self) -> str:
        return "instagram_stories"

    @property
    def display_name(self) -> str:
        return "Instagram Stories"

    def is_configured(self) -> bool:
        return bool(self.access_token and self.ig_user_id)

    def check_status(self) -> ChannelStatus:
        if not self.is_configured():
            return ChannelStatus("instagram_stories", False, False)
        return ChannelStatus("instagram_stories", True, True)

    def publish_text(self, text: str) -> PublishResult:
        return PublishResult(False, "instagram_stories", "Instagram Stories requer uma imagem")

    def publish_with_image(self, text: str, image_url: str) -> PublishResult:
        try:
            r1 = requests.post(
                f"{GRAPH_API}/{self.ig_user_id}/media",
                data={
                    "image_url": image_url,
                    "media_type": "STORIES",
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            r1.raise_for_status()
            creation_id = r1.json()["id"]

            time.sleep(3)
            r2 = requests.post(
                f"{GRAPH_API}/{self.ig_user_id}/media_publish",
                data={
                    "creation_id": creation_id,
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            r2.raise_for_status()
            return PublishResult(True, "instagram_stories", "Publicado nos Stories", r2.json())
        except Exception as e:
            return PublishResult(False, "instagram_stories", str(e))
