# DataAgent 主 Agent Runtime 优化开发与 `yifu` 验收方案

> 文档状态：开发基线  
> 日期：2026-07-27  
> 主要设计依据：`docs/DataAgent-main-agent-runtime-refactor-plan-2026-07-27.md`  
> 缺陷与测试依据：`docs/DataAgent-agent-framework-audit-and-test-plan-2026-07-27.md`  
> 产品依据：`DataAgent-final-PRD-v1.2.md`  
> 首个 Golden Task：`D:\data\yifu`

---

## 0. 结论

本轮开发不做四个 Agent 的横向重写，也不等待数据库检索完成，而是以
`yifu` 为首个 Golden Task，打通一条能够真实验收的纵向闭环：

```text
用户需求
  ↓
Conversation Layer：保存消息、会话、HITL 与事件
  ↓
数据任务规划 Agent（主 Agent）：原子化约束、澄清、确认、任务计划
  ↓
检索 Agent：从当前 Operator Registry 和历史 Pipeline 中返回候选与充分性
  ↓
数据处理 Agent：选择候选、编排三类 Pipeline、校验、试跑、修复
  ↓
用户选择 Pipeline
  ↓
Engine：分阶段执行并产生逐资产 Evidence
  ↓
QC：按 Constraint 逐项验证
  ↓
主 Agent：观察结果，决定完成、返工、重新检索或请求人工处理
```

本轮成功不以“页面上出现三个 Pipeline”或“运行没有抛异常”为准，而以以下
结果同时成立为准：

1. 用户的每一条要求都被拆成无歧义、可追踪的原子 `Constraint`；
2. 每个硬约束都有确定的 Operator、参数绑定、执行节点和 Evidence；
3. Pipeline 不完整时不能被批准或执行；
4. 每张保留图片都有所有硬约束通过的证据；
5. 每张排除图片都有明确的约束编号、失败原因和证据；
6. `face_count < 2` 被严格执行为仅允许 `0` 或 `1`；
7. 精确重复图片全部去除，近似重复策略可解释、可复现；
8. 黑色衣服判断只使用视觉语义证据，不能让 VLM 代替尺寸、文件大小、
   宽高比或去重判断；
9. QC 不允许对缺失证据做“通过”推断；
10. 主 Agent 能根据验证或执行结果回到规划环节修复，而不是失败后直接结束。

因此，本轮最先修改的不是 UI，也不是增加更多 Agent 名称，而是：

> **Constraint Contract + 完整性门禁 + 主 Agent 决策循环。**

没有这三项，Agent Loop 只会更自主地生成错误结果；有了这三项，后续检索、
编排、执行和 QC 才有共同的事实基础。

---

## 1. 范围

### 1.1 本轮包含

- 将数据任务规划 Agent 提升为全局主 Agent；
- 将 Conversation 中的需求智能逐步迁移到主 Agent；
- 为四个 Agent 建立统一但受限的 Runtime；
- 建立 `ConstraintContract`、`TaskPlan`、`AgentDecision`、`Observation`；
- 保留当前检索 Agent 的 Operator Registry 和历史 Pipeline 检索；
- 让数据处理 Agent 基于检索结果选择、编排和修复 Pipeline；
- 生成质量优先、均衡、成本优先三类真实不同的 Pipeline；
- 建立 Constraint、Operator、Pipeline Node、Evidence、QC 的全链路映射；
- 补齐图片尺寸、宽高比、文件大小、人脸数量、黑色衣服和去重的执行证据；
- 建立 Pipeline 完整性、可执行性和生产发布门禁；
- 建立分阶段并行执行；
- 建立主 Agent 可观察、可重规划、可停止的 LangGraph 循环；
- 用 `D:\data\yifu` 完成真实 provider 端到端验收。

### 1.2 本轮不包含

- 不实现同事正在开发的数据库数据检索；
- 不重写数据检索 Adapter；
- 不把数据处理 Agent 改名；
- 不增加第五个自主 Supervisor Agent；
- 不让主 Agent 直接选择具体算子；
- 不让数据处理 Agent 直接搜索 Registry 或数据库；
- 不自动下载未经用户确认的外部模型；
- 不以大规模性能压测替代正确性验收；
- 不修改源图片；
- 不将内部原始思维链暴露给前端。

当用户已经明确给出 `D:\data\yifu` 时，本轮将其作为已确认的本地数据源，
直接写入 `TaskSpec`。这条路径不依赖未来的数据库检索。

### 1.3 与数据库检索的预留接口

检索 Agent 的正式职责仍保持不变：

- 当前：检索 Operator、历史 Pipeline、参数和成功/失败实验；
- 后续：检索数据、生成 `RetrievalPlan` 和 `CandidatePool`；
- 始终：只负责找候选、排序、说明证据和报告充分性；
- 始终：不决定最终 Pipeline。

本轮只冻结统一的 `RetrievalRequest` / `RetrievalResultBundle` Interface，
为同事后续接入数据候选保留 `data_candidates` 字段。当前该字段可为空，但
Operator 和历史 Pipeline 的结果不能通过该字段绕过。

---

## 2. 当前实现基线与必须解决的问题

### 2.1 当前已有基础

当前代码已经有：

- `AgentRuntime.start()`、`resume()`、`state()`；
- LangGraph checkpoint 与 HITL interrupt；
- Requirement、Retrieval、Processing、Strategy 四个子图；
- Operator Registry、Provider、Runtime 和参数校验；
- PipelineVersion、DatasetVersion、QCReport；
- Pipeline 历史经验检索；
- 内置图片分析、感知去重、VLM 和 Manifest；
- 受控 Tool Registry 与部分 Tool trace；
- Dataset Run、修复运行、交付导出和 Operation Lineage。

因此不需要推翻现有框架。需要改变的是决策权、状态模型、验证深度和循环方式。

### 2.2 当前核心结构问题

当前主图仍近似固定顺序：

```text
requirement
  → confirm
  → retrieval
  → processing
  → approve
  → strategy
  → end
```

四个子图也主要是一次生成加一次确定性校验。它们没有形成完整的：

```text
Goal → Decide → Tool/Action → Observation → Validate → Repair/Finish
```

同时，`ConversationService` 承担了过多的 Intent、TaskSpec Patch 和回复生成，
导致 Conversation 过深，专业 Agent 过浅。

### 2.3 `yifu` 失败的直接原因

以前的规划会把尺寸、宽高比、文件大小和人脸数量留在自然语言中，仅抽出少量
能力标签。检索层虽然可能找到正确算子，但覆盖检查只遍历了不完整的能力集合，
于是正确候选又被丢弃。

随后数据处理 Agent 可能生成：

```text
decode → dedup → VLM → semantic selection → manifest
```

这个 Pipeline 实际缺少四个明确的硬约束，却仍可能通过结构校验和 QC。问题
不是单个算子失效，而是系统缺少三个贯穿全链路的事实：

- 原子 Constraint；
- Constraint Coverage；
- Constraint Evidence。

### 2.4 本轮必须消除的“假成功”

以下任一情况都必须阻断 `production_eligible`：

- 自然语言要求没有完整转成原子 Constraint；
- Constraint 没有绑定 Operator；
- Operator 参数没有通过 Schema；
- Pipeline 缺少处理某个硬约束的节点；
- 节点运行后没有产生所需 Evidence；
- QC 没有逐项检查每张保留资产；
- `uncertain` 被自动当成 `pass`；
- Provider 不可用但 Pipeline 仍显示可执行；
- 用户确认页展示的内容与实际执行 Artifact 不一致。

