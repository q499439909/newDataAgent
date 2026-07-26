# DataAgent P0 开发方案

> 日期：2026-07-23  
> 上游边界文档：`docs/development-boundaries-2026-07-23.md`  
> 目标：为 DataAgent 的 P0 开发提供可执行方案、模块边界、验收门槛和明确不做事项。

## 1. P0 目标

P0 的目标不是扩展更多算子，也不是搭一个完整 Web 平台。P0 要完成 Agent 控制平面最核心的生产闭环：

```text
LLM 通过结构化 Tool 规划和提出动作
  -> 控制面确定性校验
  -> Worker 生成逻辑 DatasetVersion
  -> 失败资产定向 Repair Run
  -> 合并为 RepairedDatasetVersion
  -> abandoned / excluded 资产显式处理
  -> SUCCEEDED 逻辑版本
  -> Deliverable Dataset Export
```

P0 完成后，DataAgent 应能证明：

1. 模型不能绕过控制面写状态；
2. Pipeline 产物由确定性工具编译和验证，不由模型手写 YAML；
3. DatasetVersion 是可审计逻辑版本，不再默认每版复制全量图片；
4. `PARTIAL` 只能是中间态；
5. 最终交付只能从 `SUCCEEDED` 逻辑版本导出完整图片目录；
6. 失败资产可以重试、合并、放弃、剔除，并保留证据。

## 2. P0 硬边界

### P0 做

- `ToolSpec` / `ToolRegistry` / `ToolResult` 骨架；
- 第一批窄 Tool：`retrieve_operators`、`compile_pipeline_artifact`、`validate_pipeline_artifact`、`query_control_facts`、`propose_control_action`；
- Tool observation 结构化返回；
- `PipelineArtifact` 编译与验证接入 Tool 层；
- Logical DatasetVersion 发布结构；
- Deliverable Dataset Export；
- Repair Run 的 operation lineage；
- RepairedDatasetVersion 的 delivery lineage；
- `still_failed`、`abandoned`、`excluded_assets`；
- 3 次修复尝试上限；
- Acceptance Dataset v1 的定义和最小验收脚本；
- 必要 API / TUI 最小入口，用于当前测试。

### P0 不做

- 不新增大批 Data-Juicer 算子；
- 不做完整 Tool UI；
- 不做完整 Web 工作台；
- 不做 shell/python/file tools；
- 不做 `execute_operator` 或 `execute_provider_operator` 这类模型可直接触达算子的 Tool；
- 不做多 Worker 调度；
- 不做 GPU 算子正式发布；
- 不从 `PARTIAL` 导出完整交付数据集；
- 不用模拟测试代替真实 Provider 冒烟；
- 不把 Tool 写成第二套业务状态机。

## 3. 架构边界

### Tool 层

Tool 是模型可调用的结构化控制面入口，不是算子，不是脚本执行器。

第一版 Tool 分组：

```text
Planning Tools:
  retrieve_operators

Artifact Tools:
  compile_pipeline_artifact
  validate_pipeline_artifact

Inspection Tools:
  query_control_facts

Control Tools:
  propose_control_action
```

Tool 调用权限：

```text
auto:
  retrieve_operators
  validate_pipeline_artifact
  query_control_facts

draft_only:
  compile_pipeline_artifact
  propose_control_action

confirmation_required:
  submit_run
  retry_failed_assets
  exclude_abandoned_assets
  export_deliverable_dataset

forbidden:
  execute_operator
  execute_provider_operator
  write_arbitrary_yaml
  delete_dataset
  run_shell
  run_python
```

Tool executor 是薄入口：

```text
Tool call
  -> validate input model
  -> call AgentRuntime / OperatorRegistry / VersionStore / deterministic compiler
  -> return ToolResult
```

任何会改变 TaskSpec、Pipeline、Run、DatasetVersion 或 Export 的动作必须回到 `ActionPolicy` 和 `AgentRuntime`。Tool 不直接写数据库。

### Operator 层

Operator 是 Pipeline 内部数据处理能力，由 Worker 执行。

禁止：

```text
LLM -> Tool -> Operator
```

必须保持：

```text
LLM -> Tool -> ActionPolicy / AgentRuntime -> Worker -> Operator
```

### Dataset 层

P0 后 DatasetVersion 默认是逻辑版本：

