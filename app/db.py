"""SQLite 스키마와 연결. 수정 금지(오케스트레이터만). 컬럼 추가가 필요하면 보고."""
import os
import sqlite3

from app.contract import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS allowed_cidr (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cidr TEXT NOT NULL, label TEXT NOT NULL); -- INTERNAL|VPN
CREATE TABLE IF NOT EXISTS service (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE');
CREATE TABLE IF NOT EXISTS gateway_token (
  id INTEGER PRIMARY KEY AUTOINCREMENT, service_id INTEGER NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, token_masked TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL);      -- ACTIVE|REVOKED
CREATE TABLE IF NOT EXISTS credential (
  id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL,          -- 'mock'
  auth_mode TEXT NOT NULL,                                               -- API_KEY|WIF
  role TEXT NOT NULL,                                                    -- PRIMARY|STANDBY
  status TEXT NOT NULL DEFAULT 'ACTIVE',                                 -- ACTIVE|REVOKED
  secret_enc TEXT,                                                       -- API_KEY: vault 암호문, WIF: NULL
  masked TEXT NOT NULL,
  audience TEXT,                                                         -- WIF 전용
  provider_ref TEXT NOT NULL,   -- Mock 사용량/폐기 식별자: API_KEY=키 문자열 자체, WIF='wif:'||audience
  replaced_by_id INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, service_id INTEGER, credential_id INTEGER NOT NULL,
  model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_krw REAL NOT NULL,
  status_code INTEGER, at REAL NOT NULL);                                -- at = epoch 초
CREATE TABLE IF NOT EXISTS provider_usage (          -- 누적값 스냅샷
  id INTEGER PRIMARY KEY AUTOINCREMENT, credential_id INTEGER NOT NULL,
  cost_krw REAL NOT NULL, tokens INTEGER NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_usage_cred ON usage_log(credential_id, at);
CREATE INDEX IF NOT EXISTS ix_pu_cred ON provider_usage(credential_id, at);
CREATE TABLE IF NOT EXISTS access_denied_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_ip TEXT, reason TEXT,     -- ip_not_allowed|invalid_token|service_suspended
  token_masked TEXT, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS incident (
  id INTEGER PRIMARY KEY AUTOINCREMENT, credential_id INTEGER NOT NULL,
  status TEXT NOT NULL,                                                  -- OPEN|ROTATED|RESOLVED
  detected_at REAL NOT NULL, blocked_at REAL, resolved_at REAL,
  loss_krw REAL, limit_krw REAL, new_credential_id INTEGER,
  timeline TEXT NOT NULL DEFAULT '[]');                                  -- JSON [{at,step,detail,ok}]
CREATE TABLE IF NOT EXISTS event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
  kind TEXT NOT NULL,   -- SUSPECT|WARNING|BLOCKED|RECOVERED|ATTACK_STARTED|ATTACK_STOPPED|ACCESS_DENIED
  message TEXT NOT NULL, data TEXT);
"""


def get_conn() -> sqlite3.Connection:
    """호출마다 새 연결. 쓰기 후 commit은 호출자 책임."""
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    with get_conn() as c:
        c.executescript(SCHEMA)
        c.execute("INSERT OR IGNORE INTO setting(key,value) VALUES('loss_limit_krw','100000')")
