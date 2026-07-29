# DataAgent 开发接手文档 v10

> 交接日期：2026-07-28  
> 仓库：`D:\newDataAgent`  
> 当前分支：`codex/agent-runtime-refactor`  
> 当前实现快照：`d95f681 feat: establish generalized agent runtime planning`  
> 上一版：`DataAgent-development-handoff-2026-07-24-v9.md`  
> v9 文档提交：`9509148 docs: capture current DataAgent project baseline`  
> 当前自动回归：`296 passed, 1 warning`
> 本文范围：v9 之后的产品定位修正、PRD v1.2、通用 Constraint/Evidence 契约、主 Agent 与四 Agent Runtime、检索和数据处理 ReAct Loop、真实 `yifu` 规划验证、Web 工作台，以及下一批执行反馈闭环计划。

## 0. 最重要的结论

DataAgent 当前正在从：

```text
LLM 负责对话理解
  -> 固定 LangGraph 节点
  -> 规则检索
  -> 固定 Pipeline 编译
  -> 执行
```

升级为：

```text
用户目标
  -> 数据任务规划 Agent（主 Agent）
  -> 模型决定下一步 Action
  -> 专业 Agent 自主选择 Tool
  -> Tool 返回 Observation
  -> Validator 检查
  -> 模型修正计划
  -> 直到满足停止条件或请求用户
```

本轮最重要的成果不是增加了几个 Agent 名称，而是建立了统一的：

```text
Goal
  -> Decision
  -> Tool Call
  -> Observation
  -> Validation
  -> Re-plan
```

第一批 Agent Runtime 改造已经落地并通过自动测试；真实 `D:\data\yifu` 已完成需求规划、算子检索和三条 Pipeline 生成验证。

但必须准确描述当前边界：

> 当前已经证明“模型可以根据 TaskSpec、算子元数据和 Tool Observation 自主检索、选择、排序并编译 Pipeline”；尚未证明“新 Runtime 生成的 Pipeline 已在真实 yifu 全量执行后，能够根据真实误删、漏删和 Evidence 自动修复”。

当前数据处理 Agent 中的 `trial_pipeline_variants` 只做确定性的结构、Coverage 和 Runtime 前置校验，不是真实小样本数据试跑。下一批开发的核心就是补上这个缺口。

## 1. 我们在做什么

### 1.1 产品目标

DataAgent 的目标是构建一个面向多模态数据生产的通用 Agent：

- 用户用自然语言提出任意数据筛选、清洗、分类、去重、标注或加工需求；
- 系统主动发现歧义并进行多轮澄清；
- 用户确认完整 TaskSpec；
- 检索 Agent 查询可用数据、Operator 和历史 Pipeline 经验；
- 数据处理 Agent决定如何使用候选 Operator、参数和历史经验；
- 数据处理 Agent 编排保留优先、均衡、质量优先三条真实 Pipeline；
- Pipeline 经过验证和小样本试跑后交给用户选择；
- 正式执行后产生逐约束 Evidence、QC、可追溯 Artifact；
- 失败或质量反馈能够回到 Agent Runtime 重新规划。

### 1.2 当前四类职责

#### Conversation Layer

负责：

- history；
- session；
- HITL 等待和恢复；
- Runtime event 传递；
- 用户消息与正式回复存储；
- 中断、取消和重连。

不应负责：

- 业务需求规划；
- 算子选择；
- Pipeline 编排；
- 专业 Agent 调度；
- 任务是否完成的业务判断。

当前实现尚未完全达到这个边界，`ConversationService -> ModelGateway.conversation_turn()` 仍承担部分 Intent、TaskSpecPatch 和回复生成逻辑，后续需要逐步降级为会话运行层。

#### 数据任务规划 Agent（主 Agent）

这是全局主导 Agent，不再只是旧的 Requirement Parser。

负责：

- 理解用户目标；
- 生成和维护 TaskSpec；
- 判断是否需要澄清；
- 提交完整需求给用户确认；
- 决定下一步调用哪个专业 Agent；
- 读取专业 Agent 返回的 Observation；
- 判断重试、返工、请求人工或结束；
- 维护 WorkOrder 全局计划和进度。

主 Agent 决定的是：

```text
下一步需要哪个任务级能力或专业 Agent
```

而不是直接决定：

```text
使用哪个具体 Data-Juicer Operator
```

#### 检索 Agent

当前负责：

- 检索个人和公共 Operator Registry；
- 检索 Operator 详细信息和参数 Schema；
- 检索历史 Pipeline、参数及成功/失败经验；
- 为每个 Constraint 返回候选和覆盖依据；
- 报告候选是否充分、缺口是什么。

后续数据库接入后还负责：

- 检索数据；
- 生成 RetrievalPlan；
- 生成 CandidatePool；
- 报告数据候选充分性。

数据库和数据检索部分由其他同事开发，本轮不得重写其实现；应保持 RetrievalRequest、CandidatePool 和 SufficiencyReport 的接入 Seam。

检索 Agent 不负责：

- 决定最终 Pipeline；
- 决定最终 Operator 顺序；
- 为数据处理 Agent偷偷生成固定参数；
- 执行数据处理。

#### 数据处理 Agent

名称保留。

负责：

- 决定如何使用检索到的 Operator 和历史 Pipeline；
- 清洗、过滤、分类、去重、标注和加工方案；
- 选择 Operator；
- 决定参数；
- 决定执行顺序；
- 编排并优化三类 Pipeline；
- 根据结构校验、执行失败、误删和漏删反馈修复方案。

当前已具备模型驱动的 Operator 检查、三 Pipeline 编译和确定性前置校验循环；真实样本执行 Observation 仍待补齐。

#### 数据策略 Agent

负责：

- SamplingPlan；
- quota；
- priority weights；
- diversity constraints；
- random seed；
- 后续成本、平衡和边界样本策略。

