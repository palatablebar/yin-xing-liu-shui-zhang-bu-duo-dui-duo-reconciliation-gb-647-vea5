// 关账发布：补录影响预览（只读）与正式发布（幂等）。
function flash(message, kind) {
  const box = document.querySelector("#flash");
  if (!box) return;
  box.textContent = message;
  box.className = kind === "ok" ? "flash ok" : "flash";
  box.hidden = false;
  window.clearTimeout(window.__pubFlashTimer);
  window.__pubFlashTimer = window.setTimeout(() => { box.hidden = true; }, 8000);
}

function money(value) {
  const text = String(value ?? "0");
  const num = Number(text);
  return Number.isFinite(num) ? num.toFixed(2) : text;
}

function renderImpact(data) {
  const box = document.querySelector("#preview-box");
  const impact = data.impact;
  const groups = impact.match_groups;
  const unmatched = impact.unmatched;
  const fees = impact.fees;

  const groupRows = (items) => items.map((item) => `
    <tr>
      <td class="mono">${item.match_id}</td>
      <td>${item.tier_name}</td>
      <td class="mono">${(item.bank_ids || []).join(" ")}</td>
      <td class="mono">${(item.ledger_ids || []).join(" ")}</td>
      <td>${item.change}</td>
      <td class="num mono">${money(item.fee_alloc)}</td>
    </tr>`).join("");

  const unmatchedRows = (items) => items.map((item) => `
    <tr>
      <td>${item.side}</td>
      <td class="mono">${item.txn_no}</td>
      <td class="num mono">${money(item.amount)}</td>
      <td>${item.memo || ""}</td>
    </tr>`).join("");

  const feeRows = (items) => items.map((item) => `
    <tr>
      <td class="mono">${item.txn_no}</td>
      <td>${item.change}</td>
      <td class="num mono">${item.fee ?? ("" + money(item.fee_before) + " → " + money(item.fee_after))}</td>
      <td class="num mono">${item.delta ?? ""}</td>
    </tr>`).join("");

  box.innerHTML = `
    <h2>补录影响预览（截止 ${data.cutoff_at}）</h2>
    <p class="empty">来源批次 <span class="mono">${data.run_id}</span>
      （批次生成于 ${data.run_created_at}）；
      基准版本 <span class="mono">${data.baseline_publication_id || "无（首次发布）"}</span></p>
    <div class="chip">
      匹配组：新增 ${groups.added.length} · 消失 ${groups.removed.length} ·
      变动 ${groups.changed.length}；
      未匹配：新增 ${unmatched.newly_unmatched.length} · 消解 ${unmatched.resolved.length}；
      手续费：补录 ${fees.fee_added.length} 笔 · 调整 ${fees.fee_changed.length} 笔 ·
      合计变动 ${money(fees.bank_fee_delta)}
    </div>
    <h3>受影响的匹配组</h3>
    <div class="table-wrap">
      <table class="grid compact">
        <thead><tr><th>匹配组</th><th>层级</th><th>银行流水</th><th>账簿凭证</th><th>变化</th><th class="num">手续费分摊</th></tr></thead>
        <tbody>${groupRows([...groups.added, ...groups.removed, ...groups.changed])}</tbody>
      </table>
      ${groups.added.length + groups.removed.length + groups.changed.length === 0
        ? '<p class="empty">匹配组无变化。</p>' : ""}
    </div>
    <h3>受影响的未匹配项</h3>
    <div class="table-wrap">
      <table class="grid compact">
        <thead><tr><th>侧</th><th>业务号</th><th class="num">金额</th><th>摘要</th></tr></thead>
        <tbody>${unmatchedRows([...unmatched.newly_unmatched, ...unmatched.resolved])}</tbody>
      </table>
      ${unmatched.newly_unmatched.length + unmatched.resolved.length === 0
        ? '<p class="empty">未匹配项无变化。</p>' : ""}
    </div>
    <h3>受影响的手续费</h3>
    <div class="table-wrap">
      <table class="grid compact">
        <thead><tr><th>流水号</th><th>变化</th><th class="num">手续费</th><th class="num">差额</th></tr></thead>
        <tbody>${feeRows([...fees.fee_added, ...fees.fee_changed])}</tbody>
      </table>
      ${fees.fee_added.length + fees.fee_changed.length === 0
        ? '<p class="empty">手续费无变化。</p>' : ""}
    </div>`;
  box.hidden = false;
}

async function callJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.message || data.detail || "请求失败");
  }
  return data;
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("#publish-form");
  if (!form) return;

  document.querySelector('[data-action="preview"]').addEventListener("click", async () => {
    const cutoff = new FormData(form).get("cutoff");
    try {
      const data = await callJson(
        `/api/publications/preview?cutoff=${encodeURIComponent(cutoff)}`
      );
      renderImpact(data);
      flash("已生成补录影响预览，尚未写入发布版本", "ok");
    } catch (error) {
      flash(error.message);
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = await callJson("/api/publications", {
        method: "POST",
        body: new FormData(form),
      });
      flash(`已发布 v${data.version_no}（${data.publication_id}），刷新查看冻结版本`, "ok");
      window.setTimeout(() => window.location.reload(), 1200);
    } catch (error) {
      flash(error.message);
    }
  });
});
