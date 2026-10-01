// 游客端逻辑：日历、购券、我的券（改期/转赠）
let CAL = {};
let SELECTED_DATE = null;
const STATUS_TEXT = {
  valid: "有效待入园", used: "已入园", checked_out: "已离园",
  refunded: "已退款", transferred: "已转赠"
};

async function loadCalendar() {
  CAL = await api(`/api/calendar?from=${todayISO()}&days=10`);
  renderCalendar();
  const first = Object.keys(CAL)[0];
  selectDate(first);
}

function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function renderCalendar() {
  const el = document.getElementById("calendar");
  el.innerHTML = "";
  for (const [day, info] of Object.entries(CAL)) {
    const total = info.zones.reduce((s, z) => s + z.remaining, 0);
    const allClosed = info.zones.every(z => !z.open);
    const cell = document.createElement("div");
    cell.className = "day-cell" + (allClosed ? " rain" : "");
    if (day === SELECTED_DATE) cell.classList.add("selected");
    let badge;
    if (allClosed) badge = `<span class="badge closed">🌧 停园</span>`;
    else if (total === 0) badge = `<span class="badge full">约满</span>`;
    else badge = `<span class="badge open">余 ${total} 个名额</span>`;
    cell.innerHTML = `<div class="dow">${dow(day)}</div><div class="d">${day.slice(5)}</div>${badge}`;
    cell.onclick = () => allClosed ? selectDate(day) : selectDate(day);
    el.appendChild(cell);
  }
}

function selectDate(day) {
  SELECTED_DATE = day;
  renderCalendar();
  const el = document.getElementById("dayZones");
  const info = CAL[day];
  const allClosed = info.zones.every(z => !z.open);
  if (allClosed) {
    const why = info.zones.map(z => z.reason).find(Boolean) || "";
    el.innerHTML = `<div class="alert info">🌧 ${day} 全园停采：${esc(why)}。已预约游客可在下方“我的券”办理<b>免费改期或退票</b>。</div>`;
    return;
  }
  el.innerHTML = info.zones.map(z => {
    const cls = !z.open ? "closed" : (z.remaining === 0 ? "full" : "open");
    const right = !z.open
      ? `<span class="why">${esc(z.reason || "停采")}</span>`
      : z.remaining === 0
        ? `<span class="remain zero">约满</span>`
        : `<span class="remain">剩 ${z.remaining}/${z.capacity}</span>`;
    return `<div class="zone-row">
      <span class="dot ${cls}"></span>
      <span class="grow">${z.emoji} ${esc(z.crop_name)} · ${esc(z.zone_name)}</span>
      ${right}
    </div>`;
  }).join("");
  // 联动购券表单
  const fDate = document.getElementById("fDate");
  if (!fDate.value || fDate.value === "") fDate.value = day;
}

// --------------------------------------------------------------- 券种
async function loadTypes() {
  const types = await api("/api/types");
  const slots = await api("/api/slots");
  const sel = document.getElementById("fType");
  sel.innerHTML = types.map(t =>
    `<option value="${t.id}">${t.emoji} ${esc(t.name)}（${esc(t.zone_name)}）${fmtMoney(t.price_cents)}</option>`
  ).join("");
  document.getElementById("fSlot").innerHTML =
    slots.map(s => `<option>${s}</option>`).join("");
  sel._types = types;
  sel.onchange = () => showTypeInfo(types[sel.selectedIndex]);
  showTypeInfo(types[0]);
}

function showTypeInfo(t) {
  document.getElementById("fDate").min = t.valid_dates.split(",")[0];
  document.getElementById("fDate").max = t.valid_dates.split(",").pop();
  document.getElementById("typeInfo").innerHTML =
    `含 <b>${t.max_persons}</b> 人入园 ｜ 基础重量 <b>${t.base_weight_g / 1000}kg</b> ｜ ` +
    `每位儿童再免 <b>${t.child_free_g / 1000}kg</b> ｜ 超出 <b>${fmtMoney(t.excess_price_cents_per_kg)}/kg</b> ｜ ` +
    `可采日期：${t.valid_dates.split(",").join("、")}`;
}

document.getElementById("buyForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  const msg = document.getElementById("buyMsg");
  const out = document.getElementById("newTicket");
  try {
    const d = await api("/api/buy", {
      type_id: Number(f.type_id.value),
      valid_date: f.valid_date.value,
      slot: f.slot.value,
      holder_name: f.holder_name.value,
      holder_phone: f.holder_phone.value,
      persons: Number(f.persons.value),
      children: Number(f.children.value),
    });
    toast(msg, `购券成功！券码 ${d.code}，请在预约日到入口核销`, "ok");
    out.innerHTML = `<div class="ticket">
      <div class="t-head">✨ 新券</div>
      <div class="code">${d.code}</div>
      <div class="meta">${f.valid_date.value} ${f.slot.value} ｜ ${f.persons.value} 大 ${f.children.value || 0} 小</div>
    </div>`;
    loadCalendar();
    queryTickets();
  } catch (err) {
    toast(msg, err.message, "err");
  }
};

