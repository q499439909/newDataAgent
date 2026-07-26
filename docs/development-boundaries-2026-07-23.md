# DataAgent 后续开发问题与边界

> 日期：2026-07-23  
> 基于：`README.md`、`DataAgent-development-handoff-2026-07-22-v6.md`、`docs/dja-reference-analysis.md` 和当前代码结构。  
> 目的：把下一阶段需要拍板的问题、可进入开发的边界、暂不进入开发的范围写清楚，避免继续堆描述性能力。

## 1. 当前判断

DataAgent 已经越过“能生成一条看起来合理的 Pipeline”的阶段，进入“真实运行、可审计、可修复、可验收”的阶段。下一阶段产品定位不是单纯的本地图片目录工具，而是**可扩展的 Agent 控制平面平台**。后续开发不应优先扩展更多算子和交互话术，而应优先收紧以下闭环：

```text
真实输入
  -> 版本化 TaskSpec
  -> 受治理的算子检索与 Pipeline 编译
  -> 人工批准
  -> 独立 Worker 执行
  -> 逐资产逐节点审计
  -> DatasetVersion / Manifest / QC
  -> 部分失败隔离
  -> 修复型合并
  -> 可复验的验收证据
```

一句话边界：**模型负责提出结构化意图和候选动作，控制平面负责校验和修改状态，Worker 负责执行已批准版本，任何事实回答都必须来自持久化证据。**

## 2. 已确认决策

