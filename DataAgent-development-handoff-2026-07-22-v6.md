# DataAgent 开发接手文档 v6

> 交接日期：2026-07-22  
> 仓库：`D:\newDataAgent`  
> 当前分支：`codex/hybrid-operator-retrieval`  
> 当前 HEAD：`769f0fd feat: distinguish partial dataset completion`  
> 上一版：`DataAgent-development-handoff-2026-07-20-v5.md`  
> 当前自动回归：`136 passed, 1 warning`  
> 本文范围：v5 之后的模型优先对话控制、可移植 Data-Juicer Provider、运行审计、失败隔离、输出契约和部分成功语义。

## 1. 我们在做什么

DataAgent 是一个面向图片数据生产的本地 Agent 控制平面。用户用自然语言描述数据目录和目标，系统将需求固化为版本化 `TaskSpec`，拆解为能力需求，从 Native、Model 和 Data-Juicer Provider Catalog 中检索真实算子，编译三条可比较的 Pipeline，经用户批准后由独立 Worker 产出不可变 `DatasetVersion`、Manifest、QC 报告和完整运行证据。

当前产品目标已经从“能生成一条看起来合理的处理流程”推进到以下闭环：

```text
自然语言
  -> 模型优先的结构化 Intent / ReAct
  -> 版本化 TaskSpec
  -> 能力拆解、混合检索和 Coverage Matrix
  -> 三条包含真实算子、真实参数的 Pipeline
  -> 用户批准
  -> 独立 Worker 执行
  -> 每张图片、每个节点的审计记录
  -> DatasetVersion / Manifest / QC
  -> 失败资产定向重试
```

仍然有效的架构边界：

1. 对话模型负责语义理解和结构化决策，不直接篡改领域状态。
2. 控制平面验证动作是否与当前工作流状态相容，再执行 TaskSpec、Pipeline 或 Run 动作。
3. 事实回答必须由持久化对象渲染，模型不得编造 Run、Pipeline、算子、数量或路径。
4. Worker 只执行已批准且通过统一准入校验的版本化 Pipeline。
5. Data-Juicer 是外部 Operator Provider，不把它的 217 个算子源码和重依赖直接复制进 DataAgent 主环境。
6. 简单稳定能力优先 Native CPU；复杂语义近期可走受治理 Remote Model Operator；专业视觉能力长期由经过 Golden Set 和许可证审查的专门模型提供。
7. `Discoverable`、`Matched`、`Executable`、`Draft Candidate` 和 `Released` 是不同状态，不能统称“可用”。

## 2. v5 之后讨论并确认的核心问题

### 2.1 智能体不能依赖同义词白名单

旧版 `conversation.py` 通过路径正则、关键词白名单和阶段特定分支判断“好”“继续”“确认”“重新跑”等输入。这类实现的问题不是词表还不够大，而是错误地把语义理解退化成字符串匹配：只要用户换一种表达，状态机就可能误判。

确认采用的方向是：

- 模型优先输出 JSON 契约；
- 每轮结合完整对话与真实控制上下文判断 Intent；
- 控制动作失败后通过 ReAct 将结构化系统反馈回喂模型；
- 领域动作仍由确定性 Policy 和状态机校验；
- 模型失败时不得静默伪装成正常回答，也不得执行任何控制动作。

这不是“让 LLM 完全接管系统”。正确分工是：LLM 识别语义，Action Schema 限制形状，Policy 限制动作，领域服务修改状态，Grounded Renderer 回答事实。

### 2.2 “重新跑”“重试失败项”“重新编译”是三件事

此前“重新跑”可能落到空 strategy，或错误复用旧 Run。现在明确区分：

- `RERUN_PIPELINE`：使用同一不可变 TaskSpec 和 Pipeline，对完整输入创建新 Run；
- `RETRY_RUN`：只对前一 Run 中 `failed` 的资产创建新 Run；
- `RECOMPILE_PIPELINE`：保留已确认 TaskSpec，使用当前 Catalog 重新检索并生成 Pipeline 候选。

切换 Pipeline 也必须先重新选择并批准，再提交新 Run。旧 Run 永远保留为历史证据。

### 2.3 Run 完成后必须主动通知