---

## 3. 目标 Module 与 Interface

### 3.1 Module 划分

| Module | 对外 Interface | 隐藏的复杂性 |
|---|---|---|
| Conversation | `send/resume/subscribe` | 消息、session、HITL、事件投递 |
| Main Agent Runtime | `run_agent_turn` | 目标、计划、路由、观察、停止条件 |
| Specialist Agent Runtime | `run_agent_turn` | 受限 Tool 选择、局部循环、结果契约 |
| Constraint | `normalize/validate/map_evidence` | 比较符、单位、作用域、歧义、证据要求 |
| Retrieval | `retrieve(request)` | Operator/经验检索与充分性 |
| Processing | `propose/repair` | 候选选择、DAG、参数、三策略、试跑 |
| Pipeline Eligibility | `evaluate(artifact)` | 五类资格和阻断原因 |
| Execution | `submit/resume/result` | 阶段调度、barrier、逐资产结果、重试 |
| QC | `evaluate(dataset, task_spec)` | 完整性、正确性、证据和发布判定 |

这里刻意不建立一个拥有大量 `plan/think/act/reflect/finish` 子类方法的宽
`BaseAgent`。统一 Runtime 只暴露一个深 Interface：

```python
run_agent_turn(context: AgentContext) -> AgentDecision
```

各 Agent 的差异由 Goal、允许的 Action、允许的 Tools、Validator 和停止条件
定义，而不是由大量继承方法定义。

### 3.2 Conversation 的职责

Conversation 只负责：

- history；
- session/thread；
- HITL 请求与恢复；
- runtime event 转发；
- 用户消息和正式输出的持久化；
- 取消、暂停、恢复等会话级控制。

Conversation 不负责：

- 判断 TaskSpec 是否完整；
- 生成最终 Constraint；
- 决定调用哪个专业 Agent；
- 选择算子；
- 生成 Pipeline；
- 判断任务是否完成。

迁移期间允许保留兼容 Adapter，但 Adapter 只能把旧的
`conversation_turn()` 结果转成主 Agent 输入，不能继续作为权威决策源。

### 3.3 数据任务规划 Agent（主 Agent）

主 Agent 负责“整个任务下一步做什么”，包括：

- 理解目标和数据源；
- 将需求拆成原子 Constraint；
- 识别歧义和缺失信息；
- 生成供用户确认的完整需求摘要；
- 维护 `TaskPlan`；
- 决定调用检索、处理或策略 Agent；
- 读取专业 Agent、Validator、Engine 和 QC 的 Observation；
- 判断继续、返工、请求人工、等待确认或完成；
- 维护全局进度和完成条件。

主 Agent 不负责：

- 从 Registry 选择具体 Operator；
- 决定历史 Pipeline 的具体复用方式；
- 编译 Pipeline；
- 直接执行数据算子；
- 自己计算 QC。

### 3.4 检索 Agent

本轮输入：

- `RetrievalRequest(kind=operator|pipeline_experience)`；
- 已确认的 Constraint；
- Capability Gap；
- 运行环境和权限。

本轮输出：

- `operator_candidates`；
- `pipeline_experience_matches`；
- `coverage_hints`；
- `sufficiency_report`；
- 若不足则输出明确 `RetrievalGap`。

后续同事的数据检索通过同一 `RetrievalResultBundle.data_candidates` 合流。

检索 Agent 不能：

- 选定最终 Pipeline；
- 私自放宽 Constraint；
- 把相似度当成硬约束覆盖；
- 自动批准外部模型下载。

### 3.5 数据处理 Agent

名称保留为“数据处理 Agent”。

它负责：

- 使用检索 Agent 返回的算子候选和历史 Pipeline；
- 为每个 Constraint 选择可执行算子；
- 绑定并验证参数；
- 编排清洗、过滤、分类、去重、标注、加工节点；
- 生成质量优先、均衡、成本优先三个方案；
- 运行静态校验、模拟或小样本试跑；
- 根据误删、漏删、运行失败和 Evidence Gap 修复方案；
- 向主 Agent返回 `PipelineProposalSet` 或 `RetrievalGapRequest`。

它不能绕过检索 Agent 直接访问 Registry 或历史经验库。

### 3.6 数据策略 Agent

数据策略 Agent 负责采样、分布、配额、平衡和成本预算。

`yifu` 任务是“对指定目录全量筛选”，没有采样或分布目标，因此本轮允许其
返回确定性的：

```json
{
  "mode": "full_scan",
  "sample_ratio": 1.0,
  "reason": "No sampling or balancing requirement was specified."
}
```

主 Agent 可以在 Pipeline 执行前并行获得该结果，但不能让一个无业务必要的
策略循环阻塞 `yifu`。

---

## 4. 统一 Agent Runtime

### 4.1 AgentContext

建议新增统一上下文，至少包含：

```text
work_order_id
thread_id
owner_id
agent_name
goal
confirmed_task_spec
task_plan
available_actions
available_tools
observations
artifacts
attempt
budgets
permissions
pending_human_request
```

上下文只放 Agent 作决定所需的信息。大文件内容、完整数据集和敏感凭据不能
直接进入模型上下文，应通过 Artifact 引用和受控 Tool 获取。

### 4.2 AgentDecision

每轮只允许返回一个结构化决定：

```text
decision_id
agent_name
reason_summary
action
action_input
plan_patch
expected_observation
completion_claim
```

其中 `action` 必须来自该 Agent 的允许集合。主 Agent 的任务级 Action 与专业
Agent 的 Tool Action 分开建模，避免把“调用检索 Agent”和“调用人脸算子”
混为同一种工具选择。

主 Agent允许：

```text
ASK_USER
REQUEST_TASK_CONFIRMATION
CALL_RETRIEVAL_AGENT
CALL_PROCESSING_AGENT
CALL_STRATEGY_AGENT
REQUEST_PIPELINE_APPROVAL
SUBMIT_DATASET_RUN
REQUEST_REPAIR
FINISH
TERMINATE
```

专业 Agent允许：

```text
CALL_TOOL
RETURN_RESULT
RETURN_GAP
REQUEST_HUMAN_APPROVAL
```

### 4.3 Observation

Tool、专业 Agent、Engine 和 QC 的输出统一转成 Observation：

```text
observation_id
source_type
source_name
status
summary
artifact_refs
evidence_refs
violations
retryable
created_at
```

模型只负责解释 Observation 并决定下一步，不能修改已经产生的 Evidence。

### 4.4 Validator

每次 AgentDecision 后先走确定性 Validator：

- action 是否属于该 Agent；
- Tool 是否属于该 Agent；
- 输入 Schema 是否合法；
- 是否需要确认；
- 是否超出权限；
- 是否超过循环、时间、成本或重试预算；
- `completion_claim` 是否满足确定性完成门禁。

Validator 是 Module，不是第五个 Agent。

### 4.5 循环限制

默认限制建议：

| 循环 | 默认上限 | 超限处理 |
|---|---:|---|
| 需求澄清 | 5 轮 | 请求用户明确选择或终止 |
| 主 Agent 全局重规划 | 8 轮 | 输出阻断原因和现有 Artifact |
| 检索修正 | 3 轮 | 返回 `RetrievalGap` |
| Pipeline 生成/修复 | 4 轮 | 返回不可满足项 |
| Strategy 修正 | 3 轮 | 回退到全量或请求用户 |
| 资产自动重试 | 3 次 | 形成 Abandoned Asset |

