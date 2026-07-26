# DataAgent 问题与踩坑总台账

> 日期：2026-07-26  
> 仓库：`D:\newDataAgent`  
> 用途：汇总项目从早期原型到当前真实运行中暴露的问题、根因、修复状态和后续动作。  
> 信息来源：历次交接文档、当前代码、Git 提交记录、真实 TUI 对话和 Dataset Run。  
> 重要说明：本文中的“已修复”表示代码路径和自动测试已经建立；除非明确写出真实 Run，不能自动等同于完整生产验收。

## 1. 状态定义

| 状态 | 含义 |
|---|---|
| `已修复` | 已有明确实现和自动测试，主要代码路径已替换 |
| `部分修复` | 核心机制已建立，但仍有边界、回归或真实环境验收缺口 |
| `待修复` | 根因已确认，但当前代码仍可能稳定复现 |
| `待建设` | 属于已确认的产品或工程能力，尚未进入当前实现范围 |
| `原则固化` | 不是单一 Bug，而是后续开发必须持续遵守的边界 |

## 2. 当前最重要的结论

当前 DataAgent 已经拥有：

- 模型优先的自然语言控制；
- 受治理的 Control Tool Loop；
- TaskSpec、Pipeline、Run、DatasetVersion 和 Prompt 版本化；
- 动态 Operator Catalog 和 Data-Juicer Provider；
- Native Remote VLM；
- Worker 执行、逐资产审计、QC、Repair、Export；
- 一键本地启动和 Asset 级有界并行。

但最新真实任务证明，系统仍存在一个 P0 级规划缺陷：

```text
用户原始约束
  -> 模型理解
  -> TaskSpec
  -> 能力拆解
  -> Operator 检索
  -> Pipeline 编译
```

这条链路中，`能力拆解` 仍依赖旧关键词规则。它可能漏掉尺寸、宽高比、文件大小、人脸数量等确定性条件。Retriever 即使找到了正确的 Data-Juicer 算子，Compiler 也会因为 TaskSpec 没声明对应能力而把它们丢掉。

因此当前最高优先级不是继续增加算子，而是建立：

```text
结构化 Constraint Contract
  -> 原子 Capability DAG
  -> Constraint Coverage Matrix
  -> Operator Coverage
  -> Pipeline Node Coverage
  -> 逐约束执行证据
```

## 3. 总览

| 领域 | 问题 | 状态 |
|---|---|---|
| 对话 | 普通聊天被误建工单、硬回答、同义词白名单 | 部分修复 |
| 对话 | 模型 JSON 偶发损坏导致整轮无操作 | 已修复 |
| 对话 | 修改、重跑、重试、重新编译状态混淆 | 部分修复 |
| TaskSpec | 澄清词和系统话术污染需求字段 | 部分修复 |
| TaskSpec | 分类标签曾硬编码猫狗 | 已修复 |
| 规划 | 关键词规则漏拆确定性约束 | 待修复，P0 |
| 规划 | 多目标任务没有逐项能力与证据映射 | 待修复，P0 |
| 检索 | 找到算子不等于被 Coverage 选中 | 待修复，P0 |
| Pipeline | 完整性校验只验证已声明能力 | 待修复，P0 |
| Pipeline | 三条策略曾只换名字或由模型编造节点 | 已修复主路径 |
| Prompt | 任务目标、分类和排除条件未绑定 | 已修复 |
| Prompt | VLM 被要求判断文件大小、去重等不可见事实 | 待修复，P0 |
| 执行 | 旧 Worker 抢新任务、数据源和进度错位 | 已修复 |
| 执行 | 单图超时拖慢整批 | 部分修复 |
| 执行 | Dataset 算子使整条 Pipeline 关闭并行 | 待修复 |
| 执行 | 完成后不主动通知 | 已修复 |
| 审计 | 只能看到汇总，无法逐图逐节点追溯 | 已修复基础能力 |
| QC | 文件发布成功但语义任务没有执行也显示成功 | 已修复主路径 |
| QC | 无法证明每条原始约束分别被验证 | 待修复，P0 |
| Dataset | 声称输出目录存在但实际没有文件 | 已修复主路径 |
| Dataset | Manifest 过长、Artifacts 重复 | 已修复分层 |
| Provider | 217 个可发现被误解为 217 个正式发布 | 原则固化 |
| Provider | Data-Juicer 本机路径依赖，不可移植 | 已修复安装主路径 |
| Provider | GPU 算子被简单说成“没有资格调用” | 已修复主路径 |
| Provider | 不同模型算子错误共用同一输出解析器 | 部分修复 |
| 性能 | Data-Juicer Remote VLM 子进程开销大 | 已增加 Native 快速路径 |
| 性能 | 简单 VLM 判断消耗过多 reasoning token | 待优化 |
| 工程 | API、Worker、TUI 要开三个命令 | 已增加一键启动 |
| 工程 | 端口被旧实例占用时启动体验差 | 部分修复 |
| 工程 | 自动测试被写成真实 Provider 已验收 | 原则固化 |

