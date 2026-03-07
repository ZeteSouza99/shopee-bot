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
