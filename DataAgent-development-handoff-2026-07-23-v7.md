# DataAgent 开发接手文档 v7

> 交接日期：2026-07-23  
> 仓库：`D:\newDataAgent`  
> 当前分支：`codex/hybrid-operator-retrieval`  
> 当前 HEAD：`f07a957 docs: document p0 repair and acceptance workflow`  
> 上一版：`DataAgent-development-handoff-2026-07-22-v6.md`  
> 本文范围：v6 之后的动态任务语义、Prompt/Pipeline 可复现、成功经验复用、受治理 Tool 层、Logical DatasetVersion、Repair/Exclude/Export 闭环，以及 P0 真实验收。  
> 当前自动回归：`186 passed, 1 warning`  
> Git 状态：当前分支比远端同名分支领先 29 个提交；v6 到当前 HEAD 共新增 21 个提交。

## 1. 我们在做什么

DataAgent 是一个面向图片数据生产的 Agent 控制平面。用户通过自然语言描述数据源、筛选规则、分类目标和交付要求，系统将其固化为版本化 `TaskSpec`，检索真实 Operator，编译并展示三条可比较的 Pipeline，经用户批准后交由独立 Worker 执行，最终生成可审计、可修复、可导出的数据集版本。

当前主链路已经从“执行一次图片处理任务”推进为：

```text
自然语言需求
  -> 模型输出结构化 Intent / Action
  -> ActionPolicy 和工作流状态校验
  -> 版本化 TaskSpec
  -> 能力拆解、Catalog 检索和候选重排
  -> 确定性编译 PipelineArtifact
  -> 用户批准 PipelineVersion
  -> Worker 执行真实 Operator
  -> 每资产、每节点审计
  -> Logical DatasetVersion + QC
  -> PARTIAL 时只修复失败资产
  -> RepairedDatasetVersion
  -> 仍失败资产进入 still_failed / abandoned
  -> 用户显式确认 excluded_assets
  -> SUCCEEDED DatasetVersion
  -> Deliverable Dataset Export
```

这里最重要的产品方向不是继续堆算子数量，而是先证明以下闭环可靠：

1. 模型只能理解语义、查询事实和提出动作，不能绕过控制面直接执行算子或修改状态。
2. Pipeline、Prompt、Provider、参数和输出都可追溯、可校验、可复现。
3. 一张图片失败不能拖死整批，也不能被静默改成 `unknown` 后伪装成功。
4. `PARTIAL` 只是可修复中间态，不能直接当成完整交付。
5. 修复不覆盖旧 Run 或 DatasetVersion，也不重复复制父版本全量图片。
6. 只有通过 QC 且没有未解决资产的 `SUCCEEDED` 逻辑版本才能导出完整交付目录。

## 2. v6 之后讨论并确认的设计结论

### 2.1 分类类别必须来自 TaskSpec，不能写死猫狗

此前 Pipeline Prompt 和语义算子容易把 `cat`、`dog` 当作固定类别，换成猪狗、车辆或其他闭集分类任务时就失效。

现在的原则是：

- 类别集合属于任务数据，是 `TaskSpec` 的结构化字段；
- Requirement 模型负责从用户需求中提取任务类别；
- Pipeline 编译时把类别传给分类 Operator 和 Prompt；
- 相同 Operator 可以服务不同任务，不为每组类别复制一份代码；
- 编译期校验参数形状，但不建立一个封闭的全局类别本体。

这项修改由 `901d3a6 feat: support task-specific classification labels` 完成。

### 2.2 Prompt 应版本化，但不能为每个任务复制一套模板目录

讨论中明确区分了三类内容：

1. **通用 Prompt 模板**：例如图片真实性判断、闭集图片分类，集中保存在仓库中，具有稳定 ID、版本和 SHA256。
2. **任务变量**：例如类别列表、真实性排除范围、策略阈值，由 TaskSpec 和 Pipeline 参数注入。
3. **运行时生效证据**：Run 只记录所用模板 ID、版本、校验和、变量和最终生效 Prompt，不复制整套 Prompt Registry。

当前内置模板：

```text
dataagent/resources/prompts/image-authenticity.v1.yaml
dataagent/resources/prompts/closed-set-image-classification.v1.yaml
```

解析与绑定：

```text
dataagent/prompts.py
dataagent/agents/processing/nodes.py
dataagent/domain/pipelines/models.py
```

对应提交：

```text
d1baf68 feat: version operator prompt templates
```

不要为每个任务、每次 TaskSpec 修订生成新的 `prompts/builtin`、Schema 或 Migration 目录。规范和模板是共享代码资产；任务版本只引用它们。