## 4. 对话与交互

### 4.1 普通聊天误触发任务状态机

**症状**

- 输入“你好”“你是什么模型”“讲个笑话”时进入数据源询问或返回固定话术；
- 已有工单后，任何问题都被理解为审批、修改或运行命令。

**根因**

- 早期 TUI 采用状态优先和关键词规则；
- 对话意图、控制动作和普通聊天没有可靠分层；
- 模型失败时静默进入固定 fallback。

**状态：部分修复**

**已经怎么改**

- Conversation 改为模型优先 JSON Action 契约；
- 增加 `CHAT`、`QUERY_CONTROL_FACTS`、`EDIT_TASK_SPEC`、`RETRY_RUN`、`RERUN_PIPELINE` 等明确 Intent；
- 控制面事实由真实对象渲染，模型不直接编造；
- 模型 fallback 和使用原因进入行动审计。

**剩余问题**

- 模型服务不稳定时仍可能出现机械回复；
- 必须继续扩充对话 Golden Set，覆盖工单各状态下的普通提问；
- 不能重新引入大规模同义词词表作为“快速修复”。

### 4.2 硬编码确认词和自然语言白名单

**症状**

代码中曾维护：

```text
好、好的、可以、继续、确认、同意、是、yes、ok
```

不在词表中的同义表达就失败，词在不同语境中也可能被误判。

**根因**

把语义理解当作字符串分类问题。

**状态：已修复主路径**

**已经怎么改**

- 模型负责把自然语言解析为结构化 Action；
- Action Schema 校验字段；
- ActionPolicy 校验当前状态是否允许；
- 不再由确认词集合直接改变工作流状态。

**仍需防止**

- 不要在新的状态分支里重新堆关键词；
- 确定性解析只适合 `/status` 等显式控制命令和严格 ID，不适合开放自然语言。

### 4.3 模型 JSON 偶发损坏

**症状**

模型返回缺逗号或截断 JSON，出现：

```text
JSONDecodeError
模型服务本轮未返回有效结果
```

**状态：已修复**

**已经怎么改**

- 引入 JSON repair；
- 修复后仍执行 Pydantic Action Schema 校验；
- 无法恢复时不执行副作用动作；
- 当前 TaskSpec、Pipeline、Run 等事实仍可接地展示。

**经验**

JSON repair 只能修语法，不能替模型补业务事实。

### 4.4 TaskSpec 修改、确认和提交混淆

**症状**

- Agent 口头说“已经修改并重新提交”，实际没有写 TaskSpec revision；
- 用户说“好”后触发 Submit Run，而不是批准或修订；
- 已确认 TaskSpec 后不能中途删除清晰度要求；
- 修改后沿用旧 Pipeline 或旧 Run。

**根因**

- 文本承诺没有对应控制动作；
- TaskSpec revision、Pipeline recompile、Pipeline approval、Run submission 边界不清；
- pending choice 没有持久化。

**状态：部分修复**

**已经怎么改**

- TaskSpec 修改产生新版本；
- 修改确认后重新进入检索和编译；
- `RERUN_PIPELINE`、`RETRY_RUN`、`RECOMPILE_PIPELINE` 分开；
- Action Guard 拒绝非法状态转换；
- Pipeline 重新选择和任务修订已有专用路径。

**剩余问题**

- 需要完整状态机矩阵测试所有“中途改变需求”组合；
- TUI 应清楚说明本次动作会创建新 TaskSpec、Pipeline 还是 Run；
- 旧版本不能静默复用。

### 4.5 澄清问题固定、遗漏用户新条件

**症状**

- 每次都询问同样三件事；
- 用户新增“不是猫狗”等条件，澄清仍不提；
- 已经回答过的字段被重复询问；
- “继续”“可以了”被追加到 `semantic_requirements`。

**状态：部分修复**

**已经怎么改**

- 代码只输出结构化缺口，不固定具体猫狗问句；
- 澄清文案由模型根据 TaskSpec 生成；
- 推荐默认值写入明确字段；
- 不再把默认值只保存为自然语言描述。

**仍待修复**

- 当前 Constraint Contract 不够结构化，模型可能把精确条件塞入松散文本；
- 确认词、澄清回答和业务需求仍需更严格分离；
- 澄清完整性需要按约束逐项验证，而不是只看 `ambiguities` 是否为空。

### 4.6 Pipeline 展示不是真实流水线

**症状**

- 只展示 Pipeline ID；
- 模型编造 `data_loader`、`pet_classifier` 等不存在的算子名；
- 三条 Pipeline 只有自然语言描述，无法看到真实参数。

**状态：已修复主路径**

**已经怎么改**

- Pipeline 详情读取持久化 `PipelineVersion`；
- 展示真实 OperatorVersion、节点顺序、参数、Provider、Runtime 和 PromptBinding；
- 三条策略由确定性编译器生成；
- 模型只能解释真实 Pipeline，不能发明节点。

