"""
Cliente Python para a API GraphQL da Shopee Afiliados.

Gerencia autenticação (SHA256), construção de queries GraphQL
e comunicação com todos os endpoints disponíveis.
"""

import hashlib
import json
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://open-api.affiliate.shopee.com.br/graphql"


def _escape_graphql_string(value: str) -> str:
    """Escapa caracteres perigosos para strings em queries GraphQL."""
    return (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


class ShopeeAffiliateClient:
    """Cliente para interação com a API Shopee Afiliados via GraphQL."""

    def __init__(self, app_id: str, secret: str):
        self.app_id = app_id
        self.secret = secret
        self.base_url = BASE_URL
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _generate_signature(self, payload: str, timestamp: int) -> str:
        """Gera a assinatura SHA256: SHA256(AppId + Timestamp + Payload + Secret)."""
        raw = f"{self.app_id}{timestamp}{payload}{self.secret}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _make_request(self, payload: dict) -> dict:
        """Envia requisição POST autenticada para a API GraphQL."""
        payload_str = json.dumps(payload, separators=(",", ":"))
        timestamp = int(time.time())
        signature = self._generate_signature(payload_str, timestamp)

        headers = {
            "Authorization": (
                f"SHA256 Credential={self.app_id}, "
                f"Timestamp={timestamp}, "
                f"Signature={signature}"
            ),
        }

        logger.info("Shopee API request: %s", payload.get("query", "")[:80])
        response = self.session.post(
            self.base_url, headers=headers, data=payload_str, timeout=30
        )
        response.raise_for_status()
        data = response.json()

        if "errors" in data:
            logger.warning("Shopee API errors: %s", data["errors"])

        return data

    # ──────────────────────────────────────────────
    # Endpoint 1.1 - Ofertas Shopee (shopeeOfferV2)
    # ──────────────────────────────────────────────

    def buscar_ofertas_shopee(
        self,
        keyword: str = "",
        sort_type: int = 1,
        page: int = 1,
        limit: int = 10,
    ) -> dict:
        """
        Busca ofertas/campanhas promocionais da Shopee.

        Args:
            keyword: Termo de busca pelo nome da oferta.
            sort_type: 1=Mais recentes, 2=Maior comissão.
            page: Número da página.
            limit: Itens por página (1-500).
        """
        params = []
        if keyword:
            params.append(f'keyword: "{_escape_graphql_string(keyword)}"')
        params.append(f"sortType: {sort_type}")
        params.append(f"page: {page}")
        params.append(f"limit: {limit}")

        query = """{{
  shopeeOfferV2({params}) {{
    nodes {{
      commissionRate
      imageUrl
      offerLink
      originalLink
      offerName
      offerType
      categoryId
      collectionId
      periodStartTime
      periodEndTime
    }}
    pageInfo {{
      page
      limit
      hasNextPage
    }}
  }}
}}""".format(params=", ".join(params))

        payload = {"query": query}
        return self._make_request(payload)

    # ──────────────────────────────────────────────
    # Endpoint 1.2 - Ofertas por Loja (shopOfferV2)
    # ──────────────────────────────────────────────

    def buscar_ofertas_loja(
        self,
        keyword: str = "",
        shop_id: Optional[int] = None,
        shop_type: Optional[list] = None,
        is_key_seller: Optional[bool] = None,
        sort_type: int = 2,
        seller_comm_cove_ratio: Optional[str] = None,
        page: int = 1,
        limit: int = 10,
    ) -> dict:
        """
        Busca lojas com comissões diferenciadas para afiliados.

        Args:
            keyword: Busca por nome da loja.
            shop_id: ID específico de uma loja.
            shop_type: Tipos de loja [1=Mall, 2=Star, 4=Star+].
            is_key_seller: Filtrar apenas Key Sellers.
            sort_type: 1=Recentes, 2=Comissão, 3=Popular.
            seller_comm_cove_ratio: Taxa mínima (ex: "0.10" = 10%).
            page: Número da página.
            limit: Itens por página (1-500).
        """
        params = []
        if keyword:
            params.append(f'keyword: "{_escape_graphql_string(keyword)}"')
        if shop_id is not None:
            params.append(f"shopId: {int(shop_id)}")
        if shop_type is not None:
            params.append(f"shopType: {shop_type}")
        if is_key_seller is not None:
            params.append(f"isKeySeller: {'true' if is_key_seller else 'false'}")
        params.append(f"sortType: {sort_type}")
        if seller_comm_cove_ratio is not None:
            params.append(f'sellerCommCoveRatio: "{_escape_graphql_string(str(seller_comm_cove_ratio))}"')
        params.append(f"page: {page}")
        params.append(f"limit: {limit}")

        query = """{{
  shopOfferV2({params}) {{
    nodes {{
      shopId
      shopName
      commissionRate
      ratingStar
      shopType
      imageUrl
      offerLink
      remainingBudget
      sellerCommCoveRatio
      periodStartTime
      periodEndTime
    }}
    pageInfo {{
      page
      limit
      hasNextPage
    }}
  }}
}}""".format(params=", ".join(params))

        payload = {"query": query}
        return self._make_request(payload)

    # ──────────────────────────────────────────────────
    # Endpoint 1.3 - Ofertas por Produto (productOfferV2)
    # ──────────────────────────────────────────────────

    def buscar_produtos(
        self,
        keyword: str = "",
        shop_id: Optional[int] = None,
        item_id: Optional[int] = None,
        list_type: int = 1,
        sort_type: int = 5,
        is_ams_offer: Optional[bool] = None,
        is_key_seller: Optional[bool] = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict:
        """
        Busca produtos com detalhes de comissão.

        Args:
            keyword: Busca por nome do produto.
            shop_id: Filtrar por loja específica.
            item_id: Filtrar por produto específico.
            list_type: 0=Recomendados, 1=Maior comissão, 2=Top performance.
            sort_type: 1=Relevância, 2=Mais vendidos, 3=Maior preço,
                       4=Menor preço, 5=Comissão.
            is_ams_offer: Filtrar ofertas com comissão do vendedor.
            is_key_seller: Filtrar apenas Key Sellers.
            page: Número da página.
            limit: Itens por página (1-500).
        """
        params = []
        if keyword:
            params.append(f'keyword: "{_escape_graphql_string(keyword)}"')
        if shop_id is not None:
            params.append(f"shopId: {int(shop_id)}")
        if item_id is not None:
            params.append(f"itemId: {int(item_id)}")
        params.append(f"listType: {list_type}")
        params.append(f"sortType: {sort_type}")
        if is_ams_offer is not None:
            params.append(f"isAMSOffer: {'true' if is_ams_offer else 'false'}")
        if is_key_seller is not None:
            params.append(f"isKeySeller: {'true' if is_key_seller else 'false'}")
        params.append(f"page: {page}")
        params.append(f"limit: {limit}")

        query = """{{
  productOfferV2({params}) {{
    nodes {{
      itemId
      productName
      productLink
      offerLink
      imageUrl
      priceMin
      priceMax
      priceDiscountRate
      sales
      ratingStar
      commissionRate
      sellerCommissionRate
      shopeeCommissionRate
      commission
      shopId
      shopName
      shopType
      periodStartTime
      periodEndTime
    }}
    pageInfo {{
      page
      limit
      hasNextPage
    }}
  }}
}}""".format(params=", ".join(params))

        payload = {"query": query}
        return self._make_request(payload)

    # ──────────────────────────────────────────────
    # Endpoint 2 - Gerar Short Link (generateShortLink)
    # ──────────────────────────────────────────────

    def gerar_short_link(
        self,
        origin_url: str,
        sub_ids: Optional[list] = None,
    ) -> dict:
        """
        Gera um link curto de afiliado com rastreamento.

        Args:
            origin_url: URL original da Shopee.
            sub_ids: Até 5 identificadores de rastreamento (aparecem como utmContent).
        """
        input_parts = [f'originUrl: "{_escape_graphql_string(origin_url)}"']
        if sub_ids:
            formatted = ", ".join(
                f'"{_escape_graphql_string(s)}"' for s in sub_ids
            )
            input_parts.append(f"subIds: [{formatted}]")

        query = """mutation {{
  generateShortLink(input: {{ {input_params} }}) {{
    shortLink
  }}
}}""".format(input_params=", ".join(input_parts))

        payload = {"query": query}
        return self._make_request(payload)

    # ──────────────────────────────────────────────────────
    # Endpoint 3.1 - Relatório de Conversão (conversionReport)
    # ──────────────────────────────────────────────────────

    def relatorio_conversao(
        self,
        purchase_time_start: int,
        purchase_time_end: int,
        order_status: Optional[str] = None,
        buyer_type: Optional[str] = None,
        device: Optional[str] = None,
        scroll_id: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        """
        Obtém relatório de conversões/pedidos gerados por links de afiliado.

        Args:
            purchase_time_start: Início do período (Unix timestamp em segundos).
            purchase_time_end: Fim do período (Unix timestamp em segundos).
            order_status: UNPAID, PENDING, COMPLETED ou CANCELLED.
            buyer_type: NEW ou EXISTING.
            device: APP ou WEB.
            scroll_id: Cursor de paginação (válido por 30 segundos).
            limit: Itens por página.
        """
        params = [
            f"purchaseTimeStart: {int(purchase_time_start)}",
            f"purchaseTimeEnd: {int(purchase_time_end)}",
        ]
        if order_status:
            params.append(f'orderStatus: "{_escape_graphql_string(order_status)}"')
        if buyer_type:
            params.append(f'buyerType: "{_escape_graphql_string(buyer_type)}"')
        if device:
            params.append(f'device: "{_escape_graphql_string(device)}"')
        if scroll_id:
            params.append(f'scrollId: "{_escape_graphql_string(scroll_id)}"')
        params.append(f"limit: {limit}")

        query = """{{
  conversionReport({params}) {{
    nodes {{
      purchaseTime
      clickTime
      conversionId
      totalCommission
      sellerCommission
      shopeeCommissionCapped
      buyerType
      device
      utmContent
      orders {{
        orderId
        orderStatus
        items {{
          itemId
          itemName
          shopName
          itemPrice
          qty
          itemTotalCommission
          attributionType
        }}
      }}
    }}
    pageInfo {{
      limit
      hasNextPage
      scrollId
    }}
  }}
}}""".format(params=", ".join(params))

        payload = {"query": query}
        return self._make_request(payload)

    # ──────────────────────────────────────────────────────
    # Endpoint 3.2 - Relatório Validado (validatedReport)
    # ──────────────────────────────────────────────────────

    def relatorio_validado(
        self,
        validation_id: int,
        scroll_id: Optional[str] = None,
        limit: int = 100,
    ) -> dict:
        """
        Obtém relatório com valores de comissão finalizados/confirmados.

        Args:
            validation_id: Identificador do período de validação.
            scroll_id: Cursor de paginação (válido por 30 segundos).
            limit: Itens por página.
        """
        params = [f"validationId: {int(validation_id)}"]
        if scroll_id:
            params.append(f'scrollId: "{_escape_graphql_string(scroll_id)}"')
        params.append(f"limit: {limit}")

        query = """{{
  validatedReport({params}) {{
    nodes {{
      conversionId
      netCommission
      totalCommission
      orders {{
        orderId
        items {{
          itemName
          itemTotalCommission
          refundAmount
        }}
      }}
    }}
    pageInfo {{
      hasNextPage
      scrollId
    }}
  }}
}}""".format(params=", ".join(params))

        payload = {"query": query}
        return self._make_request(payload)