### 2.3 每条 Pipeline 需要可复现产物，但不需要默认生成臃肿 Run Bundle

用户希望 Pipeline 能以 YAML 记录算子顺序、真实版本、参数、Prompt 和 Provider 绑定，便于复现。这个方向是正确的，但不能让 LLM 自由手写 YAML，也不能每个中间版本都复制完整 Catalog 和环境。

当前方案：

- `PipelineVersion` 是数据库中的规范事实；
- `PipelineArtifact` 由确定性 serializer 按需导出；
- YAML 带 Schema 版本和 checksum；
- 导入或执行前重新校验算子生命周期、Runtime、参数和 checksum；
- 大体积 Catalog Snapshot、环境锁和二进制输出不在每轮对话中重复生成；
- 真正的交付或归档阶段再按需生成紧凑证据包。

实现位置：

```text
dataagent/application/pipeline_artifacts.py
dataagent/tools/artifacts.py
```

对应提交：

```text
64359ea feat: export pipeline versions on demand
e6a694c feat(p0): validate PipelineArtifact production eligibility
```

### 2.4 成功 Pipeline 可以复用，但必须作为证据而不是命令

DataAgent 需要参考历史上成功、稳定、用户满意的相似 Pipeline，避免每次从零规划。当前新增 `PipelineExperience`：

- 只从已完成 Run 和真实 PipelineVersion 生成；
- 保存 TaskSpec 摘要、能力集合、策略、Operator 版本、质量结果和反馈；
- 只有明确标记可复用且满足状态条件的经验才进入召回；
- 相似任务只把历史经验作为编译证据，仍要按当前 Catalog、Runtime 和 Policy 重新验证；
- 不直接复用旧 Run，不绕过当前用户审批；
- 用户反馈 API 可以记录满意度、是否接受、是否允许复用。

对应提交：

```text
ad27ed5 feat: record reusable pipeline experiences
439eecb feat: reuse successful pipeline experience safely
a47fe20 feat: expose run satisfaction feedback
```

### 2.5 Tool 与 Operator 不是一回事

此前“tools 层是否完善”的讨论最终形成以下边界：

- **Control Tool** 是模型可调用的、带输入输出契约的控制面入口；
- **Operator** 是 Pipeline 内部的数据处理节点，只能由 Worker 执行；
- Tool 不能成为第二套状态机，也不能直接写数据库；
- Tool 不能暴露任意 shell、Python、文件写入或 `execute_operator`；
- 联网、文件写入等能力以后如果加入，也必须是窄接口、声明副作用、经过权限和审计治理，不能做成任意执行器。

允许的调用链：

```text
LLM
  -> Control Tool
  -> ActionPolicy / AgentRuntime
  -> approved PipelineVersion
  -> Worker
  -> Operator
```

禁止的调用链：

```text
LLM -> Tool -> execute_operator
```

### 2.6 DatasetVersion 是逻辑版本，完整文件目录属于 Export

旧实现容易把“数据集版本”和“一份完整复制目录”混为一谈。修复一个失败资产时，如果每次复制其余全部成功图片，会造成空间浪费、血缘模糊和版本写放大。

现在明确：

- 初始 DatasetVersion 可以物化本次保留文件；
- RepairedDatasetVersion 引用父版本成功资产和 Repair Run 新成功资产；
- Manifest 是逻辑版本真相；
- `asset_origin` 记录资产来自初始 Run、父版本、Repair Run 或显式排除；
- `materialization` 区分本地文件、引用文件和未物化；
- 完整、便于交付的文件树只由 Deliverable Dataset Export 生成。

### 2.7 `PARTIAL` 只能进入修复，不得直接交付

v6 已引入 `PARTIAL`，v7 阶段进一步补齐它的后续语义：

- `PARTIAL` 表示已有可用输出，但仍有失败资产；
- Repair Run 只接收失败或缺失资产；
- 同一资产修复少于 3 次仍失败时进入 `still_failed`；
- 第三次仍失败后进入 `abandoned_assets`，禁止第四次自动重试；
- abandoned 不能自动消失，必须由用户显式确认进入 `excluded_assets`；
- 剔除行为创建新的不可变 DatasetVersion，并保留原因和审计引用；
- 只有没有 `still_failed` 和 `abandoned_assets`、关联 Run 为 `SUCCEEDED` 的版本才能导出。

## 3. 已经完成了什么

### 3.1 动态任务语义、Prompt 和 Pipeline 复现

关键提交：