**当前新缺口**

真实展示只能暴露“已经编译了什么”，不能证明“是否漏编译了用户要求”。这需要新的 Constraint-to-Node Coverage Validator。

### 4.7 Tool 行动展示和思维链边界

**需求**

用户希望看到 Requirement Analyzer、Operator Retriever、Pipeline Compiler、Validator 等每一步。

**状态：已修复基础能力**

**已经怎么改**

- 增加受治理 Tool Registry 和 ToolResult；
- TUI 流式显示 Tool 名称、脱敏参数、耗时、结果和 evidence ID；
- 行动事件和最终事件分离；
- 不展示模型内部隐式思维链。

**经验**

`Requirement Analyzer`、`Control Action Guard`、`Pipeline Compiler` 是 Agent/Control Tool，不是图片处理 Operator。Tool、Agent、Operator 不能混为一谈。

### 4.8 完成后必须主动通知

**症状**

Run 结束后 TUI 没有任何消息，必须用户主动问“怎么样了”。

**状态：已修复**

**已经怎么改**

- TUI Watcher 订阅 Run；
- 终态主动推送状态、计数、Dataset、QC 和错误；
- 流式协议要求一个最终事件。

**剩余验收**

需要继续测试断线、TUI 重连、API 重启和多个 Run 的通知去重。

## 5. TaskSpec、能力规划和 Pipeline

### 5.1 `operators/planning.py` 职责混乱

**问题**

`dataagent/operators/planning.py` 同时包含：

- 自然语言关键词能力拆解；
- TaskCapability DAG 生成；
- 输出动作推断；
- OperatorSelector；
- 模型能力配置。

需求规划属于 Requirement Agent/Application Planning，算子选择才属于 Operator Catalog。

**状态：待修复，P0**

**准备怎么改**

- 将 Operator 选择迁移到 `dataagent/operators/selection.py`；
- 将能力规划迁移到 Requirement Agent 的深模块；
- 模型输出结构化 Constraint；
- Capability Planner 做标准化和 DAG；
- 删除 `_TASK_CAPABILITY_RULES` 在正式规划链路中的权威地位。

### 5.2 多目标需求没有完整拆解

**最新真实案例**

用户要求：

```text
宽高不少于 64
宽高比 0.3～3.5
文件大小 1KB～20MB
人脸数量 0～2
主体穿黑色衣服
去重
```

最终能力链只有：

```text
image_decode
perceptual_deduplication
visual_semantic_selection
manifest
```

**根因**

旧规则不认识尺寸、宽高比、文件大小和人脸数量这些能力。

**状态：待修复，P0**

**正确方向**

TaskSpec 需要保存结构化约束：

```text
image_dimensions
aspect_ratio
file_size
face_count
visual_semantic
perceptual_deduplication
```

每项保留参数、原始文本、required 标记和证据要求。

### 5.3 Retriever 找到算子但 Pipeline 没采用

**最新真实案例**

Retriever 已找到：

```text
datajuicer.image_shape_filter
datajuicer.image_aspect_ratio_filter
datajuicer.image_size_filter
datajuicer.image_face_count_filter
datajuicer.image_deduplicator
native.remote_vlm
```

实际 Pipeline 却缺少前四个算子。

**根因**

- Coverage 只遍历 TaskSpec 已声明的 capability；
- Candidate 被召回，不等于成为 capability coverage 的 selected operator；
- Compiler 只消费 selected coverage，召回但未被要求的算子会被丢弃。

**状态：待修复，P0**

**准备怎么改**

建立：

```text
Constraint
  -> Capability
  -> Candidate Operators
  -> Selected Operator
  -> Pipeline Node
```

任何 required constraint 没有走到最终节点时禁止批准或执行。

### 5.4 “能力完整”判断是假完整

**症状**

Retrieval Plan 显示 `sufficient=true`，但只是贫化后的能力 DAG 被覆盖，并不是原始需求被覆盖。

**状态：待修复，P0**

**准备怎么改**

- Capability Coverage 之外增加 Constraint Coverage；
- Validator 读取 TaskSpec 原始约束和约束 ID；
- Pipeline 节点声明 `satisfies_constraint_ids`；
- 必须验证参数绑定；
- required constraint 缜密覆盖后才允许 `production_eligible=true`。

### 5.5 三条 Pipeline 曾经只是三套话术

**症状**

Retention、Balanced、Quality 三条方案曾由模型描述，节点相同、参数不真实，甚至出现不存在的人工复核节点。

**状态：已修复主路径**

**已经怎么改**

- 三条 Pipeline 由同一能力链编译；
- 差异落在 uncertainty policy、quality threshold、dedup threshold、mixed/unknown policy 等真实参数；
- 必须展示真实 OperatorVersion。

**仍需补充**

- 对确定性范围约束，三条策略通常不应擅自改变用户硬阈值；
- 策略只能改变用户允许的软策略，不能改变硬约束；
- 新 Validator 应明确区分 hard constraint 与 strategy preference。

