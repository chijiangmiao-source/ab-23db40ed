"use strict";

import { createTokenGate } from "./token-gate.js";

/* ---------------- 示例网络 ---------------- */

// 关键反例：最短路 S-A-B-T = 0+3+2 = 5 占用共享桥 B-T，
// “先最短路再删边”会误报无双路；真实全局最优为
// 路径1 S-A-T = 0+10 = 10，路径2 S-B-T = 5+2 = 7，合计 17。
const EX_GLOBAL = {
  source: "S", sink: "T",
  fibers: [
    { id: "F1", source: "S", target: "A", delay: 0 },
    { id: "F2", source: "S", target: "B", delay: 5 },
    { id: "F3", source: "A", target: "B", delay: 3 },
    { id: "F4", source: "A", target: "T", delay: 10 },
    { id: "F5", source: "B", target: "T", delay: 2 },
  ],
};

// 单路割瓶颈：所有路线都必须经过桥 E3。
const EX_CUT = {
  source: "S", sink: "T",
  fibers: [
    { id: "E1", source: "S", target: "A", delay: 1 },
    { id: "E2", source: "S", target: "A", delay: 4 },
    { id: "E3", source: "A", target: "T", delay: 2 },
  ],
};

const EX_UNREACHABLE = {
  source: "S", sink: "T",
  fibers: [
    { id: "G1", source: "S", target: "A", delay: 3 },
    { id: "G2", source: "A", target: "S", delay: 1 },
    { id: "G3", source: "B", target: "T", delay: 2 },
  ],
};

const EX_PARALLEL = {
  source: "S", sink: "T",
  fibers: [
    { id: "P1", source: "S", target: "T", delay: 8 },
    { id: "P2", source: "S", target: "T", delay: 3 },
    { id: "P3", source: "S", target: "T", delay: 5 },
  ],
};

const EXAMPLES = {
  global: EX_GLOBAL, cut: EX_CUT, unreachable: EX_UNREACHABLE, parallel: EX_PARALLEL,
};

/* ---------------- DOM ---------------- */
const $ = (sel) => document.querySelector(sel);
const rowsBody = $("#fiber-rows");
const sourceInput = $("#source-input");
const sinkInput = $("#sink-input");
const submitStatus = $("#submit-status");

const tokenGate = createTokenGate(); // 单调递增；过期响应一律丢弃，不得覆盖新草稿结论

/* ---------------- 录入表格 ---------------- */

function addRow(fiber = {}) {
  const tr = document.createElement("tr");
  tr.className = "fiber-row";
  tr.innerHTML = `
    <td class="ridx"></td>
    <td><input class="c-id" /></td>
    <td><input class="c-src" /></td>
    <td><input class="c-tgt" /></td>
    <td><input class="c-delay" inputmode="numeric" /></td>
    <td><button type="button" class="del-btn" title="删除该行">✕</button></td>`;
  tr.querySelector(".c-id").value = fiber.id ?? "";
  tr.querySelector(".c-src").value = fiber.source ?? "";
  tr.querySelector(".c-tgt").value = fiber.target ?? "";
  tr.querySelector(".c-delay").value = fiber.delay ?? "";
  tr.querySelector(".del-btn").addEventListener("click", () => {
    tr.remove();
    refreshIndexes();
  });
  rowsBody.appendChild(tr);
  refreshIndexes();
  return tr;
}

function refreshIndexes() {
  rowsBody.querySelectorAll(".fiber-row").forEach((tr, i) => {
    tr.querySelector(".ridx").textContent = i + 1;
    tr.classList.remove("bad");
  });
}

function loadExample(name) {
  const ex = EXAMPLES[name];
  if (!ex) return;
  sourceInput.value = ex.source;
  sinkInput.value = ex.sink;
  rowsBody.innerHTML = "";
  ex.fibers.forEach((f) => addRow(f));
  clearResult("已载入示例，点击“提交求解”。");
}

/* ---------------- 草稿读取（前端先做轻校验，精确定性以接口为准） ---------------- */

function collectDraft() {
  const fibers = [];
  rowsBody.querySelectorAll(".fiber-row").forEach((tr) => {
    const rawDelay = tr.querySelector(".c-delay").value.trim();
    // 合法的非负整数串转成数字；非法/为空时原样发送，由接口给出定位错误
    const delay = /^\d+$/.test(rawDelay) ? Number(rawDelay) : rawDelay;
    fibers.push({
      id: tr.querySelector(".c-id").value.trim(),
      source: tr.querySelector(".c-src").value.trim(),
      target: tr.querySelector(".c-tgt").value.trim(),
      delay,
    });
  });
  return { source: sourceInput.value.trim(), sink: sinkInput.value.trim(), fibers };
}

/* ---------------- 结论渲染 ---------------- */

function showPane(name) {
  for (const id of ["result-empty", "result-success", "result-infeasible", "result-errors"]) {
    $(`#${id}`).classList.toggle("hidden", id !== name);
  }
}

