"""
Modulo de armazenamento com suporte a SQLite (local) e PostgreSQL (nuvem).

Multi-tenant: todos os dados sao isolados por user_id.
Gerencia usuarios, credenciais, lojas favoritas e historico.

Selecao automatica:
- Se DATABASE_URL estiver definida → PostgreSQL (com connection pool)
- Caso contrario → SQLite local em data/shopee.db
"""

import json
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

# ─── Deteccao de backend ────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_POSTGRES = DATABASE_URL.startswith("postgres")

_pool = None  # Connection pool para PostgreSQL

if USE_POSTGRES:
    import psycopg2
    import psycopg2.pool
    import psycopg2.extras

    _pg_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=2,
        maxconn=20,
        dsn=_pg_url,
    )
    logger.info("Usando PostgreSQL com connection pool (2-20 conexoes)")
else:
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    DB_FILE = os.path.join(DATA_DIR, "shopee.db")
    logger.info("Usando SQLite como backend de dados")

_local = threading.local()
_pg_migrations_done = False


def _get_conn():
    """Obtem conexao thread-local (uma por thread).
    Para PostgreSQL usa o pool de conexoes.
    Para SQLite usa conexao thread-local."""
    if USE_POSTGRES:
        if not hasattr(_local, "conn") or _local.conn is None or _local.conn._raw.closed:
            raw = _pool.getconn()
            raw.autocommit = False
            _local.conn = _PgConnWrapper(raw)
            global _pg_migrations_done
            if not _pg_migrations_done:
                _run_migrations_pg(raw)
                _pg_migrations_done = True
        return _local.conn
    else:
        if not hasattr(_local, "conn") or _local.conn is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            _local.conn = sqlite3.connect(DB_FILE, timeout=10)
            _local.conn.execute("PRAGMA journal_mode=WAL")
            _local.conn.execute("PRAGMA busy_timeout=5000")
            _local.conn.execute("PRAGMA foreign_keys=ON")
            _run_migrations(_local.conn)
        return _local.conn


def _release_conn():
    """Devolve conexao PostgreSQL ao pool. Chamar apos cada request."""
    if USE_POSTGRES and hasattr(_local, "conn") and _local.conn is not None:
        try:
            _pool.putconn(_local.conn._raw)
        except Exception:
            pass
        _local.conn = None


class _PgConnWrapper:
    """Wrapper que adapta a interface do psycopg2 para ser compativel com sqlite3.
    Converte placeholders ? para %s automaticamente."""

    def __init__(self, conn):
        self._raw = conn

    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        cur = self._raw.cursor()
        cur.execute(sql, params or ())
        return _PgCursorWrapper(cur)

    def executescript(self, sql):
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        cur = self._raw.cursor()
        cur.execute(sql)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()


class _PgCursorWrapper:
    """Wrapper que adapta cursor psycopg2 para interface sqlite3."""

    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def lastrowid(self):
        try:
            self._cur.execute("SELECT lastval()")
            return self._cur.fetchone()[0]
        except Exception:
            return None


# ─── Migrations PostgreSQL ─────────────────────────────────


def _run_migrations_pg(conn):
    """Cria schema completo no PostgreSQL (idempotente)."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER);

        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            last_login INTEGER,
            slug TEXT UNIQUE,
            display_name TEXT,
            is_admin INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS user_credentials (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            service TEXT NOT NULL,
            credentials_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(user_id, service)
        );

        CREATE TABLE IF NOT EXISTS lojas_favoritas (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            shop_id INTEGER NOT NULL,
            data TEXT NOT NULL,
            added_at INTEGER,
            last_updated INTEGER,
            UNIQUE(user_id, shop_id)
        );

        CREATE TABLE IF NOT EXISTS historico (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            timestamp INTEGER NOT NULL,
            item_id INTEGER,
            product_name TEXT,
            price_min TEXT,
            price_max TEXT,
            commission_rate TEXT,
            commission TEXT,
            sales TEXT,
            shop_name TEXT
        );

        CREATE TABLE IF NOT EXISTS public_promotions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            image_url TEXT,
            price_min TEXT,
            price_max TEXT,
            commission_rate TEXT,
            short_link TEXT NOT NULL,
            original_link TEXT,
            category TEXT,
            product_json TEXT,
            is_active INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_templates (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            canal TEXT NOT NULL,
            template_text TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS plans (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            price_cents INTEGER NOT NULL,
            channels TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            plan_id INTEGER REFERENCES plans(id),
            status TEXT NOT NULL DEFAULT 'trial',
            trial_ends_at INTEGER,
            current_period_start INTEGER,
            current_period_end INTEGER,
            cancelled_at INTEGER,
            gateway TEXT,
            gateway_subscription_id TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subscription_id INTEGER REFERENCES subscriptions(id),
            plan_id INTEGER REFERENCES plans(id),
            amount_cents INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'BRL',
            status TEXT NOT NULL DEFAULT 'pending',
            gateway TEXT NOT NULL,
            gateway_payment_id TEXT,
            payment_method TEXT,
            pix_qr_code TEXT,
            pix_qr_code_base64 TEXT,
            paid_at INTEGER,
            created_at INTEGER NOT NULL
        );

        -- Indices para performance
        CREATE INDEX IF NOT EXISTS idx_credentials_user ON user_credentials(user_id);
        CREATE INDEX IF NOT EXISTS idx_favoritas_user ON lojas_favoritas(user_id);
        CREATE INDEX IF NOT EXISTS idx_historico_user ON historico(user_id);
        CREATE INDEX IF NOT EXISTS idx_historico_item ON historico(item_id);
        CREATE INDEX IF NOT EXISTS idx_historico_ts ON historico(timestamp);
        CREATE INDEX IF NOT EXISTS idx_promotions_user ON public_promotions(user_id);
        CREATE INDEX IF NOT EXISTS idx_promotions_active ON public_promotions(user_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_promotions_slug ON public_promotions(user_id, is_active, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_templates_user ON user_templates(user_id);
        CREATE INDEX IF NOT EXISTS idx_templates_canal ON user_templates(user_id, canal);
        CREATE INDEX IF NOT EXISTS idx_users_slug ON users(slug);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
        CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        CREATE INDEX IF NOT EXISTS idx_payments_gateway_id ON payments(gateway_payment_id);
    """)
    conn.commit()
    _seed_plans_pg(conn)
    logger.info("Schema PostgreSQL verificado/criado com sucesso")


