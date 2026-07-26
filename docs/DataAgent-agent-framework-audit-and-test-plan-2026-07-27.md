# DataAgent Agent 框架系统审计与测试改进方案

> 日期：2026-07-27  
> 仓库：`D:\newDataAgent`  
> 审计方式：只读代码与数据审计、现有测试回归、真实 Run 取证、`D:\data\yifu` 数据检查、参考实现对照  
> 约束：本次不修改产品代码；本文是唯一新增产物  
> 结论状态：截至当前工作区与 `.dataagent/platform/control.db`

## 0. 一页结论

DataAgent 已经不是“空框架”。它已经完成了一条可运行的控制面垂直切片：

- 自然语言会话与结构化控制动作；
- TaskSpec、RetrievalPlan、Pipeline、SamplingPlan、Run、DatasetVersion、QCReport 的版本对象；
- LangGraph interrupt/checkpoint；
- 动态 Operator Catalog、Data-Juicer Provider、Native Remote VLM；
- Worker、逐资产节点审计、失败隔离、Repair、Export；
- 三条 Pipeline 的确定性生成；
- TUI 行动摘要和 Run 终态通知；
- 受控 PipelineArtifact 序列化与 Schema/算子/参数校验；
- 240 个现有自动测试全部通过。

但它目前还不是 PRD 所描述的“需求驱动、能力完整、可自证的 Agent 数据生产平台”，也还没有达到 Claude Code、Codex、nanobot 等成熟 Agent 产品给用户的持续在场感。最严重的问题不是算子少，而是：

> 用户原始要求可以在 `TaskSpec -> Capability -> Pipeline -> Evidence -> QC` 之间丢失，而系统仍会把残缺 Pipeline 判为完整、允许用户批准并执行。

`D:\data\yifu` 真实任务已经完整证明这条失败链：

1. 四条精确约束被模型写进自然语言 `exclusion_requirements`，没有进入结构化 `hard_constraints`；
2. 旧关键词规划器漏掉图片尺寸、宽高比、文件大小和人脸数量四项能力；
3. Retriever 实际召回了四个正确的 Data-Juicer 算子；
4. Coverage 只遍历贫化后的 capability 列表，因而忽略已召回算子；
5. 三条 Pipeline 都缺四个节点；
6. Pipeline Validator 仍返回 `production_eligible=true`；
7. 不适合 VLM 判断的约束被拼入 VLM Prompt；
8. QC 只检查 TaskSpec 中实际存在的硬约束，报告 `hard_rule_violation_rate=0`；
9. Run 最终因一张图超时成为 `PARTIAL`，但即使没有超时，也不能证明六条原始要求全部满足。

因此，下一阶段不能先扩算子、调大并发或继续堆对话补丁。正确顺序是：

```text
结构化 Constraint Contract
-> 需求逐项确认
-> Constraint/Capability/Operator/Node/Parameter Coverage
-> 语义完整性阻断校验
-> 逐约束 Evidence
-> 独立 QC
-> Stage-aware 并行与性能优化
-> 更大范围 PRD 能力
```

建议把当前产品对外状态定义为：

> “具备真实执行与治理基础的 Alpha 垂直切片；单一简单视觉筛选可运行，多约束生产任务尚未达到可信交付门槛。”

不建议继续使用“主闭环已完成”来描述当前状态。“对象能串起来”和“用户目标被完整执行并自证”是两件不同的事。

---

## 1. 审计目标与范围

本次审计回答五个问题：

1. 当前 Agent 框架实际完成了什么？
2. 为什么用户感到“不够智能、很多地方写死”？
3. 理想交互与当前交互的差距在哪里？
4. `yifu` 任务为什么 Pipeline 错、结果也不可信？
5. 后续应按什么顺序改，如何用测试证明改对？

### 1.1 已检查材料

产品与交接材料：

- `DataAgent-final-PRD-v1.1.md`
- `DataAgent-code-architecture-v1.1.md`
- `DataAgent-development-handoff-2026-07-24-v9.md`
- `docs/DataAgent-problem-ledger-2026-07-26.md`
- `docs/development-boundaries-2026-07-23.md`
- `docs/dja-reference-analysis.md`
- `CONTEXT.md`
- `README.md`

当前实现：

- `dataagent/`：115 个 Python 文件，约 21,047 行；
- `apps/`：11 个 Python 文件，约 1,665 行；
- `tests/`：41 个 Python 文件，约 10,010 行；
- LangGraph 四个子图、ConversationService、Tool Loop、Operator Catalog、Compiler、Worker、QC、Dataset 与 Repair 链路；
- `.dataagent/platform/control.db` 中的真实 Conversation、TaskSpec、RetrievalPlan、Pipeline、Run、Node Result、Dataset 和 QC 记录。

参考实现：

- `D:\DataAgent\data-juicer-agents`，明确排除 `dpagent` 和 `dataplatform`；
- `D:\nanobot`；
- Claude Code 官方文档；
- OpenAI Codex 官方产品材料。

真实数据：

- `D:\data\yifu` 当前有 19 张图片；
- 2026-07-24 的目标 Run 实际处理 18 张；
- 当前目录比当时多一张 `c183c1e65b434d10fb88b6f6e7672e22.jpg`。

### 1.2 本次没有做的事

- 没有修改任何产品代码、测试代码或配置；
- 没有重新发起会产生远程模型费用的新生产 Run；
- 没有为“黑色衣服”主观定义伪造唯一 Golden Label；
- 没有把现有自动测试通过解释成真实质量验收通过；
- 没有审计用户明确排除的 `dpagent`、`dataplatform`。

---

## 2. 当前架构的真实形态

### 2.1 名义架构

当前主图名义上由四个 Agent 子图组成：

```text
Requirement Agent
-> TaskSpec HITL
-> Retrieval Agent
-> Capability Resolution
-> Processing Agent
-> Pipeline HITL
-> Strategy Agent
-> Run/Worker/QC（应用层）
```

这与 PRD 的分层方向一致：LangGraph 管状态与人工中断，Worker 管图片级执行，控制面保存正式事实。

### 2.2 实际智能决策分布

当前开放语义能力主要集中在 `ConversationService -> ModelGateway.conversation_turn()`：

- 模型判断 Intent；
- 模型生成 `TaskSpecPatch`；
- 模型生成用户回复。

四个所谓 Agent 子图本身主要是固定的确定性函数：

