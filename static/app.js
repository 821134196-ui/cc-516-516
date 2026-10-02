// 通用交互：AJAX 表单、提示条、快捷预约、电子秤读数轮询
(function () {
  function toast(msg, ok) {
    var t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.className = "toast " + (ok ? "ok" : "err");
    t.style.display = "block";
    clearTimeout(t._timer);
    t._timer = setTimeout(function () { t.style.display = "none"; }, 3200);
  }
  window.toast = toast;

  document.addEventListener("submit", function (ev) {
    var f = ev.target;
    if (!f.matches("form[data-json]")) return;
    ev.preventDefault();
    var btn = f.querySelector("button[type=submit],button");
    if (btn) btn.disabled = true;
    fetch(f.action, {
      method: "POST",
      headers: {"X-Requested-With": "fetch"},
      body: new URLSearchParams(new FormData(f))
    }).then(function (r) { return r.json().then(function (j) { return {j: j, ok: r.ok}; }); })
      .then(function (res) {
        if (res.j.ok) {
          toast(res.j.msg || "操作成功", true);
          if (res.j.redirect) setTimeout(function () { location.href = res.j.redirect; }, 600);
          else if (res.j.reload) setTimeout(function () { location.reload(); }, 700);
        } else {
          toast(res.j.error || "操作失败", false);
        }
      })
      .catch(function () { toast("网络错误，请重试", false); })
      .finally(function () { if (btn) btn.disabled = false; });
  });

  // 游客端首页：输入我的券码后点“约”直接预约
  window.quickBook = function (date, slot) {
    var inp = document.getElementById("mycode");
    var code = (inp.value || "").trim().toUpperCase();
    if (!code) { inp.focus(); toast("请先在顶部输入我的券码", false); return; }
    fetch("/api/book", {
      method: "POST",
      headers: {"X-Requested-With": "fetch"},
      body: new URLSearchParams({code: code, target: date + "|" + slot})
    }).then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.ok) { toast(j.msg, true); setTimeout(function () { location.href = j.redirect; }, 700); }
        else toast(j.error, false);
      });
  };

  // 现场端：电子秤读数 1.5s 轮询（断连 / 恢复即时可见）
  var read = document.getElementById("scale-read");
  var state = document.getElementById("scale-state");
  if (read && state) {
    setInterval(function () {
      fetch("/api/scale/status").then(function (r) { return r.json(); })
        .then(function (s) {
          if (s.connected) {
            read.innerHTML = s.reading_kg.toFixed(2) + '<span class="scale-unit">kg</span>';
            read.style.color = "#ffd770";
            state.textContent = s.stable ? "读数稳定" : "读数未稳定，请稍候";
          } else {
            read.innerHTML = "— —<span class='scale-unit'>kg</span>";
            read.style.color = "#ff9d8a";
            state.textContent = "电子秤断连，请检查连接";
          }
        }).catch(function () {});
    }, 1500);
  }
})();