此前 TUI 只在提交时显示 `QUEUED`，用户必须主动追问状态。API 和 Worker 即使完成，TUI 也不会自动呈现终态。

当前方案是在 TUI 内启动非阻塞监视线程：

- 用户仍可继续输入、暂停或查询；
- 只在状态或进度变化时输出；
- `SUCCEEDED`、`PARTIAL`、`FAILED`、`CANCELLED`、`PAUSED` 都会主动通知；
- 已发布 Dataset 时自动展示真实结果。

### 2.4 仅有汇总计数不等于可审计

用户要求能回答：每张图片经过哪些节点、每个节点为什么继续、拒绝、失败或跳过。旧 `run_items` 只保存资产最终结论，无法定位具体算子。

现在新增“资产 × Pipeline 节点”审计账本，记录：

- `source_uri` 和资产序号；
- `node_id` 和完整 `operator_version_id`；
- `status`：`completed`、`failed`、`skipped`；
- `decision`：`continue`、`reject`、`failed`、`not_run`；
- 原因码、指标、标签、错误和耗时；
- 生效的 Runtime 和脱敏参数；
- VLM system prompt、模型、endpoint、字段名和结构化输出设置。

前序节点拒绝或失败后，后续节点不是消失，而是记录为 `skipped`，原因是 `UPSTREAM_REJECTED_OR_FAILED`。

### 2.5 单张图片卡住不能拖死整批

真实运行中曾在第 27 张停留约 5 分钟，原因是 Data-Juicer 远程单图调用沿用了 300 秒 Provider 总超时。当前处理原则：

1. Remote 单资产调用默认 90 秒超时；
2. 超时后终止对应 Provider 子进程；
3. 当前资产记为失败并写明可重试，继续后续图片；
4. 发布结果时展示失败项；
5. 用户可对失败资产创建定向重试 Run。

可通过以下变量调整：

```env
DATAAGENT_REMOTE_ASSET_TIMEOUT_SECONDS=90
```

### 2.6 Data-Juicer 并非所有解析器都要求相同 JSON

检查 `py-data-juicer==1.5.3` 源码后确认，严格的 `{"tags":[...]}` 是 `image_tagging_vlm_mapper` 的特定输出契约，不是 217 个算子的全局规则。该 Mapper 本身还容忍 Markdown 代码块、单引号、`tag` 别名和逗号字符串。

因此不能写一个全局 `tags` 修补器。现在采用按 `provider_operator_ref` 注册输出适配器：

- 未注册算子保持原始输出，不做猜测；
- `image_tagging_vlm_mapper` 使用 `image_tag_set:1` 契约；
- 接受嵌套数组、逗号字符串、`tag` 和 `tags` 对象；
- 标准化为扁平、去重、小写标签列表；
- 原始 Provider 值保存在 `<tag_field>__provider_raw`；
- 空或畸形结果报 `provider_output_contract_violation`，不能默认为 `unknown` 后继续假装成功。

Remote VLM 当前还固定：

```json
{
  "temperature": 0,
  "response_format": {"type": "json_object"}
}
```

Pipeline 的实际提示词位于 `dataagent/agents/processing/nodes.py`，生效参数会物化进 PipelineVersion，并写入 Run 事件。Provider 输出适配器位于 `dataagent/operators/providers/output_adapters.py`。

### 2.7 发布了数据集但有失败资产，不应等同于完全失败

旧逻辑中，31 张成功发布、5 张拒绝、1 张执行失败会显示整个 Run 为 `FAILED`，即使 Dataset 和 Manifest 已真实存在。

现在新增 `PARTIAL`：

- Dataset 已物理发布；
- 至少有一张失败资产；
- QC 唯一失败原因是 `EXECUTION_FAILURES_PRESENT`。

硬约束失败、语义结果缺失、Manifest 不存在或物理输出校验失败仍为 `FAILED`。`PARTIAL` 不是放宽 QC，而是准确表达“结果可用，但存在待修复资产”。

## 3. 已经完成了什么

### 3.1 模型优先对话控制与 ReAct

关键提交：