停止由规则决定，不由模型一句“已完成”决定。

---

## 5. Tool Governance

### 5.1 每个 Agent 是否需要 Tools

每个真正做自主判断的 Agent 都应有自己的受控 Tools，但不要求数量相同，也
不要求所有步骤都调用 Tool。

| Agent | 本轮必须可用的 Tools |
|---|---|
| 主 Agent | `parse_constraints`、`validate_task_spec`、`inspect_task_status`、`request_human_input`、`read_observation` |
| 检索 Agent | `retrieve_operators`、`inspect_operator_schema`、`retrieve_pipeline_experiences`、`assess_candidate_sufficiency` |
| 数据处理 Agent | `inspect_candidate_bundle`、`compile_pipeline_artifact`、`validate_pipeline_artifact`、`estimate_pipeline_cost`、`run_pipeline_trial`、`compare_pipeline_trials` |
| 数据策略 Agent | `inspect_dataset_profile`、`generate_sampling_plan`、`validate_sampling_plan`、`estimate_sampling_cost` |

Conversation、Engine、Validator、Evaluator、Loop Supervisor 和 Control Plane
不是 Agent，不需要 LLM Tool Loop。

### 5.2 ToolContract

每个 Tool 必须声明：

- 名称和版本；
- 输入、输出 Schema；
- 允许调用的 Agent；
- 读写权限；
- 副作用等级；
- 是否需要人工确认；
- 幂等键；
- timeout、retry 和 cost；
- 前置条件和后置条件；
- 可产生的 Evidence 类型；
- `started/progress/completed/failed` 事件；
- 脱敏规则。

现有 `dataagent/tools/registry.py`、`spec.py`、`loop.py` 应作为演进起点，不再
新建第二套 Tool 系统。当前 Tool trace 需要从“单次完成后补一条记录”提升为
真实生命周期事件。

### 5.3 权限矩阵

| 能力 | 主 Agent | 检索 | 处理 | 策略 |
|---|---:|---:|---:|---:|
| 修改 TaskSpec 草案 | 是 | 否 | 否 | 否 |
| 读取 Registry | 否 | 是 | 否 | 否 |
| 读取历史 Pipeline | 否 | 是 | 否 | 否 |
| 选择具体 Operator | 否 | 提供候选 | 是 | 否 |
| 编译 Pipeline | 否 | 否 | 是 | 否 |
| 决定采样 | 否 | 否 | 否 | 是 |
| 提交正式 Run | 发起任务级 Action | 否 | 否 | 否 |
| 直接执行任意 shell/python | 否 | 否 | 否 | 否 |
| 下载外部模型 | 仅请求用户确认 | 仅提出候选 | 否 | 否 |

---

## 6. `yifu` 冻结需求契约

### 6.1 用户原始需求

对 `D:\data\yifu` 中的图片执行筛选：

1. 图片宽高均不少于 64 像素；
2. 宽高比在 0.3 到 3.5 之间；
3. 文件大小在 1KB 到 20MB 之间；
4. 图片中的人脸数量少于 2 个；
5. 主体穿黑色衣服；
6. 去除重复图片。

### 6.2 原子 Constraint

原始六句话应规范化成至少八个执行约束：

| ID | Scope | Canonical 表达 | 边界 |
|---|---|---|---|
| C01 | asset | `width_px >= 64` | 包含 64 |
| C02 | asset | `height_px >= 64` | 包含 64 |
| C03 | asset | `aspect_ratio >= 0.3` | 包含 0.3 |
| C04 | asset | `aspect_ratio <= 3.5` | 包含 3.5 |
| C05 | asset | `file_size_bytes >= 1024` | 1KB = 1024 bytes |
| C06 | asset | `file_size_bytes <= 20971520` | 20MB = 20×1024×1024 |
| C07 | asset | `face_count < 2` | 仅 0 或 1 通过 |
| C08 | asset | `primary_subject_garment_color == black` | 见语义口径 |
| C09 | dataset | `exact_duplicate_count == 0` | 强制 |
| C10 | dataset | `perceptual_duplicate_policy_applied == true` | 阈值随方案记录 |
| C11 | dataset | `source_assets_immutable == true` | 强制 |

特别注意：

> 本次需求是“少于 2 个”，不是之前测试中的“0 到 2 个”。任何将
> `face_count=2` 判定为通过的实现都必须失败。

Canonical Constraint 保存原始比较符：

```json
{
  "field": "face_count",
  "operator": "lt",
  "value": 2,
  "value_type": "integer"
}
```

如果某个 Provider 只支持包含式 `max_face_count`，Adapter 可以绑定为
`max_face_count=1`，但必须在参数绑定记录中保存从 `< 2` 到 `<= 1` 的等价
转换，不能在 TaskSpec 中提前丢失比较符。

### 6.3 黑色衣服的冻结口径

本轮 Golden Task 使用以下定义：

- 主体是画面中最显著、面积最大或构图最中心的人；
- 判断对象是该主体可见的主要衣物，不是头发、鞋、配饰或背景；
- 主要可见衣物在视觉上以黑色为主时判定 `match`；
- 仅有少量黑色图案、黑色配饰或背景不能判定 `match`；
- 多人且无法确定唯一主体、衣物不可见、光照严重失真或颜色混合无法判断时，
  返回 `uncertain`；
- `uncertain` 进入 ReviewSet，不得静默保留到 Deliverable Dataset Export；
- 允许 0 张可见人脸，只要存在可判断衣着的主体，例如背身人物。

VLM Prompt 只包含 C08 的视觉语义定义和输出 Schema，不包含文件大小、像素、
宽高比、去重等非视觉语义约束。

### 6.4 去重口径

- 精确重复：按源文件 SHA-256 分组，必须全部识别；
- 每组保留一个 Canonical Asset；
- Canonical Asset 默认按输入序号最小，其次按规范化路径字典序选择；
- 感知重复：按所选 Pipeline 的距离阈值执行；
- 三个方案可以使用不同的感知阈值，但必须都消除精确重复；
- 每个被去重资产必须记录 `duplicate_group_id`、`canonical_asset_id`、
  `distance/hash` 和策略版本；
- 去重只能影响逻辑保留结果，不能删除或改写源文件。

### 6.5 单资产最终判定

保留一张图片的必要条件：

```text
C01 ∧ C02 ∧ C03 ∧ C04 ∧ C05 ∧ C06 ∧ C07 ∧ C08
∧ 非重复组中的被移除成员
```

语义 `uncertain` 不是通过。它应进入 ReviewSet，待人工完成后产生新的
Evidence，再决定保留或排除。

---

## 7. Constraint、Coverage 与 Evidence

### 7.1 ConstraintContract

建议为每个 Constraint 保存：

```text
id
source_text
scope
subject
field
operator
value
unit
hardness
ambiguity_status
clarification_ref
required_evidence_type
failure_policy
```

`TaskSpecVersion.hard_constraints: dict` 可在兼容期保留，但它不再是新流程的
唯一权威。新增结构化 `constraints` 后，旧字段只能由 Adapter 生成。

### 7.2 Constraint Coverage

每个 Pipeline 必须输出覆盖矩阵：