```text
901d3a6 feat: support task-specific classification labels
d1baf68 feat: version operator prompt templates
64359ea feat: export pipeline versions on demand
```

完成内容：

- 分类类别由 TaskSpec 注入，不再固定猫狗；
- 真实性和闭集分类 Prompt 已拆为共享的版本化模板；
- Pipeline 节点保存 `PromptBinding`，包含模板 ID、版本和校验和；
- PipelineVersion 可以按需导出确定性 YAML；
- Artifact 不允许由模型任意写文件或自由拼装；
- 当前 Prompt 和 Pipeline 都能在 Run 审计中回溯到真实生效版本。

### 3.2 PipelineExperience 与用户反馈

关键提交：

```text
ad27ed5 feat: record reusable pipeline experiences
439eecb feat: reuse successful pipeline experience safely
a47fe20 feat: expose run satisfaction feedback
```

完成内容：

- 从成功 Run 生成版本化 PipelineExperience；
- 记录 TaskSpec 特征、能力、策略、算子、指标和来源；
- 按相似需求召回历史经验；
- 历史经验只影响候选编译，不跳过当前 Catalog 和准入校验；
- 新增用户满意度与复用许可接口；
- 不满意、失败或未允许复用的经验不会成为正式参考。

### 3.3 P0-1：受治理 Control Tool 骨架

提交：

```text
dac50ef feat(p0): add governed control tool skeleton
```

新增：

```text
dataagent/tools/spec.py
dataagent/tools/registry.py
dataagent/tools/observations.py
dataagent/tools/planning.py
dataagent/tools/artifacts.py
dataagent/tools/inspection.py
dataagent/tools/control.py
```

第一批 Tool：

```text
retrieve_operators
compile_pipeline_artifact
validate_pipeline_artifact
query_control_facts
propose_control_action
```

已保证：

- 输入由 Pydantic 模型校验；
- 输出统一为结构化 `ToolResult`；
- Observation 附带 evidence 和 next actions；
- 重复注册失败；
- Tool 不直接写数据库；
- `propose_control_action` 仍经过 ActionPolicy；
- Registry 中不存在 `execute_operator`、任意 YAML、shell、Python 或任意文件 Tool。

当前 tools 层的准确状态是：**P0 核心控制工具已完成，不是完整通用工具平台。**

### 3.4 P0-2：PipelineArtifact 生产准入

提交：

```text
e6a694c feat(p0): validate PipelineArtifact production eligibility
```

校验内容：

- Schema 与字段结构；
- Pipeline checksum；
- OperatorVersion 是否真实存在；
- Operator 参数是否合法；
- Runtime 是否可承载；
- 生命周期是否达到生产准入；
- `DRAFT`、仅评测和 Mock 算子不能被误判为生产可执行。

模型可以建议 Pipeline，但不能靠输出一段看似正确的 YAML 绕过准入。

### 3.5 P0-3：Logical DatasetVersion

提交：

```text
c1998d5 feat(p0): publish logical dataset versions
```

核心模型：

```text
DatasetAsset
  source_uri
  source_sha256
  decision
  output_uri
  output_sha256
  asset_origin
  origin_dataset_version_id
  origin_run_id
  materialization
  reason_codes
  audit_refs

DatasetVersion
  version_kind
  parent_dataset_version_id
  repair_run_ids
  still_failed
  abandoned_assets
  excluded_assets
  deliverable
```

实现位置：

```text
dataagent/application/dataset_versions.py
dataagent/domain/runs/models.py
dataagent/execution/dataset_runner.py
```

Manifest 使用统一 builder/writer，原子落盘，并保留旧字段兼容。

### 3.6 P0-4：Deliverable Dataset Export

提交：

```text
b787aa3 feat(p0): export succeeded dataset deliverables
```

实现位置：

```text
dataagent/application/dataset_exports.py
```

导出规则：

- 关联 Run 必须为 `SUCCEEDED`；
- 不允许存在 `still_failed` 或未确认的 `abandoned_assets`；
- 所有引用文件必须存在且 SHA256 匹配；
- 目标目录已存在时拒绝覆盖；
- 先写 staging，再原子完成；
- 输出 `files/`、`manifest.json` 和 `excluded_assets.json`；
- RunStore 记录 `dataset_exported` 事件。

### 3.7 P0-5：Repair Run、合并和显式排除

关键提交：

```text
1871b7f feat(p0): govern repair run scope and lineage
ff87ce8 feat(p0): merge repaired dataset versions
29c422a feat(p0): resolve abandoned assets explicitly
```

Repair Run 新增操作血缘：

```text
operation_kind
parent_run_id
parent_dataset_version_id
repair_scope
repair_attempt
```