# ─── Migrations SQLite ─────────────────────────────────────


def _run_migrations(conn: sqlite3.Connection):
    """Executa migrations incrementais baseadas em versao."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current = row[0] if row else 0

    if current < 1:
        _migration_v1(conn)
    if current < 2:
        _migration_v2(conn)
    if current < 3:
        _migration_v3(conn)
    if current < 4:
        _migration_v4(conn)
    if current < 5:
        _migration_v5(conn)


def _migration_v1(conn: sqlite3.Connection):
    """Schema original (lojas_favoritas + historico)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS lojas_favoritas (
            shop_id INTEGER PRIMARY KEY,
            data TEXT NOT NULL,
            added_at INTEGER,
            last_updated INTEGER
        );

        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            item_id INTEGER,
            product_name TEXT,
            price_min TEXT,
            price_max TEXT,
            commission_rate TEXT,
            commission TEXT,
            sales TEXT,
            shop_name TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_historico_item_id ON historico(item_id);
        CREATE INDEX IF NOT EXISTS idx_historico_timestamp ON historico(timestamp);

        INSERT OR REPLACE INTO schema_version (version) VALUES (1);
    """)
    conn.commit()
    _migrate_from_json(conn)


def _migration_v2(conn: sqlite3.Connection):
    """Multi-tenant: usuarios, credenciais, user_id em todas as tabelas."""
    logger.info("Executando migration v2: multi-tenant")

    conn.executescript("""
        -- Usuarios
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            last_login INTEGER
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);

        -- Credenciais por usuario
        CREATE TABLE IF NOT EXISTS user_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service TEXT NOT NULL,
            credentials_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, service)
        );
        CREATE INDEX IF NOT EXISTS idx_user_credentials_user ON user_credentials(user_id);
    """)

    # Recriar lojas_favoritas com (user_id, shop_id) composite unique
    has_user_id = False
    cols = conn.execute("PRAGMA table_info(lojas_favoritas)").fetchall()
    for col in cols:
        if col[1] == "user_id":
            has_user_id = True

    if not has_user_id:
        conn.executescript("""
            ALTER TABLE lojas_favoritas RENAME TO lojas_favoritas_old;
            CREATE TABLE lojas_favoritas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                shop_id INTEGER NOT NULL,
                data TEXT NOT NULL,
                added_at INTEGER,
                last_updated INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, shop_id)
            );
            CREATE INDEX IF NOT EXISTS idx_favoritas_user ON lojas_favoritas(user_id);
        """)
        # Migrar dados existentes para user_id=1
        old_rows = conn.execute("SELECT shop_id, data, added_at, last_updated FROM lojas_favoritas_old").fetchall()
        if old_rows:
            for r in old_rows:
                conn.execute(
                    "INSERT OR IGNORE INTO lojas_favoritas (user_id, shop_id, data, added_at, last_updated) "
                    "VALUES (1, ?, ?, ?, ?)",
                    (r[0], r[1], r[2], r[3]),
                )
            logger.info("Migradas %d lojas favoritas para user_id=1", len(old_rows))
        conn.execute("DROP TABLE lojas_favoritas_old")

    # Adicionar user_id ao historico
    hist_cols = conn.execute("PRAGMA table_info(historico)").fetchall()
    hist_has_user_id = any(col[1] == "user_id" for col in hist_cols)
    if not hist_has_user_id:
        conn.execute("ALTER TABLE historico ADD COLUMN user_id INTEGER REFERENCES users(id)")
        conn.execute("UPDATE historico SET user_id = 1 WHERE user_id IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_historico_user ON historico(user_id)")
        logger.info("Adicionado user_id ao historico")

    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (2)")
    conn.commit()


def _migration_v3(conn: sqlite3.Connection):
    """Storefront publico e templates de anuncio por usuario."""
    logger.info("Executando migration v3: storefront + templates")

    # Adicionar colunas slug e display_name em users
    user_cols = {col[1] for col in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "slug" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN slug TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_slug ON users(slug)")
        logger.info("Adicionado slug e display_name em users")
    if "display_name" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS public_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            image_url TEXT,
            price_min TEXT,
            price_max TEXT,
            commission_rate TEXT,
            short_link TEXT NOT NULL,
            original_link TEXT,
            category TEXT,
            product_json TEXT,
            is_active INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_promotions_user ON public_promotions(user_id);

        CREATE TABLE IF NOT EXISTS user_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            canal TEXT NOT NULL,
            template_text TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_templates_user ON user_templates(user_id);
    """)

    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (3)")
    conn.commit()


