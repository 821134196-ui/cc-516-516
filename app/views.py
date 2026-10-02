"""页面渲染：游客端 / 现场端 / 管理端共用一套白-果园绿-金黄样式。"""
import html
from .config import COLORS, SLOTS, CHILD_FREE_KG

STATUS_CN = {
    "valid": ("未预约", "#8a9383"),
    "booked": ("已预约", "#3f7d3a"),
    "redeemed": ("已入园", "#e8a51c"),
    "weighed": ("已结算", "#2c5a28"),
    "refunded": ("已退款", "#b0463a"),
    "cancelled": ("已取消", "#8a9383"),
}
BOOKING_CN = {"booked": "待到园", "checked_in": "已入园",
              "cancelled": "已取消", "rescheduled": "已改期",
              "no_show": "未到园", "force_refunded": "停采退款"}
REASON_CN = {"rain": "雨天停园", "unripe": "果实未熟", "other": "临时管护"}


def esc(x):
    return html.escape(str(x if x is not None else ""))


def badge(status):
    label, color = STATUS_CN.get(status, (status, "#8a9383"))
    return f'<span class="badge" style="background:{color}">{label}</span>'


CSS = """
:root{--green:#3f7d3a;--green-d:#2c5a28;--green-l:#e8f2e2;--gold:#e8a51c;
--gold-l:#fbf0d4;--ink:#26301f;--gray:#8a9383;--red:#b0463a;}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
background:#fbfdf9;color:var(--ink);line-height:1.55}
a{color:var(--green-d);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1080px;margin:0 auto;padding:0 18px 60px}
header.top{background:#fff;border-bottom:3px solid var(--green);position:sticky;
top:0;z-index:20;box-shadow:0 1px 6px rgba(44,90,40,.08)}
.top .wrap{display:flex;align-items:center;gap:18px;padding:12px 18px;flex-wrap:wrap}
.brand{font-weight:800;font-size:19px;color:var(--green-d);display:flex;gap:8px;align-items:center}
nav{display:flex;gap:4px;flex-wrap:wrap;margin-left:auto}
nav a{padding:7px 14px;border-radius:20px;font-size:14.5px;color:var(--green-d)}
nav a.on,nav a:hover{background:var(--green-l);text-decoration:none}
.card{background:#fff;border:1px solid #e3ecdd;border-radius:14px;padding:18px 20px;
margin:16px 0;box-shadow:0 2px 10px rgba(44,90,40,.05)}
.card h2{font-size:17px;color:var(--green-d);margin-bottom:10px;
display:flex;align-items:center;gap:8px}
.card h3{font-size:15px;margin:12px 0 6px;color:var(--green-d)}
.grid{display:grid;gap:14px}.g3{grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.g2{grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.badge{color:#fff;border-radius:12px;padding:2px 11px;font-size:12.5px;font-weight:600}
.muted{color:var(--gray);font-size:13px}
.btn{display:inline-block;border:0;border-radius:10px;background:var(--green);
color:#fff;padding:9px 18px;font-size:14.5px;cursor:pointer;font-weight:600}
.btn:hover{background:var(--green-d);text-decoration:none}
.btn.gold{background:var(--gold)}
.btn.ghost{background:#fff;color:var(--green-d);border:1.5px solid var(--green)}
.btn.sm{padding:5px 11px;font-size:13px}
.btn:disabled{background:#c4cfbd;cursor:not-allowed}
.btn.danger{background:var(--red)}
input,select{padding:9px 11px;border:1.5px solid #cdd8c6;border-radius:9px;
font-size:14.5px;background:#fff;color:var(--ink)}
input:focus,select:focus{outline:2px solid var(--gold);border-color:var(--gold)}
label{font-size:13.5px;color:var(--green-d);font-weight:600;display:block;margin:8px 0 3px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid #edf2e9;text-align:left;vertical-align:top}
th{color:var(--green-d);font-size:13px;background:#f6faf3}
.alert{border-radius:11px;padding:11px 15px;margin:10px 0;font-size:14px}
.alert.rain{background:#eaf2f8;border:1px solid #b9d2e6;color:#36597a}
.alert.gold{background:var(--gold-l);border:1px solid #efd39a;color:#7a5710}
.alert.ok{background:var(--green-l);border:1px solid #b9d6ae;color:var(--green-d)}
.alert.err{background:#fbeae7;border:1px solid #e3b3ab;color:var(--red)}
.pill{display:inline-block;border-radius:14px;padding:1px 10px;font-size:12px;margin-right:6px;
background:var(--green-l);color:var(--green-d)}
.pill.gold{background:var(--gold-l);color:#8a6310}
.pill.gray{background:#f0f2ee;color:#6c7468}
.day-title{display:flex;align-items:center;gap:10px;margin:22px 0 4px;font-size:16px;font-weight:700}
.slot-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:8px}
.slot{border:1.4px solid #dbe7d4;border-radius:10px;padding:8px 10px;font-size:13.5px;background:#fff}
.slot.full{background:#f5f6f3;color:#99a094}
.slot.closed{background:#fdf6e6;border-color:#ecd9a4}
.slot b{display:block;color:var(--green-d)}
.slot .left{font-size:12.5px;color:#55614e}
.slot .left.warn{color:#b06a16;font-weight:700}
.zone-tag{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
.kiosk{background:var(--green-d);color:#fff;border-radius:20px;padding:34px;text-align:center;margin:18px 0}
.kiosk input{font-size:30px;letter-spacing:3px;text-align:center;font-weight:700;
width:100%;padding:16px;border-radius:14px;border:3px solid var(--gold)}
.bigcode{font-size:40px;font-weight:800;letter-spacing:4px;color:var(--green-d);
font-family:"SFMono-Regular",Consolas,monospace}
.scale-read{font-size:88px;font-weight:800;font-family:"SFMono-Regular",Consolas,monospace;
color:var(--gold);line-height:1.1}
.kiosk .scale-read{color:#ffd770}
.scale-unit{font-size:30px;margin-left:6px}
.toast{position:fixed;left:50%;bottom:34px;transform:translateX(-50%);z-index:50;
border-radius:12px;padding:13px 22px;font-size:15px;display:none;max-width:90vw}
.toast.ok{background:var(--green-d);color:#fff}
.toast.err{background:var(--red);color:#fff}
.flow{list-style:none;border-left:2.5px solid #d5e3cd;margin-left:8px;padding-left:18px}
.flow li{position:relative;padding:5px 0;font-size:13.5px}
.flow li::before{content:"";position:absolute;left:-24px;top:11px;width:9px;height:9px;
border-radius:50%;background:var(--green);border:2px solid #fff}
.flow li b{color:var(--green-d)}
.money{font-weight:800;color:var(--red)}
.money.free{color:var(--green-d)}
.hint{font-size:12.5px;color:var(--gray)}
.legend span{margin-right:16px;font-size:13px}
.legend i{display:inline-block;width:14px;height:14px;border-radius:3px;
vertical-align:-2px;margin-right:5px}
.kv{display:grid;grid-template-columns:110px 1fr;gap:4px 12px;font-size:14px}
.kv dt{color:var(--gray)}
.stickybar{position:sticky;top:57px;background:#fff;border:1.4px solid #dbe7d4;
border-radius:12px;padding:10px 14px;margin:14px 0;z-index:10;font-size:14px;
display:flex;gap:10px;align-items:center;flex-wrap:wrap;box-shadow:0 2px 8px rgba(0,0,0,.05)}
.stickybar input{width:200px;letter-spacing:1px}
"""


