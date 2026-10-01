"""SQLite 数据层：建表、连接管理（线程安全 + 事务级并发）。"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS crops (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    emoji       TEXT NOT NULL DEFAULT '🌱'
);

-- 园区（采摘分区）
CREATE TABLE IF NOT EXISTS zones (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    crop_id     INTEGER NOT NULL REFERENCES crops(id),
    -- 分区当日状态：open 可采 / closed 停采（临时停采、雨天、果实未熟）
    status      TEXT NOT NULL DEFAULT 'open',
    close_reason TEXT
);

-- 每日园区状态（雨天停园、果实未熟、管理临时停采，均按日期+分区落库）
CREATE TABLE IF NOT EXISTS zone_day_status (
    id          INTEGER PRIMARY KEY,
    zone_id     INTEGER NOT NULL REFERENCES zones(id),
    date        TEXT NOT NULL,
    status      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    UNIQUE(zone_id, date)
);

-- 采摘券产品（按作物+园区发售）
CREATE TABLE IF NOT EXISTS ticket_types (
    id              INTEGER PRIMARY KEY,
    crop_id         INTEGER NOT NULL REFERENCES crops(id),
    zone_id         INTEGER NOT NULL REFERENCES zones(id),
    name            TEXT NOT NULL,
    price_cents     INTEGER NOT NULL,          -- 券面价（分）
    base_weight_g   INTEGER NOT NULL,          -- 基础重量（克）
    excess_price_cents_per_kg INTEGER NOT NULL DEFAULT 0, -- 超额单价（分/千克）
    max_persons     INTEGER NOT NULL DEFAULT 1,
    child_free_g    INTEGER NOT NULL DEFAULT 0,-- 每张券的儿童免费重量
    valid_dates     TEXT NOT NULL              -- 逗号分隔的可采日期
);

-- 每日每分区名额
CREATE TABLE IF NOT EXISTS day_capacity (
    id          INTEGER PRIMARY KEY,
    zone_id     INTEGER NOT NULL REFERENCES zones(id),
    date        TEXT NOT NULL,
    capacity    INTEGER NOT NULL,
    UNIQUE(zone_id, date)
);

-- 券订单（一张实体券）
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,      -- 大号券码
    type_id         INTEGER NOT NULL REFERENCES ticket_types(id),
    holder_name     TEXT NOT NULL,
    holder_phone    TEXT NOT NULL,
    persons         INTEGER NOT NULL,
    children        INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'valid',
        -- valid 有效 / used 已核销入园 / checked_out 已离园 /
        -- refunded 已退 / transferred 已转赠（保留旧券记录）
    valid_date      TEXT NOT NULL,             -- 当前预约日期（改期后更新）
    zone_id         INTEGER NOT NULL REFERENCES zones(id),
    entry_gate      TEXT,                      -- 实际入园入口
    entry_time      TEXT,
    exit_time       TEXT,
    parent_ticket_id INTEGER REFERENCES tickets(id), -- 转赠来源
    created_at      TEXT NOT NULL
);

-- 预约时段（游客选择时段；名额按 zone+date 控制）
CREATE TABLE IF NOT EXISTS bookings (
    id          INTEGER PRIMARY KEY,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    zone_id     INTEGER NOT NULL REFERENCES zones(id),
    date        TEXT NOT NULL,
    slot        TEXT NOT NULL,                 -- 如 09:00-10:00
    status      TEXT NOT NULL DEFAULT 'booked',
        -- booked / cancelled / rescheduled / refunded / no_show
    created_at  TEXT NOT NULL
);

-- 核销流水：(券, 预约日期) 唯一，保证核销与改期互斥、跨入口不可重复入园
CREATE TABLE IF NOT EXISTS checkins (
    id          INTEGER PRIMARY KEY,
    ticket_id   INTEGER NOT NULL UNIQUE,
    booking_id  INTEGER NOT NULL REFERENCES bookings(id),
    date        TEXT NOT NULL,
    zone_id     INTEGER NOT NULL REFERENCES zones(id),
    gate        TEXT NOT NULL,
    checked_at  TEXT NOT NULL,
    UNIQUE(ticket_id, date)
);

-- 称重记录（检票员只读：只能看读数，不能改）
CREATE TABLE IF NOT EXISTS weighings (
    id              INTEGER PRIMARY KEY,
    ticket_id       INTEGER NOT NULL UNIQUE REFERENCES tickets(id),
    gross_g         INTEGER NOT NULL,          -- 毛重（秤读数）
    children_count  INTEGER NOT NULL DEFAULT 0,
    child_free_g    INTEGER NOT NULL DEFAULT 0,-- 核销时快照的儿童免重额度
    free_g          INTEGER NOT NULL DEFAULT 0,-- 实际使用免重
    excess_g        INTEGER NOT NULL DEFAULT 0,
    unit_price_cents_per_kg INTEGER NOT NULL,
    amount_cents    INTEGER NOT NULL DEFAULT 0,
    scale_sn        TEXT,
    weighed_at      TEXT NOT NULL,
    operator        TEXT NOT NULL
);

-- 支付/退款流水
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY,
    ticket_id   INTEGER REFERENCES tickets(id),
    kind        TEXT NOT NULL, -- purchase / excess / supplementary / refund
    amount_cents INTEGER NOT NULL,
    status      TEXT NOT NULL, -- success / failed / pending
    channel     TEXT NOT NULL DEFAULT 'mock-pay',
    ref         TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tickets_date ON tickets(valid_date, zone_id);
CREATE INDEX IF NOT EXISTS idx_bookings_lookup ON bookings(date, zone_id, status);
"""


_lock = threading.RLock()


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    with _lock:
        conn.executescript(SCHEMA)
        conn.commit()


def lock() -> threading.RLock:
    """跨线程串行化关键业务事务（核销/改期/称重）。"""
    return _lock