当前已接入统一 AgentDecisionLoop，但实现仍较浅，默认策略和 Tool Interface 需要在后续真实策略需求中重新审视。不要仅因为它叫 Agent 就继续堆 Tool。

## 2. 为什么要进行这次架构改造

### 2.1 原来的“多 Agent”主要是固定 Workflow

此前四个所谓 Agent 子图主要是确定性函数：

```text
Requirement node
  -> Retrieval node
  -> Processing node
  -> Strategy node
```

LangGraph 被使用成固定状态机，而不是 Agent Runtime。下一步由代码和 conditional edge 决定，模型没有持续的：

```text
Action
  -> Observation
  -> Reflection
  -> Re-plan
```

因此系统虽有 LLM、LangGraph、Agent 类名和 Tool 调用，仍会让用户感到“很多地方被写死”。

### 2.2 Conversation 承担了过多智能职责

旧开放语义能力主要集中在：

```text
ConversationService
  -> ModelGateway.conversation_turn()
```

模型在这里判断 Intent、生成 TaskSpecPatch 和用户回复；专业 Agent 自身却没有足够的模型决策和 Tool Loop。

这导致“大脑”位置错误：

```text
Conversation 像 Agent
专业 Agent 像普通函数
```

确认后的方向是：

```text
Conversation Layer
  -> 只管会话、HITL 和事件

数据任务规划 Agent
  -> 全局主 Agent

检索 / 数据处理 / 数据策略 Agent
  -> 有明确职责和受治理 Tools 的专业 Agent
```

### 2.3 `yifu` 暴露出规划和执行可信性问题

冻结回归需求：

```text
D:\data\yifu 中筛选：
1. 图片宽高均不少于 64 像素；
2. 宽高比在 0.3 到 3.5 之间；
3. 文件大小在 1KB 到 20MB 之间；
4. 图片中的人脸数量小于等于 2；
5. 主体穿黑色衣服；
6. 去除重复图片。
```

历史问题包括：

- Pipeline 没覆盖全部约束；
- Pipeline 仍是旧的五算子组合；
- 算子顺序不合理；
- `object_detection` 被错误推导为能力缺口；
- 出现没有语义的 `constraint:C09` 假能力；
- 重新检索和启用远程模型实际走同一个 retry 动作；
- “草案内容”必须额外问一次；
- 曾把测试图片的预期人脸数混入实现思路；
- 输出文件没有真实满足用户条件；
- 旧 API/Worker/TUI 或残留 launcher 使用旧代码抢任务；
- 用户无法区分真实 Agent 行动、内部规则和正式输出。

这些问题不能通过继续增加：

```text
if "black" in requirement
if face_count
constraint:C09 -> 某算子
```

来修复。根因是需求契约、Agent Runtime、检索职责、Pipeline 编排和 Evidence 反馈没有形成通用闭环。

## 3. v9 之后已经完成了什么

### 3.1 Provider 和 Operator 基线继续完善

v9 文档对应的后续基线提交包括：

```text
d2c6c23 feat: make discovered provider operators callable
f714824 fix: align provider proxy availability metadata
1632858 feat: explain provider runtime resolution paths
184f10f test: verify provider-available CPU execution
88cd414 fix: require task-specific classification labels
9509148 docs: capture current DataAgent project baseline
```

完成：

- Data-Juicer 发现目录中的代理 Operator 可以进入受控执行路径；
- Provider Proxy 的 discoverable、available、executable 状态更一致；
- Runtime 缺失原因可以解释；
- CPU 可执行 Provider Operator 有自动测试；
- 任务特定分类标签必须来自 TaskSpec，不能由算子静默猜测。

### 3.2 PRD v1.2

新增：

```text
DataAgent-final-PRD-v1.2.md
```

明确了：

- Conversation Layer 的边界；
- 数据任务规划 Agent 是主 Agent；
- 检索 Agent 的 Operator、历史 Pipeline 和未来数据检索职责；
- 数据处理 Agent 的选择、编排和修复职责；
- 数据策略 Agent 的 SamplingPlan 职责；
- Agent Runtime、Tool Governance 和 LangGraph 主图；
- Constraint、Coverage、Evidence 和发布门禁；
- CandidatePool、Pipeline Optimizer、Golden Set；
- 前端计划、活动和正式输出分层。

### 3.3 根目录通用开发原则

新增：

```text
AGENTS.md
```

核心禁令：

- 不得根据用户关键词增加业务 if/else；
- 编排代码不得出现任务实例名称；
- 不得为单一测试写固定 Pipeline；
- 不得硬编码“关键词 -> Operator”；
- 不得因当前案例失败增加静默默认值；
- 未定位根因前不得修改；
- 设计必须先由用户确认；
- 每个通用改动必须有当前案例、两个泛化案例、一个反例和红绿测试。

本文件现在是所有后续开发的最高优先级本地工程约束。

### 3.4 通用 ConstraintContract 和 RequirementDraft

新增或扩展：

```text
dataagent/domain/specs/models.py
dataagent/domain/specs/constraints.py
dataagent/domain/specs/binding.py
dataagent/agents/requirement/planner.py
```

关键对象：

```text
RequirementDraft
ConstraintContract
Constraint Parameter Binding
```

`ConstraintContract` 是领域数据模型，不是新的 AI 模型。它负责把用户语句中的可检验条件保存为：

```text
source_text
field
comparison
value
unit
hardness
required_evidence_type
```

例如“人脸数量小于等于 2”进入 TaskSpec 后，Pipeline 参数由数据处理 Agent根据 Operator Schema 绑定；实际有几张脸仍由运行时人脸算子产生 Evidence。

不得写入：

```text
1661926038873726.jpg -> face_count=2
```

这类内容只能存在于独立 Golden Set 的预期标注中，不能进入生产规划和执行。

### 3.5 统一 AgentDecisionLoop

新增：

```text
dataagent/agents/runtime.py
```

统一对象：