function clearResult(message) {
  showPane("result-empty");
  $(".placeholder").textContent =
    message || "提交后在此展示两条完整链路，或双路不可行时的源侧节点集合与全部外出割边。";
  refreshIndexes();
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderSuccess(data) {
  showPane("result-success");
  const routes = $("#routes");
  routes.innerHTML = "";
  data.routes.forEach((r) => {
    const card = document.createElement("div");
    card.className = "route-card";
    const segs = r.fiber_ids
      .map((fid, i) =>
        `<span class="seg">${esc(fid)} <span class="d">(${r.delays[i]})</span></span>` +
        (i < r.fiber_ids.length - 1 ? '<span class="arrow">→</span>' : ""))
      .join("");
    card.innerHTML = `
      <div class="rtitle">链路 ${r.index + 1}：${esc(r.nodes.join(" → "))}</div>
      <div>${segs}</div>
      <div class="rt-calc">段延迟复算：${esc(r.delay_expression)}　<b>小计 ${r.delay}</b></div>`;
    routes.appendChild(card);
  });
  $("#total-delay").textContent =
    `${data.routes.map((r) => r.delay).join(" + ")} = ${data.total_delay}`;

  // 贪心对照
  const g = data.greedy_compare;
  const gbox = $("#greedy-box");
  if (g && g.feasible && g.total_delay !== data.total_delay) {
    gbox.innerHTML = `对照：“先求一条最短路再删边”的贪心结果总延迟为
      <code>${g.total_delay}</code>，大于全局最优 <code>${data.total_delay}</code>，
      故本结论来自全局最小费用流，而非贪心冒充。`;
  } else if (g && !g.feasible) {
    gbox.innerHTML = `对照：“先求一条最短路再删边”会在第 2 条路处失败并误报“无双路”
      （最短路 <code>${esc((g.first_path || []).join(" → "))}</code> 占用了共享光纤），
      但全局求解确实找到了上方两条边不重复路径——这正是不能用贪心冒充全局结论的证据。`;
  } else {
    gbox.textContent = "本图中贪心恰好与全局最优一致；结论仍由全局最小费用流给出。";
  }
}

function renderInfeasible(data) {
  showPane("result-infeasible");
  $("#infeasible-msg").textContent = data.message;
  const chips = $("#source-side");
  chips.innerHTML = "";
  data.source_side_nodes.forEach((n) => {
    const c = document.createElement("span");
    c.className = "node-chip";
    c.textContent = n;
    chips.appendChild(c);
  });
  const cutRows = $("#cut-rows");
  cutRows.innerHTML = "";
  data.cut_edges.forEach((e) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${esc(e.id)}</td><td>${esc(e.source)}</td><td>${esc(e.target)}</td><td>${e.delay}</td>`;
    cutRows.appendChild(tr);
  });
  const gbox2 = $("#greedy-box2");
  const g = data.greedy_compare;
  if (g && !g.feasible && g.reason === "no_edge_disjoint_second_path") {
    gbox2.innerHTML = `对照：贪心在此同样失败，其第一条最短路
      <code>${esc((g.first_path || []).join(" → "))}</code>（延迟 ${g.first_delay}）
      已独占外出割边；割边数 ${data.cut_edges.length} = 最大流 ${data.max_flow}，
      从最大流最小割定理证明双路在物理上不可行。`;
  } else {
    gbox2.textContent = `割容量 ${data.cut_edges.length} = 最大流 ${data.max_flow}，双路物理不可行。`;
  }
}

function renderErrors(errors) {
  showPane("result-errors");
  const ul = $("#error-list");
  ul.innerHTML = "";
  errors.forEach((e) => {
    const li = document.createElement("li");
    const where = e.index >= 0 ? `第 ${e.index + 1} 行 · 字段 ${e.field}` : `字段 ${e.field}`;
    li.innerHTML = `<b>[${esc(where)}]</b> ${esc(e.message)}（收到值：${esc(e.value)}）`;
    ul.appendChild(li);
    if (e.index >= 0) {
      const row = rowsBody.querySelectorAll(".fiber-row")[e.index];
      if (row) row.classList.add("bad");
    }
  });
}

/* ---------------- 提交（真实接口 + 过期保护） ---------------- */

async function submitDraft() {
  const draft = collectDraft();
  // 任何新草稿提交都使在途旧请求作废
  const token = tokenGate.issue();
  submitStatus.textContent = "求解中…";
  try {
    const resp = await fetch("/api/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requestId: `req-${Date.now()}-${token}`, ...draft }),
    });
    // 过期响应不得覆盖新草稿
    if (!tokenGate.isCurrent(token)) return;
    const data = await resp.json();
    if (resp.status === 200 && data.feasible) {
      renderSuccess(data);
      submitStatus.textContent = "求解完成（全局最优）。";
    } else if (resp.status === 422) {
      renderInfeasible(data);
      submitStatus.textContent = "已给出割证据：无法形成双路。";
    } else if (data.kind === "validation") {
      // 校验失败：清除旧结论
      renderErrors(data.errors || []);
      submitStatus.textContent = "输入有误，旧结论已清除。";
    } else {
      clearResult(`接口返回异常：${esc(data.message || resp.status)}`);
      submitStatus.textContent = "接口异常。";
    }
  } catch (err) {
    if (!tokenGate.isCurrent(token)) return;
    clearResult(`请求失败：${esc(err.message)}`);
    submitStatus.textContent = "请求失败。";
  }
}

/* ---------------- 绑定 ---------------- */
$("#add-row").addEventListener("click", () => addRow());
$("#submit-btn").addEventListener("click", submitDraft);
$("#example-select").addEventListener("change", (e) => {
  loadExample(e.target.value);
  e.target.value = "";
});

// 初始占位行
["F1", "F2", "F3"].forEach(() => addRow());
loadExample("global");
