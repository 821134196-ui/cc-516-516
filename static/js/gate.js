// 现场端：核销、称重、补扣；轮询电子秤
let pollTimer = null;

async function loadInPark() {
  const list = await api("/api/inpark");
  const el = document.getElementById("inPark");
  if (!list.length) { el.innerHTML = `<span class="muted">当前无在园游客</span>`; return; }
  el.innerHTML = list.map(t => `
    <div class="item">
      <span class="grow">
        <code>${t.code}</code><br>
        <span class="small muted">${t.zone_name} ｜ ${t.entry_gate} ｜ ${t.entry_time?.slice(11)} 核销 ｜ ${t.persons}大${t.children || 0}小</span>
      </span>
      <button class="small ghost" onclick="document.getElementById('weighCode').value='${t.code}'">去称重</button>
    </div>`).join("");
}

document.getElementById("btnCheckin").onclick = async () => {
  const code = document.getElementById("codeIn").value.trim();
  const msg = document.getElementById("checkinMsg");
  if (!code) { toast(msg, "请输入券码", "err"); return; }
  try {
    const r = await api("/api/checkin", {
      code, gate: document.getElementById("gateSel").value
    });
    toast(msg, `✔ 核销成功 · ${r.gate} · ${r.checked_at.replace("T", " ")}`, "ok");
    document.getElementById("codeIn").value = "";
    loadInPark();
  } catch (e) { toast(msg, "✘ " + e.message, "err"); }
};
document.getElementById("codeIn").addEventListener("keydown", e => {
  if (e.key === "Enter") document.getElementById("btnCheckin").click();
});

// --------------------------------------------------------------- 电子秤
async function pollScale() {
  const s = await api("/api/scale");
  const reading = document.getElementById("scaleReading");
  const hint = document.getElementById("scaleHint");
  if (s.connected) {
    reading.classList.remove("off");
    reading.innerHTML = `${s.weight_g}<span class="unit">g</span>`;
    hint.textContent = `电子秤 ${s.sn} · 读数稳定`;
  } else {
    reading.classList.add("off");
    reading.textContent = "⚠ 电子秤断连";
    hint.textContent = s.error || "请检查蓝牙连接";
  }
}

document.getElementById("btnWeigh").onclick = async () => {
  const code = document.getElementById("weighCode").value.trim();
  const msg = document.getElementById("weighMsg");
  const out = document.getElementById("weighResult");
  if (!code) { toast(msg, "请输入离园券码", "err"); return; }
  try {
    const r = await api("/api/checkout", { code, operator: "检票员" });
    if (r.pay_status === "pending") {
      toast(msg, `称重完成：总重 ${r.gross_g}g，超重 ${r.excess_g}g，应付 ¥${(r.amount_cents / 100).toFixed(2)}。支付网关不可用，已挂账待补扣。`, "info");
    } else if (r.amount_cents === 0) {
      toast(msg, `✔ 称重完成：总重 ${r.gross_g}g，在免费重量内，欢迎下次再来！`, "ok");
    } else {
      toast(msg, `✔ 结算完成：总重 ${r.gross_g}g，超重 ${r.excess_g}g，补款 ¥${(r.amount_cents / 100).toFixed(2)}`, "ok");
    }
    out.innerHTML = `<div class="ticket">
      <div class="t-head">结算凭证</div>
      <div class="code">${code.toUpperCase()}</div>
      <div class="meta">总重 ${r.gross_g}g ｜ 免费 ${r.free_g}g ｜ 超重 ${r.excess_g}g ｜ 补款 ¥${(r.amount_cents / 100).toFixed(2)} ｜ ${r.pay_message}</div>
    </div>`;
    document.getElementById("weighCode").value = "";
    loadInPark();
    loadOutstanding();
  } catch (e) {
    if (e.data?.code === "scale_error") {
      toast(msg, "⚖ " + e.message + " —— 严禁人工估重录入", "err");
    } else {
      toast(msg, "✘ " + e.message, "err");
    }
  }
};

// --------------------------------------------------------------- 待补扣
async function loadOutstanding() {
  const list = await api("/api/outstanding");
  const el = document.getElementById("outstanding");
  if (!list.length) { el.innerHTML = `<span class="muted">无挂账</span>`; return; }
  el.innerHTML = list.map(t => `
    <div class="item">
      <span class="grow">
        <code>${t.code}</code>
        <span class="small muted">${esc(t.holder_name)} ${esc(t.holder_phone)} ｜ 待补 ¥${(t.unpaid_cents / 100).toFixed(2)}</span>
      </span>
      <button class="small gold" data-code="${t.code}">补扣款</button>
    </div>`).join("");
  el.querySelectorAll("button").forEach(b => b.onclick = async () => {
    try {
      const r = await api("/api/settle", { code: b.dataset.code });
      toast(document.getElementById("weighMsg"),
            `✔ 补扣成功 ¥${(r.amount_cents / 100).toFixed(2)}，流水号 ${r.ref}`, "ok");
      loadOutstanding();
    } catch (e) { toast(document.getElementById("weighMsg"), e.message, "err"); }
  });
}

// --------------------------------------------------------------- 模拟设备
document.getElementById("simSet").onclick = async () => {
  await api("/api/sim/scale", {
    connected: true, weight_g: Number(document.getElementById("simWeight").value)
  });
  pollScale();
};
document.getElementById("simOff").onclick = async () => {
  await api("/api/sim/scale", { connected: false });
  pollScale();
};
document.getElementById("payOff").onclick = async () => {
  await api("/api/sim/pay", { online: false });
  toast(document.getElementById("weighMsg"), "支付网关已下线：新发生的超额费用将挂账", "info");
};
document.getElementById("payOn").onclick = async () => {
  await api("/api/sim/pay", { online: true });
  toast(document.getElementById("weighMsg"), "支付网关已恢复，可执行补扣款", "ok");
};

pollScale();
loadInPark();
loadOutstanding();
pollTimer = setInterval(pollScale, 2000);
