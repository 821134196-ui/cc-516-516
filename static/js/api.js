// 通用 API 封装
async function api(path, body) {
  const opt = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || "请求失败"), { data });
  return data;
}

const fmtMoney = (cents) => `¥${(cents / 100).toFixed(2)}`;
const WEEK = ["日", "一", "二", "三", "四", "五", "六"];
function dow(s) { return "周" + WEEK[new Date(s + "T00:00:00").getDay()]; }

function toast(el, msg, kind = "ok") {
  el.className = `alert ${kind}`;
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 6000);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