```text
DatasetVersion
  manifest
  asset references
  lineage
  QC reference
  audit references
  still_failed
  abandoned
  excluded_assets
```

Deliverable Dataset Export 才是完整图片目录：

```text
Deliverable Dataset Export
  source DatasetVersion status == SUCCEEDED
  full materialized image files
  manifest copy
  excluded_assets report
```

## 4. 开发阶段

### P0-0：冻结术语与边界

状态：已基本完成。

输入：

- `CONTEXT.md`
- `docs/development-boundaries-2026-07-23.md`

产出：

- P0 方案文档；
- 术语保持一致：`Control Tool`、`Operator`、`PipelineArtifact`、`Logical DatasetVersion`、`RepairedDatasetVersion`、`Deliverable Dataset Export`。

验收：

- 文档中不再使用 `recipe` 指代 DataAgent 内部 PipelineArtifact；
- 文档中不再把 Tool 和 Operator 混用；
- `PARTIAL` 明确是非最终状态。

### P0-1：Tool 骨架

建议新增：

```text
dataagent/tools/
  __init__.py
  spec.py
  registry.py
  observations.py
  planning.py
  artifacts.py
  inspection.py
  control.py
```

核心模型：

```text
ToolSpec
  name
  description
  input_model
  output_model
  executor
  tags
  effect
  confirmation

ToolResult
  ok
  tool
  status
  summary
  data
  evidence
  next_actions
  requires_confirmation
  error_type
```

第一批工具：

```text
retrieve_operators
compile_pipeline_artifact
validate_pipeline_artifact
query_control_facts
propose_control_action
```

实现边界：

- `retrieve_operators` 只读 OperatorRegistry；
- `compile_pipeline_artifact` 只生成草案产物，不批准 Pipeline；
- `validate_pipeline_artifact` 做 schema、checksum、operator availability、parameter schema 校验；
- `query_control_facts` 复用现有事实查询渲染，不让模型编事实；
- `propose_control_action` 包装 `ConversationAction`，仍走 ActionPolicy。

测试：

- Tool input Pydantic 校验；
- Tool duplicate registration 报错；
- Tool observation 字段完整；
- forbidden tool 不存在于 registry；
- `propose_control_action` 在非法 workflow 状态返回 policy violation，而不是执行。

完成门槛：

- 不接 LLM 也能通过单元测试执行 Tool；
- Tool 不直接写库；
- ToolResult 可被后续 LLM observation 使用。

### P0-2：PipelineArtifact 编译与验证

当前已有：

```text
dataagent/application/pipeline_artifacts.py
```

P0 不重写它，而是把它接入 Tool：

```text
compile_pipeline_artifact
  input: pipeline draft / pipeline version reference
  output: pipeline_artifact_id 或 artifact payload

validate_pipeline_artifact
  input: artifact content 或 artifact id
  output: schema_ok, checksum_ok, operators_ok, parameters_ok, blockers
```

边界：

- 不叫 recipe；
- 不提供 `write_arbitrary_yaml`；
- YAML/JSON 只能由确定性 serializer 生成；
- checksum mismatch 必须失败；
- operator 参数必须经过现有 `validate_parameters`。

测试：

- round-trip YAML parse；
- checksum mismatch；
- operator unavailable；
- parameter schema violation；
- Data-Juicer draft operator 不得被误判 Released。

完成门槛：

- LLM 不需要手写 Pipeline YAML；
- 编译产物可校验、可落盘、可引用。

### P0-3：Logical DatasetVersion

当前风险：

`dataset_runner.py` 当前会复制 kept 资产到 run staging，再把 staging 变成 dataset 目录。P0 要把 DatasetVersion 改成逻辑版本，避免修复链复制全量图片。

建议新增或扩展字段：

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
  version_kind: logical
  parent_dataset_version_id
  repair_run_ids
  still_failed
  abandoned_assets
  excluded_assets
  deliverable: false