### 5.6 猫狗标签和任务专用硬编码

**症状**

- 缺少 classification contract 时默认生成猫狗标签；
- 通用语义算子内部残留 cat/dog fallback；
- 黑衣筛选可能被猫狗遗留逻辑污染。

**状态：已修复**

**已经怎么改**

- 分类标签必须来自 TaskSpec；
- 缺少 classification contract 时分类、类别解析和分目录编译失败；
- 纯视觉筛选不要求分类契约；
- 删除 `semantic.py` 中的猫狗 fallback；
- 增加测试确保任务标签是动态的。

**原则**

不能为猪狗、红衣服、黑衣服继续创建任务专用 Operator。Operator 保持原子，任务语义进入 TaskSpec、PromptBinding 和参数。

### 5.7 可合并的 VLM 应共享一次调用

**问题**

真实性、视觉条件和分类都使用同一原图、同一模型时，过去会重复调用 VLM。

**状态：已修复主路径**

**已经怎么改**

- Processing Compiler 判断视觉能力是否可共享；
- 相同输入状态、模型、Variant 和兼容输出契约时只生成一个 visual tagging 节点；
- 下游确定性节点消费同一份标签证据。

**边界**

两次 VLM 之间有图片变换、裁剪、分割或前置处理时不能合并。不能为了合并而创建无限复合 Operator。

### 5.8 Pipeline YAML、Prompt 和复现产物

**问题**

- Pipeline 是否每个任务都要手写 YAML；
- Prompt 放在代码里不便追溯；
- 每个 Run 复制完整模板、Schema、Catalog 会过于臃肿。

**状态：已修复主路径**

**已经怎么改**

- Prompt 模板版本化存放，Pipeline 记录 PromptBinding、版本和 SHA256；
- PipelineArtifact 由确定性 serializer 导出 YAML/JSON；
- LLM 不直接手写生产 Pipeline YAML；
- 默认运行只记录引用、hash 和必要快照；
- 完整 Run Bundle 按需导出，不为每个任务复制整个模板库；
- 成功 Pipeline 可记录为 PipelineExperience，后续检索只作为参考证据，仍需重新验证。

## 6. Prompt、模型和语义判断

### 6.1 Prompt 没有绑定当前任务

**症状**

黑衣筛选曾生成 VLM 节点，但 Prompt 没包含“黑色衣服”；猫狗任务也可能没有标签契约。

**状态：已修复**

**已经怎么改**

- Prompt v2/v3 强制绑定 objective、semantic requirements、exclusion requirements 和 classification contract；
- Prompt 版本进入 Pipeline；
- 已知错误 Prompt 被撤销生产执行资格，而不是原地覆盖。

### 6.2 VLM 被要求判断不可见或不适合的条件

**最新真实问题**

一个 Prompt 同时要求 VLM 判断：

- 黑色衣服；
- 图片宽高；
- 文件字节大小；
- 人脸数量；
- 是否重复。

其中精确文件大小和去重不能由单图像素判断，宽高和宽高比也应由确定性元数据算子处理。

**状态：待修复，P0**

**准备怎么改**

- 每条 Constraint 标记 evaluator type：metadata、deterministic vision、remote semantic、dataset；
- Prompt Resolver 只接收 `remote_semantic` 条件；
- 非可见条件禁止进入 VLM Prompt；
- 编译时发现错误 evaluator 绑定直接失败。

### 6.3 VLM 输出过于粗糙

**症状**

只记录：

```text
semantic_match / semantic_mismatch / semantic_uncertain
```

无法知道黑衣、尺寸、人脸数中的哪项失败。

**状态：待修复**

**准备怎么改**

- VLM 输出逐语义约束 decision、confidence、reason；
- 确定性算子输出测量值和阈值；
- 去重输出 duplicate group、代表资产和距离；
- Manifest 和 audit 通过 constraint ID 串联。

### 6.4 Data-Juicer 输出解析器过于统一

**症状**

曾把 Data-Juicer VLM 的输出统一要求为：

```json
{"tags": [...]}
```

空标签被降级成 unknown，导致错误结果被当作正常业务结果。

**状态：部分修复**

**已经怎么改**

- Provider output contract 可版本化；
- VLM tag contract 单独治理；
- 空输出和契约错误不再静默变成 unknown；
- 错误进入节点失败和 Repair。

**剩余工作**

- 为不同 Data-Juicer Mapper、Filter、Deduplicator 建立各自 Output Adapter；
- 对 217 个算子不能套一个解析器；
- Candidate 可以调用，但正式发布仍要通过对应契约测试。

### 6.5 模型路由不应按 Agent 无限拆分

**讨论结论**

近期使用三个能力槽：

```text
FAST_TEXT_MODEL
REASONING_MODEL=glm-5.2
VISION_MODEL=qwen3.7-plus
```

图片生成作为独立 Operator 配置。

**状态：已实现基础配置**

**原则**