| Constraint | Candidate | Selected Operator | Node | 参数 | Evidence | 状态 |
|---|---|---|---|---|---|---|
| C01/C02 | image shape | image shape filter | N02 | min=64 | width/height | covered |
| C03/C04 | aspect | aspect ratio filter | N03 | 0.3..3.5 | ratio | covered |
| C05/C06 | size | image size filter | N04 | 1KB..20MB | bytes | covered |
| C07 | face | face count filter | N05 | `<2` | face_count | covered |
| C08 | VLM | remote VLM + policy | N07/N08 | frozen prompt | semantic result | covered |
| C09/C10 | dedup | exact/perceptual dedup | N06 | strategy-specific | duplicate group | covered |
| C11 | Engine | source preservation | run gate | immutable | before/after hash | covered |

相似度命中、名称命中或 LLM 声称“可以处理”都不是 Coverage。只有满足以下
条件才是 `covered`：

1. Operator 能力与 Constraint 类型匹配；
2. 参数可以无损绑定；
3. Provider 和 Runtime 可用；
4. Pipeline 中存在该节点；
5. 节点声明会产生所需 Evidence；
6. Evidence 能被 QC 消费。

### 7.3 Eligibility 五个 Facet

统一资格模型：

```text
artifact_valid
operator_resolvable
constraint_complete
evidence_complete
runtime_eligible
```

只有五者全部为真，Pipeline 才能：

```text
production_eligible = true
```

静态生成阶段尚未产生运行 Evidence 时，`evidence_complete` 表示：

- 每个 Constraint 已声明所需 Evidence；
- 每个节点声明可产生对应 Evidence；
- QC 有对应检查器。

运行完成后的发布阶段，`evidence_complete` 表示实际 Evidence 已存在且可验证。
两个阶段不得使用同一个模糊布尔值。

---

## 8. `yifu` 目标 Pipeline

### 8.1 推荐阶段划分

```text
Stage A：资产发现
  enumerate local assets

Stage B：并行解码与元数据
  decode
  width / height
  aspect ratio
  file size bytes
  sha256 / perceptual hash

Stage C：并行便宜硬过滤
  dimensions
  aspect ratio
  file size

Stage D：人脸检测与过滤
  face detection
  face_count < 2

Stage E：数据集去重 barrier
  exact SHA dedup
  perceptual dedup

Stage F：并行视觉语义
  VLM black-clothing judgment
  semantic policy

Stage G：数据集发布 barrier
  manifest
  Evidence completeness
  QC
```

先执行便宜的硬过滤和人脸过滤，再调用昂贵 VLM；先去重再调用 VLM，可以避免
对重复图片重复付费。数据集级去重和发布是 barrier，不能伪装成逐资产并行。

### 8.2 三类方案必须真实不同

三个方案都必须满足全部硬约束，差异只能发生在软策略：

| 方案 | 允许差异 |
|---|---|
| 质量优先 | 更保守的语义阈值、更强的 ReviewSet、较积极的近似重复识别 |
| 均衡 | 中等语义阈值、标准 ReviewSet、平衡近似重复阈值 |
| 成本优先 | 更严格的前置便宜过滤、更小的 VLM 调用量、较保守的近似重复识别 |

禁止用固定标题包装相同 DAG。每个方案必须保存：

- 具体节点和参数差异；
- 预估调用次数、成本和耗时；
- 小样本结果；
- 误删、漏删和 `uncertain` 数；
- 选择理由和风险。

---

## 9. LangGraph 改造

### 9.1 目标主图

```text
START
  ↓
main_agent_observe
  ↓
main_agent_decide
  ↓
validate_decision
  ├─ ask_user / confirm_task ── interrupt ──→ main_agent_observe
  ├─ retrieval_subgraph ───────────────────→ record_observation
  ├─ processing_subgraph ──────────────────→ record_observation
  ├─ strategy_subgraph ────────────────────→ record_observation
  ├─ approve_pipeline ──────── interrupt ──→ main_agent_observe
  ├─ submit_run ───────────────────────────→ monitor_run
  ├─ repair ───────────────────────────────→ record_observation
  ├─ finish ───────────────────────────────→ END
  └─ terminate ────────────────────────────→ END

record_observation / monitor_run
  ↓
deterministic_completion_gate
  ↓
main_agent_observe
```

LangGraph 负责状态、路由、checkpoint、interrupt 和恢复；下一步业务 Action
由主 Agent 决定，但必须经过 Validator。

### 9.2 专业 Agent 子图

统一形态：

```text
observe
  → decide
  → validate_decision
      ├─ call_tool → record_observation → observe
      ├─ return_gap
      └─ return_result
```

子图不是固定两节点，也不是无限循环。每个子图有自己的预算和完成条件。

### 9.3 WorkOrderGraphState

建议从当前扁平字典演进为以下稳定分区：

```text
identity
conversation
task
plan
retrieval
processing
strategy
execution
evaluation
control
observations
artifacts
events
```

兼容期可以保留旧 key，但写入权必须唯一。例如 `task_spec` 只能由主 Agent
相关 Module 更新，数据处理 Agent 只能读取。

---

## 10. 按测试驱动的纵向开发切片

### 10.1 与审计缺陷的对应关系

| 审计缺陷 | 本文处理位置 | 关闭条件 |
|---|---|---|
| P0-1 Constraint Contract 缺失 | Slice 1 | C01-C11 均为结构化、带边界的 Constraint |
| P0-2 关键词规则拥有规划权 | Slice 1、3 | 关键词只用于召回，不再决定最终能力和参数 |
| P0-3 Coverage 假完整 | Slice 0、3、4 | Coverage 必须落到候选、节点、参数和 Evidence |
| P0-4 Validator 只校验结构 | Slice 0、4、6 | 五个 eligibility facet 全部通过才能生产 |
| P0-5 VLM Prompt 污染 | Slice 4、5 | VLM 只接收 C08 视觉语义约束 |
| P0-6 QC 只检查已有字段 | Slice 5、6 | QC 按 TaskSpec 要求 Evidence，缺失即失败 |
| P0-7 确认页与执行对象错位 | Slice 1、2 | 确认、批准和执行引用同一版本 Artifact |
| P0-8 语义歧义未澄清 | Slice 1、8 | 冻结口径；未决项进入 ReviewSet |
| Agent 无真实循环 | Slice 2、4 | 存在 Observation 驱动的回环和停止条件 |
| 进度是事后拼接 | Slice 7 | 真实 started/progress/completed/failed 事件 |

后续切片编号保持开发执行编号，不因本追踪表调整。

每个切片遵循：

```text
先增加一个失败的行为测试
  → 运行并确认因目标缺陷失败
  → 实现最小纵向能力
  → 通过该测试和全部回归
  → 再开始下一切片
```

禁止先为所有新类批量写单元测试，再一次性实现整个框架。测试应通过稳定
Interface 验证行为，不锁死私有节点、Prompt 文案或内部调用次数。

### Slice 0：冻结假成功

新增失败测试：

- 输入完整 `yifu` 需求；
- 当前 Pipeline 缺少尺寸、宽高比、文件大小或人脸任一约束；
- 断言 `production_eligible=false`；
- 断言不能批准、不能提交正式 Run；
- 断言输出具体缺失 Constraint ID。

最小实现：