数据库会自动迁移旧 SQLite，补齐新增列。

运行约束：

- Repair scope 只能包含父版本失败资产；
- Worker 再次校验实际输入不能逃逸 repair scope；
- 父版本成功资产不会重复进入 Repair Run；
- 修复输出全失败时仍可发布逻辑修复结果，以便继续记录 `still_failed`；
- 生产初始 Run 的空结果仍按原规则阻断。

合并规则：

```text
父版本成功资产 + 修复输出
  -> 保留父版本资产，异常修复输出只记审计

父版本失败资产 + 修复成功
  -> 使用 Repair Run 输出，记录双重血缘

父版本失败资产 + 修复失败
  -> 进入 still_failed

父版本缺失资产 + 修复成功
  -> 补入新版本并标记 Repair 来源
```

第三次修复仍失败：

- 资产进入 `abandoned_assets`；
- 第四次自动 Repair 被拒绝；
- 用户确认后生成新的 resolved DatasetVersion；
- 资产进入 `excluded_assets`，原因码为 `USER_CONFIRMED_EXCLUSION`；
- 重新执行 QC；
- 只有 QC 通过才将关联 Run 晋升为 `SUCCEEDED`。

### 3.8 P0-6：API、TUI 和 grounded repair facts

关键提交：

```text
b2d8bbd feat(p0): expose dataset repair control api
0928f43 feat(p0): add repair controls to tui
c1f4da1 feat(p0): render grounded repair lineage facts
```

API：

```text
GET  /api/runs/{run_id}/repair-candidates
POST /api/runs/{run_id}/repairs
GET  /api/datasets/{dataset_version_id}/repair-candidates
POST /api/datasets/{dataset_version_id}/exclude-abandoned
POST /api/datasets/{dataset_version_id}/exports
```

TUI：

```text
/repair [run_id|dataset_version_id]
/exclude <dataset_version_id>
/export <dataset_version_id> <destination>
```

自然语言事实查询新增 `repair` facet，可回答：

- 当前还有哪些 `still_failed`；
- 哪些资产已 `abandoned`；
- 哪些资产被用户显式排除；
- 某资产来自父版本还是 Repair Run；
- 当前允许执行 repair、exclude 还是 export。

这些内容由 Manifest、RunStore 和 QC 渲染；模型只选择查询 facet，不能编造资产或路径。

### 3.9 P0-7：Acceptance Dataset 和真实 Provider 冒烟

关键提交：

```text
ae9961c feat(p0): build reproducible acceptance dataset
0dfa582 feat(p0): record real remote provider smoke
6dd19ca feat(p0): record acceptance run and export evidence
2f056e8 fix: align worker vision provider config
f07a957 docs: document p0 repair and acceptance workflow
```

### Acceptance Dataset v1

新增：

```text
dataagent/acceptance/dataset.py
scripts/build_p0_acceptance.py
```

当前本机已经确定性生成：

```text
D:\newDataAgent\.dataagent\acceptance\p0-v1\manifest.json
```

数据集共 60 个 case：

- 40 张基准图：20 cat + 20 dog；
- 20 个派生和故障 case；
- 覆盖 cat、dog、mixed、neither；
- 覆盖模糊、低光、低分辨率、异常裁剪；
- 覆盖重复、近重复；
- 覆盖 synthetic、screenshot、illustration；
- 覆盖 corrupt、unreadable、timeout；
- 覆盖多主体、遮挡和类别边界；
- 覆盖 repair_succeeds、abandoned、excluded。

Manifest 保存源文件和派生文件 SHA256、期望标签、故障模式与最终处置。生成目标已存在时拒绝覆盖。

### 真实单图 Remote VLM 冒烟

真实记录：

```text
D:\newDataAgent\.dataagent\acceptance\smoke\remote-vlm-2026-07-23.json
```

结果：

```text
status: PASSED
provider: datajuicer 1.5.3
operator: datajuicer.image_tagging_vlm_mapper.remote_api:2
model: qwen3.7-plus
remote calls: 1
duration: 25.611 s
structured tags: ["cat"]
failure reason codes: []
```

已验证真实百炼 OpenAI-compatible endpoint、Data-Juicer 进程、结构化输出适配器和 Provider 事件。记录不包含 API Key。

### 真实两图 Worker 小集合

真实记录：

```text
D:\newDataAgent\.dataagent\acceptance\smoke\small-set-2026-07-23.json
```

结果：