- GLM-5.2 用于规划和复杂推理；
- Qwen3.7-Plus 用于视觉理解；
- 快速文本模型用于低风险短任务；
- 不因 API 平台属于某厂商就默认其模型一定最好；
- 必须用 DataAgent 自己的角色 Golden Set 决定路由。

### 6.6 简单视觉判断思考过度

**症状**

最终只需返回一个短标签，但 `qwen3.7-plus` 可能消耗数百 reasoning tokens，单图延迟明显。

**状态：待优化**

**准备方向**

- 对简单受约束视觉判断关闭 thinking；
- 收紧 `max_tokens`；
- 复用 HTTP 连接；
- 增加内容哈希缓存；
- 建立 429、超时和费用指标；
- 用 Golden Set 验证提速没有降低准确率。

## 7. Operator 与 Data-Juicer Provider

### 7.1 Operator 过度增殖

**问题**

曾出现把策略判断、分类后处理、任务特定逻辑都叫算子的倾向。

**状态：原则已固化，仍需持续审查**

**当前边界**

- Operator 是 Pipeline 中可版本化、可执行、可审计的数据处理能力；
- Tool 是 Agent 调用控制面的结构化入口；
- Strategy 是对 Operator 参数和决策规则的配置；
- 任务语义不应自动产生新 Operator。

现有 builtin 中部分属于确定性策略节点，而不是模型本体；是否保留应看它是否提供稳定、原子、可复用的执行契约，不能只看名字。

### 7.2 `native.remote_vlm` 为什么不是 builtin 前缀

**结论**

`native.remote_vlm:1` 表示 DataAgent 原生维护、但依赖远程模型服务的 Operator；`builtin.*` 主要表示本地确定性内置算子。命名体现执行和治理来源，不表示能力高低。

**状态：设计已明确**

### 7.3 217 个算子的“发现、可调用、准入、发布”混淆

**反复踩坑**

```text
Discoverable != Installed
Installed != Runtime Available
Runtime Available != Executable
Executable != Evaluated
Evaluated != Released
```

**状态：部分修复**

**已经怎么改**

- 恢复完整动态发现目录；
- 原始发现缓存和 Catalog Overlay 分离；
- 生成版本化 Candidate Proxy；
- 最近改为 Provider Available 算子可进入调用尝试；
- CPU Provider Proxy 有执行测试；
- Runtime 不满足时返回具体解决路径，而不是笼统“没有资格调用”。

**仍未完成**

- 217 个算子并非全部在当前 Windows CPU 环境真实执行过；
- 模型和 GPU 算子仍需独立 Worker、权重、许可证、revision 和 SHA256；
- 每种输出契约仍需适配和测试；
- 非图片算子可以存在于 Catalog，但不应进入图片 Agent Pipeline。

### 7.4 GPU 算子的正确处理

**过去错误**

看到 DRAFT、CUDA 不可用就直接说“不可调用”或“没有资格”。

**状态：已修复主路径**

**现在应返回**

```text
找到算子：image_aesthetics_filter
当前无法执行：需要 CUDA，当前 Worker 没有 GPU
可选方案：
1. 使用 GPU Worker
2. 使用 Remote API Variant
3. 跳过或修订任务
```

**待建设**

- 独立 Linux GPU Worker；
- GPU 模型 Golden Set；
- 权重和依赖锁；
- License、revision、SHA256 和发布审批。

### 7.5 Data-Juicer 作为外部 Provider 的代价

**确认的优点**

- 不复制和长期维护 217 个第三方实现；
- 保留上游生态和更新能力；
- DataAgent 只维护稳定 Provider Contract、Adapter 和治理；
- 自有算子仍可独立增加。

**确认的代价**

- 上游参数、依赖和输出协议变化需要兼容层；
- 子进程和数据转换有性能开销；
- 必须固定兼容版本；
- 如果上游停止开源，现有锁定版本仍可使用，但无法自动获得后续修复。

**状态：架构已确定**

固定 `py-data-juicer==1.5.3`，同时记录包 SHA256、许可证和 Catalog digest。未来升级必须产生新 Provider Compatibility Version，不能让旧 Proxy ID 静默变行为。

### 7.6 可移植安装

**历史问题**

DataAgent 依赖固定目录：

```text
D:\DataAgent\data-juicer-agents
```

另一台机器无法发现和运行。

**状态：已修复主路径**

**已经怎么改**

新增：

```text
dataagent setup --with-provider datajuicer@1.5.3 --profile auto
dataagent provider verify
dataagent provider report --blocked-only
```

支持 Windows/Linux 和 `auto/catalog/cpu/remote/linux-gpu` Profile；Registry 不再依赖固定源码目录；Windows 使用短运行时路径规避 Torch 长路径问题。

**剩余验收**

- 干净 Windows；
- Linux CPU；
- Linux GPU；
- 离线安装和镜像源；
- 安装幂等、修复和卸载。

### 7.7 不要运行时自动下载大型模型

**用户环境**

当前开发机没有 GPU，也不适合预下载大量权重。