def _migration_v4(conn: sqlite3.Connection):
    """Admin e controle de acesso."""
    logger.info("Executando migration v4: admin + bloqueio")

    user_cols = {col[1] for col in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "is_admin" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        logger.info("Adicionado is_admin em users")
    if "is_blocked" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
        logger.info("Adicionado is_blocked em users")

    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (4)")
    conn.commit()


def _migration_v5(conn: sqlite3.Connection):
    """Planos, assinaturas e pagamentos."""
    logger.info("Executando migration v5: planos + assinaturas + pagamentos")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            price_cents INTEGER NOT NULL,
            channels TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_id INTEGER REFERENCES plans(id),
            status TEXT NOT NULL DEFAULT 'trial',
            trial_ends_at INTEGER,
            current_period_start INTEGER,
            current_period_end INTEGER,
            cancelled_at INTEGER,
            gateway TEXT,
            gateway_subscription_id TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subscription_id INTEGER REFERENCES subscriptions(id),
            plan_id INTEGER REFERENCES plans(id),
            amount_cents INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'BRL',
            status TEXT NOT NULL DEFAULT 'pending',
            gateway TEXT NOT NULL,
            gateway_payment_id TEXT,
            payment_method TEXT,
            pix_qr_code TEXT,
            pix_qr_code_base64 TEXT,
            paid_at INTEGER,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        CREATE INDEX IF NOT EXISTS idx_payments_gateway_id ON payments(gateway_payment_id);
    """)

    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (5)")
    conn.commit()

    _seed_plans_sqlite(conn)


def _seed_plans_sqlite(conn: sqlite3.Connection):
    """Insere planos padrao no SQLite se nao existirem."""
    existing = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    if existing > 0:
        return
    now = int(time.time())
    plans = [
        ("Telegram", "telegram", 2990, "telegram"),
        ("WhatsApp", "whatsapp", 3990, "whatsapp"),
        ("Instagram", "instagram", 3990, "instagram"),
        ("Telegram + WhatsApp", "telegram-whatsapp", 5490, "telegram,whatsapp"),
        ("Telegram + Instagram", "telegram-instagram", 5490, "telegram,instagram"),
        ("WhatsApp + Instagram", "whatsapp-instagram", 6490, "whatsapp,instagram"),
        ("Completo", "completo", 9790, "telegram,whatsapp,instagram"),
    ]
    for name, slug, price, channels in plans:
        conn.execute(
            "INSERT INTO plans (name, slug, price_cents, channels, is_active, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (name, slug, price, channels, now),
        )
    conn.commit()
    logger.info("Inseridos %d planos padrao", len(plans))


def _seed_plans_pg(conn):
    """Insere planos padrao no PostgreSQL se nao existirem."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM plans")
    if cur.fetchone()[0] > 0:
        return
    now = int(time.time())
    plans = [
        ("Telegram", "telegram", 2990, "telegram"),
        ("WhatsApp", "whatsapp", 3990, "whatsapp"),
        ("Instagram", "instagram", 3990, "instagram"),
        ("Telegram + WhatsApp", "telegram-whatsapp", 5490, "telegram,whatsapp"),
        ("Telegram + Instagram", "telegram-instagram", 5490, "telegram,instagram"),
        ("WhatsApp + Instagram", "whatsapp-instagram", 6490, "whatsapp,instagram"),
        ("Completo", "completo", 9790, "telegram,whatsapp,instagram"),
    ]
    for name, slug, price, channels in plans:
        cur.execute(
            "INSERT INTO plans (name, slug, price_cents, channels, is_active, created_at) "
            "VALUES (%s, %s, %s, %s, 1, %s)",
            (name, slug, price, channels, now),
        )
    conn.commit()
    logger.info("Inseridos %d planos padrao (PostgreSQL)", len(plans))


def _migrate_from_json(conn: sqlite3.Connection):
    """Migra dados de arquivos JSON legados para SQLite (executa uma vez)."""
    old_fav = os.path.join(DATA_DIR, "lojas_favoritas.json")
    old_hist = os.path.join(DATA_DIR, "historico.json")

    if os.path.exists(old_fav):
        try:
            with open(old_fav, "r", encoding="utf-8") as f:
                favs = json.load(f)
            if isinstance(favs, list) and favs:
                for fav in favs:
                    shop_id = fav.get("shopId")
                    if shop_id:
                        conn.execute(
                            "INSERT OR IGNORE INTO lojas_favoritas (shop_id, data, added_at) VALUES (?, ?, ?)",
                            (shop_id, json.dumps(fav, ensure_ascii=False), fav.get("addedAt", int(time.time()))),
                        )
                conn.commit()
                logger.info("Migradas %d lojas favoritas de JSON para SQLite", len(favs))
            os.rename(old_fav, old_fav + ".bak")
        except Exception as e:
            logger.warning("Falha ao migrar favoritas de JSON: %s", e)

    if os.path.exists(old_hist):
        try:
            with open(old_hist, "r", encoding="utf-8") as f:
                hist = json.load(f)
            if isinstance(hist, list) and hist:
                for h in hist:
                    conn.execute(
                        "INSERT INTO historico (timestamp, item_id, product_name, price_min, price_max, "
                        "commission_rate, commission, sales, shop_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            h.get("timestamp", int(time.time())),
                            h.get("itemId"), h.get("productName"), h.get("priceMin"),
                            h.get("priceMax"), h.get("commissionRate"), h.get("commission"),
                            h.get("sales"), h.get("shopName"),
                        ),
                    )
                conn.commit()
                logger.info("Migrados %d registros de historico de JSON para SQLite", len(hist))
            os.rename(old_hist, old_hist + ".bak")
        except Exception as e:
            logger.warning("Falha ao migrar historico de JSON: %s", e)


# ─── Usuarios ──────────────────────────────────────────────


def create_user(email: str, password_hash: str):
    """Cria um usuario. Retorna user_id ou None se email ja existe."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (email, password_hash, int(time.time())),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower() or "UNIQUE" in str(e) or isinstance(e, sqlite3.IntegrityError):
            if USE_POSTGRES:
                conn.rollback()
            return None
        raise


def get_user_by_email(email: str):
    """Retorna dict do usuario ou None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, email, password_hash, created_at, last_login, is_admin, is_blocked FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "password_hash": row[2], "created_at": row[3],
            "last_login": row[4], "is_admin": bool(row[5]) if row[5] else False,
            "is_blocked": bool(row[6]) if row[6] else False}


def get_user_by_id(user_id: int):
    """Retorna dict do usuario ou None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, email, password_hash, created_at, last_login, is_admin, is_blocked FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "password_hash": row[2], "created_at": row[3],
            "last_login": row[4], "is_admin": bool(row[5]) if row[5] else False,
            "is_blocked": bool(row[6]) if row[6] else False}


def update_last_login(user_id: int):
    conn = _get_conn()
    conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (int(time.time()), user_id))
    conn.commit()


def get_user_by_email_or_id(user_id: int):
    """Retorna dict basico do usuario (sem password_hash) para uso em checkout."""
    return get_user_by_id(user_id)


# ─── Credenciais do Usuario ────────────────────────────────


def get_user_credentials(user_id: int, service: str = None) -> dict:
    """Retorna credenciais. Se service dado, retorna dict desse servico.
    Se None, retorna {service: {key: value}, ...} para todos."""
    conn = _get_conn()
    if service:
        row = conn.execute(
            "SELECT credentials_json FROM user_credentials WHERE user_id = ? AND service = ?",
            (user_id, service),
        ).fetchone()
        return json.loads(row[0]) if row else {}
    else:
        rows = conn.execute(
            "SELECT service, credentials_json FROM user_credentials WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}


def save_user_credentials(user_id: int, service: str, credentials: dict):
    """Upsert credenciais para um servico."""
    conn = _get_conn()
    creds_json = json.dumps(credentials, ensure_ascii=False)
    now = int(time.time())
    conn.execute(
        "INSERT INTO user_credentials (user_id, service, credentials_json, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, service) DO UPDATE SET credentials_json = excluded.credentials_json, updated_at = excluded.updated_at",
        (user_id, service, creds_json, now),
    )
    conn.commit()


def delete_user_credentials(user_id: int, service: str):
    """Remove credenciais de um servico."""
    conn = _get_conn()
    conn.execute(
        "DELETE FROM user_credentials WHERE user_id = ? AND service = ?",
        (user_id, service),
    )
    conn.commit()


# ─── Lojas Favoritas ────────────────────────────────────────


def get_favoritas(user_id: int):
    """Retorna a lista de lojas favoritas do usuario."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT data FROM lojas_favoritas WHERE user_id = ? ORDER BY added_at DESC",
        (user_id,),
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def add_favorita(user_id: int, shop_data: dict):
    """Adiciona uma loja aos favoritos do usuario."""
    conn = _get_conn()
    shop_id = shop_data.get("shopId")
    if not shop_id:
        return False

    existing = conn.execute(
        "SELECT 1 FROM lojas_favoritas WHERE user_id = ? AND shop_id = ?",
        (user_id, shop_id),
    ).fetchone()
    if existing:
        return False

    shop_data["addedAt"] = int(time.time())
    conn.execute(
        "INSERT INTO lojas_favoritas (user_id, shop_id, data, added_at) VALUES (?, ?, ?, ?)",
        (user_id, shop_id, json.dumps(shop_data, ensure_ascii=False), shop_data["addedAt"]),
    )
    conn.commit()
    logger.info("Loja %s adicionada aos favoritos do usuario %d", shop_id, user_id)
    return True