| 子图 | 当前节点 | 实际行为 |
|---|---|---|
| Requirement | `generate_task_spec`、`validate_task_spec` | 关键词拆 capability，Pydantic 校验 |
| Retrieval | `generate_retrieval_plan`、`assess_candidate_sufficiency` | Catalog 匹配、排序、按已声明 capability 做覆盖 |
| Processing | `generate_pipeline_variants`、`select_representatives` | 固定模板和策略参数编译三条 Pipeline |
| Strategy | `generate_sampling_plan` | 固定优先级权重、簇上限和随机种子 |

这解释了“看起来有四个 Agent，实际不够智能”的直觉：

- Agent 名称存在；
- LangGraph 子图存在；
- 但子图内部没有“观察 -> 假设/计划 -> 工具调用 -> 校验 -> 修正”的自治循环；
- 输入稍微超出关键词和固定字段，系统就会把语义压扁成已有槽位；
- 最终展示的是已完成动作摘要，而不是一个持续更新、可校验的工作计划。

这不是“是否用了 LLM”的二元问题，而是决策权、契约和反馈循环放错了位置。

### 2.3 当前代码的两个过深/过浅模块

`dataagent/application/conversation.py` 约 1,848 行，承担：

- Intent 解释；
- 控制动作执行；
- 工单创建；
- TaskSpec 修改；
- Pipeline 选择；
- Run 控制；
- 状态事实拼装；
- 各类自然语言回复；
- Tool Trace 生成；
- Repair、Audit、Dataset 查询回复。

它是一个过宽的应用服务，导致任何交互需求都容易继续向同一文件加分支。

与之相反，四个 Agent 子图过浅，很多只有两个固定节点。这造成：

- “智能”集中在对话入口；
- “专业 Agent”退化为流程标签；
- 规划失败时没有自己的纠错循环；
- UI 无法展示各 Agent 的真实计划、状态和证据；
- 很难对单个 Agent 建独立评测集。

### 2.4 PRD 与当前实现的范围错位

PRD 是完整平台愿景，当前实现是局部垂直切片。以下对象或闭环尚未形成可用实现：

| PRD 能力 | 当前状态 | 判断 |
|---|---|---|
| TaskSpec 版本与确认 | 已有 | 基础完成，但约束表达不完整 |
| Operator Catalog/Provider | 已有 | 基础较强 |
| RetrievalPlan | 已有对象 | 实际更像“算子检索计划”，不是多源数据召回计划 |
| CandidatePool | 未实现 | PRD 检索 Agent 主产物缺失 |
| Pipeline 三方案 | 已有 | 结构和语义完整性不足 |
| Pipeline Optimizer/Experiment Manager | 未实现 | 三方案没有同样本试跑选优 |
| Independent Pipeline Evaluator | 未实现 | 编译器直接生成代表方案 |
| CurationPlan | 有领域模型引用 | 没有完整生产闭环 |
| SamplingPlan | 已有 | 基本固定模板，未基于候选画像优化 |
| DatasetVersion/QC | 已有 | 逐约束验收缺失 |
| GoldenSet/ReviewSet | 零散或未实现 | 不能支持真实 precision/recall 与边界验收 |
| TrainingRun/ModelFeedback | 未实现 | PRD 后半闭环缺失 |
| Loop Supervisor | 未实现 | 失败路由主要是人工命令和 Repair |
| Web 主界面 | 未实现 | 目前是 TUI/API |
| Pipeline 同图对比/节点预览 | 有 API 基础 | 产品交互未达到 PRD |
| 外部模型/算子发现准入 | 未形成闭环 | 只能检索本地 Catalog 和切换运行后端 |

建议将 PRD 拆成：

- `Product North Star`：保留完整愿景；
- `Alpha Acceptance`：当前可信范围；
- `Beta Acceptance`：多约束闭环；
- `GA Acceptance`：训练反馈与完整平台。

否则交接文档很容易把“已有类或对象”误写成“产品能力完成”。

---

## 3. 已经完成且值得保留的部分

### 3.1 控制面与领域对象

以下基础是正确方向，不建议推倒重写：

- TaskSpec、Pipeline、DatasetVersion 等不可变版本；
- Owner 绑定和控制面事实接地；
- LangGraph checkpoint 与 interrupt；
- 对话 Action Schema 和非法状态 Guard；
- Pipeline、Run、Dataset、Repair 的版本血缘；
- `PARTIAL` 与 `SUCCEEDED` 的区分；
- 失败资产的 Repair/Abandoned/Exclude 语义；
- 原始目录只读，交付目录独立；
- PromptBinding、模型 ID、request ID 和逐图 evidence。

### 3.2 Operator/Provider 治理

已经具备：

- OperatorSpecVersion；
- Provider 与 Runtime 分离；
- Data-Juicer Catalog 动态发现；
- discoverable、available、released 的生命周期区分；
- Native Remote VLM；
- 参数 Schema 验证；
- Draft/Mock 生产阻断；
- CPU/Remote 等运行后端建模；
- 安装 Registry 和能力报告。

这部分已经比简单“让 LLM 拼脚本”的方案更可靠。

### 3.3 执行、审计与恢复

已经具备：

- Worker lease/claim 协议；
- 逐资产、逐节点结果；
- Run events；
- 单资产失败隔离；
- 有界并发基础；
- 暂停、恢复、取消；
- QCReport；
- Repair Run；
- Dataset export；
- Manifest 与 provider artifact 分层。

`yifu` 任务虽然失败，但控制面保留了足够证据让本次审计可以还原问题，这是重要进展。

### 3.4 对话与展示的已有改进

已经完成：

- 模型优先 Intent；
- 结构化 Action；
- JSON repair；
- 普通 CHAT 与控制动作分离；
- TaskSpec 详情查询；
- 三条真实 Pipeline 节点展示；
- Tool Action Trace；
- Run 终态主动通知。

这些能力应该演进，不应退回关键词确认词或模型自由文本直接执行。

---

## 4. `D:\data\yifu` 真实任务取证

### 4.1 用户原始要求

```text
1. 图片宽高均不少于 64 像素
2. 宽高比在 0.3 到 3.5 之间
3. 文件大小在 1KB 到 20MB 之间
4. 图片中的人脸数量在 0 到 2 之间
5. 主体穿黑色衣服
6. 去除重复图片
```

正确的原子约束至少应为：