**状态：原则固化**

**正确策略**

- 代码和 Operator Spec 可以先注册；
- 运行时发现缺少模型，返回明确 Resolution；
- 用户或部署流程显式准备权重；
- 不能在 Worker 执行任务时偷偷下载几个 GB；
- 简单算子用代码，通用语义用远程 VLM，专业模型在 GPU Worker 准入后使用。

## 8. Worker、并行和性能

### 8.1 API、Worker、TUI 为什么分开

**职责**

- API：控制面和持久化入口；
- Worker：领取并执行 Run；
- TUI：交互客户端。

过去需要三个命令，体验较差。

**状态：已修复主路径**

新增：

```text
dataagent start --owner local-user
```

统一拉起并监督本地 Stack，但没有破坏三个进程的职责边界。

### 8.2 端口被旧 DataAgent 占用

**症状**

```text
Port 8000 is already served by another DataAgent instance
```

**根因**

关闭 TUI 不等于关闭 API 和 Worker；旧 Stack 仍在运行。

**状态：部分修复**

**已经怎么改**

- 启动器校验实例身份和 revision；
- 不允许直接接管不明确的旧进程；
- Worker 有本地单实例租约。

**剩余体验问题**

- 需要正式的 `dataagent stop/status/restart` 管理命令；
- 能确认同一用户、同一仓库和兼容 revision 时，可提供安全重用或明确重启；
- 不要用宽泛 PowerShell 命令误杀其他 Python 进程。

### 8.3 旧 Worker 抢新任务

**症状**

- 数据集只有 23 张，进度显示 27/32；
- 新任务使用旧 Pipeline；
- 新代码已启动但行为没变化。

**状态：已修复**

**已经怎么改**

- `WorkerProcessLease`；
- `worker.lock.json`；
- claim protocol version；
- 活进程存在时拒绝第二个 Worker；
- 死 PID 的陈旧租约可清理。

### 8.4 单张图片卡住

**症状**

某张 VLM 请求长时间停在同一进度，整批看起来冻结。

**状态：部分修复**

**已经怎么改**

- 单资产超时；
- 单图失败不终止其他图片；
- Run 可进入 `PARTIAL`；
- 失败资产可以定向 Repair；
- 保留失败原因、节点和尝试次数。

**剩余问题**

- 当前 Remote VLM 多次重试可能仍让单图耗时接近数分钟；
- 应区分 connect、read、total 和 run budget；
- 只对 429、5xx、瞬时网络错误重试；
- 输出契约错误和确定性参数错误不能盲目重试。

### 8.5 Asset 并行已经实现，但不是所有 Pipeline 都并行

**状态：已修复 Asset 级并行**

**已经怎么改**

- 多张图片有界并行；
- 单张图内部按 DAG 顺序执行；
- Operator 必须显式 `parallel_safe`；
- Checkpoint、取消、失败隔离和 SQLite 写保护已适配；
- 默认 Native Remote VLM 并发上限为 4。

### 8.6 Dataset 算子关闭整条 Pipeline 并行

**最新真实问题**

`builtin.perceptual_dedup:1` 是 Dataset/共享状态能力，被判定为不并行安全，于是整个 Run 的 Asset 并行被关闭，VLM 也被迫串行。

**状态：待修复**

**准备怎么改**

将 Pipeline 编译为阶段：

```text
并行 Asset metadata stage
  -> Dataset dedup stage
  -> 并行 Remote VLM stage
  -> Publication stage
```

并行判定应按 Stage，而不是整条 Pipeline 一票否决。

### 8.7 并发不能盲目调大

**原则固化**

有效并发为：

```text
min(
  Worker 配置,
  Operator RuntimeProfile 上限,
  Provider 限流和连接池,
  待处理资产数
)
```

必须同时观察吞吐、P95 延迟、429、失败率、费用和准确率。把环境变量改成 16 不应绕过 Operator 上限。

## 9. Run、Dataset、Manifest、QC 和审计

### 9.1 “创建了数据集”但文件不存在

**症状**

Agent 根据配置猜测：

```text
D:\data\classes\cat
D:\data\classes\dog
```

实际目录不存在或 Dataset 发布在 `.dataagent/platform/datasets`。

**根因**

模型根据计划推断路径，没有读取物理发布事实。

**状态：已修复主路径**

**已经怎么改**

- Dataset 回复从 DatasetVersion 和物理发布校验读取；
- 展示真实根目录、Manifest、分类计数和实际存在目录；
- 路径和计数由控制面渲染，模型不猜。

### 9.2 逻辑 DatasetVersion 与交付目录混淆

**状态：已修复**

**当前定义**

- DatasetVersion：逻辑、可审计、可修复的数据版本；
- Deliverable Dataset Export：从最终 `SUCCEEDED` 版本物化的完整交付目录；
- `PARTIAL` 不可直接作为最终交付。

这避免每次 Repair 都复制全量图片，同时保留完整血缘。

### 9.3 Manifest 太长、Artifacts 重复

