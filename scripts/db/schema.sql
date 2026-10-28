CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    niche TEXT,
    chrome_profile_path TEXT NOT NULL,
    cdp_port INTEGER NOT NULL,
    publish_mode TEXT NOT NULL DEFAULT 'manual_review',
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    folder_path TEXT NOT NULL,
    folder_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    tiktok_product_id TEXT,
    display_override_text TEXT NOT NULL DEFAULT 'Promo Tiktok Disini',
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(account_id, folder_name)
);

CREATE TABLE IF NOT EXISTS contents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    file_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unused',
    used_at TEXT,
    UNIQUE(product_id, file_path)
);

CREATE TABLE IF NOT EXISTS posts_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    product_id INTEGER,
    content_id INTEGER,
    position_seen INTEGER,
    product_name_seen TEXT,
    caption_text TEXT,
    status TEXT,
    error_message TEXT,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS captions_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    caption_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