```text
797854c refactor: prototype model-first conversation control
b940a6d fix: validate semantic actions before workflow mutation
6a23d22 fix: contain rejected conversation actions
3794ed5 fix: keep action audit metadata system-owned
11ef358 feat: distinguish rerun from pipeline recompilation
```

完成内容：

- 删除 LLM 之前的大量关键词级联和同义词硬编码；
- `conversation_turn` 直接输出结构化 Intent 与参数；
- 最多 4 轮 ReAct 自纠；
- 新增 discriminated union 的 Conversation Action Schema；
- Action Policy 在修改状态前验证动作与当前阶段；
- 无效或越权动作被包含在当前轮，不污染后续上下文；
- `resolved_by`、fallback 原因等审计元数据由系统持有，模型不能伪造；
- 区分完整重跑、失败重试和重新编译。

### 3.2 Remote VLM 与 QC 闭环修正

关键提交：

```text
4ab83d5 fix: fail runs when published datasets fail QC
9ba23e0 feat: version corrected remote VLM proxy
```

完成内容：

- Dataset 物理发布后仍必须通过 QC 才能标记成功；
- Remote VLM 修正版升级为 `datajuicer.image_tagging_vlm_mapper.remote_api:2`；
- 修复版本化 Operator 身份，避免旧 Pipeline 与新实现混用；
- 保持 Remote API 和 Local CUDA Variant 分离。

### 3.3 可移植 Data-Juicer Provider 安装与 Registry

关键提交：

```text
fd5f399 feat: add portable Data-Juicer provider setup
```

主要能力：

- 新增跨平台 Data-Juicer Provider 安装器和本地 Registry；
- 固定 `py-data-juicer==1.5.3`；
- 安装后严格校验发现目录必须为 217 个算子；
- 支持 `auto`、`catalog`、`cpu`、`remote`、`linux-gpu` Profile；
- 自动标注 CPU、Remote API、Linux GPU、生命周期和阻塞原因；
- API 与 Worker 从 Provider Registry 读取 Python 和 `dj-process` 路径；
- 不再依赖固定的 `D:\DataAgent\data-juicer-agents`；
- 记录依赖锁、安装包 SHA256、许可证信息和稳定 Catalog digest；
- Windows 使用短运行时路径，规避 Torch 和依赖安装的长路径问题；
- Linux GPU Profile 要求独立 GPU Worker 证据，不能在无 GPU 开发机伪装通过。

主要命令：

```powershell
dataagent setup --with-provider datajuicer@1.5.3 --profile auto
dataagent provider verify
dataagent provider report --blocked-only
```

核心实现：

```text
dataagent/distribution/datajuicer.py
dataagent/operators/providers/datajuicer_worker.py
dataagent/config.py
dataagent/cli.py
README.md
```

这解决的是未来“另一台 Windows/Linux 机器一条命令安装并发现 217 个算子”的基础设施。尚未替代干净机器上的真实安装验收，详见后续计划。

### 3.4 TUI 自动终态通知

```text
b5f57c8 feat: notify TUI when dataset runs finish
```

- 非阻塞后台轮询；
- 状态或进度变化才输出；
- 终态主动通知；
- 同一 Run 不重复启动监视器。

### 3.5 逐资产逐节点运行审计

```text
607f792 feat: persist per-asset operator audit trails
17b3360 feat: expose grounded per-node run audits
e7ed9ff feat: audit effective pipeline prompts and parameters
```

- 新增 `run_node_results`；
- 新增接口 `GET /api/runs/{run_id}/node-results`；
- TUI 新增 `/audit [run_id]`；
- 自然语言事实查询新增 `audit` facet；
- “拒绝理由是什么”不再重复通用 Run 摘要；
- 参数与 Prompt 可回溯，凭据字段自动脱敏；
- Provider stdout/stderr 摘要继续保存在 Run events。

### 3.6 超时隔离和失败资产定向重试

```text
40a3ba8 feat: isolate and retry failed dataset assets
```

- Remote 单资产独立超时；
- 失败资产不阻塞后续资产；
- `asset_completed` 事件包含原因码和 `retryable`；
- `RETRY_RUN` 只冻结并运行失败资产计划；
- `RERUN_PIPELINE` 仍运行完整输入。

### 3.7 Provider 输出契约治理