```text
run: run_57034642f4de4105
dataset: dataset_57034642f4de4105
qc: qc_report_57034642f4de4105
pipeline: pipeline_version_7e7d95b181e54caa
status: PASSED
inputs: cat_01.jpg, dog_01.jpg
kept: 2/2
classifications: cat, dog
remote calls: 2
node results: 6
duration: 50.736 s
failure reason codes: []
```

交付导出：

```text
D:\newDataAgent\.dataagent\acceptance\exports\small-real-2026-07-23
```

导出包含两张真实分类图片、Manifest 和 excluded report。

### 真实验收中发现并修复的配置问题

单图 Provider 冒烟成功，但 Worker 首次运行未使用与 API 相同的 `vision_model` 和 `base_url`。根因是 API 和 Worker 分别构造 Runtime，Worker 没有完整传递 Settings。

`2f056e8` 已让：

```text
apps/worker/runner.py
dataagent/application/run_worker.py
```

将视觉模型和 endpoint 配置一致地传给 Worker，并增加回归测试。

## 4. 当前进行到哪里

### 4.1 当前实现状态

P0 代码主线已经完整实现：

| 能力 | 状态 |
|---|---|
| 受治理 Tool 骨架 | 已完成 |
| PipelineArtifact 确定性编译与生产校验 | 已完成 |
| Logical DatasetVersion | 已完成 |
| Deliverable Dataset Export | 已完成 |
| Repair scope 与操作血缘 | 已完成 |
| RepairedDatasetVersion 合并 | 已完成 |
| still_failed / abandoned / excluded | 已完成 |
| Repair API 与 TUI 控制 | 已完成 |
| Grounded repair facts | 已完成 |
| Acceptance Dataset v1 生成器 | 已完成 |
| 真实 Remote 单图冒烟 | 已通过 |
| 真实两图 Worker + QC + Export | 已通过 |
| 60-case 完整付费 Remote 批跑 | 未执行 |
| 60-case 真实 Repair/Abandon/Exclude 全链验收 | 未执行 |

因此最准确的结论是：

> P0 实现与最低真实 Provider 冒烟已完成；完整 Acceptance Dataset v1 的真实付费批跑和真实故障修复战役尚未完成，不能写成“全面生产验收通过”。

### 4.2 当前自动测试

2026-07-23 重新执行：

```text
186 passed, 1 warning
```

唯一警告来自 Starlette `TestClient` 对 `httpx` 的弃用提示，不是业务失败。

测试已覆盖：

- TaskSpec 动态分类标签；
- Prompt 模板版本和 hash；
- PipelineArtifact round-trip、checksum 和生产准入；
- PipelineExperience 记录、召回和反馈；
- Tool Registry、输入契约、Observation 和 Policy violation；
- Logical DatasetVersion 构建和引用校验；
- `PARTIAL` 导出阻断；
- SUCCEEDED 导出、hash 校验和 excluded report；
- Repair scope、数据库自动迁移和 Worker scope 防逃逸；
- Repair 合并；
- 三次失败进入 abandoned；
- 显式 exclude 后重新 QC 并导出；
- Repair API、TUI 和自然语言 grounded facts；
- Acceptance Dataset 生成和校验；
- Provider smoke 与 acceptance run 证据采集；
- API/Worker 视觉模型配置一致性。

### 4.3 当前 Git 与文档状态

当前：

```text
branch: codex/hybrid-operator-retrieval
HEAD: f07a957
ahead of origin/codex/hybrid-operator-retrieval: 29 commits
```

本地存在未跟踪文件：

```text
CONTEXT.md
DataAgent-development-handoff-2026-07-20-v4.md
DataAgent-development-handoff-2026-07-20-v5.md
DataAgent-development-handoff-2026-07-22-v6.md
DataAgent-development-handoff-2026-07-23-v7.md
docs/
scripts/e2e_real_gateway.py
```

这些文件不要被下一位开发者误删，也不要在未确认归属和内容前一次性全部提交。

`.dataagent/acceptance` 下的图片、运行数据库、Provider 日志、真实冒烟 JSON 和导出目录默认不进入 Git。它们是本机真实证据，不是天然可移植的仓库证据。若要作为发布证据，需要另行做脱敏、归档和校验和清单。

## 5. 下一步计划

### P0 收口：完整 Acceptance Campaign

建议先把 P0 验收口径真正关严，再扩功能：