1. **产品方向**：下一阶段以可扩展的 Agent 控制平面平台为目标，而不是只完成一个本地 CLI/TUI 工具。
2. **验收数据集**：不固定使用当前 37 张猫狗图片作为最终验收集；它可以继续用于阶段性测试，但后续需要更复杂、更接近真实生产的数据集。
3. **`PARTIAL` 语义**：`PARTIAL` 只允许作为中间状态。最终交付必须修复到 `SUCCEEDED`，不能把部分成功数据集当成正式交付结果。
4. **P0 主线**：优先完成失败资产重试后合并回父 Dataset，形成可修复、可追溯、可最终成功的生产闭环。
5. **交互入口**：当前继续使用 TUI 做测试驾驶舱；后续正式审核体验转向 Web 图片审核工作台。
6. **修复产物命名**：修复合并后产生的新不可变数据集版本命名为 `RepairedDatasetVersion`。它不是新的 Dataset 类型，而是由父 `DatasetVersion` 的成功资产和一个或多个 Repair Run 的成功资产合并出的新 `DatasetVersion`。
7. **修复血缘位置**：Repair Run 记录操作血缘，例如 `parent_run_id` 和失败资产范围；`RepairedDatasetVersion` 的 Manifest 记录交付血缘，例如 `parent_dataset_version_id`、`repair_run_ids`，以及每个资产来自父版本还是修复 Run。
8. **修复范围**：Repair Run 只允许处理父版本中失败或缺失的资产，不应运行父版本已经成功的资产。若异常产出父版本已成功资产，合并时不得覆盖父版本成功资产，只能记录为 ignored 或 contract violation。
9. **修复尝试上限与剔除**：同一资产修复超过 3 次仍失败后标记为 `abandoned`，不再自动重试。系统不得自动把它排除后交付；用户必须显式确认剔除。剔除后的资产仍必须随交付输出为 `excluded_assets` 清单和审计证据，不能静默消失。
10. **逻辑版本与交付导出分离**：中间 `DatasetVersion` / `RepairedDatasetVersion` 是逻辑版本，必须落盘 Manifest、血缘、审计引用、失败/剔除清单和可验证资产引用，但不重复复制全量图片。只有最终 `SUCCEEDED` 版本才生成完整图片目录，作为 `Deliverable Dataset Export`。
11. **交付导出门槛**：`Deliverable Dataset Export` 只允许从 `SUCCEEDED` 的逻辑 `DatasetVersion` 生成。`PARTIAL` 只能预览、审计和继续修复，不能导出完整交付目录。
12. **Web 第一版范围**：Web 图片审核工作台第一版命名为 Repair Review Workbench，聚焦失败资产查看、节点审计、修复触发、abandoned 资产显式剔除确认，以及从 `SUCCEEDED` 版本触发交付导出；不做完整任务创建、复杂 Pipeline 编辑、多用户后台、算子市场或监控大屏。
13. **验收数据集**：后续正式验收使用 `Acceptance Dataset v1`，规模约 50 到 100 张，覆盖多类别、质量问题、重复/近重复、合成或非真实图、故障样本、语义歧义、可修复失败和 abandoned/excluded 场景。
14. **Tool 层方向**：引入给 LLM 使用的 `Control Tool` 层，把对话从 JSON 文本升级到原生 function/tool calling。Tool 是控制面能力，Operator 是 Pipeline 算子，二者不能混用命名或职责。Control Tool 调用前后仍必须经过 Action Schema、Action Policy、AgentRuntime 和持久化证据渲染。
15. **Tool 第一版范围**：第一版 Tool 层同时包含控制、规划、产物和查询能力，但都必须是窄接口：`retrieve_operators`、`retrieve_pipelines`、`compile_pipeline_artifact`、`validate_pipeline_artifact`、`propose_control_action`、`query_control_facts`。其中 Pipeline 产物叫 `PipelineArtifact`，不叫 recipe；只有导出给 Data-Juicer 或外部系统时才使用 recipe 术语。
16. **Tool 执行边界**：Tool 是薄入口，不拥有业务状态机。ToolRegistry 可独立于 `AgentRuntime`，用于声明工具、schema、effects 和 confirmation；具体 executor 可以注入 `AgentRuntime`、OperatorRegistry 或 VersionStore。凡是会改变 TaskSpec、Pipeline、Run、DatasetVersion 或 Export 的动作，都必须回到 ActionPolicy 和 `AgentRuntime`，Tool 不得直接写数据库或绕过状态校验。
17. **Tool 调用权限分级**：LLM 可自动调用检索、查询、验证类 Tool；编译和控制动作只能生成草案；提交 Run、重试失败资产、剔除 abandoned 资产和导出交付数据集必须用户确认；执行 Operator、直接执行 Provider、任意写 YAML、删除 Dataset、运行 shell/python 明确禁止进入模型 Tool 层。
18. **Tool Observation**：Tool 返回给 LLM 的 observation 必须结构化，包含 `ok`、`tool`、`status`、`summary`、`data`、`evidence`、`next_actions`、`requires_confirmation` 和 `error_type`。事实必须通过 `evidence` 指向真实对象 ID 或 URI，例如 operator、pipeline artifact、run、dataset version、QC report、manifest 或 node result，避免模型根据自由文本补细节。
19. **Tool P0 范围**：Tool 骨架进入 P0，但只做 `ToolSpec` / `ToolRegistry` / `ToolResult`、`retrieve_operators`、`compile_pipeline_artifact`、`validate_pipeline_artifact`、`query_control_facts` 和 `propose_control_action` adapter；不做完整工具平台、不做 Tool UI、不做 shell/python/file tools、不做 execute_operator 或 execute_provider_operator。

## 3. 仍需回答的问题

### 3.1 产品验收问题

1. 首个可对外宣称的版本，叫“Agent 控制平面平台雏形”还是更具体的“图片数据生产 Agent 控制平面”？
2. 既然 `PARTIAL` 不能作为最终交付，下游是否应默认拒绝消费 `PARTIAL`，还是允许开发者显式 override？
3. 语义质量在没有 Golden Set 前能怎么表述？建议只说“规则与模型代理评估通过/未通过”，不要说“准确率”。
4. P0 修复闭环是否必须在 TUI 中可操作，还是 API/CLI 可完成、TUI 可查看即可？

### 3.2 数据版本与修复问题

1. 多次修复是否允许链式合并？如果允许，需要限制最大链深或提供 lineage 展平视图吗？

### 3.3 算子与 Provider 准入问题