- 增加 `constraint_complete` 门禁；
- 未建立新 Constraint 模型前可用测试 Fixture 临时注入完整 Constraint；
- 不尝试在本切片修好全部 Pipeline。

完成门禁：

- 错误 Pipeline 不再假成功；
- 所有旧测试通过。

### Slice 1：ConstraintContract 与需求确认

新增失败测试：

- 中文原始需求解析得到 C01-C11；
- 64、0.3、3.5、1KB、20MB 的包含关系正确；
- “少于 2 个”保留 `lt 2`；
- 变形表达、顺序变化和标点变化得到同一 Canonical 结果；
- 主 Agent 在黑色衣服口径缺失时能提出必要澄清；
- 用户确认页展示 Canonical Constraint，而不是重新生成的自然语言摘要。

最小实现：

- 新增 `ConstraintContract`；
- 新增 Constraint normalize/validate Tool；
- 让数据任务规划 Agent成为 TaskSpec 唯一智能写入者；
- Conversation 只传递消息和 HITL 状态；
- 保存确认版本和 confirmation ref。

完成门禁：

- 解析召回率 100%；
- 严格边界测试 100%；
- 确认内容与持久化 TaskSpec 同一版本。

### Slice 2：主 Agent 决策循环

新增失败测试：

- TaskSpec 未确认时主 Agent只能澄清或请求确认；
- 确认后主 Agent选择检索 Agent；
- 检索不足时不会直接进入 Pipeline 批准；
- 收到 Pipeline Validation violation 后会请求数据处理 Agent修复；
- 全局门禁全部通过后才允许 `FINISH`。

最小实现：

- 新增 `AgentContext`、`AgentDecision`、`Observation`；
- 将 `main_graph.py` 改为主 Agent hub；
- 增加决定 Validator 和循环预算；
- 复用现有 checkpoint 与 interrupt。

完成门禁：

- 主图存在真实回环；
- 同一 WorkOrder 可中断恢复；
- 模型不能通过文本声明绕过完成门禁。

### Slice 3：检索结果契约

本轮不改数据库检索算法，只改 Interface。

新增失败测试：

- C01-C10 能从当前 Registry 得到候选；
- 候选结果包含能力、参数 Schema、Runtime、Provider、状态和证据类型；
- 历史 Pipeline 单独放在 `pipeline_experience_matches`；
- 检索不足返回 Constraint 级 Gap；
- `data_candidates` 为空不影响本地目录任务；
- 数据处理 Agent输入只来自 `RetrievalResultBundle`。

最小实现：

- 建立统一 `RetrievalRequest` 和 `RetrievalResultBundle`；
- 为现有 Operator 和经验检索增加 Adapter；
- 移除数据处理 Agent对 Registry/经验库的直接读取；
- 暂不实现数据库 Adapter。

完成门禁：

- 候选充分性按 Constraint 计算；
- 不再按贫化后的关键词能力列表计算；
- 本地任务不被未完成的数据检索阻塞。

### Slice 4：数据处理 Agent Loop

新增失败测试：

- 根据 C01-C10 编译完整 DAG；
- `<2` 正确绑定到 face Operator；
- 非视觉约束不会进入 VLM Prompt；
- 缺候选时返回 `RetrievalGapRequest`；
- 参数校验失败后修复并再次验证；
- 三类方案硬约束相同、软参数真实不同；
- 三个方案都有独立 PipelineArtifact 文件。

最小实现：

- 将 Processing 子图改成 observe/decide/tool/validate/repair；
- 复用现有 Pipeline compile/validate Tools；
- 增加 Constraint-to-node coverage；
- 增加三方案比较 Artifact。

完成门禁：

- 参数准确率 100%；
- Constraint 静态覆盖 100%；
- 任何缺失项都不能进入批准页。

### Slice 5：执行 Evidence

新增失败测试：

- `analyze_image` 产生 `file_size_bytes`；
- width、height、aspect_ratio、sha256、dhash 有类型和单位；
- face Provider 结果被规范化为 `face_count`；
- face_count 0/1 通过，2/3 失败；
- VLM 只产生 C08 证据；
- exact/perceptual duplicate 产生 group 和 canonical ref；
- 源文件执行前后 SHA 不变；
- 每个排除结果包含 Constraint ID 和 Evidence ref。

最小实现：

- 扩展图片元数据；
- 为 Provider output 增加统一 Adapter；
- 建立 `ConstraintEvidence` 和逐资产 Decision；
- 对旧 Label/Metric 提供读取兼容；
- 去重结果保存分组证据。

完成门禁：

- Evidence 类型和单位稳定；
- 所有 hard reject 可追溯；
- 缺失 Evidence 不得被当成 pass。

### Slice 6：QC 与发布门禁

新增失败测试：

- QC 遍历所有保留资产和所有硬约束；
- 缺一项 Evidence 即失败；
- 空结果不自动代表 hard rule violation 为 0；
- `uncertain` 未人工解决时不得发布；
- 两张人脸的图片不得进入交付；
- exact duplicate 不得同时进入交付；
- DatasetVersion 为 PARTIAL 时不得导出为正式交付。

最小实现：

- 将 QC 从“检查已有字段”改为“按 TaskSpec 要求字段”；
- 增加 Evidence completeness matrix；
- 拆分运行 QC 和发布 QC；
- 复用 DatasetVersion、RepairedDatasetVersion、Deliverable Dataset Export。

完成门禁：

- Evidence 完整率 100%；
- 发布错误通过数为 0；
- QC reason code 可定位到 Constraint 和资产。

### Slice 7：分阶段调度与事件

新增失败测试：

- 逐资产安全节点可以并行；
- dataset-level 去重形成 barrier；
- VLM 不会处理已被便宜硬约束排除或去重的资产；
- Manifest/QC 在所有上游节点完成后执行；
- Tool/Agent 产生 started、progress、completed、failed；
- 前端正式输出与活动事件分离。

最小实现：

- 为 Pipeline Node 声明 `execution_scope`、`parallel_safe`、`barrier_group`；
- Dataset Runner 按 Stage 调度；
- 事件持久化后流式投递；
- UI 可以先展示 `reason_summary`、计划和状态，不展示原始思维链。

完成门禁：

- 结果与串行基线一致；
- VLM 调用量不高于进入 Stage F 的唯一资产数；
- 长时间运行时持续有真实事件。

### Slice 8：真实 `yifu` 端到端

运行真实 provider，不使用 Mock 代替：

1. 建立输入清单、文件哈希和人工 Golden 标注；
2. 提交原始中文需求；
3. 检查主 Agent的 Constraint 和澄清；
4. 确认 TaskSpec；
5. 检查检索候选和充分性；
6. 检查三个 PipelineArtifact；
7. 选择一个方案；
8. 完整运行；
9. 完成人工 ReviewSet；
10. 检查 DatasetVersion、QCReport、Manifest 和 Delivery Lineage；
11. 使用独立验收脚本重新计算尺寸、比例、大小、SHA 重复；
12. 对人脸和黑色衣服用 Golden Set 对照。

此切片通过后，才能声称本轮开发完成。

---

## 11. 文件级改造地图

以下是计划，不表示必须一次性全部修改。

### 11.1 Domain

