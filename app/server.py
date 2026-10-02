"""本地 Web 服务（标准库 http.server，无需第三方依赖）。

线程模型：ThreadingHTTPServer，每请求一个 SQLite 连接；写事务一律
BEGIN IMMEDIATE，配合 busy_timeout 串行化核销 / 改期等并发写。

权限边界：
- 现场端（/onsite/*）只能核销与“读取电子秤”称重，没有任何修改重量的入口；
- 停采/退改/补扣/秤模拟器只在管理端（/admin/*）。
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import catalog, closures as C, operations as O, tickets as T, views
from .adapters import MockPayment, MockScale, ScaleError
from .clock import Clock
from .config import STATIC_DIR, SLOTS
from .db import connect, init_db
from .errors import FarmError


class AppState:
    def __init__(self):
        self.clock = Clock()
        self.scale = MockScale()
        self.payment = MockPayment()
        self.scale.set_reading(0.0)


STATE = AppState()


def _target(qs):
    v = qs.get("target", [""])[0]
    if "|" not in v:
        raise FarmError("目标时段格式错误")
    date, slot = v.split("|", 1)
    if slot not in SLOTS:
        raise FarmError("时段无效")
    return date, slot


class Handler(BaseHTTPRequestHandler):
    server_version = "FarmPick/1.0"

    # ---------- 基础工具 ----------
    def _db(self):
        return connect()

    def _send_html(self, text, code=200):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, url):
        self.send_response(303)
        self.send_header("Location", url)
        self.end_headers()

    def _qs(self):
        return parse_qs(urlparse(self.path).query)

    def _form(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return parse_qs(raw)

    def _wants_json(self, form):
        return bool(form)  # data-json 表单一律走 AJAX

    def log_message(self, fmt, *args):
        pass  # 安静模式；错误由页面提示

    # ---------- 静态资源 ----------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/":
                return self.home()
            if path == "/buy":
                return self.buy_form()
            if path == "/ticket":
                return self.ticket_detail()
            if path == "/onsite":
                return self.onsite()
            if path == "/map":
                return self.park_map()
            if path == "/admin":
                return self.admin()
            if path == "/api/scale/status":
                return self._json(STATE.scale.snapshot())
            if path.startswith("/static/"):
                return self.static(path)
            self._send_html(views.layout("未找到", '<div class="alert err">页面不存在</div>'), 404)
        except FarmError as e:
            self._send_html(views.layout("出错了",
                            f'<div class="alert err">{views.esc(e)}</div>'), 400)
        except Exception as e:  # noqa: BLE001
            self._send_html(views.layout("服务器错误",
                            f'<div class="alert err">{views.esc(e)}</div>'), 500)

    def do_POST(self):
        path = urlparse(self.path).path
        form = self._form()
        try:
            fn = self.POST_ROUTES.get(path)
            if not fn:
                return self._json({"ok": False, "error": "未知操作"}, 404)
            result = fn(self, form)
            if self.headers.get("X-Requested-With") or form.get("__ajax__"):
                self._json(result)
            else:
                # data-json 表单：由 app.js fetch 提交；非 JS 回退到跳转
                self._json(result)
        except FarmError as e:
            self._json({"ok": False, "error": str(e)}, 400)
        except ScaleError as e:
            self._json({"ok": False, "error": str(e), "scale": True}, 409)
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": f"服务器错误：{e}"}, 500)

    # ---------- GET 页面 ----------
    def home(self):
        conn = self._db()
        cal = catalog.calendar(conn, STATE.clock, days=14)
        types = catalog.list_ticket_types(conn)
        conn.close()
        self._send_html(views.page_home(cal, types))

    def buy_form(self):
        qs = self._qs()
        conn = self._db()
        tt = conn.execute("SELECT tt.*, c.name crop_name, c.emoji, z.name zone_name "
                          "FROM ticket_types tt JOIN crops c ON c.code=tt.crop_code "
                          "JOIN zones z ON z.code=tt.zone_code WHERE tt.id=?",
                          (qs.get("type", ["0"])[0],)).fetchone()
        conn.close()
        if not tt:
            raise FarmError("票种不存在")
        self._send_html(views.page_buy(tt))

    def ticket_detail(self):
        qs = self._qs()
        code = qs.get("code", [""])[0].strip()
        msg, kind = qs.get("msg", [""])[0], qs.get("kind", ["ok"])[0]
        conn = self._db()
        d = O.inspect(conn, code)
        t = d["ticket"]
        # 若当天/预约日有停采，挂上停采信息供页面展示退改
        closure = None
        if d["booking"] and d["booking"]["status"] in ("booked", "checked_in"):
            closure = (catalog.park_closed(conn, d["booking"]["date"])
                       or catalog.zone_closed(conn, t["zone_code"],
                                              d["booking"]["date"]))
        type_row = conn.execute("SELECT title FROM ticket_types WHERE id=?",
                                (t["type_id"],)).fetchone()
        events = conn.execute("SELECT * FROM ledger WHERE ticket_id=? ORDER BY id",
                              (t["id"],)).fetchall()
        pays = conn.execute("SELECT * FROM payments WHERE ticket_id=? ORDER BY id",
                            (t["id"],)).fetchall()
        debts = conn.execute("SELECT * FROM debts WHERE ticket_id=? AND status='open'",
                             (t["id"],)).fetchall()
        cal = catalog.calendar(conn, STATE.clock, days=14)
        conn.close()
        d.update({"closure": closure, "type_name": type_row["title"],
                  "events": events, "payments": pays, "debts": debts})
        self._send_html(views.page_ticket(d, cal, msg=msg, msg_kind=kind))

    def onsite(self):
        qs = self._qs()
        gate = qs.get("gate", ["east"])[0]
        if gate not in ("east", "west"):
            gate = "east"
        code = qs.get("code", [""])[0].strip()
        conn = self._db()
        data = O.inspect(conn, code) if code else None
        conn.close()
        self._send_html(views.page_onsite(gate, STATE.scale.snapshot(),
                                          inspect_data=data))

    def park_map(self):
        qs = self._qs()
        conn = self._db()
        day = qs.get("date", [STATE.clock.date_iso()])[0]
        days = catalog.calendar(conn, STATE.clock, days=14)
        self._send_html(views.page_map(
            catalog.list_zones(conn), catalog.closure_map(conn, day),
            day, days))
        conn.close()

    def admin(self):
        qs = self._qs()
        conn = self._db()
        today = STATE.clock.date_iso()
        day = qs.get("day", [today])[0]
        zones = catalog.list_zones(conn)
        all_closures = conn.execute(
            "SELECT * FROM zone_status ORDER BY date DESC, zone_code LIMIT 30"
        ).fetchall()
        cm = catalog.closure_map(conn, day)
        # 受影响预约：全园停 -> 全部；否则汇总所有当天停采区
        if cm.get("__park__"):
            affected = C.affected_bookings(conn, day)
        else:
            seen, affected = set(), []
            for zc in [k for k in cm if k != "__park__"]:
                for b in C.affected_bookings(conn, day, zc):
                    if b["id"] not in seen:
                        seen.add(b["id"])
                        affected.append(b)
            affected.sort(key=lambda b: (b["slot"], b["tcode"]))
        cal = catalog.calendar(conn, STATE.clock, days=14)
        debts = O.open_debts(conn)
        conn.close()
        self._send_html(views.page_admin(
            day, zones, all_closures, affected, cal, debts,
            STATE.scale.snapshot()))

    def static(self, path):
        rel = path[len("/static/"):]
        f = (STATIC_DIR / rel).resolve()
        if not str(f).startswith(str(STATIC_DIR.resolve())) or not f.is_file():
            self.send_error(404)
            return
        ctype = {"js": "application/javascript", "css": "text/css",
                 "svg": "image/svg+xml", "png": "image/png"}.get(
                     f.suffix.lstrip("."), "application/octet-stream")
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ---------- POST API ----------
    def api_buy(self, form):
        type_id = int(form["type_id"][0])
        children = int(form.get("children", ["0"])[0] or 0)
        conn = self._db()
        t = T.purchase(conn, STATE.clock, type_id,
                       form["holder_name"][0].strip(),
                       form["holder_phone"][0].strip(),
                       children=children, payment=STATE.payment)
        conn.close()
        return {"ok": True, "redirect": f"/ticket?code={t['code']}",
                "code": t["code"]}

    def api_book(self, form):
        date, slot = _target(form)
        conn = self._db()
        t = T.book(conn, STATE.clock, form["code"][0], date, slot)
        conn.close()
        return {"ok": True, "redirect": f"/ticket?code={t['code']}",
                "msg": f"预约成功：{date} {slot}"}

    def api_reschedule(self, form):
        date, slot = _target(form)
        conn = self._db()
        t = T.reschedule(conn, STATE.clock, form["code"][0], date, slot)
        conn.close()
        return {"ok": True, "redirect": f"/ticket?code={t['code']}",
                "msg": f"已改期至 {date} {slot}"}

    def api_gift(self, form):
        conn = self._db()
        t = T.transfer(conn, STATE.clock, form["code"][0],
                       form["name"][0].strip(), form["phone"][0].strip())
        conn.close()
        return {"ok": True, "redirect": f"/ticket?code={t['code']}",
                "msg": f"已转赠给 {t['holder_name']}"}

    def api_refund(self, form):
        conn = self._db()
        t0 = T.get_ticket(conn, form["code"][0])
        T.refund_ticket(conn, STATE.clock, form["code"][0], t0["amount_paid"],
                        "游客申请退款", payment=STATE.payment)
        code = t0["code"]
        conn.close()
        return {"ok": True, "redirect": f"/ticket?code={code}", "msg": "已全额退款"}

    def api_redeem(self, form):
        gate = form.get("gate", ["east"])[0]
        conn = self._db()
        O.redeem(conn, STATE.clock, form["code"][0], gate)
        conn.close()
        return {"ok": True, "msg": "核销成功，欢迎入园",
                "redirect": f"/onsite?gate={gate}&code={form['code'][0]}"}

    def api_weigh(self, form):
        conn = self._db()
        r = O.weigh_and_checkout(conn, STATE.clock, form["code"][0],
                                 STATE.scale, STATE.payment)
        conn.close()
        msg = (f"结算 ¥{r['amount']:.2f}"
               + ("（扣款失败已挂账，离园后补扣）" if r.get("debt") else ""))
        return {"ok": True, "msg": msg, "result": r}

    def api_closure_set(self, form):
        zone = form.get("zone", [""])[0].strip() or None
        conn = self._db()
        C.set_closure(conn, STATE.clock, form["date"][0],
                      form["reason"][0], form.get("note", [""])[0].strip(),
                      zone_code=zone)
        conn.close()
        return {"ok": True, "msg": "停采已发布，受影响预约可退改", "reload": True}

    def api_closure_remove(self, form):
        zone = form.get("zone", [""])[0].strip() or None
        conn = self._db()
        C.remove_closure(conn, STATE.clock, form["date"][0], zone_code=zone)
        conn.close()
        return {"ok": True, "msg": "已解除停采", "reload": True}

    def api_affected_reschedule(self, form):
        date, slot = _target(form)
        conn = self._db()
        C.offer_reschedule(conn, STATE.clock, int(form["booking_id"][0]),
                           date, slot)
        conn.close()
        return {"ok": True, "msg": f"受影响预约已改期至 {date} {slot}", "reload": True}

    def api_affected_refund(self, form):
        conn = self._db()
        C.offer_refund(conn, STATE.clock, int(form["booking_id"][0]),
                       STATE.payment)
        conn.close()
        return {"ok": True, "msg": "已全额退款", "reload": True}

    def api_debt_collect(self, form):
        conn = self._db()
        ref = O.settle_debt(conn, STATE.clock, int(form["debt_id"][0]),
                            STATE.payment)
        conn.close()
        return {"ok": True, "msg": f"补扣成功（{ref}）", "reload": True}

    def api_scale(self, form):
        action = form.get("action", ["set"])[0]
        if action == "set":
            STATE.scale.set_reading(float(form["reading"][0]))
        elif action == "connect":
            STATE.scale.connect()
        elif action == "disconnect":
            STATE.scale.disconnect()
        elif action == "failpay":
            STATE.payment.fail_next = True
        return {"ok": True,
                "msg": {"set": "秤读数已更新", "connect": "电子秤已恢复连接",
                        "disconnect": "已模拟电子秤断连",
                        "failpay": "已设置：下一笔支付将失败"}[action],
                "reload": True}

    POST_ROUTES = {
        "/buy": api_buy,
        "/api/book": api_book,
        "/api/reschedule": api_reschedule,
        "/api/gift": api_gift,
        "/api/refund": api_refund,
        "/onsite/redeem": api_redeem,
        "/onsite/weigh": api_weigh,
        "/admin/closure/set": api_closure_set,
        "/admin/closure/remove": api_closure_remove,
        "/admin/affected/reschedule": api_affected_reschedule,
        "/admin/affected/refund": api_affected_refund,
        "/admin/debt/collect": api_debt_collect,
        "/admin/scale": api_scale,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="阳光果园采摘券系统（演示）")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--reset", action="store_true", help="清库并重建演示数据")
    args = parser.parse_args()

    init_db()
    from .seed import seed
    seed(reset=args.reset)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"🌱 阳光果园采摘券系统已启动：http://{args.host}:{args.port}")
    print("   游客端 /  现场端 /onsite  园区图 /map  管理端 /admin")
    print("   演示样例：后天全园雨天停园（FARM-R10001~R10006）；Ctrl+C 退出")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