1. `Discoverable`、`Matched`、`Executable`、`Released`、`Draft` 的状态机是否需要写成正式文档和枚举迁移规则？
2. Data-Juicer 217 个算子中，下一批正式准入的 CPU 图片算子是哪 5 到 10 个？
3. 每个模型算子的输出契约由谁维护：Provider Overlay、Operator Variant，还是独立 Output Adapter Registry？
4. Remote VLM 的真实性判断与猫狗分类是否合并为一次复合契约调用？如果合并，契约版本如何命名？
5. Linux GPU Worker 需要达到什么证据才允许从 `Draft` 晋升？至少应包含 CUDA 环境、模型 revision、依赖锁、许可证和 Golden Set 结果。

### 3.4 运行治理问题

1. Remote 调用的默认并发上限是多少？建议先从 1 或 2 起步，用真实耗时与费用再调。
2. 连接超时、读取超时、单资产超时、整批预算是否分开配置？
3. 限流、服务端错误、内容拒绝、解析失败、参数错误是否进入不同 reason code？
4. 是否要缓存同一图片、同一 Prompt、同一模型版本的 Remote VLM 响应？
5. 成本上限是按 Run、按 Owner、按日，还是当前阶段只做 Run 级保护？

### 3.5 用户体验问题

1. TUI 在转向 Web 前需要保留到什么能力水平：只测试流程，还是要覆盖修复、审计、结果查看？
2. 审计结果在 TUI 中优先呈现“失败项列表”，还是“单图完整轨迹”？
3. Run 完成通知是否要显示下一步可执行命令，例如重试失败项、查看审计、打开 Dataset？
4. 自然语言问答的边界是否要提示用户“该回答来自审计记录/Manifest/QC”，以免误以为是模型猜测？

## 4. 后续开发边界

### P0：只做真实验收和修复闭环

P0 允许做：

- ToolSpec / ToolRegistry / ToolResult 骨架；
- `retrieve_operators`、`compile_pipeline_artifact`、`validate_pipeline_artifact`、`query_control_facts` 和 `propose_control_action` adapter；
- 真实百炼 OpenAI-compatible endpoint 的结构化输出冒烟；
- 继续使用 37 张猫狗目录做阶段性端到端测试，但不把它视为最终验收集；
- 建立 `Acceptance Dataset v1`，规模约 50 到 100 张，覆盖多类别、质量问题、重复/近重复、合成或非真实图、故障样本、语义歧义、可修复失败和 abandoned/excluded 场景；
- Run events、`run_node_results`、DatasetVersion、Manifest、QCReport 的证据核对；
- 修复型 Dataset 合并，生成新的不可变 DatasetVersion；
- 修复合并产物使用领域名 `RepairedDatasetVersion`；
- DatasetVersion 作为逻辑版本落盘 Manifest、血缘和资产引用，不重复复制全量图片；
- 只有最终 `SUCCEEDED` 版本生成完整图片目录作为 `Deliverable Dataset Export`；
- 禁止从 `PARTIAL` 版本生成完整交付导出；
- 重试 Run 与父 Run / 父 Dataset 的显式 lineage；
- 区分 Repair Run 的操作血缘和 RepairedDatasetVersion 的交付血缘；
- Repair Run 的输入范围只包含失败或缺失资产，不包含父版本成功资产；
- 对仍失败资产保留可继续重试的状态；
- 同一资产修复 3 次仍失败后标记 abandoned，并要求用户显式确认是否剔除；
- 被剔除资产必须输出在 `excluded_assets` 清单中，连同失败原因和审计证据一起交付；
- 必要的失败 reason code 和测试补齐。

P0 不做：

- 不新增大批算子；
- 不做 Web 工作台；
- 不做完整 Tool UI 或完整工具平台；
- 不做 shell/python/file tools；
- 不做 execute_operator 或 execute_provider_operator 这类模型可直接触达算子的 Tool；
- 不做多 Worker 调度；
- 不做 GPU 算子正式发布；
- 不把 `PARTIAL` 粉饰为 `SUCCEEDED`；
- 不把 `PARTIAL` 作为最终交付结果；
- 不用模拟测试替代真实 Provider 验收。

P0 完成口径：

```text
同一批图片能够完成：
  初次 Run
  -> 部分失败
  -> 只重试失败资产
  -> 合并为新的逻辑 DatasetVersion
  -> Manifest 与审计可追溯每张图片来源
  -> 仍失败资产进入 still_failed
  -> 超过重试上限的资产进入 abandoned
  -> 用户显式确认剔除后进入 excluded_assets
  -> QC 明确区分 succeeded / partial / failed
  -> 最终交付状态必须达到 SUCCEEDED
  -> SUCCEEDED 版本导出完整图片目录
```