**症状**

每张资产重复记录 Data-Juicer `output.jsonl`、stats、stdout/stderr 等 Run 级文件。

**状态：已修复**

**已经怎么改**

- 交付 Manifest 与 Provider Execution Evidence 分层；
- 空默认字段压缩；
- Manifest 保留 Domain round-trip；
- 图片结果在 `files/`；
- Provider/VLM 证据在 `artifacts/`；
- Artifacts 不是重复交付目录，不能随意删除。

### 9.4 拒绝理由和逐节点回溯

**症状**

询问“拒绝理由”只重复 Run 汇总，无法知道哪张图片在哪个节点被拒绝。

**状态：已修复基础能力**

**已经怎么改**

- 每张资产、每个节点记录输入、状态、耗时、decision、reason、error 和 evidence；
- TUI/API 可以查询 grounded audit；
- Provider 原始证据单独保存。

**剩余问题**

- 当前 VLM 总标签仍无法解释每条原始约束；
- 需要 constraint-level evidence 和审计分页、过滤、导出。

### 9.5 空 Dataset 和错误成功状态

**症状**

- 全部图片被拒绝仍声称完成；
- 有失败资产却显示成功；
- 文件发布成功但语义没执行也显示 `SUCCEEDED`。

**状态：已修复主路径**

**已经怎么改**

- 空 Dataset 阻止发布；
- 引入 `PARTIAL`；
- 有执行失败时 QC 添加 `EXECUTION_FAILURES_PRESENT`；
- `PARTIAL` 进入 Repair，不直接交付；
- 缺少要求的语义证据时 QC 失败；
- Prompt/evidence 不完整不能伪装成语义成功。

### 9.6 `Hard violations 0` 被误解为任务完全满足

**最新问题**

如果原始硬约束根本没有进入能力 DAG，QC 没有测量它，自然可能得到 `0`。

**状态：待修复，P0**

**准备怎么改**

- 每条 hard constraint 必须注册 evaluator；
- QC 输出 `evaluated / passed / failed / missing`；
- 未执行的 required hard constraint 不能计作 0 violation；
- `semantic_verified` 和 `constraint_coverage_verified` 分开。

### 9.7 失败资产的修复闭环

**状态：已修复基础闭环**

**已经怎么改**

- Repair Run 只处理失败或缺失资产；
- 不重新处理父版本成功资产；
- 保留 operation lineage 和 delivery lineage；
- 三次失败后进入 abandoned；
- 用户显式 exclude 后才可进入最终版本；
- 旧 Run 和 DatasetVersion 不覆盖。

**剩余验收**

完整真实 Acceptance Campaign 仍需覆盖三次失败、abandoned、exclude、重新 QC 和 Export。

## 10. Tools、Agent 和 Operator 边界

### 10.1 Tools 目录和 TUI 显示名称不一致

**解释**

代码 Tool Registry 中是稳定工具规范；TUI 的 `Requirement Analyzer`、`Pipeline Compiler` 等是行动摘要中的用户可读名称。它们不是图片算子 Catalog。

**状态：设计已明确**

### 10.2 Agent 不应直接调用 Operator

**原则固化**

禁止：

```text
LLM -> execute_operator
LLM -> shell/python
LLM -> 任意写 Pipeline YAML
```

必须：

```text
LLM
  -> Tool proposal
  -> ActionPolicy / AgentRuntime
  -> 持久化控制面
  -> Worker
  -> Operator
```

Tool 不能成为第二套状态机，也不能绕过审批。

### 10.3 Tools 层不是通用电脑控制层

当前 P0 Tool 主要包括：

- Operator 检索；
- PipelineArtifact 编译和校验；
- 控制面事实查询；
- 控制动作提议。

联网、任意文件写入、Shell、Python 不应默认暴露给 DataAgent 模型。未来新增 Tool 必须有参数 Schema、权限、脱敏、审计、幂等和副作用级别。

## 11. Git、运行环境和工程流程

### 11.1 Commit、Push、工作区和运行进程不是一回事

**反复发生的问题**

- 文档存在但未 Commit；
- 已 Commit 但未 Push；
- 代码已更新但 API/Worker 还是旧进程；
- `.dataagent` 中有真实证据，但不在 Git；
- 工作区存在用户未提交修改。

**状态：原则固化**

汇报时必须分别说明：

```text
工作区状态
当前分支和 HEAD
本地提交
远端 Push
实际运行的 API/Worker revision
运行证据路径
```

### 11.2 每个小功能独立 Commit

**用户要求**

功能实现、测试通过后单独 Commit，并写清完成内容。

**状态：当前开发流程已采用**

提交说明应描述行为变化，而不是只写“update”或“fix bug”。文档变更是否提交要单独说明。

### 11.3 不要破坏脏工作区

**原则固化**

- 不使用 `git reset --hard`；
- 不回退用户修改；
- 只暂存本次相关文件；
- 遇到其他未提交修改时先区分归属；
- 全量测试被无关修改阻断时要明确说明，不能擅自修掉。