| 路径 | 计划 |
|---|---|
| `dataagent/domain/specs/models.py` | 增加结构化 Constraint、歧义、Evidence 要求；保留旧字段 Adapter |
| `dataagent/domain/plans/models.py` | 增加 TaskPlan、PlanStep、状态和依赖 |
| `dataagent/domain/pipelines/models.py` | 增加 Coverage、Stage、Evidence declaration、资格 facet |
| `dataagent/domain/evaluations/models.py` | 增加 Constraint 级 QC 和 Evidence completeness |
| `dataagent/domain/runs/models.py` | 增加逐资产 ConstraintDecision 和 Evidence ref |

### 11.2 Agent Runtime

| 路径 | 计划 |
|---|---|
| `dataagent/agents/shared/state.py` | 增加 AgentContext、Decision、Observation、TaskPlan 分区 |
| `dataagent/graph/main_graph.py` | 从固定 DAG 改成主 Agent hub 与回环 |
| `dataagent/agents/requirement/*` | 演进为数据任务规划主 Agent；保留包名兼容或后续迁移 |
| `dataagent/agents/retrieval/*` | 使用统一 RetrievalResultBundle |
| `dataagent/agents/processing/*` | 增加真实 Tool Loop、修复和 Gap 返回 |
| `dataagent/agents/strategy/*` | 增加全量策略快速完成路径 |
| `dataagent/application/agent_runtime.py` | 暴露稳定 start/resume/state；收敛资格和提交门禁 |
| `dataagent/application/conversation.py` | 移出需求权威决策，只保留会话职责 |

是否将 Python 包 `requirement` 改名不应阻塞功能。产品名称立即统一为“数据任务
规划 Agent（主 Agent）”，代码路径可在兼容 Adapter 稳定后另行迁移，避免
一次重命名扩大变更面。

### 11.3 Tools 与 Pipeline

| 路径 | 计划 |
|---|---|
| `dataagent/tools/spec.py` | 完整 ToolContract |
| `dataagent/tools/registry.py` | Agent 权限、版本、策略校验 |
| `dataagent/tools/loop.py` | 生命周期事件和真实 Observation |
| `dataagent/tools/planning.py` | Constraint 解析、验证和状态读取 |
| `dataagent/tools/artifacts.py` | Coverage、Stage、Evidence 和资格验证 |
| `dataagent/operators/planning.py` | 移除关键词作为权威能力分解 |
| `dataagent/operators/catalog_matching.py` | 只做候选检索/参数建议，不决定覆盖 |
| `dataagent/operators/validation.py` | 增加 Constraint 与 Evidence 语义校验 |

### 11.4 Execution 与 QC

| 路径 | 计划 |
|---|---|
| `dataagent/imaging.py` | 增加文件字节数等规范化元数据 |
| `dataagent/operators/builtin/image.py` | 规范化元数据和去重 Evidence |
| `dataagent/operators/providers/output_adapters.py` | 规范化 face count 等 Provider 输出 |
| `dataagent/execution/dataset_runner.py` | Stage-aware 调度和 barrier |
| `dataagent/evaluation/quality.py` | 按 Constraint 驱动 QC |
| `dataagent/application/run_worker.py` | 事件、Evidence 持久化和失败恢复 |

### 11.5 Tests

| 层 | 目标路径 |
|---|---|
| Domain | `tests/unit/test_constraint_contract.py` |
| Requirement metamorphic | `tests/unit/test_requirement_constraints.py` |
| Retrieval/Coverage | `tests/integration/test_constraint_coverage.py` |
| Processing | `tests/integration/test_processing_agent_loop.py` |
| Validator negative | `tests/unit/test_pipeline_eligibility.py` |
| Evidence/QC | `tests/unit/test_constraint_evidence.py`、`test_quality_evaluator.py` |
| Execution | `tests/integration/test_parallel_dataset_execution.py` |
| Main Agent | `tests/integration/test_main_agent_runtime.py` |
| Golden Task | `tests/acceptance/test_yifu_golden_task.py` |

---

## 12. 冻结的测试 Seam

为了避免测试绑定实现细节，本轮行为测试只通过以下稳定 Interface：

1. `AgentRuntime.start()`；
2. `AgentRuntime.resume()`；
3. `AgentRuntime.state()`；
4. LangGraph 的公开 invoke/resume 结果；
5. Tool Registry 的公开 `execute()`；
6. Pipeline Artifact 的公开 validate/eligibility Interface；
7. Dataset Run 的 submit/status/result Interface；
8. QCReport 的公开读取 Interface；
9. Conversation 的发送、订阅事件和 HITL 恢复 Interface；
10. 最终 Manifest、DatasetVersion 和 Deliverable Dataset Export。

不测试：

- 私有 helper 调用次数；
- LangGraph 内部节点的具体数量；
- Prompt 的完整字符串；
- 模型的原始思维链；
- 某个类是否继承特定 BaseAgent；
- 仅为测试暴露的生产方法。

---

## 13. 测试矩阵

### 13.1 L0：Domain 与 Schema

- Constraint 比较符、单位和边界；
- `<2` 与 `<=2` 不等价；
- Pipeline Stage 与 DAG 合法性；
- Tool 权限和副作用；
- Evidence 类型；
- Artifact checksum 和版本。

### 13.2 L1：需求变形测试

以下表达应得到相同 Canonical Constraint：

- “宽高均不少于64”；
- “宽和高都要 >= 64px”；
- “最小宽度64，最小高度64”。

以下表达必须保持差异：

- “人脸少于2个” → `<2`；
- “人脸不超过2个” → `<=2`；
- “人脸0到2个” → `[0,2]`。

### 13.3 L2：检索与 Coverage

- 每个 Constraint 至少一个候选；
- Provider 不可用时不能 `covered`；
- 参数 Schema 不支持严格边界时返回 Gap 或 Adapter；
- 历史 Pipeline 只能作为经验候选；
- 数据候选为空不阻断显式本地目录。

### 13.4 L3：Validator 负向测试

分别删除尺寸、比例、大小、人脸、语义或去重节点，Pipeline 必须失败；分别删除
Evidence declaration，也必须失败。不能只测“完整 Pipeline 能通过”。

### 13.5 L4：Executor

- 边界图片；
- 损坏图片；
- 两张人脸；
- 文件恰好 1KB/20MB；
- 比例恰好 0.3/3.5；
- exact duplicate；
- perceptual duplicate；
- Provider timeout/retry；
- barrier 与并行结果一致。

### 13.6 L5：QC 与交付

- 保留集逐 Constraint 复核；
- 排除集 reason/evidence 完整；
- ReviewSet 未清空时阻断；
- PARTIAL 不得伪装正式交付；
- Operation Lineage 和 Delivery Lineage 可追溯；
- 源文件哈希不变。

### 13.7 L6：Conversation 与 UI

- 澄清、确认、Pipeline 选择、运行审批；
- 计划/活动事件/正式输出三层分离；
- 页面刷新后状态恢复；
- 旧 confirmation 不能批准新版本 Artifact；
- 错误和阻断原因对用户可理解。

### 13.8 L7：真实 Provider 与 Golden Task

- 不使用 Mock VLM；
- 不使用人工伪造 face_count；
- 独立验收脚本不复用生产过滤结果；
- Golden Set 与系统结果逐资产对照；
- 保存模型、Prompt、Provider 和参数版本。

---

## 14. `yifu` Golden Set 与验收指标

### 14.1 验收前准备

建立不可由生产 Pipeline 改写的 Golden Manifest：

