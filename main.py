"""
Interface CLI interativa para a API Shopee Afiliados.

Menu com acesso a todos os endpoints, entrada de dados simplificada
e resultados formatados em JSON legível.
"""

import json
import sys
import time
from datetime import datetime, timedelta

import requests

from shopee_client import ShopeeAffiliateClient
from config import APP_ID, SECRET


# ─── Cores ANSI para terminal ───────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
WHITE = "\033[97m"


def print_header():
    """Exibe o cabeçalho do programa."""
    print(f"""
{CYAN}{BOLD}{'=' * 60}
   SHOPEE AFILIADOS - Cliente API Python
{'=' * 60}{RESET}
{DIM}   API GraphQL | Todos os endpoints | CLI Interativa{RESET}
""")


def print_menu():
    """Exibe o menu principal."""
    print(f"""
{BOLD}{WHITE}{'─' * 50}
  MENU PRINCIPAL
{'─' * 50}{RESET}

  {GREEN}[1]{RESET} Buscar Ofertas Shopee      {DIM}(shopeeOfferV2){RESET}
  {GREEN}[2]{RESET} Buscar Ofertas por Loja    {DIM}(shopOfferV2){RESET}
  {GREEN}[3]{RESET} Buscar Produtos            {DIM}(productOfferV2){RESET}
  {GREEN}[4]{RESET} Gerar Short Link           {DIM}(generateShortLink){RESET}
  {GREEN}[5]{RESET} Relatório de Conversão     {DIM}(conversionReport){RESET}
  {GREEN}[6]{RESET} Relatório Validado         {DIM}(validatedReport){RESET}
  {YELLOW}[7]{RESET} Teste Rápido               {DIM}(keyword: "espelho"){RESET}

  {RED}[0]{RESET} Sair
""")


def input_prompt(texto: str, padrao: str = "") -> str:
    """Input com valor padrão opcional."""
    if padrao:
        valor = input(f"  {CYAN}{texto}{RESET} [{DIM}{padrao}{RESET}]: ").strip()
        return valor if valor else padrao
    return input(f"  {CYAN}{texto}{RESET}: ").strip()


def input_int(texto: str, padrao: int) -> int:
    """Input de inteiro com valor padrão."""
    valor = input_prompt(texto, str(padrao))
    try:
        return int(valor)
    except ValueError:
        print(f"  {YELLOW}Valor inválido. Usando padrão: {padrao}{RESET}")
        return padrao


def input_sim_nao(texto: str) -> bool:
    """Input sim/não."""
    valor = input_prompt(f"{texto} (s/n)", "n")
    return valor.lower() in ("s", "sim", "y", "yes")


def print_resultado(data: dict):
    """Exibe o resultado formatado em JSON."""
    print(f"\n{GREEN}{BOLD}{'─' * 50}")
    print(f"  RESULTADO")
    print(f"{'─' * 50}{RESET}\n")

    if "errors" in data:
        print(f"{RED}{BOLD}  ERROS encontrados:{RESET}")
        for err in data["errors"]:
            msg = err.get("message", "Erro desconhecido")
            ext = err.get("extensions", {})
            code = ext.get("code", "?")
            detail = ext.get("message", "")
            print(f"  {RED}Código {code}: {msg}{RESET}")
            if detail:
                print(f"  {DIM}{detail}{RESET}")
        print()

    if "data" in data:
        print(json.dumps(data["data"], indent=2, ensure_ascii=False))
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))

    print()


def print_resumo_produtos(data: dict):
    """Exibe um resumo tabular dos produtos encontrados."""
    try:
        nodes = data["data"]["productOfferV2"]["nodes"]
        page_info = data["data"]["productOfferV2"]["pageInfo"]
    except (KeyError, TypeError):
        return

    if not nodes:
        print(f"  {YELLOW}Nenhum produto encontrado.{RESET}")
        return

    print(f"\n{MAGENTA}{BOLD}{'─' * 90}")
    print(f"  RESUMO DOS PRODUTOS ({len(nodes)} resultados)")
    print(f"{'─' * 90}{RESET}\n")

    for i, p in enumerate(nodes, 1):
        nome = (p.get("productName") or "")[:50]
        comissao = p.get("commissionRate", "0")
        try:
            comissao_pct = f"{float(comissao) * 100:.1f}%"
        except (ValueError, TypeError):
            comissao_pct = comissao
        preco_min = p.get("priceMin", "?")
        preco_max = p.get("priceMax", "?")
        vendas = p.get("sales", "?")
        loja = (p.get("shopName") or "")[:20]
        comissao_rs = p.get("commission", "?")

        print(f"  {GREEN}{i:>2}.{RESET} {BOLD}{nome}{RESET}")
        print(f"      Comissão: {CYAN}{comissao_pct}{RESET} | "
              f"Est. R$: {CYAN}{comissao_rs}{RESET} | "
              f"Preço: R${preco_min} - R${preco_max} | "
              f"Vendas: {vendas} | "
              f"Loja: {DIM}{loja}{RESET}")
        print(f"      Link: {DIM}{p.get('offerLink', 'N/A')}{RESET}")
        print()

    has_next = page_info.get("hasNextPage", False)
    if has_next:
        print(f"  {YELLOW}Há mais páginas disponíveis.{RESET}\n")