def remove_favorita(user_id: int, shop_id: int):
    """Remove uma loja dos favoritos do usuario."""
    conn = _get_conn()
    conn.execute(
        "DELETE FROM lojas_favoritas WHERE user_id = ? AND shop_id = ?",
        (user_id, shop_id),
    )
    conn.commit()
    return True


def update_favorita(user_id: int, shop_id: int, new_data: dict):
    """Atualiza dados de uma loja favorita."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT data FROM lojas_favoritas WHERE user_id = ? AND shop_id = ?",
        (user_id, shop_id),
    ).fetchone()
    if row:
        current = json.loads(row[0])
        current.update(new_data)
        current["lastUpdated"] = int(time.time())
        conn.execute(
            "UPDATE lojas_favoritas SET data = ?, last_updated = ? WHERE user_id = ? AND shop_id = ?",
            (json.dumps(current, ensure_ascii=False), current["lastUpdated"], user_id, shop_id),
        )
        conn.commit()


# ─── Historico ──────────────────────────────────────────────


def get_historico(user_id: int, item_id=None, limit: int = 200, offset: int = 0):
    """Retorna o historico do usuario com paginacao. Filtra por item_id se fornecido."""
    conn = _get_conn()
    if item_id:
        rows = conn.execute(
            "SELECT timestamp, item_id, product_name, price_min, price_max, "
            "commission_rate, commission, sales, shop_name "
            "FROM historico WHERE user_id = ? AND item_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (user_id, item_id, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT timestamp, item_id, product_name, price_min, price_max, "
            "commission_rate, commission, sales, shop_name "
            "FROM historico WHERE user_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()

    return [
        {
            "timestamp": r[0], "itemId": r[1], "productName": r[2],
            "priceMin": r[3], "priceMax": r[4], "commissionRate": r[5],
            "commission": r[6], "sales": r[7], "shopName": r[8],
        }
        for r in rows
    ]


def save_historico(user_id: int, produtos: list):
    """Salva um snapshot de produtos no historico do usuario."""
    conn = _get_conn()
    timestamp = int(time.time())
    for p in produtos:
        conn.execute(
            "INSERT INTO historico (user_id, timestamp, item_id, product_name, price_min, "
            "price_max, commission_rate, commission, sales, shop_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, timestamp, p.get("itemId"), p.get("productName"),
                p.get("priceMin"), p.get("priceMax"), p.get("commissionRate"),
                p.get("commission"), p.get("sales"), p.get("shopName"),
            ),
        )

    # Limita a 10000 registros por usuario
    count = conn.execute("SELECT COUNT(*) FROM historico WHERE user_id = ?", (user_id,)).fetchone()[0]
    if count > 10000:
        conn.execute(
            "DELETE FROM historico WHERE user_id = ? AND id IN ("
            "SELECT id FROM historico WHERE user_id = ? ORDER BY timestamp ASC LIMIT ?)",
            (user_id, user_id, count - 10000),
        )

    conn.commit()
    logger.info("Salvos %d produtos no historico do usuario %d", len(produtos), user_id)
    return len(produtos)


def clear_historico(user_id: int):
    """Limpa todo o historico do usuario."""
    conn = _get_conn()
    conn.execute("DELETE FROM historico WHERE user_id = ?", (user_id,))
    conn.commit()
    logger.info("Historico limpo para usuario %d", user_id)


# ─── Storefront Publico ─────────────────────────────────────


def get_storefront_settings(user_id: int) -> dict:
    """Retorna slug e display_name do usuario."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT slug, display_name FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row:
        return {"slug": None, "display_name": None}
    return {"slug": row[0], "display_name": row[1]}


def update_storefront_settings(user_id: int, slug: str, display_name: str):
    """Atualiza slug e display_name do usuario. Retorna True ou lanca IntegrityError se slug duplicado."""
    conn = _get_conn()
    conn.execute(
        "UPDATE users SET slug = ?, display_name = ? WHERE id = ?",
        (slug, display_name, user_id),
    )
    conn.commit()


