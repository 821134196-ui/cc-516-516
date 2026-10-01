// 管理端：概览、园区图、停采/雨天、受影响预约退改、流水
let ZONES = [];
const KIND_TEXT = {
  purchase: "购券", excess: "超额结算", supplementary: "补扣款", refund: "退款"
};
const KIND_COLOR = {
  purchase: "var(--green-700)", excess: "var(--gold-dark)",
  supplementary: "var(--gold-dark)", refund: "var(--red)"
};

function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function loadStats() {
  const o = await api("/api/overview");
  document.getElementById("stats").innerHTML = [
    ["已售券", o.sold], ["在园人数（券）", o.in_park],
    ["已离园", o.checked_out], ["待补扣", o.outstanding, true],
    ["已退款", o.refunded]
  ].map(([lbl, n, warn]) =>
    `<div class="stat ${warn ? "warn" : ""}"><div class="num">${n}</div><div class="lbl">${lbl}</div></div>`
  ).join("");
}

async function loadZones() {
  ZONES = await api("/api/zones");
  const cal = await api(`/api/calendar?from=${document.getElementById("mapDate").value}&days=1`);
  const day = Object.values(cal)[0];
  document.getElementById("zoneMap").innerHTML = day.zones.map(z => {
    const col = z.open ? "open" : "closed";
    return `<div class="zone-block ${col}">
      <div class="z-name">${z.emoji} ${esc(z.crop_name)} · ${esc(z.zone_name)}</div>
      <div class="z-status">${z.open ? "✅ 可采" : "⛔ 停采"} ｜ 今日 ${z.booked}/${z.capacity}</div>
      ${z.reason ? `<div class="z-reason">原因：${esc(z.reason)}</div>` : ""}
      <div style="margin-top:8px"><span class="gate-chip">入口 ${z.zone_id * 10 + 1} 号检票口</span></div>
    </div>`;
  }).join("");

  const sel = document.getElementById("cZone");
  sel.innerHTML = ZONES.map(z =>
    `<option value="${z.id}">${z.emoji} ${z.crop_name} · ${z.name}</option>`).join("");
}

document.getElementById("mapDate").onchange = loadZones;

// ----------------------------------------------------- 停采 / 雨天
const msg = { el: null };
function amsg(text, kind = "ok") {
  const el = document.getElementById("adminMsg");
  toast(el, text, kind);
}

document.getElementById("btnSetZoneDay").onclick = async () => {
  const reason = document.getElementById("cReason").value
    || document.getElementById("cReasonPreset").value
    || "临时停采";
  try {
    await api("/api/admin/zone-day", {
      zone_id: Number(document.getElementById("cZone").value),
      date: document.getElementById("cDate").value,
      status: document.getElementById("cStatus").value,
      reason
    });
    amsg("分区日状态已更新；下方可处理受影响预约的退改", "ok");
    loadZones();
    loadAffected();
    loadStats();
  } catch (e) { amsg(e.message, "err"); }
};

document.getElementById("btnRain")?.addEventListener("click", async () => {
  if (!confirm("确认当天全园雨天停园？受影响未入园预约可退可改")) return;
  try {
    await api("/api/admin/rain", {
      date: document.getElementById("cDate").value, reason: "雨天停园"
    });
    amsg("🌧 已执行全园雨天停园", "ok");
    loadZones();
    loadAffected();
  } catch (e) { amsg(e.message, "err"); }
});

// ----------------------------------------------------- 受影响预约
async function loadAffected() {
  const day = document.getElementById("affDate").value;
  const d = await api(`/api/closure-options?date=${day}`);
  const el = document.getElementById("affected");
  if (!d.affected.length) {
    el.innerHTML = `<span class="muted small">当天没有未入园的受影响预约（已入园游客照常采摘）。</span>`;
    return;
  }
  const altDates = d.alternatives.map(a =>
    `<div class="zone-row"><span class="grow"><b>${a.date}</b> ${dow(a.date)}
      <span class="small muted">可改至：${a.zones.map(z =>
        `${z.emoji}${esc(z.zone_name)}(余${z.remaining})`).join("、")}</span></span></div>`
  ).join("") || `<span class="small muted">后续暂无可采日期</span>`;

  el.innerHTML = `
    <div class="alert info">共 <b>${d.affected.length}</b> 张未入园券受影响，逐券提供
      <b>原路退款</b> 或 <b>免费改期</b> 选项。可选改期日期：</div>
    <div class="small">${altDates}</div>
    <table class="mt"><thead><tr><th>券码</th><th>游客</th><th>券种/分区</th><th>时段</th><th class="right">操作</th></tr></thead>
      <tbody>${d.affected.map(t => `<tr>
        <td><code>${t.code}</code></td>
        <td>${esc(t.holder_name)}<br><span class="small muted">${esc(t.holder_phone)}</span></td>
        <td>${esc(t.type_name)}<br><span class="small muted">${esc(t.zone_name)}</span></td>
        <td>${esc(t.slot || "-")}</td>
        <td class="right">
          <button class="small gold btn-re" data-id="${t.id}" data-code="${t.code}">改期</button>
          <button class="small danger btn-rf" data-id="${t.id}">退款</button>
        </td></tr>`).join("")}
      </tbody></table>`;

  el.querySelectorAll(".btn-rf").forEach(b => b.onclick = async () => {
    if (!confirm("原路全额退款？")) return;
    try {
      await api("/api/refund", { ticket_id: Number(b.dataset.id), reason: "停采/雨天退票" });
      amsg("退款成功", "ok");
      loadAffected(); loadStats(); loadTxns();
    } catch (e) { amsg(e.message, "err"); }
  });

  el.querySelectorAll(".btn-re").forEach(b => b.onclick = async () => {
    if (!d.alternatives.length) { amsg("暂无可改期日期", "err"); return; }
    const a = d.alternatives[0];
    const ndate = prompt(
      `输入改期日期（可采：${d.alternatives.map(x => x.date).join(" / ")}）`, a.date);
    if (!ndate) return;
    const slot = prompt("输入时段", a.slots[0]);
    if (!slot) return;
    try {
      await api("/api/reschedule", { code: b.dataset.code, new_date: ndate, new_slot: slot });
      amsg(`已改期至 ${ndate} ${slot}`, "ok");
      loadAffected(); loadTxns();
    } catch (e) { amsg(e.message, "err"); }
  });
}

document.getElementById("affDate").onchange = loadAffected;

// ----------------------------------------------------- 流水
async function loadTxns() {
  const rows = await api("/api/transactions?limit=80");
  document.querySelector("#txnTable tbody").innerHTML = rows.map(x => `
    <tr>
      <td class="small">${(x.created_at || "").replace("T", " ")}</td>
      <td><code>${x.code || "-"}</code></td>
      <td style="color:${KIND_COLOR[x.kind] || ""}">${KIND_TEXT[x.kind] || x.kind}</td>
      <td>¥${(x.amount_cents / 100).toFixed(2)}</td>
      <td><span class="pill ${x.status === "success" ? "valid" : "used"}">${x.status}</span></td>
      <td class="small muted">${esc(x.ref || "")}</td>
      <td class="small muted">${esc(x.note || "")}</td>
    </tr>`).join("");
}

(async function init() {
  const t = todayISO();
  document.getElementById("mapDate").value = t;
  document.getElementById("cDate").value = t;
  document.getElementById("affDate").value = t;
  await loadZones();
  await Promise.all([loadStats(), loadAffected(), loadTxns()]);
})();