def print_resumo_lojas(data: dict):
    """Exibe um resumo tabular das lojas encontradas."""
    try:
        nodes = data["data"]["shopOfferV2"]["nodes"]
    except (KeyError, TypeError):
        return

    if not nodes:
        print(f"  {YELLOW}Nenhuma loja encontrada.{RESET}")
        return

    tipos = {1: "Mall", 2: "Star", 4: "Star+"}

    print(f"\n{MAGENTA}{BOLD}{'─' * 80}")
    print(f"  RESUMO DAS LOJAS ({len(nodes)} resultados)")
    print(f"{'─' * 80}{RESET}\n")

    for i, s in enumerate(nodes, 1):
        nome = (s.get("shopName") or "")[:35]
        comissao = s.get("commissionRate", "0")
        try:
            comissao_pct = f"{float(comissao) * 100:.1f}%"
        except (ValueError, TypeError):
            comissao_pct = comissao
        tipo = tipos.get(s.get("shopType"), "?")
        rating = s.get("ratingStar", "?")
        budget = s.get("remainingBudget", "?")

        print(f"  {GREEN}{i:>2}.{RESET} {BOLD}{nome}{RESET}")
        print(f"      Comissão: {CYAN}{comissao_pct}{RESET} | "
              f"Tipo: {tipo} | "
              f"Nota: {rating} | "
              f"Budget: {budget}")
        print(f"      Link: {DIM}{s.get('offerLink', 'N/A')}{RESET}")
        print()


def print_resumo_ofertas(data: dict):
    """Exibe um resumo tabular das ofertas Shopee."""
    try:
        nodes = data["data"]["shopeeOfferV2"]["nodes"]
    except (KeyError, TypeError):
        return

    if not nodes:
        print(f"  {YELLOW}Nenhuma oferta encontrada.{RESET}")
        return

    print(f"\n{MAGENTA}{BOLD}{'─' * 80}")
    print(f"  RESUMO DAS OFERTAS ({len(nodes)} resultados)")
    print(f"{'─' * 80}{RESET}\n")

    for i, o in enumerate(nodes, 1):
        nome = (o.get("offerName") or "")[:50]
        comissao = o.get("commissionRate", "0")
        try:
            comissao_pct = f"{float(comissao) * 100:.1f}%"
        except (ValueError, TypeError):
            comissao_pct = comissao
        tipo = "Coleção" if o.get("offerType") == 1 else "Categoria"
        inicio = o.get("periodStartTime")
        fim = o.get("periodEndTime")

        datas = ""
        if inicio and fim:
            dt_inicio = datetime.fromtimestamp(inicio).strftime("%d/%m/%Y")
            dt_fim = datetime.fromtimestamp(fim).strftime("%d/%m/%Y")
            datas = f" | {dt_inicio} a {dt_fim}"

        print(f"  {GREEN}{i:>2}.{RESET} {BOLD}{nome}{RESET}")
        print(f"      Comissão: {CYAN}{comissao_pct}{RESET} | Tipo: {tipo}{datas}")
        print(f"      Link: {DIM}{o.get('offerLink', 'N/A')}{RESET}")
        print()


# ─── Funções de cada opção do menu ──────────────────────────