```

`asset_origin` 建议枚举：

```text
run_output
parent_dataset_version
repair_run
excluded
```

`materialization` 建议枚举：

```text
local_file
referenced_file
not_materialized
```

边界：

- 初次 Run 可继续物化本次 kept 文件；
- RepairedDatasetVersion 不复制父版本成功文件，只引用父版本 output_uri；
- excluded / abandoned 不作为成功文件，但必须进入 manifest；
- source hash 校验仍保留。

测试：

- 初次 DatasetVersion manifest 仍完整；
- Logical DatasetVersion 可引用父版本资产；
- 父版本资产缺失时校验失败或标记 broken reference；
- manifest 原子写；
- source_count、kept_count、rejected_count、failed_count 语义不被破坏。

完成门槛：

- 每个 DatasetVersion 都有 manifest；
- 修复链不需要复制全量图片；
- API/TUI 仍能读取 DatasetVersion 摘要。

### P0-4：Deliverable Dataset Export

新增应用用例：

```text
export_deliverable_dataset(dataset_version_id, destination)
```

规则：

- 只允许 `SUCCEEDED` DatasetVersion；
- `PARTIAL`、`FAILED`、仍有 `still_failed` 的版本禁止导出；
- 如果存在 `excluded_assets`，导出包必须包含 excluded report；
- 导出完整图片目录；
- 导出 manifest 副本；
- 校验引用资产存在且 hash 匹配；
- destination 已存在时默认拒绝覆盖。

建议导出结构：

```text
exports/export_xxx/
  manifest.json
  excluded_assets.json
  files/
    ...