```text
asset_id
relative_path
source_sha256
width
height
aspect_ratio
file_size_bytes
face_count
black_clothing_label
duplicate_group_id
reviewer
reviewed_at
notes
```

其中尺寸、比例、文件大小和 SHA 由独立脚本计算；人脸数量和黑色衣服由人工
复核，必要时双人仲裁。Golden Set 不从系统预测结果反向生成。

### 14.2 必须为 100% 的指标

| 指标 | 要求 |
|---|---:|
| Requirement Constraint recall | 100% |
| Constraint static coverage | 100% |
| Operator parameter boundary accuracy | 100% |
| Kept asset Evidence completeness | 100% |
| Rejected asset reason/evidence completeness | 100% |
| Width/height/aspect/file-size correctness | 100% |
| `face_count < 2` boundary correctness | 100% |
| Exact duplicate recall | 100% |
| Source hash unchanged | 100% |
| Unresolved ambiguity in Deliverable Dataset | 0 |
| Missing-Evidence publication | 0 |
| False production eligibility | 0 |

### 14.3 语义指标

黑色衣服不能在没有 Golden Set 的情况下临时发明一个看似好看的阈值。验收时
至少报告：

- precision；
- recall；
- false positive；
- false negative；
- uncertain rate；
- ReviewSet 人工修正数；
- 模型、Prompt 和阈值版本。

如果样本量太小，不用单个百分比掩盖问题，应逐资产列出混淆矩阵和错误案例。
交付前所有 `uncertain` 必须得到人工结论。

### 14.4 输出 Artifact

一次成功验收必须保留：

- confirmed TaskSpec；
- ConstraintContract；
- TaskPlan；
- RetrievalResultBundle；
- 三个 PipelineArtifact；
- Pipeline 对比报告；
- 用户选择记录；
- RunSnapshot 和事件；
- 每资产 Evidence；
- duplicate group；
- ReviewSet 和人工决策；
- DatasetVersion；
- QCReport；
- Manifest；
- Deliverable Dataset Export 或明确的可交付引用；
- Operation Lineage 和 Delivery Lineage。

---

## 15. 完成定义

本轮只有在以下条件全部满足时才能关闭：

### 架构

- Conversation 不再拥有需求规划的最终决策权；
- 数据任务规划 Agent 是主 Agent；
- LangGraph 由主 Agent决定的 Action 驱动；
- 四个 Agent 都有 Goal、受限 Tools、Observation、Validator、循环和停止条件；
- 不存在第五个自主 Supervisor；
- 检索与数据处理职责没有重新混合。

### 正确性

- `yifu` 的 C01-C11 全部结构化；
- 三个 Pipeline 都满足全部硬约束；
- 任何缺项都会被资格门禁阻断；
- `<2` 严格按 0 或 1 执行；
- VLM 不处理非视觉硬约束；
- QC 按要求检查 Evidence，而不是只检查已有字段；
- Golden Task 所有 100% 指标达标。

### 可复现

- Pipeline 是持久化、带 checksum 的 Artifact；
- Operator、Provider、模型、Prompt、参数和阈值有版本；
- 相同输入、Artifact 和环境能复现同一确定性过滤和去重结果；
- 语义模型的非确定性通过原始响应、标准化结果和版本记录可审计。

### 体验

- 用户在长任务开始前看到完整 TaskPlan；
- 运行中持续看到真实 Agent/Tool 生命周期事件；
- 计划、活动记录和正式输出分开；
- 阻断时告诉用户缺什么、为什么、下一步能做什么；
- 中断、刷新和恢复不会丢失任务状态。

---

## 16. 实施顺序与合并策略

建议按以下顺序提交小型、可回滚变更：

1. 假成功阻断测试与资格门禁；
2. ConstraintContract 与 `yifu` 需求解析；
3. 主 Agent Decision/Observation 和主图回环；
4. 检索结果契约 Adapter，不动数据库实现；
5. 数据处理 Agent Loop 和三 PipelineArtifact；
6. 图片/人脸/去重 Evidence；
7. QC 和发布门禁；
8. Stage-aware 调度与事件；
9. `yifu` 真实验收与错误修复；
10. 文档、迁移 Adapter 清理和性能基线。

每个提交必须：

- 只解决一个可描述的行为；
- 先有失败测试；
- 不覆盖同事的数据库检索改动；
- 不顺手重命名大量路径；
- 保留旧数据的读取兼容；
- 通过全量回归后再合并。

数据库检索完成后，以 Adapter 方式填充
`RetrievalResultBundle.data_candidates`，不修改主 Agent、数据处理 Agent 和
Pipeline 的核心 Interface。

---

## 17. 风险与控制

| 风险 | 控制 |
|---|---|
| 主 Agent 变成全能 Agent | 强制 Action/Tool 权限矩阵 |
| 四个 Loop 导致不可控成本 | 循环、时间、token、重试预算 |
| LLM 输出不稳定 | Schema、确定性 Validator、Artifact checksum |
| 检索未完成阻塞本轮 | 显式本地数据源走 direct-source Adapter |
| Provider 参数语义不同 | Canonical Constraint + Provider Adapter + 等价转换记录 |
| 黑色衣服主观 | 冻结口径、uncertain、ReviewSet、Golden Set |
| QC 再次假成功 | 要求驱动检查、缺 Evidence 即失败 |
| 并行改变结果 | Stage barrier、确定性排序、串并行一致性测试 |
| VLM 成本过高 | 便宜过滤和去重前置、唯一资产调用 |
| 与同事改动冲突 | 不改数据库检索内部，只冻结结果 Interface |

---

## 18. 开发开始前的最终检查

开始编码前，团队只需要确认以下开发基线，不再重新讨论架构名称：

- 主 Agent 名称：数据任务规划 Agent；
- 专业 Agent：检索 Agent、数据处理 Agent、数据策略 Agent；
- Conversation 是交互 Module，不是 Agent；
- Loop Supervisor 是确定性规则 Module，不是 Agent；
- 数据处理 Agent 名称保留；
- 检索 Agent 统一负责 Operator、历史 Pipeline，未来再接数据候选；
- 当前 `yifu` 不依赖数据库检索；
- `face_count < 2` 仅允许 0 或 1；
- 黑色衣服 `uncertain` 必须进入人工复核；
- 本轮采用纵向 Golden Task 驱动，不进行四 Agent 横向大改。

以上基线确认后，第一条开发测试应是：

> 当 `yifu` Pipeline 缺少任一 C01-C10 的覆盖或 Evidence 声明时，
> `production_eligible` 必须为 `false`，且不能进入正式执行。

这条测试先把错误出口封住，再逐切片让完整 Pipeline 变绿。这样本轮改造交付的
不是“看起来更像 Agent 的架构”，而是一个能对真实多约束数据任务负责的
Agent Runtime。

---

## 19. 2026-07-27 实施与验收记录

### 19.1 本轮已完成

- 主图已改为数据任务规划 Agent hub。所有需求、检索、处理、审批和策略结果均
  返回主 Agent，由主 Agent 根据当前正式状态决定下一项任务级 Action；
- 主 Agent 持续输出 `task_plan` 和结构化 `main_agent_decisions`，且主图具有
  `Main Agent -> Specialist/HITL -> Main Agent` 的真实回环；
- `TaskSpecVersion` 已增加 `ConstraintContract`，`yifu` 原始需求被规范化为
  C01-C11，保留 `gte/lte/lt` 边界、单位、作用域和 Evidence 要求；