```text
AgentPlanningRequest
AgentDecision
AgentObservation
AgentTool
AgentLoopResult
AgentDecisionLoop
GatewayAgentPlanner
```

统一行为：

- 模型根据 Goal、Context、Tools 和历史 Observation 决定下一步；
- Tool 执行结果回到模型；
- Tool 异常转换为 Observation，而不是直接伪装成功；
- finish validator 可以拒绝过早结束；
- Validator 错误继续回喂模型修复；
- 每个 Loop 有最大迭代数；
- 模型不允许虚构 Operator、Tool 结果或 Evidence。

### 3.6 数据任务规划 Agent（主 Agent）

新增：

```text
dataagent/agents/main/runtime.py
```

主 Agent 当前能够：

- 根据当前 WorkOrder 状态生成任务计划；
- 只从当前合法 Action 中选择下一步；
- 调用需求、检索、数据处理、策略等专业 Agent；
- 接收专业 Agent Observation；
- 在非法 Action 后获得 policy Observation 并重试；
- 在无模型配置时使用显式兼容路径。

LangGraph 主图已从固定 DAG 调整为：

```text
START
  -> main_agent
  -> selected specialist / HITL / control action
  -> main_agent
  -> ...
```

对应文件：

```text
dataagent/graph/main_graph.py
dataagent/agents/shared/state.py
dataagent/application/agent_runtime.py
apps/api/main.py
```

### 3.7 Requirement Planning 接入模型和约束验证

`ModelGateway` 增加：

```text
plan_requirement_draft()
plan_agent_decision()
```

需求规划要求：

- 每个独立可验收条件拆成原子 Constraint；
- Constraint `source_text` 必须来自用户需求；
- 不允许 Requirement Planner 选择 Operator、模型、参数和顺序；
- 模型草案必须经过 Domain Schema 验证；
- 需求不完整时返回 blocking ambiguities；
- 完整 TaskSpec 直接展示给用户确认。

当前仍保留 `parse_requirement_contract()` 兼容路径。它不能成为正式配置模式的权威语义规划器，后续应显式隔离。

### 3.8 检索 Agent ReAct Loop

检索 Agent 在模型配置模式拥有：

```text
inspect_dataset_source
search_operator_catalog
list_operator_catalog
inspect_operator
search_pipeline_experience
```

模型负责：

- 决定调用哪些检索 Tools；
- 查看候选和 Operator Schema；
- 为每个 Constraint 提交候选覆盖；
- 报告 sufficient 或 gap。

确定性 Validator 负责：

- Constraint 是否有候选；
- Operator 是否真实存在；
- 是否 executable；
- Operator Schema 和元数据是否支持该 Constraint；
- 必需输出能力是否存在；
- 拒绝 `constraint:C09` 之类伪 capability；
- 拒绝模型虚构的 Operator ID。

检索 Agent 输出候选和充分性报告，不决定最终 Pipeline。

### 3.9 数据处理 Agent ReAct Loop

数据处理 Agent 当前拥有：

```text
inspect_operator
compile_pipeline_variants
trial_pipeline_variants
```

模型负责：

- 从检索候选中选择 Operator；
- 决定节点顺序；
- 决定参数；
- 映射 Constraint coverage；
- 编排三类 Pipeline；
- 根据 Validator Observation 重新生成。

确定性编译 Tool 负责：

- 只接受可执行检索候选；
- 校验 Operator 参数 Schema；
- 校验每个硬约束 Coverage；
- 校验 Coverage Operator 确实由检索 Agent 返回；
- 校验参数是否实现 Constraint 比较语义；
- 校验必需输出能力；
- 校验 DAG 和 Runtime 前置条件；
- 保留模型给出的顺序，不偷偷重排。

必须注意：

```text
trial_pipeline_variants
```

当前只是 pre-execution validation：

- 检查 required constraint coverage；
- 调用 runtime.validate_pipeline；
- 返回结构违规信息。

它还没有读取真实样本、没有运行 Operator、没有产生逐资产 Constraint Evidence，也没有计算误删和漏删。

### 3.10 数据策略 Agent Loop

数据策略 Agent 已接入统一 Runtime：

```text
inspect_approved_pipeline
build_sampling_plan
```

模型可以根据 TaskSpec 和已批准 Pipeline 生成 SamplingPlan；Tool 负责 Schema 校验。

当前风险：

- 默认 priority weights 和 diversity constraints 仍较强；
- Tool Interface 尚未经过真实多策略案例验证；
- Strategy Agent 不是当前 yifu 失败的根因；
- 后续不要继续在本 Module 叠加与执行、检索或 Pipeline 编排重复的职责。

### 3.11 移除 Catalog 中的强业务规则权威

`dataagent/operators/catalog_matching.py` 已删除或降级：

- 固定业务 Intent 列表；
- 中文/英文关键词到特定 Operator 的映射；
- 黑衣、猫狗等语义概念映射；
- 从用户原句直接提取 Pipeline 参数；
- 为当前案例生成假 capability 的逻辑。

Catalog Matcher 现在以通用 token、capability tag、secondary category 和 Operator 元数据为基础。

参数选择归数据处理 Agent，Matcher 不再替它决定。

### 3.12 Constraint Evidence 和 QC

扩展：

```text
dataagent/domain/pipelines/models.py
dataagent/operators/builtin/image.py
dataagent/operators/providers/output_adapters.py
dataagent/operators/providers/datajuicer_executor.py
dataagent/evaluation/quality.py
```

完成：

- PipelineVersion 保存 required constraint IDs；
- Pipeline 节点保存 ConstraintCoverage；
- 图片分析产生尺寸和宽高比 Evidence；
- Data-Juicer face count 输出适配为 `face_count` Evidence；
- Filter rejection 产生通用 constraint rejection reason；
- QC 检查硬约束 Evidence 缺失；
- QC 检查 dataset-level duplicate/source integrity；
- Pipeline 缺 Coverage 时不能批准。