| Constraint | 类型 | 推荐 evaluator | 参数 |
|---|---|---|---|
| C1 宽度 | metadata | deterministic | `min_width=64` |
| C2 高度 | metadata | deterministic | `min_height=64` |
| C3 宽高比 | metadata | deterministic | `min=0.3,max=3.5` |
| C4 文件大小 | metadata | deterministic | `min=1024,max=20971520` |
| C5 人脸数 | deterministic vision/model | face detector | `min=0,max=2` |
| C6 黑衣 | remote semantic | VLM/专用分类器 | 需澄清口径 |
| C7 去重 | dataset | exact + perceptual dedup | 阈值需策略化 |
| C8 发布 | output | manifest/export | 保留逐项 evidence |

“宽高均不少于 64”本身包含两个原子判断，因此六条自然语言要求实际至少形成八个可验证约束。

### 4.2 对话与 TaskSpec 的事实偏差

真实对话中的草案回复显示：

```text
排除条件（hard rules）：
1. 图片宽或高少于 64 像素
2. 宽高比小于 0.3 或大于 3.5
3. 文件大小小于 1KB 或大于 20MB
4. 人脸数量超过 2 张
```

但持久化的 TaskSpec 是：

```json
{
  "hard_constraints": {
    "preserve_source": true
  },
  "semantic_requirements": [
    "主体穿黑色衣服的图片",
    "去除重复图片"
  ],
  "exclusion_requirements": [
    "图片宽或高少于64像素",
    "宽高比小于0.3或大于3.5",
    "文件大小小于1KB或大于20MB",
    "人脸数量超过2张的图片"
  ]
}
```

这是 P0 级“确认欺骗”风险：

- 用户看到的是完整 hard rules；
- 系统冻结的却只是松散文本；
- 确认页没有显示“每项由谁执行、是否已有节点、能否验收”；
- 用户的“确认”无法代表对真实执行契约的知情确认。

此外，`去除重复图片` 被错误放入 `semantic_requirements`，说明字段分类本身没有经过确定性归一化。

### 4.3 能力拆解的可重复失败

现有正式函数对原始需求的实际输出：

```text
actual:
- image_decode
- perceptual_deduplication
- visual_semantic_selection
- manifest

missing:
- image_dimensions
- aspect_ratio
- file_size
- face_count
```

当前实现由 `_TASK_CAPABILITY_RULES` 和 `_CAPABILITY_KEYWORDS` 决定，无法覆盖开放自然语言中的参数化约束。

这不是再补四组关键词就能解决的问题。继续补词会产生：

- 中文/英文/单位/表达方式组合爆炸；
- 同一词在不同上下文中的歧义；
- 参数和单位仍没有结构化落点；
- 测试只会验证已知短语，无法验证语义完整性。

### 4.4 Retriever 找对了，Coverage 丢掉了

真实 Action Trace 的 Operator Retriever evidence 包含：

```text
datajuicer.image_shape_filter:1
datajuicer.image_aspect_ratio_filter:1
datajuicer.image_size_filter:1
datajuicer.image_face_count_filter:1
datajuicer.image_deduplicator:1
native.remote_vlm:1
```

但 `_capability_coverage()` 只遍历 `spec.capability_requirements`。四项能力没有先进入 TaskSpec，就不会进入 Coverage，也不会被选中。

因此：

```text
Retriever 命中 != Constraint 覆盖
Candidate 可执行 != Pipeline 已使用
Capability 覆盖完整 != 原始需求完整
```

当前 `sufficient=true` 只能解释为“贫化后的 capability 列表已覆盖”，不能解释为“用户需求已覆盖”。

### 4.5 三条 Pipeline 的实际内容

三条 Pipeline 都只有：

```text
decode_check
-> perceptual_dedup
-> native.remote_vlm
-> visual_semantic_selection
-> manifest
```

差异只有：

| 策略 | dedup distance | uncertain policy |
|---|---:|---|
| 保留优先 | 4 | keep |
| 均衡 | 2 | review |
| 质量优先 | 1 | reject |

缺失：

- 尺寸节点；
- 宽高比节点；
- 文件大小节点；
- 人脸数量节点；
- 每个节点到原始 constraint 的映射；
- 硬阈值参数绑定；
- 三方案同样本试跑结果；
- 成本/时延/质量对比；
- 边界样本。

因此当前“三条 Pipeline”主要是三套固定策略参数，不是 PRD 中“同口径试跑后供用户选择的三个代表方案”。

### 4.6 Pipeline Validator 为什么误判

当前 Validator 检查：

- Artifact Schema；
- checksum；
- Operator 是否存在；
- Operator lifecycle；
- 参数是否符合 Operator parameter schema。

它不检查：

- 原始 required constraint 是否都有节点；
- 节点参数是否等于用户确认阈值；
- hard constraint 是否被策略改写；
- evaluator 类型是否正确；
- Dataset/Asset scope 是否合理；
- 所需 evidence 是否能由节点产出；
- Pipeline 是否仍绑定当前 TaskSpec revision。

所以它对残缺 Pipeline 返回：

```text
PipelineArtifact is valid and production eligible.
```

更准确的命名应该区分：

- `artifact_valid`：结构和 checksum 正确；
- `operator_resolvable`：算子和参数可解析；
- `constraint_complete`：用户约束逐项覆盖；
- `evidence_complete`：能生成验收所需 evidence；
- `runtime_eligible`：当前环境可运行；
- `production_eligible`：以上全部成立。

### 4.7 Prompt 越权承担了错误职责

真实 Prompt 同时包含：

```text
主体穿黑色衣服
去除重复图片
图片宽或高少于 64
宽高比范围
文件大小范围
人脸数量范围
```

其中：

- 文件大小不能由图像像素可靠判断；
- 单图不能判断数据集重复关系；
- 精确尺寸和宽高比应直接读元数据；
- 人脸数量应由可校准的人脸 detector 产生计数 evidence；
- VLM 只应判断“主体穿黑衣”这类可见语义。

当前 Prompt 虽然写了“Judge only visible image evidence”，但同时又要求模型评估不可见条件，契约自相矛盾。

### 4.8 QC 的假安全

QC 的 hard rule 检查目前支持部分尺寸和宽高比字段，但：

- 真实 TaskSpec 没写这些字段；
- 文件大小没有检查；
- 人脸数量没有检查；
- 没有逐 constraint evidence；
- `hard_rule_violation_rate=0` 只是对空/贫化规则集合的结果。