// --------------------------------------------------------------- 我的券
async function queryTickets() {
  const phone = document.getElementById("phoneQuery").value.trim();
  const wrap = document.getElementById("myTickets");
  const msg = document.getElementById("myMsg");
  if (!phone) { toast(msg, "请输入手机号", "err"); return; }
  const list = await api(`/api/tickets?phone=${encodeURIComponent(phone)}`);
  if (!list.length) {
    wrap.innerHTML = `<span class="muted small">该手机号暂无券</span>`;
    return;
  }
  wrap.innerHTML = list.map(t => ticketCard(t)).join("");
  bindTicketActions(wrap, msg);
}

function ticketCard(t) {
  const closedAlert = (t.status === "valid" && !t.zone_open)
    ? `<div class="alert info">⚠ 当日「${esc(t.close_reason || "停采")}」，可免费改期或退票</div>` : "";
  const actions = t.status === "valid" ? `
    <div class="flex wrap mt">
      <button class="ghost small btn-re">改期</button>
      <button class="ghost small btn-tr">转赠</button>
      <button class="danger small btn-rf">退票</button>
    </div>
    <div class="re-panel hidden mt"></div>` : "";
  return `<div class="ticket" data-id="${t.id}" data-code="${t.code}">
    <div class="t-head">${t.emoji} ${esc(t.type_name)} <span class="pill ${t.status}">${STATUS_TEXT[t.status] || t.status}</span></div>
    <div class="code">${t.code}</div>
    <div class="meta">${t.valid_date} ${dow(t.valid_date)} ${esc(t.slot || "")} ｜ ${esc(t.zone_name)} ｜ ${t.persons}大${t.children || 0}小 ｜ ${esc(t.holder_name)}</div>
    ${closedAlert}
    ${actions}
  </div>`;
}

function bindTicketActions(wrap, msg) {
  wrap.querySelectorAll(".ticket").forEach(card => {
    const code = card.dataset.code;
    const id = card.dataset.id;
    const panel = card.querySelector(".re-panel");

    card.querySelector(".btn-re")?.addEventListener("click", async () => {
      panel.classList.toggle("hidden");
      panel.innerHTML = `
        <label>改到日期</label><input type="date" class="re-date">
        <label>时段</label><select class="re-slot">${(await api("/api/slots")).map(s => `<option>${s}</option>`).join("")}</select>
        <button class="small gold mt btn-re-ok">确认改期</button>
        <span class="small muted">改期与入园核销互斥，已核销券不能改期</span>`;
      panel.querySelector(".btn-re-ok").onclick = async () => {
        try {
          await api("/api/reschedule", {
            code, new_date: panel.querySelector(".re-date").value,
            new_slot: panel.querySelector(".re-slot").value
          });
          toast(msg, "改期成功", "ok");
          queryTickets();
          loadCalendar();
        } catch (e) { toast(msg, e.message, "err"); }
      };
    });

    card.querySelector(".btn-tr")?.addEventListener("click", () => {
      panel.classList.remove("hidden");
      panel.innerHTML = `
        <label>受赠人姓名</label><input class="tr-name" placeholder="受赠人姓名">
        <label>受赠人手机号</label><input class="tr-phone" placeholder="受赠人手机号">
        <button class="small gold mt btn-tr-ok">确认转赠（旧码作废）</button>`;
      panel.querySelector(".btn-tr-ok").onclick = async () => {
        try {
          const r = await api("/api/transfer", {
            code,
            new_name: panel.querySelector(".tr-name").value,
            new_phone: panel.querySelector(".tr-phone").value
          });
          toast(msg, `转赠成功，新券码：${r.new_code}`, "ok");
          queryTickets();
        } catch (e) { toast(msg, e.message, "err"); }
      };
    });

    card.querySelector(".btn-rf")?.addEventListener("click", async () => {
      if (!confirm("确认退票？款项原路退回模拟支付账户")) return;
      try {
        await api("/api/refund", { ticket_id: Number(id), reason: "游客申请退票" });
        toast(msg, "退票成功，款项原路退回", "ok");
        queryTickets();
      } catch (e) { toast(msg, e.message, "err"); }
    });
  });
}

document.getElementById("btnQuery").onclick = queryTickets;

(async function init() {
  await Promise.all([loadTypes(), loadCalendar()]);
  queryTickets();
})();