### 11.4 自动测试不等于真实验收

**反复误报**

- Mock VLM 通过被描述成真实模型可用；
- 217 个 Catalog 被描述成 217 个可执行；
- 单元测试通过被描述成生产完成。

**原则固化**

必须区分：

```text
单元测试
集成测试
真实 Provider 单图冒烟
真实小集合 Run
Golden Set
完整 Acceptance Campaign
生产发布
```

### 11.5 Windows 特有问题

**已知坑**

- PowerShell 中文显示乱码不一定是源码损坏；
- 不要因终端乱码批量重写文件；
- Torch/依赖安装可能触发长路径问题；
- Data-Juicer Windows local source 需要显式配置；
- 取消外部任务要终止进程树；
- 不要用宽泛命令行模式误杀进程。

**状态：部分已工程化，原则持续适用**

### 11.6 代码语言

**讨论结论**

代码标识符、Schema 字段、日志 code、Operator ID 和文档中的技术契约优先英文；用户界面和产品文案可中文。本项目仍存在部分历史中文和编码问题，不需要为了“全英文”一次性重写所有代码。

**状态：持续治理**

## 12. 当前建议的修复顺序

### P0-1：结构化 Constraint Contract

新增可版本化约束模型，至少覆盖：

- metadata range；
- deterministic visual measurement；
- remote semantic predicate；
- dataset operation；
- output/publication；
- hard/soft；
- required/optional；
- source text 和 provenance。

### P0-2：重构能力规划边界

- 拆分 `operators/planning.py`；
- Requirement Agent 负责模型理解；
- Capability Planner 负责稳定标准化；
- 不再依赖任务关键词表。

### P0-3：Constraint Coverage Matrix

逐项验证：

```text
Constraint -> Capability -> Operator -> Parameter -> Pipeline Node
```

任何 required 项缺失都进入 Resolution Loop。

### P0-4：Pipeline 语义完整性 Validator

- 验证每个约束都有节点；
- 验证参数与原始阈值一致；
- 验证 hard constraint 没被策略修改；
- 验证不可见条件没有进入 VLM；
- 验证 Dataset/Asset scope 合法。

### P0-5：逐约束 Evidence 和 QC

- 确定性测量值；
- VLM 逐语义条件判断；
- Dedup group；
- 逐约束 missing/fail/pass；
- Manifest 和审计按 constraint ID 关联。

### P0-6：Stage-aware 并行

将 Asset 和 Dataset 节点分阶段，使 Dataset 去重不再关闭后续 VLM 并行。

### P0-7：真实回归

至少建立以下 Golden Task：

1. 黑衣 + 尺寸 + 宽高比 + 文件大小 + 人脸数 + 去重；
2. 猫狗真实性、清晰度、分类和分目录；
3. 单纯视觉筛选；
4. 纯确定性 Data-Juicer CPU Pipeline；
5. GPU 能力缺口 Resolution；
6. 单图超时、Repair 和最终 Export。

## 13. 后续开发不得重复的核心错误

1. 不要出现一个任务就新增一个复合 Operator。
2. 不要用关键词表代替开放语义理解。
3. 不要让模型直接写生产 Pipeline 或改数据库。
4. 不要让模型回答 Run、路径、计数和算子这些控制面事实。
5. 不要把 Retriever 命中写成 Pipeline 已覆盖。
6. 不要把贫化后的 Capability Coverage 写成用户需求完整。
7. 不要把文件大小、精确尺寸、去重等条件交给 VLM 猜。
8. 不要把空标签、解析错误或超时降级成正常 unknown。
9. 不要给所有 Data-Juicer 算子套同一个输出解析器。
10. 不要把 217 个 discoverable 算子说成 217 个 released 算子。
11. 不要未经验证承诺 GPU 算子可回退 CPU。
12. 不要在任务运行时偷偷下载大型模型。
13. 不要让一个 Dataset 算子永久关闭所有后续并行。
14. 不要把 `PARTIAL`、空 Dataset 或语义未验证写成成功交付。
15. 不要从计划配置推测物理输出路径。
16. 不要只给 Run 汇总而没有逐图逐节点证据。
17. 不要把 Provider 调试证据重复塞进每张资产的交付 Manifest。
18. 不要把 Tool Trace 伪装成模型思维链。
19. 不要只关闭 TUI 就认为 API 和 Worker 已更新。
20. 不要把自动测试通过写成真实 Provider 已验收。
21. 不要覆盖旧 Prompt、Pipeline、Run、DatasetVersion 或修复证据。
22. 不要在脏工作区做破坏性 Git 操作。

## 14. 一句话结论

DataAgent 当前最大风险已经不再是“没有算子”，而是“用户约束在规划链路中丢失后，系统仍可能把残缺 Pipeline 判为完整并执行”。下一阶段必须优先建立结构化约束、逐约束覆盖和逐约束证据，再继续扩充算子、提高并发或优化模型成本。