- Operator 检索已使用结构化 capability 召回，不再依赖 “64”“20MB”“黑色”
  等字面关键词决定能力；
- `PipelineVersion` 已记录 required constraint 和
  `constraint_coverage`。缺少任一 required constraint 的 Pipeline 不允许批准
  或提交生产 Run；
- 数据处理编排已生成完整的尺寸、比例、文件大小、人脸、去重、视觉语义和
  Manifest 节点，并把 `face_count < 2` 等价绑定为 `max=1`；
- VLM Prompt 仅包含黑色衣服视觉语义约束，不再接收尺寸、文件大小或人脸数量；
- Data-Juicer 人脸输出已规范化为 `face_count` Evidence，过滤结果带有
  `CONSTRAINT_REJECTED:face_count`；
- 感知去重支持 dataset batch preparation，重复组和 canonical asset 可审计；
- QC 改为按 TaskSpec 主动检查必需 Evidence。缺 Evidence、未决语义复核、逐资产
  硬约束或数据集级去重/源文件约束违反都会阻断；
- 执行器支持 batch preparation 后的逐资产并行，保持确定性顺序和阶段 barrier。

### 19.2 自动化测试

全量测试结果：

```text
254 passed
```

新增或加强的测试覆盖：

- Pipeline 约束覆盖资格门禁；
- C01-C11 需求解析和边界；
- 主 Agent hub、HITL 中断恢复和 TaskPlan；
- 结构化 capability 检索；
- `yifu` Pipeline 参数、顺序和 Prompt 隔离；
- 图片尺寸、比例、文件大小和人脸 Evidence；
- Data-Juicer 输出适配；
- 感知去重 batch preparation；
- 缺 Evidence 和未决语义的 QC 阻断；
- batch operator 与逐资产并行重放。

### 19.3 `D:\data\yifu` 真实验收

> **2026-07-27 复核更正：**人工查看
> `1661926038873726.jpg` 可见两张清晰人脸，而本次 Run 保存的 Provider
> Evidence 是 `face_count=1`。因此下面的 `QC: PASSED` 只能说明当时的字段完整性
> 和规则门禁通过，不能证明人脸计数正确；“人脸数量严格少于 2 通过”的原结论
> 撤回。本次 Run 保留为失败取证，不能作为 Golden Task 正确性验收。新的测试
> 需求改为 `face_count <= 2`，其中该图片应保留且 Golden Label 必须为 2。后续
> 改造见 `docs/DataAgent-generalized-agent-planning-refactor-plan-2026-07-27.md`。

使用新工作单、新 Pipeline 和真实 Provider 运行：

```text
work_order_id: work_order_a61379a2c57e4373
pipeline_version_id: pipeline_version_97f741d853b64ae9
run_id: run_7c64d83a2d1b4323
dataset_version_id: dataset_7c64d83a2d1b4323
source_count: 19
kept: 5
rejected: 14
failed: 0
QC: PASSED
effective_concurrency: 4
elapsed: about 169 seconds
```

独立验算结果：

| 检查项 | 结果 |
|---|---|
| 宽高均不少于 64 | 通过 |
| 宽高比在 0.3 到 3.5 | 通过 |
| 文件大小在 1KB 到 20MB | 通过 |
| 人脸数量严格少于 2 | **撤回：Provider 将 2 张脸误报为 1** |
| 保留项均有黑色衣服语义 match | 通过 |
| 保留项无重复 SHA | 通过 |
| 19 个源文件 SHA 均未变化 | 通过 |
| QC | PASSED |

保留文件：

```text
1661926038873726.jpg
MEN-Denim-id_00000089-18_4_full.jpg
MEN-Denim-id_00000265-01_1_front.jpg
WOMEN-Rompers_Jumpsuits-id_00003965-04_1_front.jpg
WOMEN-Tees_Tanks-id_00000079-09_1_front.jpg
```

与改造前同一目录的成功运行相比，耗时由约 464 秒降至约 169 秒，下降约 64%。
这是小样本单次对比，仅用于验证阶段并行已实际生效，不作为稳定性能基准。

### 19.4 尚未在本轮宣称完成的部分

- 同事负责的数据库数据检索和 `data_candidates` Adapter 尚未接入；
- 黑色衣服的人工标注 Golden Set、precision/recall 和误删/漏删评估仍需建立；
- 当前主 Agent hub 已有正式回环、TaskPlan 和决定记录，但统一
  `AgentContext/AgentDecision/Observation` 类型、四个专业 Agent 的模型驱动
  Tool 选择与局部修复循环仍应作为下一阶段横向 Runtime 改造；
- Conversation 的 history/session/HITL 已保留，Agent/Tool 生命周期事件还需
  进一步统一为 started/progress/completed/failed 协议并完整接入界面；
- 三种 Pipeline 已生成并具有不同策略参数，但尚缺面向用户的正式比较 Artifact
  和小样本 trial 对比。

因此，本轮完成了执行链、门禁和主 Agent hub 基础，但没有完成 Golden Task 的
正确性闭环：需求规划仍有场景硬编码，且人脸 Evidence 存在已确认的错误。不能
据此宣称 `yifu` 或整个四 Agent Runtime 已经验收完成。

## 20. 2026-07-28 第二批开发结果：Pipeline Trial 与数据处理 Agent 修复循环

第二批按接手文档冻结的单一根因实施：规划产物与真实 Operator 执行之间缺少
通用 Trial Observation，因此数据处理 Agent 看不到真实证据，也无法据此修复。

新增的深 Module 只暴露：

```text
PipelineTrialRunner.run(PipelineTrialRequest)
  -> PipelineTrialObservation
```

它负责在隔离副本上执行有界样本、收集逐 Constraint Evidence、区分失败与
上游已拒绝、汇总 dataset-level Evidence，并返回 Artifact 引用。它不负责
选择算子、修改参数或重排 Pipeline。

数据处理 Agent 的正式模型循环现在为：

```text
inspect candidates
  -> compile three Pipeline variants
  -> trial_pipeline_variants
  -> real Operator observations
  -> model decides finish or recompiles
```

这满足“模型决策 + Tool 调用 + 环境反馈 + 状态循环”：Tool 提供事实，模型保留
规划权。兼容模式仍只做前置验证，并以 `trial_mode=pre_execution_validation`
显式区分，不能报告成真实试跑。

本批没有修改检索 Agent 的职责。它仍只返回 Operator/历史 Pipeline 候选和
充分性；未来数据库检索继续通过既定 `RetrievalPlan/CandidatePool` 接口接入。

验证覆盖了当前缺 Evidence 失败、车辆数量和文档字符数两个异语义案例、缺
Evidence 反例、dataset-level 去重、错误拒绝、上游拒绝以及源文件隔离。
完整回归结果为 `296 passed`。这证明 Trial/repair Interface 具备字段和模态
替换能力，但不等于 Registry 已经具备任意数据处理 Operator，也不等于 yifu
全量正式执行与人工 Golden Set 已完成。

真实 yifu 冒烟目前先暴露了上游 Requirement Planner 缺陷：定义性补充被
逐子句 Coverage 门禁要求必须各自产生新的原子 Constraint，导致 Grounding
失败。该问题应通过通用的“constraint definition/qualifier”语义建模修复，
不能在第二批 Trial Tool 内处理，更不能增加 yifu 关键词例外。
