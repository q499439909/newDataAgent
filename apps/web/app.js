const state = {
  apiBase: "",
  owner: localStorage.getItem("dataagent.owner") || "local-user",
  workOrderId: localStorage.getItem("dataagent.workOrderId") || "",
  response: null,
  runId: "",
};

const demo = {
  work_order_id: "work_order_demo",
  thread_id: "thread_demo",
  state: {
    requirement:
      "筛选短边至少 1440、主体清晰、无明显水印的猫狗生活照，去重并输出可训练数据集。",
    current_agent: "requirement_agent",
    next_action: "confirm_task_spec",
    task_spec_confirmed: false,
    candidate_sufficient: true,
    task_spec: {
      id: "task_spec_demo_v1",
      objective: "生产猫狗生活照训练数据集",
      source: "D:\\images\\incoming",
      target_count: 1200,
      constraints: ["短边至少 1440", "主体清晰", "去除明显水印", "保留猫狗生活场景"],
      acceptance_criteria: ["人工边界样本审核通过", "原始数据只读", "DatasetVersion 可追溯"],
    },
    representative_pipelines: [
      {
        id: "pipe_recall_demo",
        strategy: "recall_first",
        display_name: "保留优先",
        retention_rate: 0.86,
        rule_pass_rate: 0.81,
        model_score: 0.78,
        confidence: 0.74,
        estimated_cost: 28.4,
        latency_seconds: 530,
      },
      {
        id: "pipe_balanced_demo",
        strategy: "balanced",
        display_name: "均衡方案",
        retention_rate: 0.72,
        rule_pass_rate: 0.89,
        model_score: 0.85,
        confidence: 0.82,
        estimated_cost: 42.8,
        latency_seconds: 690,
      },
      {
        id: "pipe_quality_demo",
        strategy: "quality_first",
        display_name: "质量优先",
        retention_rate: 0.58,
        rule_pass_rate: 0.94,
        model_score: 0.9,
        confidence: 0.87,
        estimated_cost: 61.5,
        latency_seconds: 920,
      },
    ],
    trace: [
      "requirement:task_spec_drafted",
      "hitl:task_spec_requested",
      "retrieval:historical_pipeline_matched",
      "processing:representatives_prepared",
    ],
  },
  interrupts: [
    {
      kind: "task_spec_confirmation",
      work_order_id: "work_order_demo",
      allowed_actions: ["approve", "reject", "edit_spec", "terminate"],
      task_spec: {
        objective: "生产猫狗生活照训练数据集",
        target_count: 1200,
        constraints: ["短边至少 1440", "主体清晰", "去除明显水印"],
      },
    },
  ],
};

const flowSteps = [
  ["DRAFT", "创建任务"],
  ["SPEC_PLANNING", "TaskSpec"],
  ["RETRIEVING", "候选召回"],
  ["PIPELINE_EXPERIMENTING", "Pipeline 试跑"],
  ["WAITING_PIPELINE_APPROVAL", "方案审批"],
  ["DATASET_RUNNING", "全量生产"],
  ["COMPLETED", "终验发布"],
];

const agents = [
  ["需", "需求规划 Agent", "TaskSpec、歧义、验收口径"],
  ["检", "检索 Agent", "RetrievalPlan、CandidatePool"],
  ["处", "数据处理 Agent", "三类 Pipeline、节点预览"],
  ["策", "数据策略 Agent", "SamplingPlan、切片配额"],
];

const $ = (id) => document.getElementById(id);

function getPayload() {
  return state.response || demo;
}

function unwrapInterrupt(interrupt) {
  return interrupt?.value || interrupt || null;
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 2800);
}