真实 QC：

```text
status = FAILED
hard_rule_violation_rate = 0
execution_failure_rate = 0.055556
semantic_quality_verified = false
reason = EXECUTION_FAILURES_PRESENT
```

如果那张超时图片没有失败，QC 很可能通过，但仍然无法证明六条要求完整执行。这是比单次超时更严重的问题。

### 4.9 当前数据的独立检查

当前 19 张图片：

- 宽度最小 153，高度最小 224；
- 宽高比约 0.664～0.757；
- 文件大小约 5.2KB～3.24MB；
- 因而当前所有图片都满足前三类确定性范围；
- 最后两张图片 SHA256 完全相同，是 1 组精确重复；
- 从联系表人工观察，所有图片可见人脸数均不超过 2，但这不是正式 detector/Golden Label 证据；
- “黑衣”存在多种边界：黑色上衣、黑色连体衣、黑色背带裤、黑色裤子、黑白条纹、局部黑色、多人物。

旧 Run 在 18 张中：

- keep 3；
- reject 14；
- failed 1；
- 正确识别了 1 张 exact duplicate；
- VLM 对 16 张给出 match/mismatch；
- 没有 reason 或逐条件结果，只有单一 `semantic_match/mismatch` 标签。

因此不能仅凭 3 张保留图判断语义筛选准确率。需要先冻结“主体”和“黑衣”的标注手册，再做 Golden Set。

### 4.10 性能问题

真实 Run：

- wall time 约 854 秒；
- VLM 节点 duration 总和约 853 秒；
- VLM P50 约 24.1 秒；
- 最慢 285 秒后超时；
- Run event 明确记录：

```text
asset_parallelism_disabled
reason = operator_not_parallel_safe
operator = builtin.perceptual_dedup:1
requested_concurrency = 4
```

这说明整条 Pipeline 实际串行。当前并行策略是全有或全无：只要一个节点不 parallel-safe，所有资产的后续 VLM 也串行。

正确方向是 stage-aware：

```text
并行 metadata/decode
-> dataset dedup barrier
-> 对保留资产并行 face/VLM
-> dataset publish/QC barrier
```

---

## 5. 为什么当前交互不能降低用户焦虑

### 5.1 当前事件是在“完成后”才出现

TUI 先显示：

```text
DataAgent 正在处理...
```

随后 `ConversationService` 同步等待 `_decide()` 完成。真实首轮模型判断耗时约 13.1 秒，结束后才记录：

```text
正在理解需求
Requirement Analyzer
耗时 13127ms
```

工具调用也是 `registry.execute()` 返回后才构造 trace。因此当前 action stream 本质上是：

> “逐项发送已经完成的摘要”，不是“开始/进度/完成”的实时事件。

这就是为什么截图中的成熟产品体验没有出现：

- 用户看不到系统现在打算做什么；
- 长模型调用期间只有一个模糊 spinner；
- 看不到剩余任务数；
- 看不到哪些步骤可并行；
- 看不到某一步失败后是否正在重试；
- 正式回答与行动轨迹虽然视觉上分开，但事件生命周期没有真正分开。

### 5.2 不应把完整私有思维链当作产品目标

用户需要的是可理解、可验证、实时更新的“工作过程”，而不是模型全部隐藏思维链。

建议产品展示三类内容：

1. **Plan**：系统准备完成哪些任务；
2. **Activity**：正在调用什么工具、读取什么证据、状态和耗时；
3. **Decision summary**：为什么选择这个结果、基于哪些事实、有什么不确定性。

不展示：

- 原始内部 token-by-token 隐式思维链；
- 可能包含敏感信息或不稳定自言自语的长 reasoning；
- 无法审计、无法复现的“我想了很久所以正确”。

这与 PRD 14.4 的“不默认展示冗长思维过程，只展示可审计的决策依据和结构化结果”一致。

### 5.3 建议的统一事件协议

所有 Agent、Tool、Engine 使用同一事件 envelope：

```json
{
  "event_id": "evt_xxx",
  "work_order_id": "work_order_xxx",
  "turn_id": "turn_xxx",
  "run_id": null,
  "parent_id": "task_xxx",
  "sequence": 12,
  "timestamp": "...",
  "channel": "plan|activity|decision|result|alert",
  "type": "task_started|task_progress|tool_started|tool_completed|...",
  "visibility": "user|expert|internal",
  "payload": {}
}
```

最小事件集：

```text
turn_started
plan_created
plan_revised
task_started
task_progress
task_completed
task_failed
tool_started
tool_progress
tool_completed
tool_failed
clarification_required
approval_required
decision_recorded
run_progress
run_terminal
final_response_started
final_response_completed
```

关键要求：

- `started` 必须在调用前发送；
- `completed/failed` 必须带同一个 task/tool ID；
- 每个事件单调 sequence；
- 断线重连可按 cursor 补发；
- final 是独立 channel，不覆盖 activity；
- 事件入库，UI 刷新后仍可恢复；
- 对重复消息和断线重试幂等；
- 长步骤定期 heartbeat；
- 支持用户在任务运行中补充、改变方向或取消。

### 5.4 建议的交互流程

```text
用户提出需求
-> Agent 显示初始任务计划
-> 解析为逐项 Constraint
-> 检查缺失/冲突/不可观测项
-> 提出最少且高价值的澄清问题
-> 生成“确认版需求”
-> 用户确认
-> 检索 Operator/Catalog/历史 Pipeline
-> 生成 Constraint Coverage Matrix
-> 能力缺口则进入 Resolution
-> 生成并保存三条 PipelineArtifact
-> 小样本同口径试跑
-> 展示三方案差异、样例、成本、风险
-> 用户选择
-> 全量 Run
-> 逐约束 QC
-> Repair/Exclude
-> Deliverable Export
```

首轮可显示：

```text
共 6 个任务，已完成 0 个
1. 理解并拆分需求
2. 检查歧义与不可观测条件
3. 生成确认版需求
4. 检索并核验算子覆盖
5. 生成三条 Pipeline
6. 等待你选择后试跑/执行
```

澄清完成后，计划可以修订，但必须显示变更原因。

### 5.5 `yifu` 任务应如何提问

系统不应重复询问已经明确的阈值，只问会显著改变结果的问题，例如：

