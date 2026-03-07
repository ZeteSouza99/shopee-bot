"""
Modulo de armazenamento usando SQLite.

Multi-tenant: todos os dados sao isolados por user_id.
Gerencia usuarios, credenciais, lojas favoritas e historico.
"""

import json
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_FILE = os.path.join(DATA_DIR, "shopee.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Obtem conexao SQLite thread-local (uma por thread)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _local.conn = sqlite3.connect(DB_FILE, timeout=10)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _run_migrations(_local.conn)
    return _local.conn


# ─── Migrations ────────────────────────────────────────────


def _run_migrations(conn: sqlite3.Connection):
    """Executa migrations incrementais baseadas em versao."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current = row[0] if row else 0

    if current < 1:
        _migration_v1(conn)
    if current < 2:
        _migration_v2(conn)


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
    except sqlite3.IntegrityError:
        return None


def get_user_by_email(email: str):
    """Retorna dict do usuario ou None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, email, password_hash, created_at, last_login FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "password_hash": row[2], "created_at": row[3], "last_login": row[4]}


def get_user_by_id(user_id: int):
    """Retorna dict do usuario ou None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, email, password_hash, created_at, last_login FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "password_hash": row[2], "created_at": row[3], "last_login": row[4]}


def update_last_login(user_id: int):
    conn = _get_conn()
    conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (int(time.time()), user_id))
    conn.commit()


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


def get_historico(user_id: int, item_id=None):
    """Retorna o historico do usuario. Filtra por item_id se fornecido."""
    conn = _get_conn()
    if item_id:
        rows = conn.execute(
            "SELECT timestamp, item_id, product_name, price_min, price_max, "
            "commission_rate, commission, sales, shop_name "
            "FROM historico WHERE user_id = ? AND item_id = ? ORDER BY timestamp DESC",
            (user_id, item_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT timestamp, item_id, product_name, price_min, price_max, "
            "commission_rate, commission, sales, shop_name "
            "FROM historico WHERE user_id = ? ORDER BY timestamp DESC",
            (user_id,),
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