```text
6ced207 feat: govern Data-Juicer model output contracts
```

- 新增可扩展的按算子 Output Adapter Registry；
- VLM 请求启用确定性结构化 JSON；
- VLM 标签输出标准化且保留原始值；
- 空语义输出成为明确契约错误；
- 未注册算子不套用 VLM 标签规则。

### 3.8 `PARTIAL` 终态

```text
769f0fd feat: distinguish partial dataset completion
```

- 区分完全成功、部分完成和完全失败；
- TUI 将 `PARTIAL` 视为终态并展示已发布 Dataset；
- 只有纯资产执行失败才允许成为 `PARTIAL`，其他 QC 问题仍失败。

## 4. 当前进行到哪里

### 4.1 已有自动测试保障的状态

当前全量测试：

```text
136 passed, 1 warning
```

警告来自 Starlette `TestClient` 的弃用提示，不是业务失败。

已经由测试覆盖：

- Conversation Action Schema、Policy 和 ReAct 控制；
- TaskSpec 修订、Pipeline 选择、重跑和重新编译；
- Remote VLM Catalog Variant 与参数物化；
- Data-Juicer 217 算子安装目录校验和 Provider Registry；
- 单进程 Dataset 批处理；
- 每资产节点审计、API 与 TUI 展示；
- Remote 单资产超时配置；
- 只重试失败资产；
- VLM 输出多种形态标准化；
- Dataset 物理发布和 QC；
- `PARTIAL` 判定。

### 4.2 仍未完成真实环境验收的部分

以下能力有代码和模拟测试，但不能声称已经完成生产验收：

1. `qwen3.7-plus` 经百炼 OpenAI-compatible endpoint 的真实结构化输出兼容性；
2. 37 张猫狗数据在新代码下的完整端到端运行；
3. 干净 Windows 机器的一条命令 Provider 安装；
4. 干净 Linux CPU 机器的一条命令 Provider 安装；
5. Linux 独立 GPU Worker 的 CUDA 算子评测；
6. Remote API 并发、限流、成本和性能基线；
7. TUI 后台通知在线编辑提示符时的长时间人工体验测试。

### 4.3 当前最重要的产品缺口

1. **定向重试结果尚未合并回父 Dataset。** 当前失败重试会生成一个只含失败资产的新 Dataset；下一步需要 Repair/Merge 语义，生成引用父 Dataset 和修复 Run 的新 DatasetVersion。
2. **Output Adapter 目前只正式覆盖 VLM Tagging。** 不能因为建立了注册表就宣称所有模型算子的响应契约都已适配。应随正式准入逐个增加契约和 Golden Set。
3. **审计查询缺少分页和过滤。** 37 张可以直接显示，数万张时必须按 decision、node、reason、source 分页查询和导出。
4. **安装器缺少真实干净机器矩阵。** 单元测试不能替代 Windows/Linux 安装、卸载、重复安装和 Registry 恢复测试。
5. **服务启动仍是多个进程。** Provider 可移植不等于 API、Worker、TUI 已经成为一个进程；本地产品可增加统一 launcher，但不应把控制平面和 Worker 的职责重新耦合。
6. **缺少真实成本治理。** Remote VLM 每张图可能执行真实性与分类两次调用，需要缓存、合并多任务 Prompt 或受控并发评测后再优化。

## 5. 下一步计划

建议严格按以下顺序推进，每个小功能测试通过后单独提交。

### P0：真实冒烟和端到端验收

1. 重启 API、Worker、TUI，确认加载当前 HEAD 和 Provider Registry；
2. 用一张图片执行 Remote VLM，验证 `response_format=json_object` 在真实百炼接口可用；
3. 检查 Run events、node results、Provider stdout/stderr 和原始输出；
4. 用 5 张小集合覆盖 cat、dog、mixed、synthetic、模糊图；
5. 最后再运行 37 张目录，记录耗时、调用数、失败数、分类数和费用。

验收时不要只看 TUI 文案，必须核对：

```text
RunSnapshot
Run events
run_node_results
DatasetVersion
manifest.json
实际输出文件
QCReport
Provider execution artifacts
```

### P0：修复型 Dataset 合并