def opcao_ofertas_shopee(client: ShopeeAffiliateClient):
    """Opção 1: Buscar ofertas Shopee (shopeeOfferV2)."""
    print(f"\n{BOLD}  BUSCAR OFERTAS SHOPEE (shopeeOfferV2){RESET}\n")

    keyword = input_prompt("Palavra-chave (ou Enter para todas)", "")
    sort_type = input_int("Ordenar por (1=Recentes, 2=Maior comissão)", 1)
    page = input_int("Página", 1)
    limit = input_int("Resultados por página", 10)

    print(f"\n  {DIM}Buscando ofertas...{RESET}")
    data = client.buscar_ofertas_shopee(keyword, sort_type, page, limit)
    print_resumo_ofertas(data)
    if input_sim_nao("Exibir JSON completo?"):
        print_resultado(data)


def opcao_ofertas_loja(client: ShopeeAffiliateClient):
    """Opção 2: Buscar ofertas por loja (shopOfferV2)."""
    print(f"\n{BOLD}  BUSCAR OFERTAS POR LOJA (shopOfferV2){RESET}\n")

    keyword = input_prompt("Palavra-chave (ou Enter para todas)", "")

    shop_type = None
    if input_sim_nao("Filtrar por tipo de loja?"):
        print(f"    {DIM}1=Mall, 2=Star, 4=Star+{RESET}")
        tipos = input_prompt("Tipos separados por vírgula", "1,4")
        shop_type = [int(t.strip()) for t in tipos.split(",") if t.strip()]

    is_key_seller = None
    if input_sim_nao("Filtrar apenas Key Sellers?"):
        is_key_seller = True

    sort_type = input_int("Ordenar (1=Recentes, 2=Comissão, 3=Popular)", 2)
    page = input_int("Página", 1)
    limit = input_int("Resultados por página", 10)

    print(f"\n  {DIM}Buscando lojas...{RESET}")
    data = client.buscar_ofertas_loja(
        keyword=keyword,
        shop_type=shop_type,
        is_key_seller=is_key_seller,
        sort_type=sort_type,
        page=page,
        limit=limit,
    )
    print_resumo_lojas(data)
    if input_sim_nao("Exibir JSON completo?"):
        print_resultado(data)


def opcao_produtos(client: ShopeeAffiliateClient):
    """Opção 3: Buscar produtos (productOfferV2)."""
    print(f"\n{BOLD}  BUSCAR PRODUTOS (productOfferV2){RESET}\n")

    keyword = input_prompt("Palavra-chave", "")
    list_type = input_int("Tipo de lista (0=Recomendados, 1=Maior comissão, 2=Top)", 1)
    sort_type = input_int(
        "Ordenar (1=Relevância, 2=Vendidos, 3=Maior preço, 4=Menor preço, 5=Comissão)",
        5,
    )

    is_ams_offer = None
    if input_sim_nao("Filtrar apenas ofertas com comissão do vendedor?"):
        is_ams_offer = True

    is_key_seller = None
    if input_sim_nao("Filtrar apenas Key Sellers?"):
        is_key_seller = True

    page = input_int("Página", 1)
    limit = input_int("Resultados por página", 20)

    print(f"\n  {DIM}Buscando produtos...{RESET}")
    data = client.buscar_produtos(
        keyword=keyword,
        list_type=list_type,
        sort_type=sort_type,
        is_ams_offer=is_ams_offer,
        is_key_seller=is_key_seller,
        page=page,
        limit=limit,
    )
    print_resumo_produtos(data)
    if input_sim_nao("Exibir JSON completo?"):
        print_resultado(data)


def opcao_short_link(client: ShopeeAffiliateClient):
    """Opção 4: Gerar short link (generateShortLink)."""
    print(f"\n{BOLD}  GERAR SHORT LINK (generateShortLink){RESET}\n")

    url = input_prompt("URL da Shopee")
    if not url:
        print(f"  {RED}URL é obrigatória.{RESET}")
        return

    sub_ids = None
    if input_sim_nao("Adicionar identificadores de rastreamento (subIds)?"):
        ids = input_prompt("IDs separados por vírgula (máx 5)")
        sub_ids = [s.strip() for s in ids.split(",") if s.strip()][:5]

    print(f"\n  {DIM}Gerando short link...{RESET}")
    data = client.gerar_short_link(url, sub_ids)

    try:
        short = data["data"]["generateShortLink"]["shortLink"]
        print(f"\n  {GREEN}{BOLD}Short Link gerado:{RESET} {CYAN}{short}{RESET}\n")
    except (KeyError, TypeError):
        print_resultado(data)