def get_user_by_slug(slug: str):
    """Retorna usuario pelo slug ou None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, email, display_name, slug FROM users WHERE slug = ?", (slug,)
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "display_name": row[2], "slug": row[3]}


def add_promotion(user_id: int, data: dict) -> int:
    """Adiciona produto ao site publico. Retorna o id inserido."""
    conn = _get_conn()
    now = int(time.time())
    cursor = conn.execute(
        "INSERT INTO public_promotions "
        "(user_id, title, image_url, price_min, price_max, commission_rate, "
        "short_link, original_link, category, product_json, is_active, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (
            user_id,
            data.get("title", ""),
            data.get("imageUrl"),
            data.get("priceMin"),
            data.get("priceMax"),
            data.get("commissionRate"),
            data.get("shortLink", ""),
            data.get("originalLink"),
            data.get("category"),
            json.dumps(data.get("product", {}), ensure_ascii=False),
            now,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def remove_promotion(user_id: int, promotion_id: int):
    """Remove uma promocao do site publico."""
    conn = _get_conn()
    conn.execute(
        "DELETE FROM public_promotions WHERE id = ? AND user_id = ?",
        (promotion_id, user_id),
    )
    conn.commit()


def toggle_promotion(user_id: int, promotion_id: int) -> bool:
    """Alterna is_active de uma promocao. Retorna o novo estado."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT is_active FROM public_promotions WHERE id = ? AND user_id = ?",
        (promotion_id, user_id),
    ).fetchone()
    if not row:
        return False
    new_state = 0 if row[0] else 1
    conn.execute(
        "UPDATE public_promotions SET is_active = ? WHERE id = ? AND user_id = ?",
        (new_state, promotion_id, user_id),
    )
    conn.commit()
    return bool(new_state)


def get_user_promotions(user_id: int, limit: int = 100, offset: int = 0) -> list:
    """Retorna promocoes do usuario com paginacao (ativas e inativas)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, title, image_url, price_min, price_max, commission_rate, "
        "short_link, original_link, category, is_active, created_at "
        "FROM public_promotions WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ).fetchall()
    return [
        {
            "id": r[0], "title": r[1], "imageUrl": r[2], "priceMin": r[3],
            "priceMax": r[4], "commissionRate": r[5], "shortLink": r[6],
            "originalLink": r[7], "category": r[8], "isActive": bool(r[9]),
            "createdAt": r[10],
        }
        for r in rows
    ]


def get_public_promotions(slug: str, limit: int = 50, offset: int = 0) -> list:
    """Retorna promocoes ativas de um usuario pelo slug (acesso publico)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT pp.id, pp.title, pp.image_url, pp.price_min, pp.price_max, "
        "pp.commission_rate, pp.short_link, pp.category, pp.created_at "
        "FROM public_promotions pp "
        "JOIN users u ON u.id = pp.user_id "
        "WHERE u.slug = ? AND pp.is_active = 1 "
        "ORDER BY pp.created_at DESC LIMIT ? OFFSET ?",
        (slug, limit, offset),
    ).fetchall()
    return [
        {
            "id": r[0], "title": r[1], "imageUrl": r[2], "priceMin": r[3],
            "priceMax": r[4], "commissionRate": r[5], "shortLink": r[6],
            "category": r[7], "createdAt": r[8],
        }
        for r in rows
    ]


# ─── Templates de Anuncio ───────────────────────────────────


def get_templates(user_id: int, canal: str = None) -> list:
    """Retorna templates do usuario, opcionalmente filtrados por canal."""
    conn = _get_conn()
    if canal:
        rows = conn.execute(
            "SELECT id, name, canal, template_text, is_default, created_at "
            "FROM user_templates WHERE user_id = ? AND canal = ? ORDER BY is_default DESC, name ASC",
            (user_id, canal),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, canal, template_text, is_default, created_at "
            "FROM user_templates WHERE user_id = ? ORDER BY canal, is_default DESC, name ASC",
            (user_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "name": r[1], "canal": r[2],
            "templateText": r[3], "isDefault": bool(r[4]), "createdAt": r[5],
        }
        for r in rows
    ]


def save_template(user_id: int, name: str, canal: str, template_text: str) -> int:
    """Cria um novo template. Retorna o id."""
    conn = _get_conn()
    now = int(time.time())
    cursor = conn.execute(
        "INSERT INTO user_templates (user_id, name, canal, template_text, is_default, created_at) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (user_id, name, canal, template_text, now),
    )
    conn.commit()
    return cursor.lastrowid


def update_template(user_id: int, template_id: int, name: str, template_text: str):
    """Atualiza nome e texto de um template."""
    conn = _get_conn()
    conn.execute(
        "UPDATE user_templates SET name = ?, template_text = ? WHERE id = ? AND user_id = ?",
        (name, template_text, template_id, user_id),
    )
    conn.commit()


def delete_template(user_id: int, template_id: int):
    """Remove um template."""
    conn = _get_conn()
    conn.execute(
        "DELETE FROM user_templates WHERE id = ? AND user_id = ?",
        (template_id, user_id),
    )
    conn.commit()


def set_default_template(user_id: int, template_id: int, canal: str):
    """Define um template como padrao para o canal, removendo padrao anterior."""
    conn = _get_conn()
    conn.execute(
        "UPDATE user_templates SET is_default = 0 WHERE user_id = ? AND canal = ?",
        (user_id, canal),
    )
    conn.execute(
        "UPDATE user_templates SET is_default = 1 WHERE id = ? AND user_id = ?",
        (template_id, user_id),
    )
    conn.commit()