1. 为 60-case 运行建立固定的预算、调用数和最大耗时上限；
2. 先选择 5 到 10 个高风险 case 试跑，验证 Prompt、Provider 契约和失败注入；
3. 再运行完整 60-case Remote/Native 组合 Pipeline；
4. 保存 Run、DatasetVersion、QC、node results、Provider events、原因码、调用数、耗时和费用；
5. 对 `repair_succeeds` 执行真实 Repair Run，并确认只处理失败资产；
6. 对 `abandoned` 连续执行三次失败，验证第四次被阻断；
7. 用户显式排除 abandoned，确认生成新 DatasetVersion；
8. 从最终 `SUCCEEDED` 版本导出 Deliverable Dataset；
9. 核对导出文件数、分类目录、Manifest、excluded report 和所有 SHA256；
10. 将脱敏后的验收摘要和 checksums 归档为发布证据。

不要一开始就对 60 张图片盲目执行多个 Remote VLM 节点。当前真实数据表明单图约 25 秒，两图约 51 秒；先计算预计调用数和费用。

### P1：Remote Provider 韧性和成本治理

1. 区分连接、读取、单资产和 Dataset 总超时；
2. 对 429、5xx 和瞬时网络错误增加有限、可审计的指数退避；
3. 对参数错误、输出契约错误和内容拒绝禁止无意义重试；
4. 增加受控并发，不能一次放大到几十个付费请求；
5. 评估真实性判断和闭集分类能否使用一个版本化复合 Prompt，减少重复调用；
6. 建立缓存键：输入 hash + OperatorVersion + PromptBinding + 参数 + 模型版本；
7. 建立角色级和 Operator 级 Golden Set、延迟、费用和准确率基线。

### P1：Repair Review 和大规模审计

1. 为 node results 增加分页；
2. 支持按 source、decision、status、node、reason 过滤；
3. 支持 JSONL/CSV 审计导出；
4. 提供单资产完整节点轨迹；
5. 提供 abandoned/excluded 的人工复核列表；
6. 终态摘要直接展示失败资产和合法 next actions。

### P1：可移植安装矩阵

1. 在干净 Windows 环境验证 `catalog`、`cpu` 和 `remote` Profile；
2. 在干净 Linux CPU 环境验证相同流程；
3. 验证重复安装幂等、Registry 损坏恢复、离线阻断和卸载；
4. 验证 217 个发现项、Catalog digest、许可证和 wheel SHA256；
5. Linux GPU Worker 独立评测模型算子后再申请生产发布。

### P2：更多 Operator 正式准入

1. 保留完整 217 个动态发现目录；
2. Discovery Catalog 和 Admission Catalog 继续分离；
3. 当前图片 Agent 只召回图片相关能力；
4. CPU 图片算子按 Golden Set 和性能基线批量准入；
5. 每个模型算子绑定独立输入、输出和解析契约；
6. GPU/模型算子保持 Draft，直到许可证、revision、SHA256 和独立 Worker 评测完成；
7. 通过准入后再晋升 `PERSONAL_RELEASE`。

### 后续 Tool 层

P0 Tool 骨架完成后，不应立刻扩成任意自动化平台。后续优先加入：

- 有限的审计查询和导出 Tool；
- Repair 候选查询 Tool；
- Deliverable Export 提议 Tool；
- Provider 健康与成本估算 Tool；
- 所有有副作用动作仍需 confirmation 和 ActionPolicy。

暂不加入：

- 任意 shell；
- 任意 Python；
- 任意文件写入；
- 任意网络请求；
- `execute_operator`；
- 绕过 PipelineVersion 和 Worker 的 Provider 调用。

## 6. 必须记住的经验，不要重复踩坑

### 6.1 不要把“自动测试通过”写成“真实生产验收通过”

必须分层写结论：

```text
单元测试通过
集成测试通过
真实 Provider 单图通过
真实 Worker 小集合通过
完整 Acceptance Dataset 通过
干净机器与生产规模通过
```

当前只到前四层，完整 60-case Campaign 尚未执行。

### 6.2 API 和 Worker 必须使用同一份有效配置

API 中能调用模型，不代表 Worker 一定拿到了相同的：

```text
VISION_MODEL
MODEL_BASE_URL
MODEL_API_KEY
Provider Registry
timeout
output contract
```

真实验收时必须核对 Worker 的生效配置和 Run 证据，不能只跑一个 API 侧脚本。

### 6.3 不要把 217 个 Discoverable 算子写成 217 个 Released 算子

每个算子至少要分别检查：

```text
discovery
normalization overlay
current Agent relevance
runtime compatibility
dependencies
parameter schema
I/O contract
output adapter
license
evaluation evidence
lifecycle
```

完整动态 Catalog 是“看得见”，正式准入目录才是“允许生产执行”。

### 6.4 不要让模型直接执行算子或写 Pipeline YAML