function normalizeStage(payload) {
  const appState = payload.state || {};
  if (appState.terminated) return ["已终止", "terminated"];
  if (payload.interrupts?.length) {
    const kind = unwrapInterrupt(payload.interrupts[0])?.kind;
    if (kind === "task_spec_confirmation") return ["需求确认", "等待人工确认 TaskSpec"];
    if (kind === "pipeline_approval") return ["Pipeline 审批", "等待选择代表方案"];
    if (kind === "capability_resolution") return ["能力缺口", "等待处理算子覆盖"];
  }
  if (appState.next_action === "submit_dataset_run") return ["等待全量执行", "Pipeline 已批准"];
  if (appState.sampling_plan) return ["采样策略完成", "可提交 Dataset Run"];
  if (appState.representative_pipelines?.length) return ["Pipeline 试跑", "对比三条代表方案"];
  if (appState.retrieval_plan) return ["候选召回", "检查候选充分性"];
  return ["需求规划", appState.next_action || "准备生成 TaskSpec"];
}

function renderFlow(payload) {
  const appState = payload.state || {};
  const [stage] = normalizeStage(payload);
  const activeIndex =
    stage === "需求确认" ? 1 :
    stage === "候选召回" ? 2 :
    stage === "Pipeline 试跑" ? 3 :
    stage === "Pipeline 审批" ? 4 :
    stage === "等待全量执行" || stage === "采样策略完成" ? 5 :
    appState.terminated ? 0 :
    1;

  $("flowList").innerHTML = flowSteps
    .map(([code, label], index) => {
      const cls = index === activeIndex ? "active" : index < activeIndex ? "done" : "";
      return `<li class="${cls}"><strong>${label}</strong><span>${code}</span></li>`;
    })
    .join("");
}

function valuePercent(value) {
  if (value === undefined || value === null) return "-";
  return `${Math.round(Number(value) * 100)}%`;
}

function money(value) {
  if (value === undefined || value === null) return "-";
  return `¥${Number(value).toFixed(1)}`;
}

function renderPipelines(payload) {
  const pipelines = payload.state?.representative_pipelines || demo.state.representative_pipelines;
  const rows = pipelines
    .map((item) => {
      const name = item.display_name || strategyLabel(item.strategy);
      const selected = payload.state?.selected_pipeline_id === item.id ? "已选" : "选择";
      return `
        <div class="pipeline-row">
          <div class="pipeline-name"><strong>${name}</strong><small>${item.id}</small></div>
          <span>${valuePercent(item.retention_rate)}</span>
          <span>${valuePercent(item.rule_pass_rate)}</span>
          <span>${valuePercent(item.model_score)}</span>
          <span>${valuePercent(item.confidence)}</span>
          <span>${money(item.estimated_cost)}</span>
          <button class="ghost-button" type="button" data-pipeline="${item.id}">${selected}</button>
        </div>
      `;
    })
    .join("");

  $("pipelineTable").innerHTML = `
    <div class="pipeline-row header">
      <span>方案</span><span>保留率</span><span>规则通过</span><span>模型评价</span><span>置信度</span><span>成本</span><span></span>
    </div>
    ${rows}
  `;

  document.querySelectorAll("[data-pipeline]").forEach((button) => {
    button.addEventListener("click", () => approvePipeline(button.dataset.pipeline));
  });
}

function strategyLabel(strategy) {
  return {
    recall_first: "保留优先",
    balanced: "均衡方案",
    quality_first: "质量优先",
  }[strategy] || strategy || "候选方案";
}