def get_template_by_id(user_id: int, template_id: int) -> dict:
    """Retorna um template pelo id ou None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, name, canal, template_text, is_default, created_at "
        "FROM user_templates WHERE id = ? AND user_id = ?",
        (template_id, user_id),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "name": row[1], "canal": row[2],
        "templateText": row[3], "isDefault": bool(row[4]), "createdAt": row[5],
    }


# ─── Admin ─────────────────────────────────────────────────


# ─── Planos ──────────────────────────────────────────────


def get_plans(active_only: bool = True) -> list:
    """Retorna lista de planos disponiveis."""
    conn = _get_conn()
    if active_only:
        rows = conn.execute(
            "SELECT id, name, slug, price_cents, channels, is_active, created_at "
            "FROM plans WHERE is_active = 1 ORDER BY price_cents ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, slug, price_cents, channels, is_active, created_at "
            "FROM plans ORDER BY price_cents ASC"
        ).fetchall()
    return [
        {"id": r[0], "name": r[1], "slug": r[2], "price_cents": r[3],
         "channels": r[4].split(","), "is_active": bool(r[5]), "created_at": r[6]}
        for r in rows
    ]


def get_plan_by_id(plan_id: int) -> dict:
    """Retorna plano por ID."""
    conn = _get_conn()
    r = conn.execute(
        "SELECT id, name, slug, price_cents, channels, is_active, created_at "
        "FROM plans WHERE id = ?", (plan_id,)
    ).fetchone()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "slug": r[2], "price_cents": r[3],
            "channels": r[4].split(","), "is_active": bool(r[5]), "created_at": r[6]}


def get_plan_by_slug(slug: str) -> dict:
    """Retorna plano pelo slug."""
    conn = _get_conn()
    r = conn.execute(
        "SELECT id, name, slug, price_cents, channels, is_active, created_at "
        "FROM plans WHERE slug = ?", (slug,)
    ).fetchone()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "slug": r[2], "price_cents": r[3],
            "channels": r[4].split(","), "is_active": bool(r[5]), "created_at": r[6]}


# ─── Assinaturas ─────────────────────────────────────────


def get_user_subscription(user_id: int) -> dict:
    """Retorna assinatura ativa/trial do usuario. None se nao existir."""
    conn = _get_conn()
    r = conn.execute(
        "SELECT s.id, s.user_id, s.plan_id, s.status, s.trial_ends_at, "
        "s.current_period_start, s.current_period_end, s.cancelled_at, "
        "s.gateway, s.gateway_subscription_id, s.created_at, s.updated_at, "
        "p.name as plan_name, p.slug as plan_slug, p.price_cents, p.channels "
        "FROM subscriptions s "
        "LEFT JOIN plans p ON p.id = s.plan_id "
        "WHERE s.user_id = ? AND s.status IN ('trial', 'active', 'past_due') "
        "ORDER BY s.created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not r:
        return None
    return {
        "id": r[0], "user_id": r[1], "plan_id": r[2], "status": r[3],
        "trial_ends_at": r[4], "current_period_start": r[5],
        "current_period_end": r[6], "cancelled_at": r[7],
        "gateway": r[8], "gateway_subscription_id": r[9],
        "created_at": r[10], "updated_at": r[11],
        "plan_name": r[12], "plan_slug": r[13],
        "plan_price_cents": r[14],
        "plan_channels": r[15].split(",") if r[15] else [],
    }


def create_trial_subscription(user_id: int) -> int:
    """Cria assinatura trial de 7 dias (todos os canais). Retorna subscription_id."""
    conn = _get_conn()
    now = int(time.time())
    trial_end = now + 7 * 86400  # 7 dias
    cursor = conn.execute(
        "INSERT INTO subscriptions (user_id, plan_id, status, trial_ends_at, "
        "current_period_start, current_period_end, created_at, updated_at) "
        "VALUES (?, NULL, 'trial', ?, ?, ?, ?, ?)",
        (user_id, trial_end, now, trial_end, now, now),
    )
    conn.commit()
    logger.info("Trial criado para usuario %d (expira em 7 dias)", user_id)
    return cursor.lastrowid


def activate_subscription(subscription_id: int, plan_id: int, gateway: str = None, gateway_sub_id: str = None):
    """Ativa assinatura apos pagamento. Define periodo de 30 dias."""
    conn = _get_conn()
    now = int(time.time())
    period_end = now + 30 * 86400  # 30 dias
    conn.execute(
        "UPDATE subscriptions SET status = 'active', plan_id = ?, "
        "current_period_start = ?, current_period_end = ?, "
        "gateway = ?, gateway_subscription_id = ?, updated_at = ? "
        "WHERE id = ?",
        (plan_id, now, period_end, gateway, gateway_sub_id, now, subscription_id),
    )
    conn.commit()
    logger.info("Assinatura %d ativada (plano %d)", subscription_id, plan_id)


def renew_subscription(subscription_id: int):
    """Renova assinatura por mais 30 dias."""
    conn = _get_conn()
    now = int(time.time())
    period_end = now + 30 * 86400
    conn.execute(
        "UPDATE subscriptions SET current_period_start = ?, current_period_end = ?, "
        "updated_at = ? WHERE id = ?",
        (now, period_end, now, subscription_id),
    )
    conn.commit()


def cancel_subscription(subscription_id: int):
    """Cancela assinatura."""
    conn = _get_conn()
    now = int(time.time())
    conn.execute(
        "UPDATE subscriptions SET status = 'cancelled', cancelled_at = ?, updated_at = ? WHERE id = ?",
        (now, now, subscription_id),
    )
    conn.commit()


def expire_subscription(subscription_id: int):
    """Marca assinatura como expirada."""
    conn = _get_conn()
    now = int(time.time())
    conn.execute(
        "UPDATE subscriptions SET status = 'expired', updated_at = ? WHERE id = ?",
        (now, subscription_id),
    )
    conn.commit()


def check_subscription_access(user_id: int, channel: str) -> dict:
    """Verifica se usuario pode usar um canal. Retorna {allowed, reason, subscription}."""
    sub = get_user_subscription(user_id)
    if not sub:
        return {"allowed": False, "reason": "Sem assinatura ativa. Assine um plano para publicar.", "subscription": None}

    now = int(time.time())

    # Trial: todos os canais ate expirar
    if sub["status"] == "trial":
        if sub["trial_ends_at"] and now > sub["trial_ends_at"]:
            expire_subscription(sub["id"])
            return {"allowed": False, "reason": "Seu trial expirou. Assine um plano para continuar.", "subscription": None}
        return {"allowed": True, "reason": "trial", "subscription": sub}

    # Active/past_due: verificar se periodo nao expirou e canal esta no plano
    if sub["current_period_end"] and now > sub["current_period_end"]:
        expire_subscription(sub["id"])
        return {"allowed": False, "reason": "Sua assinatura expirou. Renove seu plano.", "subscription": None}

    if channel and channel not in sub.get("plan_channels", []):
        plan_name = sub.get("plan_name", "seu plano")
        return {"allowed": False, "reason": f"O canal {channel} nao esta incluso no plano {plan_name}.", "subscription": sub}

    return {"allowed": True, "reason": "active", "subscription": sub}


# ─── Pagamentos ──────────────────────────────────────────


def create_payment(user_id: int, subscription_id: int, plan_id: int,
                   amount_cents: int, gateway: str, gateway_payment_id: str = None,
                   payment_method: str = None, pix_qr_code: str = None,
                   pix_qr_code_base64: str = None) -> int:
    """Registra um pagamento. Retorna payment_id."""
    conn = _get_conn()
    now = int(time.time())
    cursor = conn.execute(
        "INSERT INTO payments (user_id, subscription_id, plan_id, amount_cents, "
        "currency, status, gateway, gateway_payment_id, payment_method, "
        "pix_qr_code, pix_qr_code_base64, created_at) "
        "VALUES (?, ?, ?, ?, 'BRL', 'pending', ?, ?, ?, ?, ?, ?)",
        (user_id, subscription_id, plan_id, amount_cents,
         gateway, gateway_payment_id, payment_method,
         pix_qr_code, pix_qr_code_base64, now),
    )
    conn.commit()
    return cursor.lastrowid


def update_payment_status(payment_id: int, status: str, paid_at: int = None):
    """Atualiza status de um pagamento pelo ID interno."""
    conn = _get_conn()
    if paid_at:
        conn.execute(
            "UPDATE payments SET status = ?, paid_at = ? WHERE id = ?",
            (status, paid_at, payment_id),
        )
    else:
        conn.execute(
            "UPDATE payments SET status = ? WHERE id = ?",
            (status, payment_id),
        )
    conn.commit()


def get_payment_by_gateway_id(gateway_payment_id: str) -> dict:
    """Retorna pagamento pelo ID do gateway."""
    conn = _get_conn()
    r = conn.execute(
        "SELECT id, user_id, subscription_id, plan_id, amount_cents, status, "
        "gateway, gateway_payment_id, payment_method, paid_at, created_at "
        "FROM payments WHERE gateway_payment_id = ?",
        (str(gateway_payment_id),),
    ).fetchone()
    if not r:
        return None
    return {
        "id": r[0], "user_id": r[1], "subscription_id": r[2], "plan_id": r[3],
        "amount_cents": r[4], "status": r[5], "gateway": r[6],
        "gateway_payment_id": r[7], "payment_method": r[8],
        "paid_at": r[9], "created_at": r[10],
    }


def get_user_payments(user_id: int, limit: int = 50) -> list:
    """Retorna historico de pagamentos do usuario."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT p.id, p.amount_cents, p.status, p.gateway, p.payment_method, "
        "p.paid_at, p.created_at, pl.name as plan_name "
        "FROM payments p LEFT JOIN plans pl ON pl.id = p.plan_id "
        "WHERE p.user_id = ? ORDER BY p.created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [
        {"id": r[0], "amount_cents": r[1], "status": r[2], "gateway": r[3],
         "payment_method": r[4], "paid_at": r[5], "created_at": r[6],
         "plan_name": r[7]}
        for r in rows
    ]