当前仍需区分：

```text
Constraint Coverage
  !=
真实运行 Evidence
```

Coverage 证明“计划中有人负责”，Evidence 才证明“运行时实际测量并满足”。

### 3.13 Web 工作台

当前提交包含两套前端源码：

```text
apps/web/
dataagent-web (5)/
```

`apps/web/` 是与 FastAPI 同源的轻量工作台。

`dataagent-web (5)/` 是较完整的 React 工作台，包含：

- 对话和 Agent Cockpit；
- TaskSpec 详情；
- Pipeline 对比；
- Operator Library；
- Boundary Sample Review；
- Audit History；
- 用户和权限界面；
- Python 控制面 Adapter。

当前问题：

- 两套实现重复；
- `dataagent-web (5)` 名称是临时导入名称，不适合作为长期正式目录；
- 尚未决定哪套成为正式前端；
- 自动测试只覆盖了轻量 Web Cockpit 的基本路由和资源；
- React 工作台尚未在本轮执行完整构建和浏览器验收。

不要在未确认正式前端方案前同时维护两套功能。

### 3.14 文档

新增：

```text
docs/DataAgent-main-agent-runtime-optimization-development-plan-2026-07-27.md
docs/DataAgent-generalized-agent-planning-refactor-plan-2026-07-27.md
```

记录了：

- 主 Agent 与四 Agent Runtime 目标；
- Tool Governance；
- Conversation 边界；
- yifu 失败根因；
- 通用 ConstraintContract；
- 检索候选契约；
- 数据处理 Agent 动态编排；
- Evidence 和 Golden Set；
- 禁止任务硬编码；
- 测试驱动实施顺序。

## 4. 当前进行到哪里

### 4.1 当前状态表

| 能力 | 状态 |
|---|---|
| PRD v1.2 和职责定义 | 已完成 |
| 根目录通用开发原则 | 已完成 |
| RequirementDraft / ConstraintContract | 已完成第一版 |
| 主 Agent Action/Observation Loop | 已完成第一版 |
| 检索 Agent ReAct Loop | 已完成第一版 |
| 数据处理 Agent ReAct Loop | 已完成第一版 |
| 数据策略 Agent Loop | 已接入第一版 |
| 模型驱动 Operator 选择、参数和顺序 | 已完成第一版 |
| 三条 Pipeline 模型编排 | 已完成第一版 |
| 伪 `constraint:C09` capability 清理 | 已完成 |
| Catalog 关键词到 Operator 强映射清理 | 已完成 |
| Constraint Coverage / 参数绑定 | 已完成第一版 |
| Provider face count Evidence 适配 | 已完成 |
| QC 缺 Evidence 拒绝 | 已完成第一版 |
| 真实 yifu 需求规划和 Operator 检索 | 已验证 |
| 真实 yifu 三条 Pipeline 生成 | 已验证 |
| 真实 yifu 新 Runtime 全量执行 | 未完成 |
| 真实小样本 Trial Observation | 未完成 |
| 根据误删/漏删自动修复 | 未完成 |
| Conversation 完全降为 Session Layer | 未完成 |
| Tool Observation 实时流式展示 | 未完成 |
| 数据库数据检索 | 其他同事开发中 |
| React Web 正式整合 | 未完成 |
| 60-case Acceptance Campaign | 未执行 |

### 4.2 真实 `yifu` 规划验证

使用：

```text
模型：glm-5.2
源数据：D:\data\yifu
Data-Juicer Provider Catalog：约 218 个 Operator
```

需求模型在真实中文输入下识别出需要确认的歧义：

- 主体定义；
- 黑色容忍口径；
- 重复定义；
- 宽高比方向；
- KB/MB 计量口径。

在明确 TaskSpec 后，检索结果覆盖：

```text
宽度/高度
  -> datajuicer.image_shape_filter:1

宽高比
  -> datajuicer.image_aspect_ratio_filter:1

文件大小
  -> datajuicer.image_size_filter:1

人脸数量
  -> datajuicer.image_face_count_filter:1

黑色衣服视觉语义
  -> native.remote_vlm:1
  -> builtin.visual_semantic_selection:1

去重
  -> datajuicer.image_deduplicator:1

输出
  -> builtin.manifest:1
```

数据处理 Agent 生成三条包含上述能力的 Pipeline，并给出：

- 1KB 到 20MB；
- 最小宽高 64；
- 宽高比 0.3 到 3.5；
- 人脸数 0 到 2；
- 感知哈希去重；
- 黑色衣服视觉 Prompt/标签；
- uncertain 的保留、复核、拒绝策略；
- Manifest 输出。

真实 Tool History 显示：

- 检索 Agent调用 Catalog 列举、搜索和 Operator 检查；
- 数据处理 Agent调用 Operator 检查、编译和前置 Trial；
- Validator 错误会返回模型并触发重新编排。

这证明的是规划能力，不是最终数据质量。

### 4.3 自动测试

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

结果：

```text
296 passed, 1 warning
```

唯一警告：

```text
Starlette TestClient 当前 httpx 适配方式的弃用提示
```

不是业务失败。

新增测试覆盖：

- AgentDecisionLoop；
- Main Agent 模型路由；
- Requirement Planner；
- 任意未见 Constraint；
- Constraint 参数绑定；
- Retrieval 不生成伪 capability；
- Processing 决定顺序和参数；
- Validator Observation 后重新规划；
- Pipeline 缺 Coverage 不可批准；
- face count Evidence；
- dataset-level dedup Evidence；
- Conversation 草案确认；
- LangGraph 主 Agent 路由；
- Web Cockpit 基本资源。

### 4.4 Git 状态

实现快照：

```text
branch: codex/agent-runtime-refactor
commit: d95f681 feat: establish generalized agent runtime planning
```

本地运行产物现在被忽略：