def opcao_relatorio_conversao(client: ShopeeAffiliateClient):
    """Opção 5: Relatório de conversão (conversionReport)."""
    print(f"\n{BOLD}  RELATÓRIO DE CONVERSÃO (conversionReport){RESET}\n")

    print(f"  {DIM}Informe o período (timestamps Unix em segundos).{RESET}")
    print(f"  {DIM}Dica: use 'hoje' para o dia atual ou informe timestamps.{RESET}\n")

    periodo = input_prompt("Período", "hoje")
    if periodo.lower() == "hoje":
        hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start = int(hoje.timestamp())
        end = int((hoje + timedelta(days=1)).timestamp())
    else:
        start = int(input_prompt("Timestamp início"))
        end = int(input_prompt("Timestamp fim"))

    order_status = None
    status_input = input_prompt(
        "Status do pedido (UNPAID/PENDING/COMPLETED/CANCELLED ou Enter para todos)", ""
    )
    if status_input:
        order_status = status_input.upper()

    limit = input_int("Resultados por página", 50)

    print(f"\n  {DIM}Buscando relatório de conversão...{RESET}")
    data = client.relatorio_conversao(
        purchase_time_start=start,
        purchase_time_end=end,
        order_status=order_status,
        limit=limit,
    )
    print_resultado(data)


def opcao_relatorio_validado(client: ShopeeAffiliateClient):
    """Opção 6: Relatório validado (validatedReport)."""
    print(f"\n{BOLD}  RELATÓRIO VALIDADO (validatedReport){RESET}\n")

    validation_id = input_int("Validation ID", 0)
    if validation_id == 0:
        print(f"  {RED}Validation ID é obrigatório.{RESET}")
        return

    limit = input_int("Resultados por página", 100)

    print(f"\n  {DIM}Buscando relatório validado...{RESET}")
    data = client.relatorio_validado(validation_id=validation_id, limit=limit)
    print_resultado(data)


def opcao_teste_rapido(client: ShopeeAffiliateClient):
    """Opção 7: Teste rápido com keyword 'espelho'."""
    print(f"\n{BOLD}  TESTE RÁPIDO{RESET}")
    print(f"  {DIM}Buscando produtos com keyword 'espelho'...{RESET}\n")

    data = client.buscar_produtos(
        keyword="espelho",
        list_type=1,
        sort_type=5,
        page=1,
        limit=5,
    )
    print_resumo_produtos(data)
    if input_sim_nao("Exibir JSON completo?"):
        print_resultado(data)


# ─── Loop principal ─────────────────────────────────────────


def main():
    """Função principal - loop do menu interativo."""
    if not APP_ID or not SECRET:
        print(f"""
{RED}{BOLD}  CREDENCIAIS NÃO CONFIGURADAS!{RESET}

  Edite o arquivo {CYAN}config.py{RESET} e preencha:
    - APP_ID  = "seu_app_id"
    - SECRET  = "sua_chave_secreta"

  Obtenha suas credenciais em:
  {DIM}https://affiliate.shopee.com.br/{RESET}
""")
        sys.exit(1)

    client = ShopeeAffiliateClient()

    print_header()
    print(f"  {GREEN}Credenciais carregadas.{RESET} App ID: {DIM}{APP_ID[:8]}...{RESET}\n")

    opcoes = {
        "1": opcao_ofertas_shopee,
        "2": opcao_ofertas_loja,
        "3": opcao_produtos,
        "4": opcao_short_link,
        "5": opcao_relatorio_conversao,
        "6": opcao_relatorio_validado,
        "7": opcao_teste_rapido,
    }

    while True:
        print_menu()
        escolha = input(f"  {BOLD}Escolha uma opção:{RESET} ").strip()

        if escolha == "0":
            print(f"\n  {CYAN}Até mais!{RESET}\n")
            break

        if escolha in opcoes:
            try:
                opcoes[escolha](client)
            except requests.exceptions.HTTPError as e:
                print(f"\n  {RED}Erro HTTP: {e}{RESET}")
                print(f"  {DIM}Verifique suas credenciais e tente novamente.{RESET}\n")
            except requests.exceptions.ConnectionError:
                print(f"\n  {RED}Erro de conexão. Verifique sua internet.{RESET}\n")
            except KeyboardInterrupt:
                print(f"\n  {YELLOW}Operação cancelada.{RESET}")
            except Exception as e:
                print(f"\n  {RED}Erro inesperado: {e}{RESET}\n")
        else:
            print(f"\n  {YELLOW}Opção inválida. Tente novamente.{RESET}")

        input(f"\n  {DIM}Pressione Enter para continuar...{RESET}")


if __name__ == "__main__":
    main()