# ─── Admin - Financeiro ──────────────────────────────────


def admin_get_subscription_stats() -> dict:
    """Retorna metricas financeiras para o admin dashboard."""
    conn = _get_conn()
    now = int(time.time())

    # Assinaturas por status
    trial_count = conn.execute("SELECT COUNT(*) FROM subscriptions WHERE status = 'trial'").fetchone()[0]
    active_count = conn.execute("SELECT COUNT(*) FROM subscriptions WHERE status = 'active'").fetchone()[0]
    expired_count = conn.execute("SELECT COUNT(*) FROM subscriptions WHERE status = 'expired'").fetchone()[0]
    cancelled_count = conn.execute("SELECT COUNT(*) FROM subscriptions WHERE status = 'cancelled'").fetchone()[0]

    # Trials expirando em 3 dias
    three_days = now + 3 * 86400
    trials_expiring = conn.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE status = 'trial' AND trial_ends_at <= ?",
        (three_days,),
    ).fetchone()[0]

    # MRR (receita recorrente mensal) = soma dos planos ativos
    mrr_row = conn.execute(
        "SELECT COALESCE(SUM(p.price_cents), 0) FROM subscriptions s "
        "JOIN plans p ON p.id = s.plan_id WHERE s.status = 'active'"
    ).fetchone()
    mrr_cents = mrr_row[0] if mrr_row else 0

    # Receita total confirmada
    total_revenue = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM payments WHERE status = 'approved'"
    ).fetchone()[0]

    # Pagamentos pendentes
    pending_payments = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE status = 'pending'"
    ).fetchone()[0]

    # Pagamentos dos ultimos 30 dias
    month_ago = now - 30 * 86400
    revenue_month = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM payments WHERE status = 'approved' AND paid_at >= ?",
        (month_ago,),
    ).fetchone()[0]

    # Churn: cancelados no ultimo mes / (ativos + cancelados no ultimo mes)
    cancelled_month = conn.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE status = 'cancelled' AND cancelled_at >= ?",
        (month_ago,),
    ).fetchone()[0]
    churn_base = active_count + cancelled_month
    churn_rate = round(cancelled_month / churn_base * 100, 1) if churn_base > 0 else 0

    # Planos mais populares
    plan_dist = conn.execute(
        "SELECT p.name, COUNT(*) as total FROM subscriptions s "
        "JOIN plans p ON p.id = s.plan_id WHERE s.status = 'active' "
        "GROUP BY p.name ORDER BY total DESC"
    ).fetchall()
    plan_distribution = [{"name": r[0], "count": r[1]} for r in plan_dist]

    # Conversao trial -> pago
    total_trials_ever = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
    converted = conn.execute(
        "SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE status = 'active'"
    ).fetchone()[0]
    conversion_rate = round(converted / total_trials_ever * 100, 1) if total_trials_ever > 0 else 0

    return {
        "subscriptions": {
            "trial": trial_count, "active": active_count,
            "expired": expired_count, "cancelled": cancelled_count,
            "trials_expiring_soon": trials_expiring,
        },
        "financial": {
            "mrr_cents": mrr_cents,
            "total_revenue_cents": total_revenue,
            "revenue_month_cents": revenue_month,
            "pending_payments": pending_payments,
            "churn_rate": churn_rate,
            "conversion_rate": conversion_rate,
        },
        "plan_distribution": plan_distribution,
    }


# ─── Admin ─────────────────────────────────────────────────