```text
.dataagent-dev/
.dataagent-dev2/
.dataagent-yifu-runtime/
```

这些目录包含：

- SQLite WAL/SHM；
- Provider Catalog 缓存；
- Data-Juicer recipe 和 output；
- yifu 测试 Dataset；
- 运行 Evidence。

它们不是源码，不得进入提交。

## 5. 下一步计划

### 5.1 第二批唯一目标：真实 Pipeline Trial Observation

第二批必须遵循 `AGENTS.md`，收敛成一个纵向切片：

```text
数据处理 Agent 编排
  -> 真实小样本试跑
  -> 收集 Constraint Evidence
  -> 生成 Trial Observation
  -> 模型根据 Observation 修复
  -> 再编排
  -> 再试跑
```

单一根因：

> 当前 Pipeline 规划流程和真实执行/QC之间缺少通用、结构化的 Trial Observation Interface，也缺少真实试跑失败返回数据处理 Agent 的状态循环。

### 5.2 建立深的 PipelineTrialRunner Module

建议只暴露一个主要 Interface：

```text
run_pipeline_trial(PipelineTrialRequest)
  -> PipelineTrialObservation
```

内部 Implementation 隐藏：

- 小样本选择；
- 隔离运行目录；
- Pipeline 执行；
- Provider 输出归一化；
- Constraint Evidence 汇总；
- 硬约束验证；
- dataset-level 约束；
- Artifact 管理；
- 失败分类；
- 成本和耗时摘要。

数据处理 Agent 不应学习每种 Provider 和 QC 的内部细节。

### 5.3 不增加 `repair_pipeline` Tool

修复权属于数据处理 Agent：

```text
compile_pipeline_variants
  -> run_pipeline_trial
  -> Observation
  -> 模型决定改算子、参数或顺序
  -> compile_pipeline_variants
```

如果增加一个会自动修改 Pipeline 的 `repair_pipeline` Tool，只是把新的规则系统藏到了 Tool 内部。

### 5.4 Trial Observation 契约

至少包含：

```text
pipeline_version_id
trial_run_id
status
constraint_results
execution_failures
dataset_level_failures
cost
artifact_refs
```

每个 Constraint 结果：

```text
constraint_id
status
operator_version_id
evidence_type
observed_summary
failure_code
```

通用失败代码：

```text
MISSING_EVIDENCE
CONSTRAINT_NOT_SATISFIED
OPERATOR_EXECUTION_FAILED
PARAMETER_SCHEMA_INVALID
DATASET_CONSTRAINT_FAILED
```

生产代码不得出现：

```text
BLACK_CLOTHING_FAILED
YIFU_FACE_COUNT_MISMATCH
```

### 5.5 第二批红绿测试

必须先冻结：

1. 当前 yifu 失败案例；
2. 两个结构相同、语义不同的泛化案例；
3. 一个反例或边界案例；
4. 修改前失败、修改后通过；
5. 完整测试结果；
6. 修改文件及职责。

建议：

```text
当前案例
  多约束图片筛选：尺寸、比例、大小、人数、视觉语义、去重

泛化案例 A
  车辆数量、清晰度和去重

泛化案例 B
  文本长度、语言和去重

反例
  Operator 执行成功，但缺少硬约束要求的 Evidence
```

人脸边界必须覆盖：

```text
0、1、2 -> 满足 <= 2
3 -> 不满足
```

这些预期只能位于测试 Fixture/Golden Set，不能写入生产规划代码。

### 5.6 后续批次

第三批：

- 正式生产执行；
- QC；
- 主 Agent 全局返工；
- Repair Run；
- WorkOrder 完成门禁。

第四批：

- 根据真实需求重新评估 Strategy Agent Interface；
- Sampling、成本、平衡和边界样本 Observation。

第五批：

- Conversation 完全降为 Session Layer；
- Tool Observation 实时事件；
- TUI/Web 计划、活动和正式输出分层。

单独清理批次：

- 正式配置模式禁用静默 legacy fallback；
- 兼容模式必须显式开启；
- 合并两套 Web；
- 重命名或移除 `dataagent-web (5)`；
- 清理历史强规则兼容代码。

## 6. 必须记住的经验，不要重复踩坑

### 6.1 Agent 不等于 LangGraph 节点

真正的 Agent 至少需要：

```text
Goal
Memory/State
Model Decision
Tools
Observation
Validator
Loop
Stop Condition
```

固定节点调用函数属于 Workflow，不因为类名叫 Agent 就获得自主性。

### 6.2 Conversation 不是大脑

Conversation 是 Session Runtime。数据任务规划 Agent 才是主 Agent。

不要再次把 Intent、需求规划、专业 Agent 调度和完成判断全部塞回 ConversationService。

### 6.3 主 Agent 不直接选择具体 Operator

主 Agent 决定调用检索 Agent还是数据处理 Agent。

检索 Agent找候选并报告充分性。

数据处理 Agent决定使用哪些候选、顺序和参数。

### 6.4 检索 Agent 不编排 Pipeline

检索输出：

```text
候选 Operator
历史 Pipeline 经验
Coverage Evidence
SufficiencyReport
```

不要让检索 Agent为了“方便”偷偷返回最终顺序和固定参数。

### 6.5 不要再出现伪 capability

`constraint:C09` 是 Constraint ID 被误当作 capability 的结果。

Constraint 标识用户约束；capability 描述 Operator 能力；二者必须通过 Coverage 映射，不能混为一个字符串。

### 6.6 ConstraintContract 是领域模型，不是执行模型

TaskSpec 保存用户想要什么。

Operator 参数由数据处理 Agent根据 Schema 绑定。

真实值由 Operator 运行测量。

不要把用户要求、Pipeline 参数、实际 Evidence 和测试答案混在同一个对象里。

### 6.7 Coverage 不等于 Evidence

```text
Coverage
  = 计划中哪个节点负责约束

Evidence
  = 运行时实际测量或判断结果
```