```text
“主体穿黑色衣服”有一个口径需要确认：
是否只保留主体的上衣/连体主服装以黑色为主？
黑色裤子、黑白条纹、局部黑色和多人中仅一人穿黑色，默认不算。
如果这个默认口径可以，我会据此生成语义判定规则。
```

确认版需求应逐项展示：

| ID | 要求 | 类型 | 执行方式 | 阈值 | 不确定策略 |
|---|---|---|---|---|---|
| C1 | 宽度不少于 64 | hard | metadata | 64px | reject |
| C2 | 高度不少于 64 | hard | metadata | 64px | reject |
| C3 | 宽高比范围 | hard | metadata | 0.3～3.5 | reject |
| C4 | 文件大小范围 | hard | metadata | 1KB～20MB | reject |
| C5 | 人脸数量 | hard | face detector | 0～2 | review/error |
| C6 | 主体黑衣 | hard semantic | VLM | 已确认口径 | review |
| C7 | 去重 | hard dataset | SHA256+pHash | 策略参数 | reject duplicate |

用户确认的是这个表，而不是一段可能与内部对象不一致的自然语言摘要。

---

## 6. Pipeline 与算子缺口闭环的目标设计

### 6.1 三个不同层次的文件

不要让一个 YAML 同时承担需求、规划、运行和验收全部职责。建议至少分为：

1. `task-spec.yaml`
   - 用户确认的目标、Constraint、验收和歧义处置；
2. `pipeline-{strategy}.yaml`
   - 节点、边、算子版本、参数、PromptBinding、constraint mapping；
3. `run-bundle.json`
   - 输入快照、执行环境、模型、evidence、QC 和输出引用。

正常对话流程必须真正持久化文件或可下载 artifact，而不是只在内存中返回 YAML 字符串。

每个 Pipeline node 至少增加：

```yaml
satisfies_constraint_ids:
  - C1
evidence_contract:
  type: scalar_measurement
  fields: [width]
failure_policy: reject
```

### 6.2 Coverage Matrix

编译前后都维护：

| Constraint | Capability | Candidate | Selected | Parameter binding | Node | Evidence | Status |
|---|---|---|---|---|---|---|---|
| C1 | image_dimensions | shape_filter | shape_filter | min_width=64 | n1 | width | covered |
| C4 | file_size | size_filter | size_filter | min=1024 | n2 | bytes | covered |
| C6 | visual_semantic | remote_vlm | remote_vlm | predicate hash | n5 | predicate result | covered |

阻断规则：

- required constraint 没有 capability：阻断；
- 没有可执行 operator：进入能力缺口；
- 参数没绑定或单位不一致：阻断；
- node 不存在：阻断；
- evidence contract 不可满足：阻断；
- metadata constraint 被分配给 VLM：阻断；
- hard threshold 被三策略修改：阻断；
- Pipeline 绑定旧 TaskSpec revision：阻断。

### 6.3 三条策略的合法差异

不能修改用户硬阈值：

```text
min_width=64
aspect_ratio=0.3..3.5
file_size=1KB..20MB
face_count=0..2
```

允许差异：

- VLM uncertain 是 keep/review/reject；
- perceptual dedup distance；
- 是否只做 exact dedup；
- 是否启用昂贵的二次语义复核；
- 低置信度送人工的比例；
- 成本/时延预算。

三个方案必须用同一代表样本试跑，输出真实差异，而不是只有说明文字。

### 6.4 外部能力发现与下载

当前实现没有完整闭环。建议状态机：

```text
CAPABILITY_MISSING
-> 搜索已配置可信来源
-> 生成 CandidateImplementation[]
-> 许可证/权重/资源/维护/安全报告
-> 用户选择：
   A. 自己下载并提供路径
   B. 授权 Agent 自动下载
   C. 使用 Remote API
   D. 修改/放弃需求
-> Worker 准备模型或 Provider
-> SHA256/版本/许可证校验
-> Sandbox + Golden Set
-> 个人 Operator EVALUATED
-> 人工批准为 PERSONAL_RELEASE
-> 重新做 Coverage 与三方案编排
```

自动下载必须满足：

- 明确显示来源、体积、许可证、预计时间、磁盘/GPU需求；
- 用户显式确认；
- 只在 Worker 执行；
- 固定 revision 和 SHA256；
- 下载锁、断点恢复、缓存复用；
- 默认不允许未经审核的新代码进入生产；
- 不把“网上找到”当作“已准入”。

---

## 7. 参考 Agent 的可借鉴机制

### 7.1 Data-Juicer Agents

值得借鉴：

- 把 inspect、retrieve、get operator info、build dataset/process/system spec、assemble、validate、save 拆成显式工具链；
- 每个阶段都有输入输出 Schema；
- ReAct Agent 根据工具结果修正下一步；
- Tool runtime 在调用前后发送事件；
- reasoning step 与 final reply 分离；
- 明确 plan_validate 后再 plan_save/apply；
- 能力不足时有 operator dev 工具入口。

不应直接照搬：

- 主要靠超长 system prompt 约束顺序，协议仍可能漂移；
- 通用 execute shell/python 不适合直接进入 DataAgent 生产控制面；
- 生成算子脚手架不等于算子实现质量或生产准入；
- apply 后“不做验证”的策略不适合 DataAgent 的交付责任。

最值得吸收的是“工具化的分阶段规划”，不是复制它的 prompt。

### 7.2 nanobot

值得借鉴：

- reasoning、answer、tool activity 是独立流；
- progress callback 支持增量输出；
- tool call 有 start/end/error 生命周期；
- UI 将多个活动聚合成可折叠 Activity Cluster；
- checkpoint 区分 awaiting tools、tools completed、final response；
- 工具批次可并行；
- 会话持久化、断线和恢复测试很丰富；
- 长任务、取消、持续目标有独立状态。

不应直接照搬：

- nanobot 是通用 Agent 框架，DataAgent 仍要保持领域控制面和算子治理；
- 原始 reasoning 内容不应默认完整展示；
- 通用文件/命令工具权限模型不能替代数据生产审批和血缘。

### 7.3 Claude Code / Codex

可借鉴的产品原则：

- Plan/Ask 与执行阶段分开；
- 长任务可在后台运行；
- 用户可查看进度、补充信息、改变方向、批准重要动作；
- 工具活动、文件变更和最终回答分层展示；
- 多个独立任务可以并行；
- 任务完成后主动通知并保留可审查产物。

参考：