def admin_get_stats() -> dict:
    """Retorna estatisticas completas para o dashboard admin."""
    conn = _get_conn()
    now = int(time.time())
    hour_ago = now - 3600
    day_ago = now - 86400
    week_ago = now - 604800
    month_ago = now - 2592000

    # Usuarios
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    blocked = conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1").fetchone()[0]
    admins = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
    new_today = conn.execute("SELECT COUNT(*) FROM users WHERE created_at >= ?", (day_ago,)).fetchone()[0]
    new_week = conn.execute("SELECT COUNT(*) FROM users WHERE created_at >= ?", (week_ago,)).fetchone()[0]
    new_month = conn.execute("SELECT COUNT(*) FROM users WHERE created_at >= ?", (month_ago,)).fetchone()[0]
    active_now = conn.execute("SELECT COUNT(*) FROM users WHERE last_login >= ?", (hour_ago,)).fetchone()[0]
    active_day = conn.execute("SELECT COUNT(*) FROM users WHERE last_login >= ?", (day_ago,)).fetchone()[0]
    active_week = conn.execute("SELECT COUNT(*) FROM users WHERE last_login >= ?", (week_ago,)).fetchone()[0]
    never_logged = conn.execute("SELECT COUNT(*) FROM users WHERE last_login IS NULL").fetchone()[0]

    # Canais configurados
    tg_count = conn.execute("SELECT COUNT(*) FROM user_credentials WHERE service = 'telegram'").fetchone()[0]
    wa_count = conn.execute("SELECT COUNT(*) FROM user_credentials WHERE service = 'whatsapp'").fetchone()[0]
    ig_count = conn.execute("SELECT COUNT(*) FROM user_credentials WHERE service = 'instagram'").fetchone()[0]
    shopee_count = conn.execute("SELECT COUNT(*) FROM user_credentials WHERE service = 'shopee'").fetchone()[0]
    total_creds = conn.execute("SELECT COUNT(*) FROM user_credentials").fetchone()[0]

    # Dados de uso
    total_favoritas = conn.execute("SELECT COUNT(*) FROM lojas_favoritas").fetchone()[0]
    total_historico = conn.execute("SELECT COUNT(*) FROM historico").fetchone()[0]
    total_promotions = conn.execute("SELECT COUNT(*) FROM public_promotions").fetchone()[0]
    active_promotions = conn.execute("SELECT COUNT(*) FROM public_promotions WHERE is_active = 1").fetchone()[0]
    total_templates = conn.execute("SELECT COUNT(*) FROM user_templates").fetchone()[0]

    # Cadastros por dia (ultimos 7 dias)
    signups_daily = []
    for i in range(6, -1, -1):
        day_start = now - (i + 1) * 86400
        day_end = now - i * 86400
        count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ? AND created_at < ?",
            (day_start, day_end),
        ).fetchone()[0]
        signups_daily.append({"day": i, "count": count})

    # Ultimos 5 usuarios cadastrados
    recent_users = conn.execute(
        "SELECT id, email, created_at, last_login FROM users ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    recent = [{"id": r[0], "email": r[1], "created_at": r[2], "last_login": r[3]} for r in recent_users]

    # Usuarios mais ativos (por quantidade de historico)
    top_users_rows = conn.execute(
        "SELECT u.id, u.email, COUNT(h.id) as total "
        "FROM users u LEFT JOIN historico h ON h.user_id = u.id "
        "GROUP BY u.id, u.email ORDER BY total DESC LIMIT 5"
    ).fetchall()
    top_users = [{"id": r[0], "email": r[1], "total_searches": r[2]} for r in top_users_rows]

    return {
        "users": {
            "total": total, "blocked": blocked, "admins": admins,
            "new_today": new_today, "new_week": new_week, "new_month": new_month,
            "active_now": active_now, "active_today": active_day, "active_week": active_week,
            "never_logged": never_logged,
        },
        "channels": {
            "telegram": tg_count, "whatsapp": wa_count, "instagram": ig_count,
            "shopee": shopee_count, "total_configs": total_creds,
        },
        "usage": {
            "total_favoritas": total_favoritas, "total_historico": total_historico,
            "total_promotions": total_promotions, "active_promotions": active_promotions,
            "total_templates": total_templates,
        },
        "signups_daily": signups_daily,
        "recent_users": recent,
        "top_users": top_users,
    }


def admin_list_users(search: str = "", limit: int = 50, offset: int = 0) -> list:
    """Lista todos os usuarios para o painel admin."""
    conn = _get_conn()
    if search:
        pattern = f"%{search}%"
        rows = conn.execute(
            "SELECT id, email, created_at, last_login, is_admin, is_blocked, slug, display_name "
            "FROM users WHERE email LIKE ? OR display_name LIKE ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (pattern, pattern, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, email, created_at, last_login, is_admin, is_blocked, slug, display_name "
            "FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

    return [
        {
            "id": r[0], "email": r[1], "created_at": r[2], "last_login": r[3],
            "is_admin": bool(r[4]) if r[4] else False,
            "is_blocked": bool(r[5]) if r[5] else False,
            "slug": r[6], "display_name": r[7],
        }
        for r in rows
    ]


def admin_count_users(search: str = "") -> int:
    """Conta total de usuarios (para paginacao)."""
    conn = _get_conn()
    if search:
        pattern = f"%{search}%"
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE email LIKE ? OR display_name LIKE ?",
            (pattern, pattern),
        ).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def admin_get_user_detail(user_id: int) -> dict:
    """Retorna detalhes completos de um usuario para o admin."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, email, created_at, last_login, is_admin, is_blocked, slug, display_name "
        "FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None

    user = {
        "id": row[0], "email": row[1], "created_at": row[2], "last_login": row[3],
        "is_admin": bool(row[4]) if row[4] else False,
        "is_blocked": bool(row[5]) if row[5] else False,
        "slug": row[6], "display_name": row[7],
    }

    # Canais configurados
    creds = conn.execute(
        "SELECT service, updated_at FROM user_credentials WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    user["channels"] = [{"service": c[0], "updated_at": c[1]} for c in creds]

    # Contadores
    user["total_favoritas"] = conn.execute(
        "SELECT COUNT(*) FROM lojas_favoritas WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    user["total_historico"] = conn.execute(
        "SELECT COUNT(*) FROM historico WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    user["total_promotions"] = conn.execute(
        "SELECT COUNT(*) FROM public_promotions WHERE user_id = ?", (user_id,)
    ).fetchone()[0]

    return user


def admin_toggle_block(user_id: int) -> bool:
    """Bloqueia/desbloqueia usuario. Retorna novo estado."""
    conn = _get_conn()
    row = conn.execute("SELECT is_blocked FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return False
    new_state = 0 if row[0] else 1
    conn.execute("UPDATE users SET is_blocked = ? WHERE id = ?", (new_state, user_id))
    conn.commit()
    return bool(new_state)


def admin_set_admin(user_id: int, is_admin: bool):
    """Define se usuario e admin."""
    conn = _get_conn()
    conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, user_id))
    conn.commit()