结构校验通过不能证明图片真的符合条件。

### 6.8 不要为 yifu 写生产规则

生产代码不得出现：

- `D:\data\yifu`；
- 测试图片文件名；
- 黑色衣服专用分支；
- 人脸期望答案；
- 固定七/九个节点 Pipeline；
- 面向这一个目录的默认参数。

yifu 是 Golden Task，不是产品逻辑。

### 6.9 不要从文件名推断人脸数

人脸数量必须来自：

- OpenCV/模型 Operator 输出；
- Provider 归一化 Evidence；
- 独立 Golden Set 只用于评测。

文件名和人工观察不得成为生产 Evidence。

### 6.10 `< 2` 和 `<= 2` 不一样

本轮最终测试口径是：

```text
face_count <= 2
```

0、1、2 都允许。比较符必须来自 TaskSpec，参数绑定按 Schema 和 comparator 语义转换，不能由默认值覆盖。

### 6.11 不要用关键词到 Operator 映射修检索

正确检索依据：

- Operator capability metadata；
- tags；
- input/output schema；
- parameter schema；
- limitations；
- Runtime availability；
- 历史 Pipeline 经验；
- 后续数据索引。

关键词可以用于通用检索 Query，但不能成为隐藏的业务权威映射。

### 6.12 Tool 负责确定性能力，Agent 负责选择

Tool 可以：

- 搜索；
- 检查；
- 编译；
- 校验；
- 执行；
- 返回 Observation。

Tool 不应：

- 根据业务关键词选择方案；
- 偷偷重排模型给出的 Pipeline；
- 静默添加算子；
- 替 Agent决定如何修复；
- 伪造成功结果。

### 6.13 不要机械地给每个 Agent 堆 Tools

Tools 只有在 Agent需要根据环境结果决定下一步时才有意义。

Conversation、Executor、Validator、QC 不是 Agent，不需要假装拥有推理 Tool。

优先设计深 Module 和小 Interface，而不是许多只转发一层的浅 Tool。

### 6.14 Validator 不能替 Agent 规划

Validator 可以拒绝：

- Operator 不存在；
- 参数不合法；
- Coverage 缺失；
- DAG 无效；
- Evidence 缺失；
- 权限或资源不满足。

Validator 不应替模型补算子、改参数和排顺序。

### 6.15 不要静默走旧路径

当前仍存在未配置模型时的 compatibility path。

正式模式与兼容模式必须明确区分：

```text
configured production
  -> Agent Planner 必须可用

explicit offline/test compatibility
  -> 才允许 deterministic fallback
```

如果正式 TUI 看似使用新 Agent，实际静默退回旧编译器，会再次出现“明明改好了却还是五个旧算子”。

### 6.16 Data-Juicer Catalog 需要 Runtime Root

真实测试时，如果 AgentRuntime 没有 `datajuicer_runtime_root`，Provider Proxy Catalog 可能为空，检索 Agent会诚实报告缺口。

这不是模型不会检索，而是 Runtime 没加载 Provider Catalog。遇到检索不到算子时先检查 Catalog 和 Runtime 状态。

### 6.17 先查旧进程，再怀疑新代码

出现以下情况：

- 8000 端口被占；
- 新 TUI 连到旧 API；
- 旧 Worker 抢任务；
- 新代码结果像旧逻辑；
- launcher 残留。

先检查：

- API instance/revision；
- Worker PID 和 lease；
- launcher 记录；
- 端口进程；
- 当前源码 revision。

不要先增加 Prompt 或业务规则。

### 6.18 一键启动必须是真正托管

目标入口：

```powershell
.\.venv\Scripts\dataagent.exe start --owner local-user
```

它应管理 API、Worker 和 TUI 的统一生命周期。

如果已有同仓库实例，应复用或给出可执行的替换流程；如果是未知进程占用端口，应明确拒绝，不能盲目杀进程。

本轮未重新完成干净 Windows 启动矩阵，接手者不要宣称该问题已经彻底关闭。

### 6.19 中文真实测试不要通过易损的 PowerShell 内联文本

此前 PowerShell 内联中文曾变成 `?`，导致需求规划结果失真。

真实中文测试应使用：

- UTF-8 文件；
- 明确 UTF-8 环境变量；
- TUI 正常输入；
- 可验证的请求日志。

### 6.20 不要把自动测试等同于产品完成

当前证据等级：

```text
280 条自动测试通过
真实 glm-5.2 需求规划通过
真实 Data-Juicer Catalog 检索通过
真实三 Pipeline 规划通过
新 Runtime 真实 yifu 全量执行尚未通过
真实误删/漏删修复闭环尚未通过
```

任何交接和汇报必须保持这个区分。

### 6.21 不要提交本地运行产物

禁止提交：

```text
.dataagent-dev/
.dataagent-dev2/
.dataagent-yifu-runtime/
*.db-shm
*.db-wal
Provider run output
临时 Dataset 文件
```

这些目录现已加入 `.gitignore`。

## 7. 关键代码位置