1. 给重试 Run 建立显式 `parent_run_id` 或 repair lineage；
2. 只重跑失败资产；
3. 将成功修复结果与父 Dataset 的成功资产合并；
4. 生成新的不可变 DatasetVersion，不覆盖旧 Dataset；
5. Manifest 标记 repaired、still_failed 和来源 Run；
6. 对仍失败资产继续允许定向重试。

### P1：审计查询产品化

1. `/node-results` 增加 decision、status、node、reason、source 过滤；
2. 增加分页，禁止一次返回大型 Run 的全部记录；
3. TUI 增加拒绝项、失败项、单图完整轨迹视图；
4. 支持审计 JSONL/CSV 导出；
5. Run 终态摘要直接列出失败样本和下一步可执行命令。

### P1：Remote Provider 性能与韧性

1. 分别设置连接、读取、单资产和 Dataset 超时；
2. 增加有限次数、指数退避且可审计的 API 重试；
3. 区分限流、服务端错误、解析错误、内容拒绝和永久参数错误；
4. 评测受控并发，不能盲目并行 37 个付费请求；
5. 研究真实性和猫狗分类能否在一次 VLM 调用中返回版本化复合契约；
6. 建立真实 API Golden Set、延迟和成本基线。

### P1：Provider 安装矩阵验收

1. 全新 Windows 虚拟机执行 `catalog`、`cpu`、`remote`；
2. 全新 Linux CPU 环境执行同一流程；
3. 验证重复安装幂等、损坏 Registry 恢复、离线阻断和卸载；
4. 验证 217 数量、Catalog digest、许可证和 wheel SHA256；
5. Linux GPU Worker 独立完成模型算子评测后，才允许申请发布。

### P2：更多 Data-Juicer 算子正式准入

1. 保持完整 217 动态发现目录；
2. 当前图片 Agent 只召回图片能力；
3. CPU 图片算子按 Golden Set 和性能基线批量准入；
4. 每个模型算子声明自己的输入、输出和解析契约；
5. GPU/模型算子在 Draft 阶段不可因为“能发现”就当作 Released；
6. 通过许可证、revision、SHA256、GPU 证据和质量评测后晋升 `PERSONAL_RELEASE`。

## 6. 不要重复踩的坑

### 6.1 不要再增加自然语言词表

出现新说法识别失败时，先检查模型结构化输出、Action Schema、控制上下文和 Policy，不要继续向 `{"好", "继续", "可以"}` 追加词。

### 6.2 模型优先不等于模型有写权限

模型只能提出 Action。TaskSpec revision、Pipeline approval、Run submission 和 retry 都必须经过确定性状态校验。ReAct 只能修正提议，不能绕过控制面。

### 6.3 不要让模型回答控制平面事实

Run ID、Pipeline、算子、参数、进度、拒绝原因、输出路径和计数必须来自 Store、Version 或真实文件校验。模型只负责选择查询 facet。

### 6.4 不要把 Data-Juicer 217 个算子等同于 217 个可执行算子

发现成功只说明 Catalog 可见。还要分别检查：

```text
Catalog metadata
DataAgent normalization overlay
runtime profile
dependencies
parameter schema
I/O contract
output adapter
license
evaluation evidence
lifecycle status
current Agent relevance
```

### 6.5 不要修改原始发现缓存来修元数据

继续使用 Normalization/Overlay 和版本化 Variant。原始目录用于证明 Provider 实际发现结果，DataAgent 适配层用于补充自身语义，两者必须可比较。

### 6.6 不要给所有模型算子套同一个 JSON 解析器

不同 Data-Juicer 算子有 JSON、正则、自由文本和自定义结构。输出契约必须绑定到 `provider_operator_ref + provider_version + variant version`。适配失败要显式报错，不能猜测结果。

### 6.7 不要把空标签当成正常 unknown

空标签可能是 API 失败、response path 错误、解析失败或 Prompt 不兼容。将其默认为 `unknown` 会造成“37 张全部保留”一类假成功。必须先判定契约是否满足。

### 6.8 不要让一张图片占满整个批次超时

Remote ASSET 算子必须有资产级 deadline。Dataset 级算子可以批处理，但需要独立的批次预算、取消检查和失败定位，不能照搬单资产策略。

