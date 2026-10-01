"""HTTP 服务：JSON API + 三端静态页面（游客端 / 现场端 / 管理端）。

仅用标准库实现，ThreadingHTTPServer + 服务层进程锁保证并发正确。
"""
from __future__ import annotations

import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db
from .adapters import pay, scale, ScaleError
from .services import FarmService, ServiceError, SLOTS, today_str

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = os.environ.get("FARM_DB", str(BASE_DIR / "farm.db"))

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = db.connect(DB_PATH)
        db.init_db(_conn)
    return _conn


def svc() -> FarmService:
    return FarmService(get_conn())


PAGES = {
    "/": ("visitor.html", "采摘日历 · 云枝农场"),
    "/gate": ("gate.html", "入口核验 · 云枝农场"),
    "/admin": ("admin.html", "园区管理 · 云枝农场"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "FarmTickets/1.0"

    # ---------------------------------------------------------- 基础工具
    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _page(self, filename: str) -> None:
        p = STATIC_DIR / filename
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _static(self, url_path: str) -> bool:
        """提供 /styles.css、/js/*、/img/* 等本地静态资源（含演示图片）。"""
        if not url_path.startswith("/") or ".." in url_path.split("/"):
            return False
        p = (STATIC_DIR / url_path.lstrip("/")).resolve()
        if not str(p).startswith(str(STATIC_DIR.resolve())) or not p.is_file():
            return False
        ctype = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".ico": "image/x-icon",
        }.get(p.suffix, "application/octet-stream")
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            qs = parse_qs(raw.decode())
            return {k: v[0] for k, v in qs.items()}

    def _query(self) -> dict:
        return parse_qs(urlparse(self.path).query)

    def log_message(self, fmt, *args) -> None:  # 安静一点
        pass

    # ---------------------------------------------------------- 路由
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in PAGES:
            return self._page(PAGES[path][0])
        served = self._static(path)
        if served:
            return
        if path == "/api/calendar":
            q = self._query()
            d = q.get("from", [today_str()])[0]
            return self._json(svc().calendar(d, int(q.get("days", ["10"])[0])))
        if path == "/api/types":
            return self._json([dict(r) for r in svc().list_ticket_types()])
        if path == "/api/zones":
            return self._json([dict(r) for r in svc().list_crops_zones()])
        if path == "/api/slots":
            return self._json(SLOTS)
        if path == "/api/tickets":
            phone = self._query().get("phone", [""])[0]
            return self._json(svc().my_tickets(phone))
        if path == "/api/ticket":
            return self._json(svc().ticket_detail(self._query()["code"][0]))
        if path == "/api/inpark":
            return self._json([dict(r) for r in svc().in_park_tickets()])
        if path == "/api/scale":
            return self._json(svc().scale_status())
        if path == "/api/outstanding":
            return self._json(svc().outstanding())
        if path == "/api/transactions":
            return self._json(svc().transactions())
        if path == "/api/overview":
            return self._json(svc().overview())
        if path == "/api/affected":
            q = self._query()
            day = q.get("date", [today_str()])[0]
            zone = q.get("zone", [None])[0]
            return self._json([dict(r) for r in
                               svc().affected_tickets(day, int(zone) if zone else None)])
        if path == "/api/closure-options":
            day = self._query().get("date", [today_str()])[0]
            return self._json(svc().closure_options(day))
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        b = self._body()
        try:
            if path == "/api/buy":
                return self._json(svc().buy_ticket(
                    int(b["type_id"]), b["valid_date"], b["slot"],
                    b["holder_name"], b["holder_phone"],
                    int(b["persons"]), int(b.get("children", 0))))
            if path == "/api/reschedule":
                return self._json(svc().reschedule(
                    b["code"], b["new_date"], b["new_slot"]))
            if path == "/api/transfer":
                return self._json(svc().transfer(
                    b["code"], b["new_name"], b["new_phone"]))
            if path == "/api/checkin":
                return self._json(svc().checkin(b["code"], b.get("gate", "主入口")))
            if path == "/api/checkout":
                return self._json(svc().weigh_and_checkout(
                    b["code"], b.get("operator", "检票员")))
            if path == "/api/settle":
                return self._json(svc().settle_supplementary(b["code"]))
            if path == "/api/refund":
                return self._json(svc().refund_ticket(
                    int(b["ticket_id"]), b.get("reason", "停采退票")))
            if path == "/api/admin/zone-day":
                return self._json(svc().set_zone_day(
                    int(b["zone_id"]), b["date"], b["status"],
                    b.get("reason", "")))
            if path == "/api/admin/zone-default":
                return self._json(svc().set_zone_default(
                    int(b["zone_id"]), b["status"], b.get("reason")))
            if path == "/api/admin/rain":
                return self._json(svc().rain_close_park(
                    b.get("date", today_str()), b.get("reason", "雨天停园")))
            # ---- 模拟设备控制台（真实系统中由硬件/支付网关驱动）----
            if path == "/api/sim/scale":
                if b.get("connected") is False:
                    scale.disconnect()
                else:
                    scale.connect()
                    scale.set_weight_g(int(b.get("weight_g", 0)))
                return self._json(svc().scale_status())
            if path == "/api/sim/pay":
                pay.online = bool(b.get("online", True))
                return self._json({"online": pay.online})
        except ServiceError as e:
            return self._json({"error": str(e), "code": e.code}, e.status)
        except ScaleError as e:
            return self._json({"error": str(e), "code": "scale_error"}, 503)
        except KeyError as e:
            return self._json({"error": f"缺少参数: {e}", "code": "bad_params"}, 400)
        self.send_error(404)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    get_conn()  # 预热建表
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"🌿 云枝农场采摘券系统已启动: http://{host}:{port}")
    print(f"   游客端 http://{host}:{port}/ ｜ 现场端 /gate ｜ 管理端 /admin")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    a = ap.parse_args()
    serve(a.host, a.port)