```text
AGENTS.md
  通用开发原则和禁止事项

DataAgent-final-PRD-v1.2.md
  当前产品和 Agent 职责基线

dataagent/agents/runtime.py
  统一 AgentDecisionLoop、Tool 和 Observation

dataagent/agents/main/runtime.py
  数据任务规划 Agent 的任务级 Action 决策

dataagent/agents/requirement/planner.py
dataagent/agents/requirement/nodes.py
  RequirementDraft 和 TaskSpec 规划

dataagent/agents/retrieval/nodes.py
  Operator/历史 Pipeline 检索 ReAct Loop 和充分性验证

dataagent/agents/processing/nodes.py
  Operator 检查、三 Pipeline 编排、Coverage 和前置 Trial

dataagent/agents/strategy/nodes.py
  SamplingPlan Agent Loop

dataagent/domain/specs/models.py
dataagent/domain/specs/constraints.py
dataagent/domain/specs/binding.py
  ConstraintContract、RequirementDraft 和参数绑定

dataagent/domain/pipelines/models.py
  Pipeline ConstraintCoverage

dataagent/gateway.py
  Requirement Planning 和 Agent Decision 模型调用

dataagent/graph/main_graph.py
  主 Agent 驱动的 LangGraph 主图

dataagent/application/agent_runtime.py
  WorkOrder Runtime 和 Pipeline 批准门禁

dataagent/operators/catalog_matching.py
  通用 Catalog metadata 匹配

dataagent/operators/providers/output_adapters.py
dataagent/operators/providers/datajuicer_executor.py
  Provider 输出和 Constraint Evidence

dataagent/evaluation/quality.py
  Constraint Evidence 和 dataset-level QC

tests/unit/test_agent_decision_loop.py
  主、检索、处理 Agent Loop 与修复 Observation

tests/integration/test_react_agent_planning_flow.py
  模型 Action 和专业 Agent Tool Observation 完整规划流程

tests/unit/test_requirement_planner.py
tests/unit/test_constraint_parameter_binding.py
tests/unit/test_constraint_evidence.py
tests/unit/test_pipeline_eligibility.py
  通用需求、参数、Evidence 和执行资格
```

## 8. 接手后的检查步骤

### 8.1 Git 和文档

```powershell
cd D:\newDataAgent
git status --short --branch
git log --oneline --decorate -20
Get-Content .\AGENTS.md
Get-Content .\DataAgent-development-handoff-2026-07-28-v10.md
```

### 8.2 完整测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

当前预期：

```text
296 passed, 1 warning
```

### 8.3 Agent Runtime 检查

重点验证：

- 配置模型时 Main Agent decision source 为 `model`；
- 检索 Agent Tool History 不为空；
- Processing Agent Tool History 不为空；
- Constraint 不被当作 capability；
- Pipeline 只使用检索返回的 executable Operator；
- 三条 Pipeline 都有完整 required constraint coverage；
- Pipeline 顺序来自模型 Proposal；
- Validator 失败后模型会再次调用 Tool；
- 无模型时是否明确进入 compatibility，而不是静默伪装 Agent。

### 8.4 真实 yifu 再验收

下一次真实验收必须分开报告：

```text
需求规划
检索
Pipeline 编排
小样本真实 Trial
用户选择
正式执行
QC
输出文件人工/Golden Set 核验
```

不能只展示最终 `SUCCEEDED`。

## 9. 提交索引

v9 文档后的关键提交：

```text
d2c6c23 feat: make discovered provider operators callable
f714824 fix: align provider proxy availability metadata
1632858 feat: explain provider runtime resolution paths
184f10f test: verify provider-available CPU execution
88cd414 fix: require task-specific classification labels
9509148 docs: capture current DataAgent project baseline
d95f681 feat: establish generalized agent runtime planning
```

## 10. 第二批开发进展（2026-07-28）

第二批的真实 Pipeline Trial Observation 纵向切片已经落地：

- 新增 `dataagent/execution/pipeline_trial.py`，公开
  `PipelineTrialRunner.run(PipelineTrialRequest) -> PipelineTrialObservation`；
- Trial 从确认后的本地数据源发现有界样本，在隔离目录复制输入后执行，不修改
  用户源文件；
- Trial 通过正式 `OperatorRuntime` 执行数据准备、逐资产节点和 dataset-level
  节点，并按 Constraint 汇总 Evidence；
- 缺 Evidence、约束未满足、Operator 异常和 dataset-level 失败均以结构化
  Observation 返回；
- 上游正确拒绝后未执行的下游约束标记为 `not_evaluated`，不产生假缺失；
- 数据处理 Agent 的 `trial_pipeline_variants` 在正式模型配置下会真实执行三条
  Pipeline；失败 Observation 返回模型，由模型决定重新编排；
- Trial Tool 不拥有修复权，不选择算子、不重排节点；
- Data-Juicer 尺寸、宽高比和文件大小输出增加固定协议 Adapter，和既有人脸计数
  Adapter 一起形成可比较的运行 Evidence；
- API 仅在模型 Gateway 可用时启用真实 Trial；兼容模式明确报告
  `pre_execution_validation`。

本批泛化证明没有使用 yifu 硬编码：

```text
image.vehicle_count <= 3
document.character_count >= 10
Operator 成功但缺 Evidence -> 必须失败
源文件被试验 Operator 修改 -> 只修改隔离副本
```

完整自动化测试当前为：

```text
296 passed
1 个既有 Starlette/FastAPI 弃用警告
```

仍未完成：

- 全量正式执行、QC 和主 Agent 全局返工闭环；
- 误删率、漏删率、成本与三策略统计比较；
- 数据库数据检索（由其他同事开发）；
- 无模型兼容路径中的历史场景正则清理；
- Conversation 完全降为 Session Layer 和前端计划/活动/正式输出分层。

真实 yifu 冒烟的当前阻断点需要单独记录：补齐“主体、黑色、可见人脸、感知
重复”定义后，Requirement Planner 的逐子句 Grounding 校验仍把其中两条
定义性子句判为“没有原子约束”，因此停在 TaskSpec 生成阶段，尚未进入第二批
Trial。不要为了让 yifu 变绿而增加任务正则、静默删除定义子句或注入固定
TaskSpec；下一批开始前应先让 RequirementDraft 能区分“新增硬约束”和“对已有
约束的定义/限定”，并保留两者的 source trace。

### 第三批 A 当前进展

上述 Requirement Planner 阻断已按通用机制改造：

- `RequirementClauseTrace` 区分 constraint、definition、preference、output
  和 context；
- 定义只引用已有 Constraint，不生成伪 capability；
- Requirement Agent 使用 `validate_requirement_draft` Tool 和 Grounding
  Observation 自主修订；
