const state = {
  runs: [],
  selectedRun: null,
};

const $ = (selector) => document.querySelector(selector);

function fmt(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function pct(value) {
  if (value === null || value === undefined) return "—";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function loadStatus() {
  const container = $("#status");
  try {
    const data = await fetchJson("/api/status");
    const gpuParts = String(data.gpu || "").split(",").map((part) => part.trim());
    container.innerHTML = [
      card("服务层", "运行中", "ok"),
      card("llama-server", data.llama_ok ? "可用" : "不可用", data.llama_ok ? "ok" : "bad"),
      card("GPU", esc(gpuParts[0] || "—"), "ok"),
      card("显存", `${esc(gpuParts[2] || "—")} / ${esc(gpuParts[1] || "—")}`, "warn"),
      card("模型", esc(data.model || "—"), "ok"),
    ].join("");
    $("#last-updated").textContent = `状态更新：${new Date().toLocaleTimeString()}`;
  } catch (error) {
    container.innerHTML = card("服务层", `不可用：${esc(error.message)}`, "bad");
  }
}

function card(label, value, tone = "") {
  return `<div class="card"><div class="label">${label}</div><div class="value ${tone}">${value}</div></div>`;
}

async function loadRuns() {
  try {
    const data = await fetchJson("/api/runs");
    state.runs = data.runs || [];
    renderRuns();
    populateCompare();
  } catch (error) {
    $("#runs-table").innerHTML = `<p class="bad">读取运行记录失败：${esc(error.message)}</p>`;
  }
}

function renderRuns() {
  if (!state.runs.length) {
    $("#runs-table").innerHTML = '<p class="muted">还没有运行记录。</p>';
    return;
  }
  const rows = state.runs
    .map(({ run_id, summary, meta }) => {
      return `<tr>
        <td><span class="run-link" data-run="${esc(run_id)}">${esc(run_id)}</span></td>
        <td>${esc(meta.concurrency ?? "—")}</td>
        <td>${summary.success ?? 0}/${summary.total ?? 0}</td>
        <td>${fmt(summary.ttft_median_s)} s</td>
        <td>${fmt(summary.ttft_p95_s)} s</td>
        <td>${fmt(summary.latency_median_s)} s</td>
        <td>${fmt(summary.latency_p95_s)} s</td>
        <td>${fmt(summary.tpot_median_s * 1000, 2)} ms</td>
        <td>${fmt(summary.throughput_tokens_per_s, 1)}</td>
        <td><button data-run="${esc(run_id)}">查看请求</button></td>
      </tr>`;
    })
    .join("");
  $("#runs-table").innerHTML = `<table>
    <thead><tr>
      <th>运行编号</th><th>并发</th><th>成功/总数</th><th>TTFT 中位数</th><th>TTFT P95</th>
      <th>完成中位数</th><th>完成 P95</th><th>TPOT</th><th>吞吐 tok/s</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  document.querySelectorAll("[data-run]").forEach((element) => {
    element.addEventListener("click", () => showRun(element.dataset.run));
  });
}

async function showRun(runId) {
  state.selectedRun = runId;
  const panel = $("#run-detail");
  panel.classList.remove("hidden");
  panel.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const data = await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
    const rows = data.records
      .map((record) => {
        const tone = record.status === "success" ? "success" : record.status;
        return `<tr>
          <td>${record.request_index + 1}</td>
          <td>${esc(record.prompt_id)}</td>
          <td><span class="pill ${tone}">${esc(record.status)}</span></td>
          <td>${fmt(record.ttft_s)} s</td>
          <td>${fmt(record.tpot_s * 1000, 2)} ms</td>
          <td>${fmt(record.latency_s)} s</td>
          <td>${record.input_tokens ?? "—"}</td>
          <td>${record.output_tokens ?? "—"}</td>
          <td>${record.error_message ? esc(record.error_message) : "—"}</td>
          <td><button data-output="${esc(record.request_id)}">输出</button></td>
        </tr>`;
      })
      .join("");
    panel.innerHTML = `
      <div class="toolbar"><strong>${esc(runId)}</strong>
        <span class="muted">${esc(data.meta.started_at || "")} · 并发 ${esc(data.meta.concurrency)} · ${esc(data.meta.tasks?.length || 0)} 条任务</span>
      </div>
      <table>
        <thead><tr>
          <th>#</th><th>任务</th><th>状态</th><th>TTFT</th><th>TPOT</th><th>完成耗时</th>
          <th>输入 tokens</th><th>输出 tokens</th><th>错误</th><th></th>
        </tr></thead><tbody>${rows}</tbody>
      </table>`;
    panel.querySelectorAll("[data-output]").forEach((element) => {
      element.addEventListener("click", () => showOutput(runId, element.dataset.output));
    });
  } catch (error) {
    panel.innerHTML = `<p class="bad">读取运行详情失败：${esc(error.message)}</p>`;
  }
}

async function showOutput(runId, requestId) {
  try {
    const data = await fetchJson(
      `/api/runs/${encodeURIComponent(runId)}/outputs/${encodeURIComponent(requestId)}`
    );
    $("#modal-title").textContent = `${requestId}`;
    $("#modal-content").textContent = data.content;
    $("#modal").classList.remove("hidden");
  } catch (error) {
    $("#modal-title").textContent = "读取失败";
    $("#modal-content").textContent = error.message;
    $("#modal").classList.remove("hidden");
  }
}

function populateCompare() {
  const options = state.runs
    .map(({ run_id }) => `<option value="${esc(run_id)}">${esc(run_id)}</option>`)
    .join("");
  $("#compare-a").innerHTML = options;
  $("#compare-b").innerHTML = options;
  const baseline = state.runs.find(({ run_id }) => run_id.includes("baseline_c4"));
  const optimized = state.runs.find(({ run_id }) => run_id.includes("optimized_c4"));
  if (baseline) $("#compare-a").value = baseline.run_id;
  if (optimized) $("#compare-b").value = optimized.run_id;
}

function renderCompare() {
  const a = state.runs.find(({ run_id }) => run_id === $("#compare-a").value);
  const b = state.runs.find(({ run_id }) => run_id === $("#compare-b").value);
  if (!a || !b) {
    $("#compare-content").innerHTML = '<p class="muted">请选择两个运行。</p>';
    return;
  }
  const deltaText = (aValue, bValue, unit = "", scale = 1) => {
    if (aValue === null || aValue === undefined || bValue === null || bValue === undefined) {
      return "—";
    }
    const av = Number(aValue) * scale;
    const bv = Number(bValue) * scale;
    const diff = bv - av;
    const pct = av !== 0 ? (diff / av) * 100 : null;
    const sign = diff >= 0 ? "+" : "";
    const pctText = pct === null ? "" : ` (${sign}${pct.toFixed(1)}%)`;
    return `${sign}${diff.toFixed(2)}${unit}${pctText}`;
  };
  const metrics = [
    ["成功/总数", (run) => `${run.summary.success}/${run.summary.total}`, () => "—"],
    ["TTFT 中位数", (run) => `${fmt(run.summary.ttft_median_s)} s`, (aRun, bRun) => deltaText(aRun.summary.ttft_median_s, bRun.summary.ttft_median_s, " s")],
    ["TTFT P95", (run) => `${fmt(run.summary.ttft_p95_s)} s`, (aRun, bRun) => deltaText(aRun.summary.ttft_p95_s, bRun.summary.ttft_p95_s, " s")],
    ["完成耗时中位数", (run) => `${fmt(run.summary.latency_median_s)} s`, (aRun, bRun) => deltaText(aRun.summary.latency_median_s, bRun.summary.latency_median_s, " s")],
    ["完成耗时 P95", (run) => `${fmt(run.summary.latency_p95_s)} s`, (aRun, bRun) => deltaText(aRun.summary.latency_p95_s, bRun.summary.latency_p95_s, " s")],
    ["TPOT", (run) => `${fmt(run.summary.tpot_median_s * 1000, 2)} ms`, (aRun, bRun) => deltaText(aRun.summary.tpot_median_s, bRun.summary.tpot_median_s, " ms", 1000)],
    ["吞吐", (run) => `${fmt(run.summary.throughput_tokens_per_s, 1)} tok/s`, (aRun, bRun) => deltaText(aRun.summary.throughput_tokens_per_s, bRun.summary.throughput_tokens_per_s, " tok/s")],
    ["输出 tokens", (run) => `${run.summary.output_tokens ?? "—"}`, (aRun, bRun) => deltaText(aRun.summary.output_tokens, bRun.summary.output_tokens)],
  ];
  const rows = metrics
    .map(([label, getter, delta]) => {
      return `<div class="head">${label}</div><div>${esc(getter(a))}</div><div>${esc(getter(b))}</div><div>${esc(delta(a, b))}</div>`;
    })
    .join("");
  $("#compare-content").innerHTML = `<div class="compare-grid">
    <div class="head">指标</div><div class="head">${esc(a.run_id)}</div><div class="head">${esc(b.run_id)}</div><div class="head">差值/对比</div>
    ${rows}
  </div>`;
}

async function loadRecovery() {
  const panel = $("#recovery-content");
  panel.innerHTML = '<p class="muted">加载恢复记录…</p>';
  try {
    const data = await fetchJson("/api/recovery");
    const last = data.state?.last_event;
    const tests = data.tests || [];
    const events = data.events || [];
    const testRows = tests.length
      ? tests
          .map((test) => `<tr>
            <td>${esc(test.run_id)}</td>
            <td>${esc(test.started_at || "—")}</td>
            <td>${test.success ?? 0}/${test.total ?? 0}</td>
            <td>${test.failed ?? 0}</td>
            <td>${test.retries ?? 0}</td>
            <td>${fmt(test.recovery_s)} s</td>
            <td>${esc(test.verification_status || "—")}</td>
          </tr>`)
          .join("")
      : '<tr><td colspan="7" class="muted">还没有恢复测试记录。</td></tr>';
    const eventRows = events.length
      ? events
          .slice()
          .reverse()
          .slice(0, 40)
          .map(
            (event) => `<tr>
              <td>${esc(event.timestamp)}</td>
              <td>${esc(event.event)}</td>
              <td>${esc(event.pid ?? "—")}</td>
              <td>${esc(event.reason ?? event.exit_code ?? event.restart_count ?? "—")}</td>
            </tr>`
          )
          .join("")
      : '<tr><td colspan="4" class="muted">还没有监督器事件。</td></tr>';
    panel.innerHTML = `
      <div class="cards">
        ${card("监督器", last ? esc(last.event) : "未启动", last ? "ok" : "warn")}
        ${card("最近事件", last ? esc(last.timestamp) : "—", "ok")}
        ${card("受监督 PID", last?.pid ?? "—", "ok")}
      </div>
      <h3>恢复测试</h3>
      <table>
        <thead><tr><th>编号</th><th>开始时间</th><th>成功/总数</th><th>失败</th><th>重试</th><th>恢复耗时</th><th>恢复后验证</th></tr></thead>
        <tbody>${testRows}</tbody>
      </table>
      <h3>监督器事件</h3>
      <table>
        <thead><tr><th>时间</th><th>事件</th><th>PID</th><th>附加信息</th></tr></thead>
        <tbody>${eventRows}</tbody>
      </table>`;
  } catch (error) {
    panel.innerHTML = `<p class="bad">读取恢复记录失败：${esc(error.message)}</p>`;
  }
}

function setupTabs() {
  document.querySelectorAll(".tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tabs button").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      $(`#tab-${button.dataset.tab}`).classList.add("active");
      if (button.dataset.tab === "compare") renderCompare();
      if (button.dataset.tab === "recovery") loadRecovery();
    });
  });
}

function setupModal() {
  $("#modal-close").addEventListener("click", () => $("#modal").classList.add("hidden"));
  $("#modal").addEventListener("click", (event) => {
    if (event.target === $("#modal")) $("#modal").classList.add("hidden");
  });
}

function init() {
  setupTabs();
  setupModal();
  $("#refresh").addEventListener("click", loadRuns);
  $("#compare-run").addEventListener("click", renderCompare);
  loadStatus();
  loadRuns();
  setInterval(loadStatus, 5000);
}

init();
