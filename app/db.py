"""SQLite 存储层：建表、连接管理。

券与全部流水（预约 / 核销 / 入园 / 称重 / 支付 / 退改 / 补扣）均落 SQLite。
所有涉及状态流转的写操作使用 BEGIN IMMEDIATE 串行化，
保证“一张券不可在两个入口重复入园”“核销与改期互斥”。
"""
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH, DATA_DIR

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS crops (
    code        TEXT PRIMARY KEY,          -- APP / PEAR / GRAPE ...
    name        TEXT NOT NULL,
    unit_price  REAL NOT NULL,             -- 超额重量单价（元/kg）
    color       TEXT NOT NULL,
    emoji       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zones (
    code        TEXT PRIMARY KEY,          -- A / B / C ...
    name        TEXT NOT NULL,
    crop_code   TEXT NOT NULL REFERENCES crops(code),
    polygon     TEXT NOT NULL,             -- 园区图上的多边形点 "x1,y1;x2,y2;..."
    label_x     REAL NOT NULL,
    label_y     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS zone_status (
    -- 按 (zone, date) 记录停采；NULL zone 表示全园（雨天停园）
    zone_code   TEXT REFERENCES zones(code),
    date        TEXT NOT NULL,
    reason      TEXT NOT NULL,             -- rain / unripe / other
    note        TEXT DEFAULT '',
    set_at      TEXT NOT NULL,
    set_by      TEXT NOT NULL DEFAULT 'manager',
    PRIMARY KEY (zone_code, date)
);

CREATE TABLE IF NOT EXISTS ticket_types (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,             -- PICK-APP-2P
    title       TEXT NOT NULL,
    crop_code   TEXT NOT NULL REFERENCES crops(code),
    zone_code   TEXT NOT NULL REFERENCES zones(code),
    people      INTEGER NOT NULL,          -- 入园人数（成人）
    base_kg     REAL NOT NULL,             -- 基础重量
    price       REAL NOT NULL,             -- 售价
    valid_days  INTEGER NOT NULL DEFAULT 14
);

CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,  -- 大号券码 FARM-XXXXXX
    type_id         INTEGER NOT NULL REFERENCES ticket_types(id),
    crop_code       TEXT NOT NULL,
    zone_code       TEXT NOT NULL,
    people          INTEGER NOT NULL,
    children        INTEGER NOT NULL DEFAULT 0,
    base_kg         REAL NOT NULL,
    unit_price      REAL NOT NULL,
    amount_paid     REAL NOT NULL,
    holder_name     TEXT NOT NULL,
    holder_phone    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'valid',
        -- valid 已购未预约 / booked 已预约 / redeemed 已核销入园 /
        -- weighed 已结算离园 / refunded 已退 / cancelled 已取消
    valid_from      TEXT NOT NULL,
    valid_until     TEXT NOT NULL,
    gifted_from     TEXT,                  -- 原持有人（转赠留痕）
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    date        TEXT NOT NULL,
    slot        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'booked',
        -- booked / checked_in / cancelled / no_show /
        -- rescheduled / force_refunded
    created_at  TEXT NOT NULL,
    UNIQUE (ticket_id, date, slot)
);

CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    booking_id  INTEGER REFERENCES bookings(id),
    gate        TEXT NOT NULL,             -- east / west
    entered_at  TEXT NOT NULL,
    exited_at   TEXT
);

CREATE TABLE IF NOT EXISTS weighings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL UNIQUE REFERENCES tickets(id),
    entry_id    INTEGER NOT NULL REFERENCES entries(id),
    gross_kg    REAL NOT NULL,             -- 秤上毛重
    free_kg     REAL NOT NULL,             -- 免费额度（基础+儿童）
    charge_kg   REAL NOT NULL,             -- 计费重量
    amount      REAL NOT NULL,             -- 超额金额
    weighed_at  TEXT NOT NULL,
    weighed_by  TEXT NOT NULL DEFAULT 'staff',
    locked      INTEGER NOT NULL DEFAULT 1 -- 称重结果锁定，检票员不可改
);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    kind        TEXT NOT NULL,             -- ticket 购票 / extra 超额 /
                                           -- refund 退款 / arrears 离园后补扣
    amount      REAL NOT NULL,             -- 正数扣款，负数退款
    channel     TEXT NOT NULL DEFAULT 'mock',
    status      TEXT NOT NULL DEFAULT 'ok',-- ok / failed
    ref_no      TEXT NOT NULL,
    note        TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS debts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    amount      REAL NOT NULL,             -- 待补扣金额
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open', -- open / collected / waived
    created_at  TEXT NOT NULL,
    paid_at     TEXT,
    pay_ref     TEXT
);

CREATE TABLE IF NOT EXISTS ledger (
    -- 通用事件流水（审计用）
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER,
    event       TEXT NOT NULL,
    detail      TEXT DEFAULT '',
    actor       TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(date, slot, status);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
"""


def connect(db_path=None):
    path = db_path or DB_PATH
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(db_path=None):
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def tx(conn):
    """立即加写锁的事务上下文，并发写互相串行化。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