```

测试：

- `PARTIAL` 导出失败；
- `SUCCEEDED` 导出成功；
- missing referenced file 失败；
- hash mismatch 失败；
- excluded report 一起输出。

完成门槛：

- 最终交付文件只从 SUCCEEDED 逻辑版本生成；
- 用户可以明确看到剔除资产。

### P0-5：Repair Run 与 RepairedDatasetVersion

Repair Run 输入范围：

```text
parent failed assets
parent missing assets
not parent successful assets
```

Repair Run 操作血缘：

```text
parent_run_id
parent_dataset_version_id
repair_scope
repair_attempt
```

RepairedDatasetVersion 交付血缘：

```text
parent_dataset_version_id
repair_run_ids
asset_lineage
still_failed
abandoned_assets
excluded_assets
```

合并规则：

```text
parent success + repair output -> keep parent, record ignored/violation
parent failed + repair success -> use repair asset
parent failed + repair failed -> still_failed
parent missing + repair success -> use repair asset with lineage
```

修复尝试：

```text
attempts < 3 -> still retryable
attempts >= 3 -> abandoned, no automatic retry
abandoned -> user can explicitly exclude
exclude confirmed -> excluded_assets, can reach SUCCEEDED if no still_failed remains
```

测试：

- Repair Run 不包含父版本成功资产；
- 修复成功资产合入；
- 修复失败资产进入 `still_failed`；
- 第三次失败进入 `abandoned`；
- 未确认 exclude 时不可 SUCCEEDED；
- 确认 exclude 后进入 `excluded_assets`；
- RepairedDatasetVersion manifest 展平 asset lineage。

完成门槛：

- 从 PARTIAL 到 SUCCEEDED 的修复链可跑通；
- 所有丢弃/剔除资产有证据；
- 旧 DatasetVersion 和旧 Run 不被覆盖。

### P0-6：API / TUI 最小接入

P0 不做 Web 工作台，但当前 TUI/API 需要能测试闭环。

API 最小能力：

```text
GET repair candidates for dataset/run
POST retry failed assets
GET repaired dataset version
POST confirm exclude abandoned assets
POST export deliverable dataset
```

TUI 最小能力：

```text
/repair [run_id|dataset_version_id]
/exclude [dataset_version_id]
/export [dataset_version_id] [destination]
```

自然语言事实查询：

- 可查询 still_failed；
- 可查询 abandoned；
- 可查询 excluded_assets；
- 可查询 asset lineage；
- 回答必须来自 manifest / audit / QC。

测试：

- API 端到端；
- TUI session 单元测试；
- 自然语言查询不编造路径或数量。

完成门槛：

- 不打开 Web 也能完成 P0 手工验收；
- 用户能看到下一步可执行动作。

### P0-7：Acceptance Dataset v1 与真实冒烟

Acceptance Dataset v1 规模：50 到 100 张。

必须覆盖：

```text
cat / dog / mixed / neither
blur / low light / low resolution / crop abnormal
duplicate / near duplicate
synthetic / screenshot / illustration
corrupt file / unreadable path / timeout simulation
multi-subject / occlusion / boundary class
repair succeeds after initial failure
abandoned after 3 failures
excluded after user confirmation
```

真实冒烟：

- 1 张图验证真实 Remote VLM 结构化输出；
- 小集合验证 Provider stdout/stderr、node results、QC；
- Acceptance Dataset v1 验证完整修复交付链。

记录：

```text
run_id
dataset_version_id
qc_report_id
provider version
model version
duration
remote call count
failure reason codes
export path
```

完成门槛：

- 真实 Provider 至少冒烟通过；
- Acceptance Dataset v1 可复跑；
- 失败不静默降级为 unknown。

## 5. 建议开发顺序

严格建议按以下顺序提交，每步保持测试通过：

```text
1. Tool 骨架与 observation
2. PipelineArtifact tools
3. Logical DatasetVersion manifest schema
4. Deliverable Dataset Export
5. Repair Run scope 与 lineage
6. RepairedDatasetVersion merge
7. abandoned / excluded_assets
8. API / TUI 最小接入
9. Acceptance Dataset v1 与真实冒烟脚本
```

不要先做第 6 步。否则修复合并会继续绑在“每版复制目录”的旧发布模型上。

## 6. 测试策略

### 单元测试

- ToolSpec / ToolRegistry / ToolResult；
- PipelineArtifact 编译、校验、checksum；
- Logical manifest builder；
- Repair merge 纯函数；
- export eligibility；
- abandoned/excluded 状态转换。

### 集成测试

- Run -> PARTIAL DatasetVersion；
- Repair Run -> RepairedDatasetVersion；
- abandoned -> explicit exclude -> SUCCEEDED；
- SUCCEEDED -> Deliverable Export；
- natural language query -> grounded facts；
- Tool call -> Policy violation -> structured observation。

### 真实冒烟

- Remote VLM 单图；
- 小集合；
- Acceptance Dataset v1。

## 7. 迁移策略

当前已有测试和 CLI 假设 DatasetVersion 有文件目录。P0 迁移要保守：

1. 先保持初次 Run 的当前文件物化行为；
2. 对 RepairedDatasetVersion 引入引用式逻辑 manifest；
3. 再逐步让 DatasetVersion 语义以 manifest 为准；
4. 最后将完整图片目录语义迁移到 Deliverable Dataset Export。

兼容要求：

- 旧 API 字段不立即删除；
- 新字段可选引入；
- 测试先覆盖新逻辑，再收紧旧行为；
- README 在代码完成后再更新用户命令。

## 8. 风险与防线

| 风险 | 防线 |
|---|---|
| Tool 变成第二套业务逻辑 | Tool executor 只调用 AgentRuntime / Policy |
| 模型直接执行算子 | 禁止 execute_operator / execute_provider_operator tools |
| 修复链复制全量图片 | Logical DatasetVersion 引用父资产 |
| `PARTIAL` 被误交付 | export 只接受 SUCCEEDED |
| abandoned 资产静默消失 | excluded_assets 必须随交付输出 |
| YAML 再次由模型手写 | 只允许 compile_pipeline_artifact |
| 大 Run 审计不可用 | P0 保留证据，P1 做分页过滤 |
| 真实 Provider 问题被模拟测试掩盖 | P0 必须保留真实冒烟记录 |

## 9. P0 完成定义

P0 完成时，必须满足：

1. 第一批 Tool 可用，且模型不能通过 Tool 绕过 Policy；
2. PipelineArtifact 可确定性编译和验证；
3. DatasetVersion 支持逻辑版本；
4. `PARTIAL` 不能导出；
5. Repair Run 只处理失败/缺失资产；
6. RepairedDatasetVersion 保留完整 delivery lineage；
7. 3 次失败后进入 abandoned；
8. 用户确认后进入 excluded_assets；
9. SUCCEEDED 版本可导出完整图片目录；
10. Acceptance Dataset v1 或其前置小集合能复现完整链路。

## 10. P0 之外

以下进入 P1 或更后：

- Repair Review Workbench；
- 审计分页过滤和 JSONL/CSV 导出；
- Remote Provider 受控并发、成本治理和缓存；
- 干净 Windows/Linux 安装矩阵；
- 更多 Data-Juicer 算子正式准入；
- Linux GPU Worker；
- 完整 Tool UI；
- 多 Worker 调度；
- 外部 recipe export。
