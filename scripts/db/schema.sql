CREATE TABLE IF NOT EXISTS content_groups (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    folder_path TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    niche TEXT,
    chrome_profile_path TEXT NOT NULL,
    cdp_port INTEGER NOT NULL,
    publish_mode TEXT NOT NULL DEFAULT 'manual_review',
    active INTEGER NOT NULL DEFAULT 1,
    caption_style TEXT NOT NULL DEFAULT 'generic',
    content_group_id INTEGER REFERENCES content_groups(id)
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
    source_folder TEXT,
    claimed_by INTEGER REFERENCES accounts(id),
    claim_product_id INTEGER REFERENCES products(id),
    claim_token TEXT,
    claimed_at TEXT,
    used_by INTEGER REFERENCES accounts(id),
    submission_log_id INTEGER REFERENCES posts_log(id),
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

CREATE UNIQUE INDEX IF NOT EXISTS shared_video_path ON contents(source_folder,file_path) WHERE source_folder IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS one_active_video_per_account ON contents(claimed_by) WHERE status IN ('claimed','submitting');
CREATE INDEX IF NOT EXISTS queue_pick ON contents(source_folder,status,id);

CREATE TABLE IF NOT EXISTS account_sources (
 account_id INTEGER PRIMARY KEY REFERENCES accounts(id), folder_path TEXT UNIQUE NOT NULL,
 next_index INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS content_assignments (
 content_id INTEGER PRIMARY KEY REFERENCES contents(id), product_id INTEGER NOT NULL REFERENCES products(id),
 tiktok_product_id TEXT NOT NULL);