### P1：做审计产品化和 Remote 韧性

P1 允许做：

- `/node-results` 分页、过滤和排序；
- 按 decision、status、node、reason、source 查询；
- 审计 JSONL/CSV 导出；
- TUI 展示拒绝项、失败项和单图完整轨迹；
- Repair Review Workbench 第一版：失败资产查看、节点审计、修复触发、显式剔除确认和交付导出触发；
- Remote Provider 的分层超时、有限重试、指数退避和错误分类；
- 受控并发与费用、延迟基线；
- 干净 Windows / Linux CPU 安装矩阵验收。

P1 不做：

- 不把所有 Provider 输出套同一个 JSON 解析器；
- 不把所有 217 个算子显示为“可用”；
- 不在没有分页时返回大型 Run 的完整审计；
- 不把控制平面和 Worker 合并成一个职责不清的进程。
- 不在 Repair Review Workbench 第一版里做完整任务创建、复杂 Pipeline 编辑、多用户后台、算子市场或监控大屏。

### P2：做算子准入和产品扩展

P2 允许做：

- 批量准入更多 Data-Juicer CPU 图片算子；
- 为每个正式模型算子建立独立输入、输出、解析契约和 Golden Set；
- Linux GPU Worker 的独立验收；
- Web 图片审核工作台的扩展能力；
- 统一 launcher；
- 更完整的成本治理和缓存策略；
- Pipeline recipe / YAML 产物导出。

P2 不做：

- 不因为 Catalog 能发现就晋升为 Released；
- 不修改原始 Provider 发现缓存来补 DataAgent 元数据；
- 不让模型直接写领域状态；
- 不让模型编造 Run、Pipeline、路径、计数或 QC 结论。

## 5. 模块边界

### 对话与控制面

- `dataagent/application/conversation.py` 只负责对话编排、意图解释、事实查询入口和 ReAct 自纠。
- `conversation_actions.py` 定义模型可提出的动作形状。
- `conversation_policy.py` 是动作能否落地的确定性闸门。
- 任何改变 TaskSpec、Pipeline、Run 的动作，都必须经过 Policy 和领域服务。
- 后续 `Control Tool` 层只包装控制面动作和事实查询，不直接执行 Operator，不直接写数据库，也不绕过 Policy。
- 可借鉴 Data-Juicer Agents 的 `ToolSpec` 外形：Pydantic input/output、`effects`、`confirmation`、结构化 `ok/error_type/next_actions` 结果；不借鉴开放 bash/python/file tool 的能力范围。
- 第一版 Tool 集合包含 `retrieve_operators`、`retrieve_pipelines`、`compile_pipeline_artifact`、`validate_pipeline_artifact`、`propose_control_action`、`query_control_facts`。
- `compile_pipeline_artifact` 和 `validate_pipeline_artifact` 处理 DataAgent 的 `PipelineArtifact`，不使用 recipe 命名；`write_arbitrary_yaml` 明确不进入 Tool 层。
- ToolRegistry 负责能力声明和发现；Tool executor 是薄入口，可以调用 `AgentRuntime`、OperatorRegistry 或 VersionStore，但不能拥有另一套工作流状态机。

Tool 调用权限分级：