- Claude Code CLI 支持 plan permission mode、resume、stream-json 和 verbose：  
  <https://docs.anthropic.com/en/docs/claude-code/cli-usage>
- OpenAI Codex 支持后台任务与并行 Agent 工作流：  
  <https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan>  
  <https://openai.com/codex/>

DataAgent 的目标不是模仿 UI 外观，而是实现同样清晰的：

```text
计划可见
状态持续
重要动作可控
产物可审
失败可恢复
结果可验证
```

---

## 8. 问题清单与优先级

### 8.1 P0：不修复就不能声称多约束任务可信

#### P0-1 缺少结构化 Constraint Contract

症状：

- 精确阈值落入自由文本；
- 去重落入语义要求；
- 无 source span、单位、evaluator、hard/soft、required；
- 无法逐项确认和验收。

验收：

- 任意用户原始要求可追溯到一个或多个 constraint ID；
- 数值、单位、边界开闭、默认策略都结构化；
- 原始文本和解析来源保留；
- 不允许 required constraint 静默消失。

#### P0-2 需求规划仍由关键词规则主导

症状：

- `_TASK_CAPABILITY_RULES` 漏开放表达；
- 新需求靠补词；
- 四 Agent 的智能性集中在 Conversation。

验收：

- 模型输出 Constraint Draft；
- 确定性 normalizer 校验/标准化；
- Capability Planner 只消费结构化 Constraint；
- 关键词只能做诊断 fallback，不能做生产权威；
- 使用同义表达、不同顺序、不同单位的 metamorphic test 结果一致。

#### P0-3 假 Coverage

验收：

- Coverage 根对象是 Constraint，不是 capability；
- 每个 required constraint 必须走通 `Constraint -> Capability -> Operator -> Parameter -> Node -> Evidence`；
- Retriever 召回与最终选中明确分列；
- 任何缺口进入 resolution，不能 `sufficient=true`。

#### P0-4 Validator 不验证语义完整性

验收：

- 对漏节点、错阈值、错 evaluator、旧 spec、缺 evidence、非法 scope 分别有明确 blocker code；
- `production_eligible` 只在全链路通过后为 true；
- 现有 `yifu` 错误 Pipeline 必须稳定判红。

#### P0-5 Prompt 污染

验收：

- metadata/dataset 条件绝不进入 VLM Prompt；
- VLM 输出逐 semantic constraint 的 pass/fail/uncertain/confidence/reason；
- PromptBinding 能追溯 constraint IDs；
- 不再只有一个合并后的 `semantic_match`。

#### P0-6 QC 无法逐约束自证

验收：

- QC 输出每个 constraint 的 evaluated/pass/fail/missing；
- 文件大小、人脸数、dedup group 都有 evidence；
- 任一 required constraint missing 时 QC 失败；
- `hard_rule_violation_rate=0` 必须附 checked constraint count；
- 代理语义结果不能写成真实准确率。

#### P0-7 需求确认页与真实对象不一致

验收：

- 确认页面完全由持久化 TaskSpec/Constraint 渲染；
- 不允许把自由文本 exclusions 标成已结构化 hard rules；
- 显示 evaluator、阈值、缺口和不确定策略；
- 用户确认后生成 immutable revision。

#### P0-8 缺少语义澄清

验收：

- 对“主体”“黑色衣服”等影响标签边界的概念触发最少澄清；
- 问题基于 ambiguity type，不是固定问句；
- 已明确字段不重复问；
- 用户回答能绑定对应 constraint，不污染其他字段。

### 8.2 P1：影响产品可用性、速度与信任

#### P1-1 Action Trace 不是实时生命周期

- 增加 started/progress/completed/failed；
- LLM 开始等待时立即显示任务；
- Tool 在执行前发 started；
- 断线重连可恢复；
- formal answer 独立。

#### P1-2 没有持久化用户可用的 Pipeline 文件

- 三条 PipelineArtifact 保存并提供路径/下载；
- hash 与 DB 中 PipelineVersion 一致；
- Run 明确引用所选 artifact；
- 支持导入后复验。

#### P1-3 三方案没有真实试跑比较

- 同样本、同模型、同预算；
- 输出保留率、逐约束通过、语义指标、失败、P50/P95、成本；
- 边界样本和分歧样本；
- 无 Golden Set 时明确为代理评估。

#### P1-4 Stage-aware 并行

- 不再因一个 Dataset 节点关闭整条 Pipeline 并行；
- DAG/stage barrier；
- remote I/O、CPU、GPU、dataset batch 分开调度；
- backpressure、429、超时、取消和 checkpoint；
- 记录为什么并行或串行。

#### P1-5 VLM 可靠性与成本

- 关闭简单枚举任务的 reasoning 前先做 A/B；
- 缩小 max tokens；
- 连接池；
- 缓存；
- 超时预算和 retry 分类；
- circuit breaker；
- 单图 P95/P99、token、费用指标。

#### P1-6 能力缺口 Resolution 不完整

- 从“切后端/改需求”扩展为外部发现、评估、授权、准备、准入、重编排；
- 所有下载与新代码必须 HITL。

#### P1-7 Agent 子图过浅、Conversation 过宽

- Requirement Agent 拥有完整的 constraint refinement loop；
- Processing Agent 拥有 retrieve/inspect/compile/validate/revise loop；
- Strategy Agent 基于真实 profile，而不是固定常量；
- Conversation 只负责编排用户回合和渲染，不承载全部业务用例。

### 8.3 P2：PRD 平台化能力

- Web 工作台；
- CandidatePool 与多源数据召回；
- Pipeline Optimizer/Experiment Manager；
- GoldenSet/ReviewSet 管理；
- TrainingRun/ModelFeedback；
- Loop Supervisor；
- 数据分布与 train/val/test 泄漏控制；
- 个人/公共 Pipeline 晋升；
- Operator 样例库；
- 多租户、角色权限与合规审批；
- Windows/Linux/GPU 安装矩阵；
- 全链路成本与审计面板。

---

## 9. 建议的实施路线

### Phase 0：冻结错误成功

目标：在新架构完成前，先阻止系统继续把相同错误标为可生产。

动作：

- 把 `yifu` 需求加入 Golden Task；
- 让当前 Pipeline 在 constraint completeness 检查中必定失败；
- 确认页若只有自由文本约束，明确显示“未结构化/不可执行”；
- required constraint missing 时禁止批准和 Run。

退出条件：