### 6.9 不要把 `PARTIAL` 当作 `SUCCEEDED`

`PARTIAL` 表示已有可用输出，同时存在需要用户知情和可重试的失败资产。发布、消费或训练前是否接受部分数据，应由显式策略决定。

### 6.10 不要覆盖旧 Dataset 或旧 Run

重跑、重试、切换 Pipeline 和修改 TaskSpec 都创建新版本。修复型合并也必须生成新 DatasetVersion，并保留完整 lineage。

### 6.11 不要只关闭 TUI 就认为服务已更新

TUI 是客户端。修改 Catalog、Runtime、API 或 Worker 后必须重启对应进程。连接旧 API 是此前多次出现“代码明明改了但行为没变”的根因。

### 6.12 不要把自动测试当成真实 Provider 验收

模拟 `dj-process` 能验证协议和状态机，但不能证明真实百炼响应、Data-Juicer 解析、网络超时、费用和干净机器安装都正常。发布结论必须带真实运行证据。

## 7. 关键代码位置

```text
dataagent/application/conversation.py
  模型优先 ReAct、事实查询、重跑/重试/重编译语义

dataagent/application/conversation_actions.py
dataagent/application/conversation_policy.py
  JSON Action 契约和确定性动作策略

dataagent/gateway.py
  Conversation JSON Schema、模型提示词和 facet 说明

dataagent/application/agent_runtime.py
  Run 提交、失败资产重试、Pipeline 准入和版本读取

dataagent/execution/dataset_runner.py
  Dataset 执行、逐节点审计、超时失败隔离、发布和 PARTIAL 判定

dataagent/infrastructure/database.py
  runs、run_items、run_node_results、run_events

dataagent/operators/providers/datajuicer_executor.py
  隔离 dj-process、超时、stdout/stderr、输出读取和 Provider 契约入口

dataagent/operators/providers/output_adapters.py
  按算子的输出标准化与契约校验

dataagent/operators/providers/proxy.py
  Data-Juicer Candidate Proxy 和 Remote/Local VLM Variant

dataagent/agents/processing/nodes.py
  三条 Pipeline 编译、策略参数、VLM Prompt

dataagent/distribution/datajuicer.py
  跨平台 Provider 安装、Registry、Profile、digest 和许可证证据

apps/tui/app.py
apps/tui/session.py
apps/tui/api_client.py
  自动 Run 监视、/audit 和结果展示
```

## 8. 接手后的检查步骤

### 8.1 Git 与回归

```powershell
cd D:\newDataAgent
git status --short --branch
git log --oneline --decorate -20
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

预期自动测试：

```text
136 passed, 1 warning
```

工作区目前存在未跟踪文档、`docs/` 和 `scripts/e2e_real_gateway.py`。不要在不确认归属的情况下删除、覆盖或顺手提交。

### 8.2 Provider 验证

```powershell
.\.venv\Scripts\dataagent.exe provider verify
.\.venv\Scripts\dataagent.exe provider report --blocked-only
```

若目标机器尚未安装：

```powershell
.\.venv\Scripts\dataagent.exe setup --with-provider datajuicer@1.5.3 --profile auto
```

### 8.3 重启服务后再验收

确保旧 API 和 Worker 已结束，再启动当前代码。不要只重开 TUI。

运行后重点命令：

```text
/run
/watch
/audit
/result
```

自然语言也应支持：

```text
现在进度怎么样
这次用了哪个 Pipeline 和哪些真实算子
拒绝了哪些图片，分别是什么原因
哪张图片失败了，卡在哪个节点
只重试失败的图片
按原 Pipeline 完整重新跑
```

## 9. 一句话交接结论

DataAgent 已从“真实算子检索和 Pipeline 编译”推进到“模型优先但受确定性策略约束的对话控制、可移植 Data-Juicer Provider、逐资产逐节点审计、单图失败隔离、按算子输出契约和部分成功状态”；当前最重要的工作不是继续增加描述性能力，而是用真实百炼 API 和干净 Windows/Linux 环境完成端到端验收，并补上失败重试结果合并、审计分页以及 Remote Provider 的性能和成本治理。
