# database.py
import sqlite3
from config import Config

def get_conn():
    conn = sqlite3.connect(Config.DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    
    # ---- Users ----
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        balance REAL DEFAULT 0,
        referral_code TEXT UNIQUE,
        referred_by INTEGER,
        is_admin INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # ---- Orders ----
    c.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        uid TEXT NOT NULL,
        plan TEXT NOT NULL,
        price REAL DEFAULT 0,
        status TEXT DEFAULT 'pending',
        api_response TEXT DEFAULT '',
        api_status TEXT DEFAULT '',
        request_url TEXT DEFAULT '',
        likes_added INTEGER DEFAULT 0,
        likes_before INTEGER DEFAULT 0,
        likes_after INTEGER DEFAULT 0,
        player_name TEXT DEFAULT '',
        server_name TEXT DEFAULT '',
        status_code TEXT DEFAULT '',
        order_type TEXT DEFAULT 'plan',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # ---- Deposits ----
    c.execute("""CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        method TEXT DEFAULT 'UPI',
        reference TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # ---- Transfers ----
    c.execute("""CREATE TABLE IF NOT EXISTS transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user INTEGER NOT NULL,
        to_user INTEGER NOT NULL,
        amount REAL NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # ---- FamGateway Orders ----
    c.execute("""CREATE TABLE IF NOT EXISTS fam_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        status TEXT DEFAULT 'pending',
        qr_url TEXT DEFAULT '',
        credited INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # ---- Settings (single row) ----
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        api_url TEXT DEFAULT '',
        api_key TEXT DEFAULT '',
        api_method TEXT DEFAULT 'GET',
        api_param_name TEXT DEFAULT 'uid',
        api_key_param TEXT DEFAULT 'key',
        api_extra_params TEXT DEFAULT '',
        api_server_param TEXT DEFAULT 'server_name',
        api_servers TEXT DEFAULT '',
        api_success_mode TEXT DEFAULT 'json',
        api_success_keyword TEXT DEFAULT 'success',
        api_status_field TEXT DEFAULT 'status',
        api_status_value TEXT DEFAULT '1',
        api_max_field TEXT DEFAULT 'status',
        api_max_value TEXT DEFAULT '2',
        api_likes_field TEXT DEFAULT 'LikesGivenByAPI',
        api_total_field TEXT DEFAULT 'LikesafterCommand',
        api_player_field TEXT DEFAULT 'PlayerNickname',
        api_before_field TEXT DEFAULT 'LikesbeforeCommand',
        api_region_field TEXT DEFAULT 'Region',
        instant_like_cost REAL DEFAULT 30,
        instant_like_count INTEGER DEFAULT 1000
    )""")
    
    # ---- Packages (auto like packages — CRUD-able from admin) ----
    c.execute("""CREATE TABLE IF NOT EXISTS packages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        likes INTEGER NOT NULL,
        price REAL NOT NULL,
        badge TEXT DEFAULT '',
        description TEXT DEFAULT '',
        is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # ---- Migrations (safe additions) ----
    _migrate(c, "settings", [
        ("api_method", "TEXT DEFAULT 'GET'"),
        ("api_param_name", "TEXT DEFAULT 'uid'"),
        ("api_key_param", "TEXT DEFAULT 'key'"),
        ("api_extra_params", "TEXT DEFAULT ''"),
        ("api_server_param", "TEXT DEFAULT 'server_name'"),
        ("api_servers", "TEXT DEFAULT ''"),
        ("api_success_mode", "TEXT DEFAULT 'json'"),
        ("api_success_keyword", "TEXT DEFAULT 'success'"),
        ("api_status_field", "TEXT DEFAULT 'status'"),
        ("api_status_value", "TEXT DEFAULT '1'"),
        ("api_max_field", "TEXT DEFAULT 'status'"),
        ("api_max_value", "TEXT DEFAULT '2'"),
        ("api_likes_field", "TEXT DEFAULT 'LikesGivenByAPI'"),
        ("api_total_field", "TEXT DEFAULT 'LikesafterCommand'"),
        ("api_player_field", "TEXT DEFAULT 'PlayerNickname'"),
        ("api_before_field", "TEXT DEFAULT 'LikesbeforeCommand'"),
        ("api_region_field", "TEXT DEFAULT 'Region'"),
        ("instant_like_cost", "REAL DEFAULT 30"),
        ("instant_like_count", "INTEGER DEFAULT 1000"),
    ])
    _migrate(c, "orders", [
        ("api_response", "TEXT DEFAULT ''"),
        ("api_status", "TEXT DEFAULT ''"),
        ("request_url", "TEXT DEFAULT ''"),
        ("likes_added", "INTEGER DEFAULT 0"),
        ("likes_before", "INTEGER DEFAULT 0"),
        ("likes_after", "INTEGER DEFAULT 0"),
        ("player_name", "TEXT DEFAULT ''"),
        ("server_name", "TEXT DEFAULT ''"),
        ("status_code", "TEXT DEFAULT ''"),
        ("order_type", "TEXT DEFAULT 'plan'"),
    ])
    _migrate(c, "deposits", [
        ("method", "TEXT DEFAULT 'UPI'"),
        ("reference", "TEXT DEFAULT ''"),
    ])
    _migrate(c, "users", [
        ("is_admin", "INTEGER DEFAULT 0"),
        ("is_banned", "INTEGER DEFAULT 0"),
    ])
    
    # ---- Seed settings ----
    c.execute("SELECT COUNT(*) FROM settings")
    if c.fetchone()[0] == 0:
        c.execute("""INSERT INTO settings
            (api_url, api_method, api_param_name, api_key_param, api_server_param,
             api_servers, api_success_mode, api_success_keyword,
             api_status_field, api_status_value, api_max_field, api_max_value,
             api_likes_field, api_total_field, api_player_field,
             api_before_field, api_region_field, instant_like_cost, instant_like_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (Config.DEFAULT_LIKE_API_URL, "GET", "uid", "key", "server_name",
             Config.DEFAULT_SERVERS, "json", "success",
             "status", "1", "status", "2",
             "LikesGivenByAPI", "LikesafterCommand", "PlayerNickname",
             "LikesbeforeCommand", "Region",
             Config.DEFAULT_INSTANT_LIKE_COST, Config.DEFAULT_INSTANT_LIKE_COUNT))
    
    # ---- Seed default packages ----
    c.execute("SELECT COUNT(*) FROM packages")
    if c.fetchone()[0] == 0:
        defaults = [
            ("STARTER",  1540,  150.00, "BASIC",   "Perfect for a quick boost",   1),
            ("STANDARD", 3300,  220.00, "POPULAR", "Most loved by players",       2),
            ("PRO",      6600,  400.00, "BEST",    "Maximum engagement power",    3),
            ("MEGA",     10000, 599.00, "MEGA",    "For serious competitors",     4),
        ]
        for name, likes, price, badge, desc, sort in defaults:
            c.execute("""INSERT INTO packages (name, likes, price, badge, description, sort_order)
                         VALUES (?,?,?,?,?,?)""", (name, likes, price, badge, desc, sort))
    
    conn.commit()
    conn.close()

def _migrate(c, table, cols):
    existing = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    for name, ddl in cols:
        if name not in existing:
            try: c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            except sqlite3.OperationalError: pass