- 现有错误 Pipeline 无法 `production_eligible`；
- 不产生新的假成功记录。

### Phase 1：Constraint Contract

动作：

- 设计版本化 `ConstraintSpec`；
- evaluator type：metadata、deterministic vision、remote semantic、dataset、output；
- hard/soft、required、operator hints、evidence contract；
- 单位标准化；
- source span/provenance；
- ambiguity 和 default policy。

退出条件：

- `yifu` 原始需求稳定生成 C1～C8；
- 多种同义表达生成等价规范；
- 用户确认页完全由 contract 渲染。

### Phase 2：Planner 与 Coverage

动作：

- 从 `operators/planning.py` 移出需求理解；
- 建立 Capability DAG；
- Operator selection 独立模块；
- Coverage Matrix；
- 参数绑定和 scope 校验；
- gap resolution。

退出条件：

- 四个正确 Data-Juicer 算子进入候选与节点；
- 漏任一 constraint 都阻断；
- Retriever “召回但未选择”可解释。

### Phase 3：PipelineArtifact 与三方案

动作：

- 持久化 task spec/pipeline artifacts；
- 编译三条合法策略；
- 小样本试跑；
- 独立 evaluator；
- 用户对比与批准。

退出条件：

- 三方案硬阈值完全一致；
- 软策略差异真实；
- artifact 可导出、导入、复验；
- 选择的 Pipeline 与 Run hash 一致。

### Phase 4：Evidence 与 QC

动作：

- 节点输出绑定 constraint IDs；
- VLM 拆为逐语义 predicate；
- QC 逐项聚合；
- Manifest 记录 pass/fail/missing；
- Repair 能定位责任节点。

退出条件：

- 每张保留图都有 C1～C7 全部通过证据；
- 任一缺失 evidence 都不能发布；
- exact duplicate 形成 group 和 canonical asset。

### Phase 5：体验与调度

动作：

- 统一实时事件协议；
- 计划面板、活动流、正式回答分区；
- stage-aware scheduler；
- VLM 连接池、缓存和速率治理；
- 恢复、取消、转向。

退出条件：

- 首个可见事件 < 500ms；
- 长步骤每 5～10 秒有 heartbeat/progress；
- 断线重连无重复 final；
- `yifu` Remote VLM 阶段实际并发；
- 单图失败不阻塞其余资产。

### Phase 6：扩展到完整 PRD

按 CandidatePool、Optimizer、ReviewSet、Training Feedback、Loop Supervisor、Web 的顺序推进。每一阶段都必须有独立验收，不以“类已创建”作为完成。

---

## 10. 测试总方案

### 10.1 测试原则

1. 先建立能抓住用户原始症状的红灯，再实现；
2. 每个测试声明它证明的是 Schema、编译、真实 Provider 还是业务质量；
3. 自动测试通过不等于真实 Run 通过；
4. 代理评估不等于 Golden Label；
5. 每个 required constraint 必须有独立断言；
6. 负例比“能跑完”更重要；
7. 同义表达、单位变化、顺序变化做 metamorphic test；
8. 固定随机种子、模型版本、Prompt hash 和数据 manifest；
9. 真实模型测试记录成本，默认不在普通单元测试中运行；
10. Release Gate 由多个证据层共同组成。

### 10.2 当前已运行基线

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

结果：

```text
240 passed
1 warning
```

警告为 Starlette/httpx 适配弃用提示。

这个结果只证明现有断言通过，不证明 `yifu` 需求完整。现有 requirement decomposition 测试只覆盖猫狗、真实性、清晰度、分类和分目录，没有覆盖多约束任务。

### 10.3 当前最小红灯

目标：

```text
给定 yifu 原始需求
期望 capability 至少包含：
image_dimensions
aspect_ratio
file_size
face_count
visual_semantic_selection
perceptual_deduplication
```

当前实际失败：

```text
actual:
image_decode
manifest
perceptual_deduplication
visual_semantic_selection

missing:
aspect_ratio
face_count
file_size
image_dimensions
```

该测试应首先正式加入回归集。

### 10.4 测试分层

#### L0 Schema/Domain

覆盖：

- ConstraintSpec 单位和边界；
- DAG 无环；
- source span；
- required/optional；
- evaluator type；
- revision immutable；
- unsupported constraint 不可确认。

#### L1 Requirement Golden Set

每个任务至少测试：

- 原始中文；
- 同义改写；
- 英文；
- 条件顺序打乱；
- 单位改写；
- 否定表达；
- 用户中途修改；
- 用户删除某项；
- 模糊语义需要澄清；
- 精确条件不重复提问。

核心断言不是整段文本相等，而是 constraint 集合和参数语义等价。

#### L2 Coverage/Compiler

注入固定 Catalog，断言：

- 每个 constraint 的 selected operator；
- 参数绑定；
- node；
- evidence contract；
- pipeline order/scope；
- 三策略硬阈值不变；
- 不可见条件不进入 Prompt；
- 缺算子进入 Resolution。

#### L3 Validator 反例

主动构造：

- 漏尺寸节点；
- 错误 aspect ratio；
- KB/MB 单位错误；
- 用 VLM 判断文件大小；
- face_count 节点没有 count evidence；
- dedup asset scope 错误；
- 旧 TaskSpec；
- Prompt hash 不匹配；
- Draft operator；
- 缺 required evidence。

每种错误必须有稳定 blocker code。

#### L4 Executor

覆盖：

- metadata stage 并行；
- dataset dedup barrier；
- VLM stage 并行；
- 保序；
- exact/near duplicate；
- 一张超时；
- 429；
- 5xx；
- pause/resume/cancel；
- Worker restart；
- checkpoint；
- cache hit/miss；
- Repair 只处理失败资产。

#### L5 QC/Delivery

覆盖：

- 每项 constraint pass；
- 单项 fail；
- 单项 missing；
- 0 kept；
- partial；
- duplicate group；
- abandoned/excluded；
- final export 文件数、SHA256、Manifest；
- 原始文件不变。

#### L6 Conversation/UI

覆盖：

- 500ms 内 `plan_created`；
- LLM 慢响应时仍有 `task_started`；
- tool started 先于 completed；
- 多任务状态计数；
- final 与 activity 分离；
- 断线重连；
- 重复事件去重；
- 用户中途补充需求；
- 用户改变方向；
- approval 恰好一次；
- 完成主动通知。

#### L7 真实 Provider/Golden Set

