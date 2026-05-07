# 🚀 Deploy AffiliateHub — Oracle Cloud Free Tier

Guia completo para deploy na VM ARM gratuita da Oracle (4 vCPUs, 24GB RAM, 200GB disco).

---

## 📋 Pré-Requisitos

- Conta Oracle Cloud (cartão de crédito para verificação, sem cobrança)
- Um domínio apontando para o IP da VM (pode usar Cloudflare DNS gratuito)
- Acesso SSH (terminal ou PuTTY no Windows)

---

## 1. Criar a VM no Oracle Cloud

1. Acesse [cloud.oracle.com](https://cloud.oracle.com) e faça login
2. Vá em **Compute → Instances → Create Instance**
3. Configure:

| Campo | Valor |
|-------|-------|
| **Name** | affiliatehub |
| **Image** | Ubuntu 22.04 (ou 24.04) Minimal |
| **Shape** | VM.Standard.A1.Flex |
| **OCPUs** | 4 |
| **Memory** | 24 GB |
| **Boot Volume** | 200 GB (máx. gratuito) |
| **SSH Key** | Cole sua chave pública |

4. Clique em **Create** e aguarde o status ficar **Running**
5. Anote o **IP Público** da instância

> ⚠️ **Dica**: Se a shape A1 não estiver disponível, tente em horários diferentes ou via Terraform (script de retry). A demanda por instâncias gratuitas é alta.

---

## 2. Configurar Firewall (Security List)

No painel Oracle:

1. Vá em **Networking → Virtual Cloud Networks**
2. Clique na VCN da instância → **Security Lists** → **Default Security List**
3. Adicione **Ingress Rules**:

| Porta | Protocolo | Source CIDR | Descrição |
|-------|-----------|-------------|-----------|
| 80 | TCP | 0.0.0.0/0 | HTTP |
| 443 | TCP | 0.0.0.0/0 | HTTPS |

> As portas 5000, 5432 e 8080 **NÃO** precisam ser abertas — ficam internas no Docker.

---

## 3. Conectar e Preparar a VM

```bash
ssh ubuntu@SEU_IP_PUBLICO
```

### 3.1 Atualizar sistema

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git
```

### 3.2 Abrir portas no firewall da VM (iptables)

O Ubuntu da Oracle bloqueia portas por padrão via iptables:

```bash
sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

### 3.3 Instalar Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Faça logout e login novamente para aplicar o grupo docker:

```bash
exit
ssh ubuntu@SEU_IP_PUBLICO
```

### 3.4 Instalar Docker Compose

```bash
sudo apt install -y docker-compose-plugin
```

Verificar instalação:

```bash
docker --version
docker compose version
```

---

## 4. Clonar o Projeto

```bash
cd ~
git clone SEU_REPOSITORIO_GIT affiliatehub
cd affiliatehub
```

> Se não usa Git, pode transferir via `scp`:
> ```bash
> scp -r ./shopee-bot ubuntu@SEU_IP_PUBLICO:~/affiliatehub
> ```

---

## 5. Configurar Variáveis de Ambiente

```bash
cp deploy/.env.production .env
nano .env
```

Preencha os valores:

```env
FLASK_SECRET_KEY=<gerar com: python3 -c "import secrets; print(secrets.token_hex(32))">
POSTGRES_PASSWORD=<senha forte para o banco>
EVOLUTION_API_KEY=<chave para a Evolution API>
DOMAIN=seudominio.com.br
```

---

## 6. Configurar Domínio no Nginx

Substitua `SEU_DOMINIO_AQUI` no arquivo Nginx:

```bash
sed -i "s/SEU_DOMINIO_AQUI/seudominio.com.br/g" deploy/nginx.conf
```

---

## 7. Apontar DNS

No seu provedor de DNS (Cloudflare, etc.):

| Tipo | Nome | Valor |
|------|------|-------|
| A | @ | SEU_IP_PUBLICO |
| A | www | SEU_IP_PUBLICO |

> No Cloudflare, desative o proxy (nuvem cinza) inicialmente para o Certbot funcionar.

---

## 8. Obter Certificado SSL (Primeiro Deploy)

Antes do primeiro `docker compose up`, precisamos do certificado SSL. Temporariamente, altere o Nginx para aceitar HTTP sem redirecionar:

```bash
# Subir apenas o Nginx em modo HTTP temporário
docker compose -f docker-compose.prod.yml up -d nginx

# Gerar certificado
docker compose -f docker-compose.prod.yml run --rm certbot \
  certonly --webroot -w /var/www/certbot \
  -d seudominio.com.br \
  --email seuemail@email.com \
  --agree-tos --no-eff-email

# Parar o Nginx temporário
docker compose -f docker-compose.prod.yml down
```

> Se o Certbot falhar por causa do redirect HTTPS, comente temporariamente o bloco `server 443` e o `return 301` no nginx.conf, suba o Nginx, gere o certificado, e depois restaure o arquivo.

---

## 9. Deploy Completo

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Verificar se tudo está rodando:

```bash
docker compose -f docker-compose.prod.yml ps
```

Deve mostrar todos os containers com status **Up**:

```
affiliatehub-app        Up
affiliatehub-db         Up (healthy)
affiliatehub-evolution  Up
affiliatehub-nginx      Up
affiliatehub-certbot    Up
```

Testar no navegador: `https://seudominio.com.br`

---

## 10. Comandos Úteis

### Ver logs

```bash
# Todos os serviços
docker compose -f docker-compose.prod.yml logs -f

# Apenas o app
docker compose -f docker-compose.prod.yml logs -f app

# Apenas erros
docker compose -f docker-compose.prod.yml logs -f app 2>&1 | grep -i error
```

### Reiniciar serviços

```bash
# Reiniciar tudo
docker compose -f docker-compose.prod.yml restart

# Reiniciar apenas o app (após atualizar código)
docker compose -f docker-compose.prod.yml up -d --build app
```

### Atualizar o código

```bash
cd ~/affiliatehub
git pull
docker compose -f docker-compose.prod.yml up -d --build app
```

### Acessar o banco de dados

```bash
docker exec -it affiliatehub-db psql -U affiliatehub
```

### Backup do banco

```bash
docker exec affiliatehub-db pg_dump -U affiliatehub affiliatehub > backup_$(date +%Y%m%d).sql
```

### Restaurar backup

```bash
cat backup_20250101.sql | docker exec -i affiliatehub-db psql -U affiliatehub
```

---

## 11. Backup Automático (Cron)

Criar script de backup:

```bash
mkdir -p ~/backups

cat > ~/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR=~/backups
DATE=$(date +%Y%m%d_%H%M)
docker exec affiliatehub-db pg_dump -U affiliatehub affiliatehub > $BACKUP_DIR/db_$DATE.sql
# Manter apenas últimos 7 dias
find $BACKUP_DIR -name "*.sql" -mtime +7 -delete
EOF

chmod +x ~/backup.sh
```

Agendar via cron (diário às 3h):

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * ~/backup.sh") | crontab -
```

---

## 12. Monitoramento

### Verificar uso de recursos

```bash
# CPU e memória dos containers
docker stats --no-stream

# Disco
df -h
```

### Health check simples (cron a cada 5 min)

```bash
cat > ~/healthcheck.sh << 'EOF'
#!/bin/bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" https://seudominio.com.br)
if [ "$STATUS" != "200" ]; then
  docker compose -f ~/affiliatehub/docker-compose.prod.yml restart
  echo "$(date) - Reiniciado (status: $STATUS)" >> ~/healthcheck.log
fi
EOF

chmod +x ~/healthcheck.sh
(crontab -l 2>/dev/null; echo "*/5 * * * * ~/healthcheck.sh") | crontab -
```

---

## 13. Renovação SSL Automática

O container `certbot` no docker-compose já renova automaticamente a cada 12h. Para testar manualmente:

```bash
docker compose -f docker-compose.prod.yml run --rm certbot renew --dry-run
```

---

## 📊 Capacidade Estimada (Free Tier ARM)

| Recurso | Capacidade |
|---------|------------|
| **Usuários simultâneos** | ~100-200 |
| **Instâncias WhatsApp** | ~50-100 |
| **Banco PostgreSQL** | ~10GB+ de dados |
| **Uptime** | 99.9% (Always Free, sem expiração) |

---

## ⚠️ Pontos de Atenção

1. **Nunca exponha** portas 5432 (PostgreSQL) ou 8080 (Evolution) diretamente
2. **Faça backup** antes de qualquer atualização
3. **Monitore o disco** — Evolution API pode acumular dados de mídia
4. A instância Free Tier é **permanente** — não expira após trial
5. Se precisar de mais desempenho, considere upgrade para instância paga
