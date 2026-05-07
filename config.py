"""Configuracao da aplicacao. Apenas variaveis de nivel de app (sem credenciais de API)."""

import os
from dotenv import load_dotenv

load_dotenv()

# Flask
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# Evolution API (instancia compartilhada - cada usuario conecta seu proprio WhatsApp)
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

# Gemini (IA) - chave centralizada no servidor
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Instagram OAuth (Instagram API with Instagram Login)
IG_APP_ID = os.getenv("IG_APP_ID", "")
IG_APP_SECRET = os.getenv("IG_APP_SECRET", "")

# Mercado Pago (pagamentos PIX)
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "")