function renderApproval(payload) {
  const interrupt = unwrapInterrupt(payload.interrupts?.[0]);
  $("interruptKind").textContent = interrupt?.kind || "none";
  const panel = $("approvalPanel");

  if (!interrupt) {
    panel.innerHTML = `
      <div class="summary-item">
        <span>当前状态</span>
        <strong>暂无待处理审批</strong>
      </div>
    `;
    return;
  }

  if (interrupt.kind === "pipeline_approval") {
    const pipelines = interrupt.pipelines || payload.state?.representative_pipelines || [];
    panel.innerHTML = `
      <div class="approval-summary">
        ${pipelines
          .slice(0, 3)
          .map((item) => `<div class="summary-item"><span>${strategyLabel(item.strategy)}</span><strong>${item.id}</strong></div>`)
          .join("")}
      </div>
      <div class="approval-actions">
        <button class="primary-button" id="approveBalancedButton" type="button">批准均衡方案</button>
        <button class="danger-button" id="rejectButton" type="button">拒绝并终止</button>
      </div>
    `;
    $("approveBalancedButton").addEventListener("click", () => {
      const balanced = pipelines.find((item) => item.strategy === "balanced") || pipelines[0];
      approvePipeline(balanced?.id);
    });
    $("rejectButton").addEventListener("click", () => resume({ approved: false }));
    return;
  }

  if (interrupt.kind === "capability_resolution") {
    const gaps = interrupt.gaps || [];
    panel.innerHTML = `
      <div class="approval-summary">
        <div class="summary-item"><span>缺口数量</span><strong>${gaps.length}</strong></div>
        <div class="summary-item"><span>建议动作</span><strong>重试能力匹配</strong></div>
        <div class="summary-item"><span>尝试次数</span><strong>${interrupt.attempt || 1}</strong></div>
      </div>
      <div class="approval-actions">
        <button class="primary-button" id="retryCapabilityButton" type="button">重试匹配</button>
        <button class="danger-button" id="terminateCapabilityButton" type="button">终止任务</button>
      </div>
    `;
    $("retryCapabilityButton").addEventListener("click", () => resume({ action: "retry" }));
    $("terminateCapabilityButton").addEventListener("click", () => resume({ action: "terminate" }));
    return;
  }

  const spec = interrupt.task_spec || payload.state?.task_spec || {};
  const constraints = spec.constraints || spec.hard_constraints || [];
  panel.innerHTML = `
    <div class="approval-summary">
      <div class="summary-item"><span>目标</span><strong>${spec.objective || payload.state?.requirement || "待确认"}</strong></div>
      <div class="summary-item"><span>目标规模</span><strong>${spec.target_count || spec.sample_size || "未冻结"}</strong></div>
      <div class="summary-item"><span>约束数量</span><strong>${constraints.length || 0}</strong></div>
    </div>
    <div class="approval-actions">
      <button class="primary-button" id="approveSpecButton" type="button">确认 TaskSpec</button>
      <button class="danger-button" id="rejectButton" type="button">拒绝并终止</button>
    </div>
  `;
  $("approveSpecButton").addEventListener("click", () => resume({ approved: true }));
  $("rejectButton").addEventListener("click", () => resume({ approved: false }));
}

function renderAgents(payload) {
  const trace = payload.state?.trace || [];
  $("agentList").innerHTML = agents
    .map(([icon, title, fallback]) => {
      const hit = trace.findLast?.((item) => item.includes(title.slice(0, 2))) || trace.at?.(-1) || fallback;
      return `
        <div class="agent-item">
          <span class="agent-icon">${icon}</span>
          <div><strong>${title}</strong><small>${hit}</small></div>
        </div>
      `;
    })
    .join("");
}

function renderLineage(payload) {
  const appState = payload.state || {};
  const items = [
    ["TaskSpec", appState.task_spec?.id || "draft"],
    ["RetrievalPlan", appState.retrieval_plan?.id || "pending"],
    ["PipelineVersion", appState.selected_pipeline_id || appState.representative_pipelines?.[1]?.id || "candidate"],
    ["SamplingPlan", appState.sampling_plan?.id || "pending"],
    ["DatasetVersion", appState.dataset_version_id || "not published"],
  ];
  $("lineageList").innerHTML = items
    .map(([name, id]) => `<div class="lineage-item"><strong>${name}</strong><small>${id}</small></div>`)
    .join("");
}