```text
auto:
  retrieve_operators
  retrieve_pipelines
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

Tool observation 统一结构：

```json
{
  "ok": true,
  "tool": "retrieve_operators",
  "status": "succeeded",
  "summary": "Found 6 candidate operators.",
  "data": {},
  "evidence": [],
  "next_actions": [],
  "requires_confirmation": false,
  "error_type": null
}
```

`evidence` 应优先引用真实对象：`operator_version_id`、`pipeline_artifact_id`、`run_id`、`dataset_version_id`、`qc_report_id`、`manifest_uri`、`node_result_id`。

边界问题：如果新增一种用户说法，优先检查模型结构化输出、Action Schema、上下文渲染和 Policy，而不是追加中文同义词表。

### Agent Runtime 与版本对象

- `agent_runtime.py` 是 Run 提交、Pipeline 准入、失败资产重试和版本读取的应用层入口。
- TaskSpec、PipelineVersion、DatasetVersion、QCReport 都是不可变事实对象。
- DatasetVersion 默认是逻辑版本，保存 Manifest、血缘、审计引用和资产引用；完整图片目录属于最终交付导出。
- 重跑、重试、重编译、修复合并都创建新版本，不覆盖旧版本。

边界问题：修复型合并应作为新的应用层用例出现，不应塞进普通 `retry_run` 后静默覆盖结果。

### Worker 与执行

- `dataset_runner.py` 负责逐资产执行、节点审计、超时隔离、发布和 `PARTIAL` 判定。
- Worker 只执行已批准 PipelineVersion，不解释自然语言，不自行调整 TaskSpec。
- 单资产失败不能拖死整批，后续节点必须写入 skipped 审计，而不是消失。

边界问题：Dataset 级算子可以有批处理预算，但不能复用单资产超时语义假装可定位每张图。

### Provider 与 Operator

- `distribution/datajuicer.py` 管安装、Registry、Profile、digest 和许可证证据。
- `operators/providers/*` 管 Provider 适配、执行、输出契约和版本化 Variant。
- Data-Juicer 原始 Catalog 是证据，不是 DataAgent 元数据编辑区。
- Operator 是 Pipeline 内部数据处理能力，不是模型可自由调用的 Control Tool。

边界问题：Output Adapter 必须绑定具体 `provider_operator_ref + provider_version + variant version`，不能写全局猜测修补器。

### API/TUI

- API 是控制面入口，TUI 只调用 API，不直接访问 SQLite。
- TUI 是当前测试驾驶舱，可以主动通知和展示结果，但不拥有领域事实。
- Web 图片审核工作台是后续正式审核体验的目标入口。
- 事实回答必须从 Store、Version、Manifest、QC 或审计记录渲染。

边界问题：如果 TUI 展示与数据库事实不一致，修 API/Store 渲染，不让模型“补一句解释”。

## 6. 验收清单

每个进入主线的后续功能，至少回答以下问题：

1. 它修改的是哪个领域状态？是否创建新版本而不是覆盖旧版本？
2. 它的事实证据落在哪里？Run event、node result、Manifest、QCReport 还是 Provider artifact？
3. 它失败时的 reason code 是什么？是否可重试？
4. 它是否改变 `SUCCEEDED`、`PARTIAL`、`FAILED` 的判定？
5. 它是否需要真实 Provider 验收，而不仅是模拟测试？
6. 它是否让模型获得了不该拥有的写权限？
7. 它是否把 Discoverable 误说成 Executable 或 Released？
8. 它是否会在数万张图片时一次返回不可控数据量？
9. 它是否保留 Owner 隔离和路径脱敏？
10. 它是否有最小可复现的端到端命令或 API 流程？

## 7. 建议的最近三步

1. **先打底逻辑版本与 Tool 骨架。** DatasetVersion 先转向逻辑版本，Tool 层先提供 read/draft/artifact 编译验证能力，避免修复合并继续压在复制目录和 JSON 文本上。
2. **再做修复型 Dataset 合并设计与实现。** 这是当前最影响平台闭环的缺口，且能直接检验不可变版本、lineage、Manifest、QC 和审计是否站得住。
3. **并行保留真实冒烟记录。** 用 1 张图验证真实 Remote VLM 结构化输出，再用当前简单集合作为阶段性测试；后续设计更复杂验收集。
4. **随后做审计分页过滤。** 没有分页过滤之前，审计能力只能服务小样本演示，不能支撑真实数据生产。

## 8. 默认回答

当后续开发出现分歧时，默认采用以下判断：

- 能用真实证据回答的，不让模型猜。
- 能创建新版本的，不覆盖旧版本。
- 能显式失败的，不用 `unknown` 假装成功。
- 能按具体算子注册契约的，不写全局解析器。
- 能先用小集合验收的，不直接跑大批付费请求。
- 能保持控制面和 Worker 分离的，不为了启动方便合并职责。