独立测试标记，记录：

- 模型和 Prompt 版本；
- 语义 precision/recall/F1；
- uncertain；
- P50/P95/P99；
- token/费用；
- 429/超时/重试；
- thinking on/off；
- concurrency 1/2/4/6/8；
- 连接池和缓存对比。

### 10.5 `yifu` Golden Task 验收

先冻结人工标注手册：

- “主体”的定义；
- 上衣、裤子、裙子、连体衣如何算；
- 黑白条纹是否算；
- 局部黑色比例；
- 多人物；
- 遮挡；
- 无人物；
- uncertain。

验收步骤：

1. 生成 source manifest；
2. 人工标注 face count 和 black-clothing label；
3. 标注 exact/near duplicate group；
4. 运行三条 Pipeline；
5. 逐项检查 C1～C7；
6. 对比分歧和边界；
7. 选择一条后全量运行；
8. 注入一张 VLM timeout；
9. Repair；
10. Export；
11. 核对文件、Manifest、evidence、hash 和原目录。

最低 Gate：

```text
Constraint parse recall = 100%
Required constraint coverage = 100%
Parameter binding accuracy = 100%
Required evidence completeness = 100%
Metadata rule accuracy = 100%
Exact duplicate recall = 100%
原始文件 hash 不变 = 100%
未解决 ambiguity = 0
Missing required evidence 的发布次数 = 0
```

黑衣语义质量门槛必须在 Golden Set 建立后冻结，不建议现在拍脑袋给百分比。

### 10.6 建议新增 Golden Tasks

1. 黑衣 + 尺寸 + 宽高比 + 文件大小 + 人脸数 + 去重；
2. 猫狗 + 真实性 + 清晰度 + 分类 + 分目录；
3. 单纯视觉筛选；
4. 纯 metadata Pipeline；
5. 纯 Data-Juicer CPU Pipeline；
6. GPU 能力缺口 + 外部发现 + 用户拒绝下载；
7. 用户授权下载 + 准入失败；
8. 单图超时 + Repair + Export；
9. 任务确认后修改阈值；
10. Pipeline 选择后修改需求；
11. 普通聊天穿插在每个状态；
12. 多人、多主体和语义 uncertain。

### 10.7 发布 Gate

任何 Beta 发布必须同时满足：

- 全量 unit/integration/e2e；
- Golden Task；
- 固定真实 Provider campaign；
- 安装矩阵；
- 恢复/断线；
- 权限；
- 性能；
- 成本；
- 人工结果抽检；
- 文档与实际行为一致。

禁止使用以下替代品：

- “pytest 全绿”替代真实 Provider；
- “Run SUCCEEDED”替代用户目标；
- “Retriever 找到了”替代 Pipeline 覆盖；
- “hard violation 0”替代逐约束验收；
- “有三个 Pipeline ID”替代三方案比较；
- “生成了 YAML 字符串”替代持久化 artifact。

---

## 11. 建议的完成度口径

以后每项能力使用五级状态：

| 状态 | 定义 |
|---|---|
| S0 设计 | 只有文档/接口设想 |
| S1 骨架 | 有对象、路由或占位实现 |
| S2 自动测试 | 有正确 seam 的自动测试 |
| S3 真实运行 | 固定真实数据与 Provider 已运行 |
| S4 产品验收 | 有 Golden Set、用户交互、性能、恢复与发布证据 |

示例：

| 能力 | 当前建议状态 |
|---|---|
| TaskSpec 版本化 | S3 |
| 多约束 TaskSpec 完整性 | S1 |
| Operator Catalog | S3 |
| Constraint Coverage | S0 |
| 三 Pipeline 编译 | S3 |
| 三 Pipeline 独立试跑比较 | S1 |
| Native VLM | S3 |
| 逐语义条件证据 | S1 |
| Dataset/QC | S3 |
| 逐约束 QC | S0 |
| Asset 并行基础 | S3 |
| Stage-aware 并行 | S0 |
| Repair/Export | S3 |
| 实时计划/Activity UX | S1 |
| 外部能力发现与准入 | S1 |
| PRD 训练反馈闭环 | S0/S1 |

交接文档应同时写：

```text
实现状态
自动测试证据
真实 Run 证据
Golden/产品验收证据
已知边界
```

---

## 12. 后续开发的禁止事项

1. 不再为尺寸、文件大小、人脸数继续补关键词表作为正式修复；
2. 不允许用户确认页展示一个对象、后台冻结另一个对象；
3. 不允许 required constraint 只保存在自由文本；
4. 不允许 Retriever 命中被表述为已覆盖；
5. 不允许 Pipeline Validator 只验结构就称 production eligible；
6. 不允许 VLM 猜文件大小、精确尺寸或数据集重复；
7. 不允许三策略修改用户硬阈值；
8. 不允许把语义条件压成单一标签且不保留逐项理由；
9. 不允许 QC 对未检查的约束给出“0 violation”印象；
10. 不允许一个 Dataset 节点永久关闭全部后续并行；
11. 不允许能力缺口时静默改需求或自动下载模型；
12. 不允许未经许可证、安全、Golden Set 和人工审核的新算子进入生产；
13. 不允许把固定 LangGraph 节点名称当成 Agent 智能性已经完成；
14. 不允许把所有交互继续堆进 ConversationService；
15. 不允许把完成后 action summary 称为实时进度；
16. 不允许用完整隐藏思维链替代结构化计划和证据；
17. 不允许以类、表、API 已存在作为产品完成证据；
18. 不允许以一次成功 Run 代替任务族验收。

---

## 13. 最终建议

DataAgent 当前最有价值的资产是治理基础，不是现有规划智能：

- 版本对象；
- 控制面；
- Operator/Provider；
- Worker；
- 审计；
- Repair/Export。

下一步应在这些基础上重建“需求正确性主链”，而不是推倒重来：

```text
用户语言
-> Constraint Contract
-> Clarification
-> Confirmed TaskSpec
-> Coverage Matrix
-> Three persisted PipelineArtifacts
-> Trial/Evaluation
-> Approved Run
-> Per-constraint Evidence
-> QC/Repair/Export
```

当这条链对 `yifu` Golden Task 达到 100% required coverage、100% evidence completeness，并且用户从计划、活动、批准到结果始终能看见当前状态时，DataAgent 才真正跨过“能运行的框架”到“可信的 Agent 产品”的门槛。