function render() {
  const payload = getPayload();
  const appState = payload.state || {};
  const [stage, next] = normalizeStage(payload);
  const pipelines = appState.representative_pipelines || demo.state.representative_pipelines;
  const balanced = pipelines.find((item) => item.strategy === "balanced") || pipelines[0] || {};

  $("ownerInput").value = state.owner;
  $("taskName").textContent = appState.requirement?.slice(0, 18) || "当前工单";
  $("taskMeta").textContent = `${stage} · ${state.owner}`;
  $("currentStage").textContent = stage;
  $("nextAction").textContent = next;
  $("candidateMetric").textContent = appState.candidate_sufficient === false ? "不足" : "78%";
  $("costMetric").textContent = money(balanced.estimated_cost);
  $("hitlMetric").textContent = String(payload.interrupts?.length || 0);
  $("threadId").textContent = payload.thread_id || "thread_demo";
  $("runProgress").style.width = state.runId ? "18%" : "0%";
  $("runStatus").textContent = state.runId ? "已提交" : "未提交";
  $("runDetail").textContent = state.runId || "Pipeline 通过后可提交全量执行";

  renderFlow(payload);
  renderApproval(payload);
  renderPipelines(payload);
  renderAgents(payload);
  renderLineage(payload);
}

async function request(path, options = {}) {
  const response = await fetch(`${state.apiBase}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Owner-ID": state.owner,
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(data?.detail || response.statusText);
  }
  return data;
}

async function startAgent() {
  state.owner = $("ownerInput").value.trim() || "local-user";
  localStorage.setItem("dataagent.owner", state.owner);
  const source = $("sourceInput").value.trim();
  const requirement = $("requirementInput").value.trim();
  if (!source || !requirement) {
    showToast("请填写数据目录和训练目标。");
    return;
  }

  try {
    const payload = await request("/api/work-orders/agent/start", {
      method: "POST",
      body: JSON.stringify({
        requirement,
        data_sources: [{ type: "local_directory", uri: source, mapping: {} }],
      }),
    });
    state.response = payload;
    state.workOrderId = payload.work_order_id;
    localStorage.setItem("dataagent.workOrderId", state.workOrderId);
    showToast("Agent 已启动，当前状态已同步。");
    render();
  } catch (error) {
    showToast(`启动失败：${error.message}`);
  }
}

async function loadState() {
  state.owner = $("ownerInput").value.trim() || "local-user";
  localStorage.setItem("dataagent.owner", state.owner);
  const workOrderId = state.workOrderId || prompt("输入 WorkOrder ID");
  if (!workOrderId) return;
  try {
    const payload = await request(`/api/work-orders/${workOrderId}/agent/state`);
    state.response = payload;
    state.workOrderId = payload.work_order_id;
    localStorage.setItem("dataagent.workOrderId", state.workOrderId);
    showToast("状态已载入。");
    render();
  } catch (error) {
    showToast(`载入失败：${error.message}`);
  }
}

async function resume(decision) {
  const payload = getPayload();
  if (!state.response || !state.workOrderId) {
    showToast("当前是演示数据，连接 API 后可执行审批。");
    return;
  }
  try {
    state.response = await request(`/api/work-orders/${payload.work_order_id}/agent/resume`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    });
    showToast("审批已提交。");
    render();
  } catch (error) {
    showToast(`审批失败：${error.message}`);
  }
}

function approvePipeline(pipelineId) {
  if (!pipelineId) {
    showToast("没有可选择的 Pipeline。");
    return;
  }
  resume({ approved: true, pipeline_id: pipelineId });
}

async function submitRun() {
  const payload = getPayload();
  if (!state.response || !payload.work_order_id) {
    showToast("当前是演示数据，连接 API 后可提交 Run。");
    return;
  }
  try {
    const run = await request(`/api/work-orders/${payload.work_order_id}/runs`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({}),
    });
    state.runId = run.run_id || run.id;
    showToast("全量 Run 已提交。");
    render();
  } catch (error) {
    showToast(`提交失败：${error.message}`);
  }
}

$("startButton").addEventListener("click", startAgent);
$("loadStateButton").addEventListener("click", loadState);
$("refreshButton").addEventListener("click", () => {
  if (state.workOrderId) loadState();
  else {
    showToast("当前显示演示工作台。");
    render();
  }
});
$("submitRunButton").addEventListener("click", submitRun);

render();