模型擅长理解用户意图和提出候选，但以下内容必须确定性完成：

- PipelineArtifact 序列化；
- checksum；
- OperatorVersion 解析；
- 参数校验；
- Runtime 校验；
- 生命周期校验；
- ActionPolicy；
- 状态变更；
- Worker 执行。

### 6.5 不要把 Prompt 模板、Schema 和 Catalog 每任务复制一份

共享规范按版本集中管理；任务只保存：

- TaskSpec revision；
- PipelineVersion；
- PromptBinding；
- 模板 ID、版本和 hash；
- 动态变量；
- Provider 和模型版本；
- 必要审计引用。

完整快照只在发布、迁移或归档需要时按需生成。

### 6.6 不要把历史 Pipeline 当成无需验证的答案

PipelineExperience 只提供经验先验。每次复用仍要：

- 匹配当前 TaskSpec；
- 使用当前 Catalog；
- 检查当前 Runtime；
- 重新验证 Operator 生命周期；
- 重新编译 Pipeline；
- 重新获得用户批准。

### 6.7 不要从 `PARTIAL` 直接导出交付目录

`PARTIAL` 必须经过：

```text
repair
  -> repaired version
  -> still_failed 或 abandoned
  -> 必要时显式 exclude
  -> QC PASSED
  -> SUCCEEDED
  -> export
```

### 6.8 不要覆盖旧 Run、DatasetVersion、验收记录或导出目录

所有版本和证据默认不可变。重跑、修复、排除和导出创建新对象。脚本拒绝覆盖已有目标是有意设计，不要为了“方便”改成静默覆盖。

### 6.9 修复必须同时保留两种血缘

- Run 保存操作血缘：为什么执行、修了谁、父 Run 是谁；
- DatasetVersion 保存交付血缘：最终每个资产来自父版本还是 Repair Run。

只保留其中一种，最终 Manifest 都无法完整解释。

### 6.10 abandoned 不能自动转成排除

连续三次失败只代表系统无法处理，不代表用户同意丢弃。必须显式确认，并把资产、原因和审计引用写入 `excluded_assets.json`。

### 6.11 不要把空 VLM 输出降级成正常 unknown

空或畸形输出可能代表接口错误、Prompt 不兼容、response path 错误或 Provider 解析失败。必须报输出契约错误，不能因为保留优先策略而把全批图片伪装成成功。

### 6.12 不要给所有 Data-Juicer 模型算子套同一个解析器

`{"tags":[...]}` 是 `image_tagging_vlm_mapper` 的特定契约，不是 Data-Juicer 全局契约。Output Adapter 必须绑定 Provider Operator、版本和 Variant，并逐个建立测试与 Golden Set。

### 6.13 Tool 不等于 Operator，也不等于任意系统能力

Tools 层目前完成的是受治理控制入口。不要因为目录名叫 `tools` 就加入任意联网、文件、shell 或 Python 能力。每个 Tool 都应有窄输入、窄输出、副作用声明、确认要求和审计证据。

### 6.14 不要把运行目录中的真实证据误认为已经进入版本库

`.dataagent` 默认被忽略。真实冒烟 JSON、数据库、图片和导出在本机存在，但换机器或清理目录后可能丢失。正式发布前应生成脱敏摘要、checksums 和独立归档。

### 6.15 修改服务代码后要重启实际受影响的进程

TUI 是客户端。修改 API、Worker、Catalog 或 Provider 配置后，只关闭并重开 TUI 不会让旧 Worker 更新。验收前要确认运行的是当前 HEAD 对应进程。

## 7. 关键代码位置

```text
dataagent/tools/
  受治理 Control Tool、Registry、Observation 和第一批 Tool

dataagent/application/pipeline_artifacts.py
  PipelineVersion 的确定性 YAML Artifact

dataagent/prompts.py
dataagent/resources/prompts/
  版本化 Prompt Registry 和内置模板

dataagent/agents/processing/nodes.py
  TaskSpec 类别、策略参数、PromptBinding 和 Pipeline 编译

dataagent/experiences.py
dataagent/domain/experiences/
  PipelineExperience 记录、检索和复用约束

dataagent/application/dataset_versions.py
  Logical DatasetVersion、Repair merge、abandoned/excluded

dataagent/application/dataset_exports.py
  SUCCEEDED 版本的完整交付导出

dataagent/application/agent_runtime.py
  Run、Repair、候选查询、Exclude、Export 和反馈用例

dataagent/execution/dataset_runner.py
  Worker 执行、节点审计、逻辑发布、QC 和修复合并

dataagent/infrastructure/database.py
  Run operation lineage、自动迁移、事件和节点结果

dataagent/application/conversation.py
dataagent/application/conversation_actions.py
dataagent/application/conversation_policy.py
  模型优先意图、Action 契约、Repair fact 和 Policy

apps/api/main.py
  Repair、Exclude、Export、Feedback API

apps/tui/app.py
apps/tui/session.py
apps/tui/api_client.py
  /repair、/exclude、/export 和状态展示

dataagent/acceptance/dataset.py
dataagent/acceptance/provider_smoke.py
scripts/build_p0_acceptance.py
scripts/run_p0_provider_smoke.py
scripts/record_p0_acceptance_run.py
  P0 Acceptance Dataset、真实 Provider 冒烟和验收证据

apps/worker/runner.py
dataagent/application/run_worker.py
  Worker Settings 和视觉 Provider 配置传递
```