def layout(title, body, active="", toast=True):
    nav = [("/", "游客端", "home"), ("/onsite", "现场端", "onsite"),
           ("/map", "园区图", "map"), ("/admin", "管理端", "admin")]
    links = "".join(
        f'<a href="{u}" class="{ "on" if k==active else "" }">{n}</a>'
        for u, n, k in nav)
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · 阳光果园</title><style>{CSS}</style></head><body>
<header class="top"><div class="wrap">
<span class="brand">🍃 阳光果园采摘券</span><nav>{links}</nav>
</div></header><main class="wrap">{body}
<div id="toast" class="toast"></div>
</main>
{'<script src="/static/app.js"></script>' if toast else ''}
</body></html>"""


# ---------------- 游客端：日历 ----------------
def page_home(cal, types):
    cards = "".join(
        f'<div class="slot"><b>{esc(t["title"])}</b>'
        f'<span class="muted">{esc(t["crop_name"])} · {esc(t["people"])}人 · 含{t["base_kg"]}kg</span><br>'
        f'<b style="display:inline;margin-top:4px">¥{t["price"]:.0f}</b> '
        f'<a class="btn sm" href="/buy?type={t["id"]}">购买</a></div>'
        for t in types)
    parts = [f"""
    <div class="card" style="padding:0;overflow:hidden;margin-top:18px">
      <img src="/static/img/hero.svg" style="width:100%;display:block" alt="阳光果园">
    </div>
    <div class="stickybar">🗓️ 快捷预约：输入我的券码
      <input id="mycode" placeholder="FARM-XXXXXX" style="text-transform:uppercase">
      <span class="hint">点击下方任意可约时段即完成预约，结果见提示</span>
    </div>
    <div class="card"><h2>🎫 在售采摘券</h2>
      <div class="slot-grid" style="grid-template-columns:repeat(auto-fit,minmax(230px,1fr))">
      {cards}</div>
    </div>
    <h2 style="margin:22px 0 0;color:var(--green-d)">📅 采摘日历（可约日期与剩余名额优先）</h2>"""]

    for day in cal:
        if day["park_closure"]:
            c = day["park_closure"]
            head = (f'<span class="pill gold">🌧 {esc(REASON_CN[c["reason"]])}</span>'
                    f'<span class="muted">{esc(c["note"])}</span>')
        elif not day["pickable"]:
            head = '<span class="pill gray">部分区域停采</span>'
        else:
            head = '<span class="pill">正常开放</span>'
        parts.append(
            f'<div class="day-title"><span>{day["date"][5:]} 周{day["weekday"]}</span>{head}</div>')
        if day["park_closure"]:
            parts.append('<div class="card" style="background:#f4f8fc;margin:6px 0">'
                         '<b>今日全园停采。</b><span class="muted"> 已预约游客请在'
                         '<a href="/onsite">券订单页</a>办理改期或全额退款。</span></div>')
            continue
        zparts = []
        for zc in day["zones"]:
            z = zc["zone"]
            if not zc["open"]:
                slots = "".join(
                    f'<div class="slot closed"><b>{esc(s["slot"])}</b>'
                    f'<span class="left">停采</span></div>' for s in zc["slots"])
                zparts.append(
                    f'<div class="card" style="margin:6px 0;background:#fffdf5">'
                    f'<h3><span class="zone-tag" style="background:{esc(z["color"])}"></span>'
                    f'{z["emoji"]} {esc(z["name"])} '
                    f'<span class="pill gold">{esc(REASON_CN[zc["closure"]["reason"]])}</span></h3>'
                    f'<div class="slot-grid">{slots}</div></div>')
                continue
            slots = []
            for s in zc["slots"]:
                if s["full"]:
                    slots.append(f'<div class="slot full"><b>{esc(s["slot"])}</b>'
                                 f'<span class="left">已满</span></div>')
                else:
                    warn = "warn" if s["left"] <= 8 else ""
                    slots.append(
                        f'<div class="slot"><b>{esc(s["slot"])}</b>'
                        f'<span class="left {warn}">剩余 {s["left"]} 人</span>'
                        f'<button class="btn sm" style="margin-top:5px" '
                        f'onclick="quickBook(\'{day["date"]}\',\'{s["slot"]}\')">约</button></div>')
            zparts.append(
                f'<div class="card" style="margin:6px 0"><h3>'
                f'<span class="zone-tag" style="background:{esc(z["color"])}"></span>'
                f'{z["emoji"]} {esc(z["name"])}</h3>'
                f'<div class="slot-grid">{"".join(slots)}</div></div>')
        parts.append("".join(zparts))
    return layout("采摘日历", "".join(parts), "home")


# ---------------- 购买 ----------------
def page_buy(tt, msg=""):
    alert = f'<div class="alert err">{esc(msg)}</div>' if msg else ""
    body = f"""
    <div class="card" style="max-width:560px;margin:22px auto">
      <h2>{tt["emoji"]} 购买 · {esc(tt["title"])}</h2>
      {alert}
      <p class="muted">{esc(tt["crop_name"])} / {esc(tt["zone_name"])} ·
        {tt["people"]} 位成人 · 基础重量 {tt["base_kg"]}kg ·
        超额按作物单价现场结算</p>
      <form method="post" action="/buy" data-json="1">
        <input type="hidden" name="type_id" value="{tt["id"]}">
        <label>持券人姓名</label><input name="holder_name" required placeholder="游客姓名">
        <label>手机号</label><input name="holder_phone" required pattern="[0-9]{{6,15}}" placeholder="购票/核销凭证">
        <label>同行儿童人数（每名儿童加免 {CHILD_FREE_KG}kg）</label>
        <input name="children" type="number" min="0" max="6" value="0">
        <p style="margin-top:14px"><button class="btn" type="submit">
          模拟支付 ¥{tt["price"]:.0f} 出票</button></p>
        <p class="hint">支付走 MockPayment 模拟适配器，不会发生真实扣款。</p>
      </form>
    </div>"""
    return layout("购买采摘券", body, "home")


# ---------------- 券订单 ----------------
def page_ticket(d, calendar_dates, msg="", msg_kind="err"):
    t = d["ticket"]
    alert = f'<div class="alert {msg_kind}">{esc(msg)}</div>' if msg else ""
    label, _ = STATUS_CN.get(t["status"], (t["status"], ""))
    b = d["booking"]
    closure = d.get("closure")
    type_name = d.get("type_name", "")
    events = d.get("events", [])
    pays = d.get("payments", [])
    debts = d.get("debts", [])

    # 可预约日期选项（只列开放且未满的时段）
    opts = "".join(f'<option value="{day["date"]}|{sl["slot"]}">'
                   f'{day["date"]} 周{day["weekday"]} {sl["slot"]}（剩{sl["left"]}）</option>'
                   for day in calendar_dates if not day["park_closure"]
                   for zc in day["zones"] if zc["zone"]["code"] == t["zone_code"] and zc["open"]
                   for sl in zc["slots"] if not sl["full"])

    closure_banner = ""
    if b and b["status"] in ("booked", "checked_in") and closure:
        closure_banner = f"""
        <div class="alert rain">🌧 您预约的 <b>{esc(b['date'])} {esc(b['slot'])}</b>
          受「{esc(REASON_CN.get(closure['reason'], closure['reason']))}」影响
          （{esc(closure['note'])}）。请选择改期或全额退款：
          <form method="post" action="/api/reschedule" data-json="1"
                style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center">
            <input type="hidden" name="code" value="{esc(t['code'])}">
            <select name="target">{opts or '<option>暂无可约时段</option>'}</select>
            <button class="btn gold btn-sm" {'disabled' if not opts else ''}>免费改期</button>
          </form>
          <form method="post" action="/api/refund" data-json="1" style="margin-top:8px">
            <input type="hidden" name="code" value="{esc(t['code'])}">
            <button class="btn danger sm">全额退款 ¥{t['amount_paid']:.0f}</button>
          </form>
        </div>"""

    booking_box = ""
    if b:
        booking_box = f"""
        <p><span class="pill">{esc(BOOKING_CN.get(b['status'], b['status']))}</span>
           预约：<b>{esc(b['date'])} {esc(b['slot'])}</b></p>"""
    else:
        booking_box = '<p class="muted">尚未选择采摘时段</p>'

    can_book = t["status"] == "valid"
    can_reschedule = (b is not None and b["status"] == "booked"
                      and t["status"] == "booked" and not closure)
    can_gift = t["status"] in ("valid", "booked")

    book_form = f"""
      <h3>🗓️ {'' if b else '预约时段'}{'改期' if can_reschedule else ''}</h3>"""
    if can_book:
        book_form += f"""
        <form method="post" action="/api/book" data-json="1">
          <input type="hidden" name="code" value="{esc(t['code'])}">
          <select name="target" required>{opts}</select>
          <button class="btn sm">确认预约</button>
        </form>"""
    elif can_reschedule:
        book_form += f"""
        <form method="post" action="/api/reschedule" data-json="1">
          <input type="hidden" name="code" value="{esc(t['code'])}">
          <select name="target" required>{opts}</select>
          <button class="btn sm gold">免费改期</button>
        </form>"""

    gift_box = ""
    if can_gift:
        gift_box = f"""
        <h3>🎁 券转赠</h3>
        <form method="post" action="/api/gift" data-json="1"
              style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
          <input type="hidden" name="code" value="{esc(t['code'])}">
          <div><label>受赠人姓名</label><input name="name" required></div>
          <div><label>手机号</label><input name="phone" required></div>
          <button class="btn ghost sm">转赠</button>
        </form>"""
    if t["gifted_from"]:
        gift_box += f'<p class="hint" style="margin-top:6px">本券由 {esc(t["gifted_from"])} 转赠而来</p>'

    events_html = "".join(
        f'<li><b>{esc(e["created_at"][11:16])}</b> {esc(e["event"])} '
        f'{esc(e["detail"])} <span class="muted">{esc(e["actor"])}</span></li>'
        for e in events) or '<li class="muted">暂无流水</li>'
    pays_html = "".join(
        f'<tr><td>{esc(p["created_at"])}</td><td>{esc(p["kind"])}</td>'
        f'<td class="money {"free" if p["amount"]<0 else ""}">{p["amount"]:+.2f}</td>'
        f'<td>{esc(p["status"])}</td><td class="muted">{esc(p["ref_no"])} {esc(p["note"])}</td></tr>'
        for p in pays) or '<tr><td colspan="5" class="muted">暂无支付流水</td></tr>'
    debt_html = "".join(
        f'<div class="alert gold">待补扣款 <b class="money">¥{db["amount"]:.2f}</b>'
        f'（{esc(db["reason"])}）—— 管理员将在离园后发起补扣</div>'
        for db in debts)

    body = f"""
    {alert}
    <div class="card">
      <div style="display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;align-items:center">
        <div><h2 style="margin:0">{d['crop']['emoji']} {esc(type_name)}</h2>
        <p class="muted">{esc(t['crop_code'])} / {esc(t['zone_code'])} 区 ·
           {t['people']} 成人 + {t['children']} 儿童 · 基础 {t['base_kg']}kg
           （儿童共免 {t['children']*CHILD_FREE_KG}kg）· ¥{t['amount_paid']:.0f}</p></div>
        {badge(t['status'])}</div>
      <div class="bigcode" style="margin:14px 0;text-align:center;background:#f6faf3;
        border:2px dashed #b9d0ae;border-radius:14px;padding:14px">{esc(t['code'])}</div>
      <dl class="kv">
        <dt>持券人</dt><dd>{esc(t['holder_name'])} / {esc(t['holder_phone'])}</dd>
        <dt>有效期</dt><dd>{esc(t['valid_from'])} ~ {esc(t['valid_until'])}</dd>
      </dl>
      <div style="margin-top:10px">{booking_box}</div>
      {closure_banner}
      {book_form}
      {gift_box}
      {debt_html}
    </div>
    <div class="grid g2">
      <div class="card"><h2>🧾 状态流水</h2><ul class="flow">{events_html}</ul></div>
      <div class="card"><h2>💰 支付与退款</h2>
        <table><tr><th>时间</th><th>类型</th><th>金额</th><th>状态</th><th>凭证</th></tr>
        {pays_html}</table>
        <p class="hint" style="margin-top:8px">票款 / 超额 / 退款 / 离园补扣均在此留痕。</p>
      </div>
    </div>"""
    return layout(f"券订单 {t['code']}", body, "home")


# ---------------- 现场端：入口核验 + 称重 ----------------
def page_onsite(gate, scale_snap, inspect_data=None, msg="", msg_kind="err",
                weigh_result=None):
    snap = scale_snap
    scale_line = (f'<span style="color:#9fd0ff">● 已连接</span>'
                  if snap["connected"] else
                  '<span style="color:#ff9d8a">● 断连</span>')
    result_box = ""
    ticket_panel = ""
    if inspect_data:
        d = inspect_data
        t, b, e, z, crop = (d["ticket"], d["booking"], d["entry"],
                            d["zone"], d["crop"])
        lines = [
            ("券码", f'<span class="bigcode">{esc(t["code"])}</span>'),
            ("持券人", esc(f'{t["holder_name"]} / {t["holder_phone"]}')),
            ("品类/园区", f'{crop["emoji"]} {esc(crop["name"])} · {esc(z["name"])}'),
            ("人数", f'{t["people"]} 成人 + {t["children"]} 儿童'),
            ("免费额度", f'{t["base_kg"]}kg 基础 + {t["children"]*CHILD_FREE_KG}kg 儿童'
                       f' = <b>{t["base_kg"]+t["children"]*CHILD_FREE_KG}kg</b>'),
            ("预约", esc(f'{b["date"]} {b["slot"]}') if b else "无"),
            ("状态", badge(t["status"])),
        ]
        rows = "".join(f'<div style="display:flex;gap:12px;padding:6px 0;'
                       f'border-bottom:1px dashed #cfe0c8">'
                       f'<span style="width:96px;color:#5f6b57">{k}</span><span>{v}</span></div>'
                       for k, v in lines)
        actions = ""
        if t["status"] == "booked":
            actions = (f'<form method="post" action="/onsite/redeem" data-json="1">'
                       f'<input type="hidden" name="code" value="{esc(t["code"])}">'
                       f'<input type="hidden" name="gate" value="{gate}">'
                       f'<button class="btn gold" style="font-size:20px;padding:12px 30px">'
                       f'✅ 核销入园（{ "东" if gate=="east" else "西" }入口）</button></form>')
        elif t["status"] == "redeemed":
            actions = ('<p class="alert gold" style="margin:0">该券已在园内，'
                       '请到下方称重台结算离园</p>')
        ticket_panel = f"""
        <div class="card" id="ticket-panel"><h2>🎫 券面核验</h2>{rows}
          <div style="margin-top:14px">{actions}</div></div>"""

    alert = f'<div class="alert {msg_kind}" id="action-msg">{esc(msg)}</div>' if msg else ""
    if weigh_result:
        r = weigh_result
        debt = ('<div class="alert gold">现场扣款失败，已挂账 '
                f'<b class="money">¥{r["amount"]:.2f}</b>，游客可先离场，'
                '系统将离园后补扣。</div>' if r.get("debt") else "")
        result_box = f"""
        <div class="alert ok"><b>结算完成</b>：
          毛重 {r['gross_kg']}kg － 免费 {r['free_kg']}kg ＝
          计费 {r['charge_kg']}kg × 作物单价 =
          <b class="money">¥{r['amount']:.2f}</b></div>{debt}"""

    body = f"""
    <div class="kiosk">
      <div style="font-size:17px;opacity:.85">入口核验 · 请游客出示券码</div>
      <form method="get" action="/onsite" style="margin-top:14px">
        <div style="display:flex;gap:10px;justify-content:center">
          <select name="gate" style="color:#26301f;font-size:16px">
            <option value="east" {"selected" if gate=="east" else ""}>东入口</option>
            <option value="west" {"selected" if gate=="west" else ""}>西入口</option>
          </select>
          <input name="code" placeholder="FARM-XXXXXX" required
                 style="text-transform:uppercase;max-width:420px" autofocus>
          <button class="btn gold" style="font-size:18px;padding:0 26px">查 验</button>
        </div>
      </form>
      <div style="margin-top:12px;font-size:14px">{scale_line}
        <span style="opacity:.7">（核验同时校验当天停采 / 时段 / 重复入园）</span></div>
    </div>
    {alert}
    <div class="grid g2">
      {ticket_panel or '<div class="card muted">输入券码查验后显示券面信息。<br>演示：FARM-T10001（今天可核销）、FARM-IN0001（已入园，重放被拒）、FARM-DONE01（已结算）</div>'}
      <div class="card">
        <h2>⚖️ 离园称重台 {scale_line}</h2>
        <div class="kiosk" style="padding:22px;margin:8px 0">
          <div id="scale-read" class="scale-read"
               style="{'color:#ffd770' if snap['connected'] else 'color:#ff9d8a'}">
            {f"{snap['reading_kg']:.2f}" if snap['connected'] else "— —"}<span class="scale-unit">kg</span></div>
          <div style="font-size:14px;opacity:.85" id="scale-state">
            {"读数稳定" if snap['stable'] and snap['connected'] else
             ("读数未稳定" if snap['connected'] else "电子秤断连，请检查连接")}</div>
        </div>
        <form method="post" action="/onsite/weigh" data-json="1"
              style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <label style="margin:0">已入园券码</label>
          <input name="code" placeholder="FARM-XXXXXX" required style="flex:1;min-width:180px">
          <button class="btn">读取秤重并结算</button>
        </form>
        <p class="hint" style="margin-top:8px">🔒 重量仅来自电子秤读数，检票员不能手工修改；
          秤断连时按钮将返回错误，请恢复连接后重试。现场支付失败自动转欠账。</p>
        {result_box}
      </div>
    </div>"""
    return layout("现场端", body, "onsite")


# ---------------- 园区图 ----------------
def page_map(zones, closures_today, date_iso, calendar_dates):
    date_opts = "".join(
        f'<option value="{d["date"]}" {"selected" if d["date"]==date_iso else ""}>'
        f'{d["date"]} 周{d["weekday"]}'
        + ("（雨天全园停）" if d["park_closure"] else
           ("（部分停采）" if not d["pickable"] else "（开放）")) + "</option>"
        for d in (calendar_dates or []))
    selector = f"""
    <form method="get" action="/map" style="display:flex;gap:8px;align-items:center;
      margin:16px 0 0">
      <label style="margin:0">查看日期</label>
      <select name="date" onchange="this.form.submit()">{date_opts}</select>
      <span class="hint">切换可预览雨天停园边界样例（后天）</span>
    </form>""" if calendar_dates else ""
    polys = []
    legend = ['<span><i style="background:#3f7d3a"></i>可采摘</span>',
              '<span><i style="background:#e8a51c"></i>临时停采</span>',
              '<span><i style="background:#7fa8c9"></i>雨天全园停园</span>']
    park = closures_today.get("__park__")
    for z in zones:
        pts = " ".join(x.replace(",", ",") for x in z["polygon"].split())
        clo = closures_today.get(z["code"])
        if park:
            fill, stroke = "#7fa8c9", "#5b8bb0"
        elif clo:
            fill, stroke = "#f0c969", "#c9911a"
        else:
            fill, stroke = z["color"], "#2c5a28"
        tag = ""
        if park:
            tag = "🌧 停园"
        elif clo:
            tag = "⛔ " + esc(REASON_CN[clo["reason"]])
        polys.append(f"""
        <g>
          <polygon points="{pts}" fill="{fill}" fill-opacity="0.28"
                   stroke="{stroke}" stroke-width="3" stroke-dasharray="{ "2,0" if not (park or clo) else "9,6"}"/>
          <text x="{z['label_x']}" y="{z['label_y']-6}" text-anchor="middle"
                font-size="22" font-weight="700" fill="#26301f">{z['emoji']} {esc(z['name'])}</text>
          <text x="{z['label_x']}" y="{z['label_y']+20}" text-anchor="middle"
                font-size="15" fill="#5b6753">{esc(z['crop_name'])} ·
                {("停采边界" if (park or clo) else "可采边界")} {tag}</text>
        </g>""")
    # 边界说明与入口
    extra = """
    <g>
      <rect x="330" y="508" width="140" height="34" rx="8" fill="#fff" stroke="#3f7d3a" stroke-width="2"/>
      <text x="400" y="530" text-anchor="middle" font-size="15" fill="#2c5a28">游客中心</text>
      <text x="60" y="545" font-size="16" fill="#2c5a28">🚪 东入口</text>
      <text x="660" y="545" font-size="16" fill="#2c5a28">🚪 西入口</text>
    </g>"""
    note = ""
    if park:
        note = (f'<div class="alert rain">🌧 {esc(date_iso)} 全园停园：'
                f'{esc(park["note"] or REASON_CN[park["reason"]])}。'
                '已预约游客可在券订单页免费改期或全额退款。</div>')
    elif any(k for k in closures_today if k != "__park__"):
        names = "、".join(esc(REASON_CN[v["reason"]]) + "(" + esc(k) + "区)"
                          for k, v in closures_today.items() if k != "__park__")
        note = f'<div class="alert gold">⛔ 今日停采区域：{names}，边界以黄色虚线标注。</div>'
    else:
        note = '<div class="alert ok">✅ 今日四区全部开放，绿色实线为可采边界。</div>'

    body = f"""
    <div class="card" style="margin-top:18px">
      <h2>🗺️ 园区采摘状态图 · {esc(date_iso)}</h2>
      {selector}
      {note}
      <svg viewBox="0 0 800 560" style="width:100%;height:auto;background:#f3f8ee;
        border:1.5px solid #d5e3cd;border-radius:12px">
        <rect x="0" y="0" width="800" height="560" fill="#f3f8ee"/>
        <path d="M0 505 Q400 470 800 500" stroke="#b9d0ae" stroke-width="3" fill="none"/>
        {''.join(polys)}
        {extra}
      </svg>
      <p class="legend" style="margin-top:10px">{''.join(legend)}
        <span class="muted">实线=可采边界，虚线=停采边界</span></p>
    </div>"""
    return layout("园区图", body, "map")


# ---------------- 管理端 ----------------
def page_admin(day, zones, closures, affected, calendar_dates, debts,
               scale_snap, msg="", msg_kind="ok"):
    alert = f'<div class="alert {msg_kind}">{esc(msg)}</div>' if msg else ""
    date_opts = "".join(f'<option value="{d["date"]}">{d["date"]} 周{d["weekday"]}'
                        + ("（已全园停）" if d["park_closure"] else "") + "</option>"
                        for d in calendar_dates)
    zone_opts = "".join(f'<option value="{z["code"]}">{z["emoji"]} {esc(z["name"])}</option>'
                        for z in zones)

    closure_rows = ""
    for c in closures:
        scope = "全园" if c["zone_code"] is None else f'{c["zone_code"]}区'
        closure_rows += f"""
        <tr><td>{esc(c['date'])}</td><td><b>{scope}</b></td>
        <td><span class="pill gold">{esc(REASON_CN[c['reason']])}</span></td>
        <td class="muted">{esc(c['note'])}</td><td>{esc(c['set_at'])}</td>
        <td><form method="post" action="/admin/closure/remove" data-json="1">
          <input type="hidden" name="date" value="{esc(c['date'])}">
          <input type="hidden" name="zone" value="{esc(c['zone_code'] or '')}">
          <button class="btn ghost sm">解除</button></form></td></tr>"""
    closure_rows = closure_rows or '<tr><td colspan="6" class="muted">近期无停采记录</td></tr>'

    # 受影响预约：给出退/改操作（每行按其券所属园区生成可选时段）
    def row_targets(zone_code):
        out = []
        for d in calendar_dates:
            if d["park_closure"] or d["date"] <= day:
                continue
            for zc in d["zones"]:
                if zc["zone"]["code"] != zone_code or not zc["open"]:
                    continue
                for s in zc["slots"]:
                    if not s["full"]:
                        out.append(f'<option value="{d["date"]}|{s["slot"]}">'
                                   f'{d["date"]} 周{d["weekday"]} {s["slot"]}'
                                   f'（剩{s["left"]}）</option>')
        return "".join(out)
    aff_rows = ""
    for b in affected:
        target_opts = row_targets(b["tzone"])
        aff_rows += f"""
        <tr><td><b>{esc(b['tcode'])}</b></td><td>{esc(b['holder_name'])}<br>
          <span class="muted">{esc(b['holder_phone'])}</span></td>
        <td>{b['emoji']} {esc(b['zone_name'])}</td><td>{esc(b['slot'])}</td>
        <td style="white-space:nowrap">
          <form method="post" action="/admin/affected/reschedule" data-json="1"
                style="display:flex;gap:4px">
            <input type="hidden" name="booking_id" value="{b['id']}">
            <select name="target" style="padding:4px;font-size:12.5px">{target_opts}</select>
            <button class="btn sm gold">改</button>
          </form>
          <form method="post" action="/admin/affected/refund" data-json="1" style="margin-top:4px">
            <input type="hidden" name="booking_id" value="{b['id']}">
            <button class="btn sm danger">全额退</button>
          </form></td></tr>"""
    aff_rows = aff_rows or '<tr><td colspan="5" class="muted">该停采记录下暂无进行中的预约</td></tr>'

    debt_rows = ""
    for d in debts:
        debt_rows += f"""
        <tr><td><b>{esc(d['tcode'])}</b></td><td>{esc(d['holder_name'])}<br>
          <span class="muted">{esc(d['holder_phone'])}</span></td>
        <td class="money">¥{d['amount']:.2f}</td><td class="muted">{esc(d['reason'])}</td>
        <td>{esc(d['created_at'])}</td>
        <td><form method="post" action="/admin/debt/collect" data-json="1">
          <input type="hidden" name="debt_id" value="{d['id']}">
          <button class="btn sm">发起补扣</button></form></td></tr>"""
    debt_rows = debt_rows or '<tr><td colspan="6" class="muted">暂无待补扣欠账 🎉</td></tr>'

    sc = scale_snap
    body = f"""
    {alert}
    <div class="card">
      <h2>⛔ 设置停采（雨天停园 / 果实未熟）</h2>
      <form method="post" action="/admin/closure/set" data-json="1"
            style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
        <div><label>日期</label><select name="date">{date_opts}</select></div>
        <div><label>范围</label>
          <select name="zone" id="cz"><option value="">全园（雨天停园）</option>{zone_opts}</select></div>
        <div><label>原因</label>
          <select name="reason"><option value="rain">雨天停园</option>
          <option value="unripe">果实未熟</option><option value="other">临时管护</option></select></div>
        <div style="flex:1;min-width:200px"><label>说明（对游客可见）</label>
          <input name="note" style="width:100%" placeholder="如：暴雨橙色预警，全天闭园"></div>
        <button class="btn gold">发布停采</button>
      </form>
      <p class="hint" style="margin-top:8px">发布后，游客端日历与园区图即时更新；
        受影响的进行中预约会在下方列出，可逐条改期或全额退款。</p>
    </div>

    <div class="card">
      <h2>🌧 {esc(day)} 受影响预约与退改处理</h2>
      <form method="get" action="/admin" style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
        <label style="margin:0">查看日期</label>
        <select name="day" onchange="this.form.submit()">{date_opts}</select>
      </form>
      <table><tr><th>券码</th><th>持券人</th><th>园区</th><th>时段</th><th>退改选项</th></tr>
      {aff_rows}</table>
    </div>

    <div class="grid g2">
      <div class="card"><h2>📋 近期停采记录</h2>
        <table><tr><th>日期</th><th>范围</th><th>原因</th><th>说明</th><th>设置时间</th><th></th></tr>
        {closure_rows}</table></div>
      <div class="card"><h2>💳 离园后补扣款</h2>
        <table><tr><th>券码</th><th>持券人</th><th>金额</th><th>原因</th><th>挂账时间</th><th></th></tr>
        {debt_rows}</table>
        <p class="hint" style="margin-top:6px">现场扣款失败不拦游客离场，统一在此重试 MockPayment 补扣。</p>
      </div>
    </div>

    <div class="card">
      <h2>⚖️ 电子秤模拟器（演示控制台）</h2>
      <p>当前：
        <span class="pill {'gray' if not sc['connected'] else ''}">
          {'已连接' if sc['connected'] else '断连'}</span>
        <span class="pill gray">{'稳定' if sc['stable'] else '未稳定'}</span>
        <span class="pill gold">读数 {sc['reading_kg']:.2f} kg</span></p>
      <form method="post" action="/admin/scale" data-json="1"
            style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px">
        <div><label>设置读数 kg</label><input name="reading" type="number" step="0.01"
          value="{sc['reading_kg']:.2f}" style="width:130px"></div>
        <button class="btn sm" name="action" value="set">设置读数</button>
        <button class="btn sm ghost" name="action" value="connect">恢复连接</button>
        <button class="btn sm danger" name="action" value="disconnect">模拟断连</button>
        <button class="btn sm ghost" name="action" value="failpay">下一笔支付失败</button>
      </form>
      <p class="hint" style="margin-top:6px">断连后到现场端称重，将得到“电子秤断连”错误而不会产生任何称重记录。</p>
    </div>"""
    return layout("管理端", body, "admin")