- `ask_user/report_gap` 会产生 `requirement_clarification` interrupt；
- Conversation 新增 `CLARIFY_REQUIREMENT`，只负责把用户回答送回 Agent；
- 未授权 Tool 变为可修复 Observation，不再直接炸掉 WorkOrder；
- 大型 Draft 的模型可见 Observation 使用摘要，避免循环上下文膨胀。

泛化测试覆盖音频静音定义、文本敏感信息定义、非法 Constraint 引用、缺 Trace
修复和澄清恢复。生产代码没有新增 yifu、黑色衣服或人脸关键词分支。

## 11. 一句话接手结论

DataAgent 已从“模型生成后只做结构校验”推进到“数据处理 Agent 编排后在隔离小样本上真实运行，并依据 Constraint Evidence Observation 自主重编排”；下一批应连接正式执行、独立 QC 与主 Agent 全局返工，而不是继续增加业务关键词、固定 Pipeline 或 Tool 内自动修复规则。

## 12. 第三批 A 完成状态（2026-07-28）

第三批 A 已收口：

- 新增通用 `RequirementClauseTrace`，把 constraint、definition、preference、output、
  context 与用户原文建立可验证关系；
- Requirement Agent 通过 `validate_requirement_draft` Tool 读取 Observation、自主修订，
  或通过 LangGraph interrupt 请求用户澄清；
- Conversation 只转交澄清答案，不再承担 TaskSpec 智能修改；
- Agent Runtime 将未授权 Tool 选择返回为可恢复 Observation，并支持大型 Tool 输入摘要；
- 修复“动作句被后续定义冒号吞掉”的通用分句漏洞；
- 当前案例、音频案例、文本案例、非法引用和漏动作反例均有自动化测试；
- 全量 `307` 项测试通过，`compileall` 与 `git diff --check` 通过；
- 真实 yifu 验证只运行到 Requirement 阶段：得到 9 个 Constraint、11 个 ClauseTrace，
  进入 TaskSpec 确认，并显式保留去重阈值歧义。

不要把这次验证表述为 yifu 端到端成功。Retrieval、三 Pipeline Trial、用户选择、正式执行、
QC 和输出 Golden Set 核验没有在本次真实模型验证中运行。

下一批应继续做单一纵向切片：

```text
Approved Pipeline
  -> formal run
  -> independent QC Observation
  -> Main Agent reads evidence
  -> finish | reretrieve | recompile | rerun | ask_user
```

仍然遵守 `AGENTS.md`：失败由 Observation 暴露，修复决定属于模型；Tool 不读取任务关键词
决定方案，不在 Validator 中写补丁，不用固定 yifu Pipeline 伪造成功。

## 13. 第三批 B：正式执行结果回到主 Agent（2026-07-28）

第三批 B 已连接原本断开的正式执行链：

```text
Approved Pipeline
  -> Dataset Run
  -> Worker execution
  -> QCReport
  -> RunOutcomeObservation
  -> Main Agent decision
  -> complete / repair / rerun / reretrieve / recompile / HITL
```

关键边界：

- `RunOutcomeObserver` 只投影 Run、DatasetVersion、QCReport 和失败资产事实；
- Main Agent 模型保留修复选择权；
- 检索 Agent 仍负责候选，数据处理 Agent 仍负责 Pipeline；
- Worker 在后台主动通知，不依赖前端刷新；
- 无模型模式只记录 Observation，不伪装成模型决策；
- 相同 Run 通知幂等，模型暂时失败后仍能恢复消费；
- 非终态 Run、QC 未通过、无失败资产重试等均由确定性门禁保护。

前端需要识别：

```text
state.latest_run_observation
state.observed_run_ids
state.resolved_run_ids
state.active_run_id
interrupt.kind = run_outcome_resolution
Conversation intent = RESOLVE_RUN_OUTCOME
task_plan: execute_dataset / evaluate_quality / resolve_run_outcome
Run events: run_outcome_notification_requested /
            run_outcome_notified /
            run_outcome_notification_failed
```

本批没有修复具体人脸或黑衣模型质量，也没有接入同事负责的数据库检索。完整 yifu
端到端和人工 Golden Set 验收仍需单独执行，不能用自动化控制流通过代替结果正确性。

后台交接采用持久化请求标记：Worker 只重试带
`run_outcome_notification_requested` 且尚未成功通知的终态 Run。这样 Agent 模型
临时失败后仍可恢复，同时不会在升级后把历史终态 Run 全部重新唤醒。相同 Run 的
请求标记幂等，Repair Run 在修复计划完整落盘后才登记通知。

本批最终验证：

- 主流程整组 `48` 项通过；
- 全量自动化 `320` 项通过；
- Python 编译与差异格式检查通过；
- 仅有既存 Starlette `httpx` 弃用警告。

## 14. 非 GPU 多模态 Provider Runtime（2026-07-28）

原实现只允许 Data-Juicer 图像算子进入 Provider 执行。现已改为由 Catalog 模态标签
驱动通用记录序列化，支持 text、image、audio、video 和显式 structured
`record_fields`。正式 Run 也按 Pipeline 模态扫描数据源，不再固定扫描图片。

真实 Catalog 验证结果：

- 本地 CPU 候选 `146`；
- 统一参数与运行门禁通过 `146`；
- CPU 图像真实烟测 `11/11`；
- CPU 文本 `text_length_filter` 真实烟测通过；
- 音频/视频契约测试通过，但因本机没有真实样本，不能标记为真实 FFmpeg 验收。

需要牢记：

- Provider 环境可用不等于 DataAgent 端到端已验收；
- 不得为了批测而执行 S3 上传、下载或付费远程 API；
- 后续按 Operator Contract Family 准备真实 Fixture，而不是为 146 个名字写分支；
- Catalog Schema 变化必须提升缓存版本，否则生产仍会读取旧参数类型。

本批完成后全量自动化回归为 `328` 项通过；Python 编译与差异格式检查通过。