## 8. 接手后的检查步骤

### 8.1 Git 与测试

```powershell
cd D:\newDataAgent
git status --short --branch
git log --oneline --decorate -30
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

当前预期：

```text
186 passed, 1 warning
```

### 8.2 Provider

```powershell
.\.venv\Scripts\dataagent.exe provider verify
.\.venv\Scripts\dataagent.exe provider report --blocked-only
```

新机器安装：

```powershell
.\.venv\Scripts\dataagent.exe setup --with-provider datajuicer@1.5.3 --profile auto
```

### 8.3 P0 Acceptance Dataset

```powershell
.\.venv\Scripts\python.exe scripts\build_p0_acceptance.py `
  --source D:\data\cats_dogs_mixed `
  --destination .dataagent\acceptance\p0-v1
```

### 8.4 真实 Remote 单图冒烟

```powershell
.\.venv\Scripts\python.exe scripts\run_p0_provider_smoke.py `
  --repo . `
  --image .dataagent\acceptance\p0-v1\assets\base\cat_01.jpg `
  --output .dataagent\acceptance\smoke\remote-vlm-new.json
```

不要覆盖已有的 `remote-vlm-2026-07-23.json`。

### 8.5 记录完成 Run 和 Export

```powershell
.\.venv\Scripts\python.exe scripts\record_p0_acceptance_run.py `
  --repo . `
  --runtime-home <本次运行目录> `
  --run-id <run_id> `
  --owner <owner> `
  --export <新的导出目录> `
  --output <新的验收记录.json>
```

### 8.6 TUI 修复交付链

```text
/repair <run_id|dataset_version_id>
/exclude <dataset_version_id>
/export <dataset_version_id> <destination>
```

同时核对：

```text
RunSnapshot
Run events
run_node_results
DatasetVersion manifest
QCReport
Provider stdout/stderr summary
实际引用文件
Deliverable Export
excluded_assets.json
```

## 9. v6 之后提交索引

```text
901d3a6 feat: support task-specific classification labels
d1baf68 feat: version operator prompt templates
64359ea feat: export pipeline versions on demand
ad27ed5 feat: record reusable pipeline experiences
439eecb feat: reuse successful pipeline experience safely
a47fe20 feat: expose run satisfaction feedback
dac50ef feat(p0): add governed control tool skeleton
e6a694c feat(p0): validate PipelineArtifact production eligibility
c1998d5 feat(p0): publish logical dataset versions
b787aa3 feat(p0): export succeeded dataset deliverables
1871b7f feat(p0): govern repair run scope and lineage
ff87ce8 feat(p0): merge repaired dataset versions
29c422a feat(p0): resolve abandoned assets explicitly
b2d8bbd feat(p0): expose dataset repair control api
0928f43 feat(p0): add repair controls to tui
c1f4da1 feat(p0): render grounded repair lineage facts
ae9961c feat(p0): build reproducible acceptance dataset
0dfa582 feat(p0): record real remote provider smoke
6dd19ca feat(p0): record acceptance run and export evidence
2f056e8 fix: align worker vision provider config
f07a957 docs: document p0 repair and acceptance workflow
```

## 10. 一句话接手结论

DataAgent 当前已经具备“结构化规划、真实 Pipeline、受治理 Tool、真实 Worker、逐节点审计、逻辑数据集、失败资产修复、显式排除和最终导出”的 P0 代码闭环，并已通过真实 Remote VLM 单图和两图小集合验证；下一步最优先的工作不是继续堆功能，而是用完整 Acceptance Dataset v1 跑通受预算约束的真实 Repair/Abandon/Exclude/Export 验收，归档可移植证据后再进入 P1 的并发、成本、审计工作台和更多算子准入。
