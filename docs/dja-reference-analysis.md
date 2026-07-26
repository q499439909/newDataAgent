# data-juicer-agents(dja)架构调研与可借鉴点

> 调研对象:`d:/DataAgent/data-juicer-agents`(忽略 `data_platform/` 子目录)。
> 来源:三份 Explore 报告(recipe/文件表征、本体/类别与算子选择、agent 循环与 LLM 调用)的中文整合。所有结论带 file:line 证据。
> 对照项目:`d:/newDataAgent`(newDataAgent)。

## 0. 概述

dja = data-juicer-agents,基于 **AgentScope 1.0.14 + py-data-juicer + openai SDK**,用 **PyYAML**。两个核心子系统:
- `data_juicer_agents/`(`djx` CLI):新的 per-task planner,会写可读 YAML。
- `interactive_recipe/`:旧的 Streamlit "Interactive Recipe" 应用,算子池手编 + LLM 建议。
- 另有 `qa-copilot/`(独立 ReAct 微服务)、`skills/`。

`pyproject.toml` 依赖:`core = [agentscope==1.0.14, openai, prompt-toolkit, py-data-juicer>=1.5.1, PyYAML, rich]`;`platform = [fastapi, uvicorn, PyYAML]`。

---

## 一、本体 / 类别处理:dja 没有可硬编码的本体

**核心事实**:grep 全 repo(排除 `data_platform/`)**没有任何 cat/dog/authentic/synthetic/mixed/unknown 数据类字面量**——只有 Unix `cat` 命令(`tools/process/_shared/parser.py:208`、`diagnostics.py:43`、`execute_bash/*`)和 prose 里的 "unknown"。也就是说,dja "避免"硬编码本体的方式是**根本就没有分类层**。

### 分类如何发生:free-text intent → LLM → operator 开放 params
- 用户意图以 free-text `user_intent` 传入(`tools/plan/build_dataset_spec/input.py:12`)。
- `capabilities/plan/generator.py:43-54` 的 `ProcessOperatorGenerator._prompt` 把 intent 插值进 LLM 提示,指示模型返回 `{"operators":[{name, params}]}`,并"Fill concrete params ... based on the user request"。
- 类别集合会作为 operator 的 **开放 `params: Dict[str, Any]`** 流入(`tools/plan/build_process_spec/input.py:13`)。
- **值集不在 plan 期冻结**:`tools/plan/_shared/process_spec.py:11-13` 明确 `PROCESS_SPEC_DEFERRED_WARNING = "operator parameter validation deferred; runtime errors will be used as the repair signal"`;`:67-79` 只校验 param **key** 是否已知算子参数名,**不校验值枚举**。

### attributor 不是分类器
`interactive_recipe/attributor.py:13` `TextEmbdSimilarityAttributor`,`:29-41` 的逻辑是:对 dataset 和 reference `valid_dataset` 算 embedding,取**余弦相似度均值**作为 `text_embedding_similarity` 分数,写入 `ATTRIBUTION_KEY = "__attribution__"`(`:7`)。即"attribute = 质量评分",不是"assign class label"。

### prompt 是动态 f-string,不是字面量
- `interactive_recipe/prompts.py:1-6` `SYSTEM_PROMPT` 通用,无类别。
- `:83-132` `ATTRIBUTION_PROMPT` 是质量维度评分(accuracy/grammar/informativeness/coherence 1-5 分,`recommendation: keep/review/discard`),这里的 keep/review/discard 是**质量动作**,不是数据类别。
- `:134-152` `single_op_prompt(op_state, task_description, user_prompt)`、`:154-175` `multi_op_prompt` 都是 **f-string 从 `task_description`/`user_prompt` 插值**——类别会从任务描述插值进来,不烤进字面量。
- 唯一对 LLM 的值约束是 per-arg `options`/`[v_min, v_max]`(`prompts.py:73-74` "the new value lies in options"),且这些来自 YAML 配置,不是全局本体。

### 非标任务逃生口:intent-driven 算子 scaffold
`djx dev develop_operator`(`tools/dev/develop_operator/scaffold.py:101-109`)从 intent 生成自定义 mapper/filter 脚手架,留 `# TODO: replace placeholder logic with intent-specific transformation.`(`:161`)和 `# TODO: replace placeholder metric ...`(`:206`)。新算子的 `__init__` 参数(如 filter 的 `min_score`/`max_score`,`:195`)本身是开放 kwargs。

### 用户如何指定"区分车辆类型 {轿车, 越野, 卡车}"
无专门 `classes`/`labels`/`categories` 字段(plan schema `tools/plan/_shared/schema.py` 的 `DatasetSpec`/`ProcessSpec`/`PlanModel` 只有 `user_intent: str`、modality、keys、operator list)。路径是 `user_intent` free-text → LLM planner → 放进某分类算子的 open `params`。

> **注意**:repo 里"categories/labels"作为结构化概念出现的地方(`dialogue.py:43,55,73`、`session.py:335,715`、`taskspec_driven.py:234`)**全在 `data_platform/`(被忽略)**。这恰恰说明:newDataAgent 式的 task-spec 类别概念在 dja 里属于 data_platform 范畴,不在 dja core。

---

## 二、recipe / pipeline 文件表征:per-task YAML 两文件

### 两层 YAML
**Layer 1 — plan 文件(任务级,顶层)**:`.djx/{plan_id}.yaml`,由 `djx plan` 写。
- `data_juicer_agents/commands/plan_cmd.py:143-147`:`output_path = Path("plans") / f"{plan_id}.yaml"`;`yaml.safe_dump(plan.to_dict(), handle, allow_unicode=False, sort_keys=False)`。
- 实际产物在 `.djx/`(如 `.djx/plan_7773671344b1.yaml`、`.djx/aesthetic_score_plan.yaml`、`.djx/recipes/plan_*.yaml`),`.djx/` 是真实 per-run workspace 根。
- 内容:`plan_id`/`user_intent`/`modality`/`operator_names: List[str]`/`risk_notes`/`estimation`/`warnings`/`approval_required`/`created_at` + 嵌入 `recipe:` dict(含 `dataset_path`/`export_path`/`text_keys`/`image_key`/`process:` 有序算子列表/`np`/`executor_type` + ~60 DJ 系统字段)。
- `PlanModel` 是 `@dataclass`(`tools/plan/_shared/schema.py:410-500`),非 pydantic;`from_dict`/`to_dict` 对称(反)序列化,YAML 只是文本传输。

**Layer 2 — recipe 文件(执行级,派生)**:`.djx/recipes/{plan_id}.yaml`。
- `tools/apply/apply_recipe/logic.py:204-234`(`ApplyUseCase._write_recipe`):`recipe_path = runtime_dir / f"{plan_id}.yaml"`,`runtime_dir = ctx.resolve_artifacts_dir() / "recipes"`(`tool.py:104`)。
- 从 plan 提取 `recipe` dict,`process` 从 `[{name, params}]` 转 DJ-native `[{op_name: params}]`(`:224-230`),strip 平台 only key 如 `max_sample_num`(`:221`),`yaml.safe_dump(recipe, handle, allow_unicode=False, sort_keys=False)`(`:233`)。
- `generated_recipe_path` 记入 `ApplyResult`(`:97,342`)。

**Layer 3 — interactive_recipe 的临时工作态文件**(非 per-task):`interactive_recipe/save/op_pool_state.yaml`(算子池全状态 dump)、`./configs/demo*.yaml`(导出的 run 配置)。

### 算子列表两种 shape
- 内存/plan shape:`{name, params}`(`schema.py:393-395`,`ProcessOperator`)。
- DJ-YAML `process:` shape:`[{op_name: params}]`(`logic.py:226-230`)。
- apply 层在两者间转换。

### PyYAML 用法
- 序列化首选:`yaml.safe_dump(obj, handle, allow_unicode=False, sort_keys=False)`——5 处一致使用(`plan_cmd.py:147`、`plan_save/logic.py:48`、`apply_recipe/logic.py:233`、tests `test_apply_execution.py:38,191`)。`sort_keys=False` 保字段序;`allow_unicode=False` 转义非 ASCII(注意:`user_intent` 在真实 plan 文件里是 `\u...` 转义,`.djx/plan_7773671344b1.yaml:2`)。
- 加载:`yaml.safe_load(path.read_text(encoding="utf-8"))`(`apply_recipe/tool.py:46`、`session/runtime.py:222`、`apply_cmd.py:88`、`recipe_utils.py:81`、`operator_pool.py:245`)。
- `oyaml`(保序 YAML)在 `operator_pool.py:1` `import oyaml as yaml`。
- catalog 重生成:`get_op_info.py:191` `yaml.safe_dump(all_op_info, f, sort_keys=False)`——算子目录本身是 YAML,从 DJ `OPSearcher` 内省生成(`:74-92`)。
- **无 atomic write**:全是 `mkdir` + `open(path,"w")` + `yaml.safe_dump`,无 temp+rename。崩溃中途会留截断 YAML。
- 无自定义 YAML representer/tag,全 plain dict round-trip。

### 算子选择三机制
1. **interactive_recipe**:静态 catalog `configs/all_op_info.yaml` → `OperatorPool`(`operator_pool.py:242-251`);用户 enable/disable/edit args(Streamlit);顺序 = `OrderedDict` 插入序(无重排 API);LLM(`assistant.py:38-57`,`qwen-turbo`)返回 `{op_name, action, arg_name, value, reason}` JSON,`action` 只 `enable|disable|modify`,**不发明算子不重排**;params 校验/clamp(`operator_pool.py:78-105`)。
2. **djx = retrieval-then-LLM**(关键模式):
   - `PlanOrchestrator.generate_plan`(`capabilities/plan/service.py:73-117`):`retrieve_operator_candidates(intent, top_k=5, mode="auto")`(`:67-71`)缩小目录 → `generator.generate`(`:112`)选最终有序算子。
   - retrieval backend 链(`tools/retrieve/_shared/backend/retriever.py:1-15`):`LLMRetriever`(DashScope 语义排序)/`BM25Retriever`/`RegexRetriever`/`GrepRetriever`;`auto` = `llm → bm25 → grep` fallback;`auto/bm25/regex/grep` 本地(`logic.py:32`),`llm` API。
   - LLM ranking prompt(`retriever.py:40-82`):加权评分(functional 40% / scenario 30% / technical 20% / rating 10%),返回 top-k 带 `relevance_score`/`key_match`。
   - LLM selection prompt(`generator.py:43-54`):**约束在 retrieved 候选名内**——"Use canonical operator names from retrieved candidates. Fill concrete params whenever a threshold, mode, or explicit option is already known."输出 `{"operators":[{name, params}]}`(`:79-82`)。
3. **interactive_recipe 无 retrieval**:`StOperatorPool.filter_operators`(`st_operator_pool.py:382-391`)是纯 regex 过滤算子名;`RecipeManager`(`utils/recipe_utils.py:19-141`)从 hub 整体加载 recipe 应用。

### 算子目录与参数 spec
- `interactive_recipe/configs/all_op_info.yaml`:每条 `{op_name: {desc, args: {arg_name: {desc, type, default, min, max, options}}}}`。
- `OperatorPool`(`operator_pool.py:235-340`):`pool = OrderedDict()`,`act(op_name, action_type, ...)` for `enable/disable/set_arg`(`:292-334`),`export_config` 按序遍历 enabled ops(`:281-289`)。
- `OperatorArg`(`:58-69`):`name/desc/type/default/v/options/min/max` → 验证+clamp+自动 UI 渲染。
- djx catalog:`tools/retrieve/_shared/operator_registry.py` + `backend/catalog.py` + `backend/cache.py`。

---

## 三、agent 循环与 LLM 调用

### 两条 LLM 路径(互为反面)
- **对话路径 = AgentScope `ReActAgent`(框架原生 function-calling),非手写**:session 与 qa-copilot 都用。
- **`djx plan` planner gateway = JSON-as-text**(和 newDataAgent 一样弱)。

### ReAct agent 构建
`data_juicer_agents/capabilities/session/orchestrator.py:283-339`:
```python
from agentscope.agent import ReActAgent
agent = ReActAgent(name="DJSessionReActAgent", sys_prompt=..., model=model,
    formatter=formatter, toolkit=toolkit, max_iters=15, parallel_tool_calls=False)
```
一次 user→reply:`reply = await self._react_agent(Msg(name="user", role="user", content=prompt))`(`:443`)。循环完全由 AgentScope 提供。dja 只加:`post_reasoning` hook 发 reasoning-step 事件(`:341-357`)、interrupt 机制(`request_interrupt → agent.interrupt()`,`:160-181`)、monkeypatch `agent.print` 转发流块(`:329-337`)。

qa-copilot 同模式(`qa-copilot/app_deploy.py:291-303`,`max_iters=20`,`parallel_tool_calls=True`)。

### LLM 调用风格:原生 function-calling(对话)vs JSON-as-text(planner)
- **ReAct = 原生 `tools=`**:tool 注册进 AgentScope `Toolkit`,JSON schema 从 pydantic 派生(`capabilities/session/toolkit.py:46-60`,`build_agentscope_tool_function` + `build_agentscope_json_schema`);schema 来自 `spec.input_model.model_json_schema()`(`adapters/agentscope/tools.py:17-26`)→ `OpenAIChatModel`(`orchestrator.py:307-316`)→ `chat.completions.create(tools=)`。
- **planner gateway = JSON-as-text**:`utils/llm_gateway.py:53-72`,`client.chat.completions.create(model, messages=[{role:user, content}], temperature=0, extra_body={enable_thinking})`,**无 `tools=`、无 `response_format=`**;`_extract_json_text`(regex strip ```` ```json ```` fence,`:12-21`)+ `json.loads`(失败抛)。旧 `interactive_recipe/assistant.py:16-27,60-63` 更原始(`dashscope.Generation.call` + `re.sub` + `json.loads`)。

### 流式:ReAct 路径有,TUI 无 token 流
- `OpenAIChatModel(..., stream=self._enable_streaming)`(`orchestrator.py:310`);默认 CLI/TUI `False`,`--ui as_studio` `True`(`session_cli.py:166`)、qa-copilot `True`(`app_deploy.py:79`)。
- **流回调模式**:monkeypatch `agent.print` → `_forward_stream_chunk(msg, last)` → `_stream_callback`(`orchestrator.py:272-281,329-337`);`handle_as_studio_turn_async` 用 `_emit_studio_chunk(msg, last)`,`metadata["dj_stream"]=True`,`should_emit_final = not (stream_emitted and stream_last)`(`:619-660`)。
- qa-copilot 用 AgentScope `stream_printing_messages` pipeline,yield `(msg, last)` async generator(`app_deploy.py:325-358`)。
- `copilot_client.py` 消费 SSE:`response.iter_content`,split `\n`,strip `data:`,`json.loads`,yield text deltas(`:63-91`)。
- **TUI 不 token 流**:`tui/app.py:340-422` 30ms 轮询 `controller.drain_events()`,渲染 `tool_start`/`tool_end`/`reasoning_step` + spinner,turn 末才打全 reply。

### 重试 / 结构化输出(弱,prompt 委托)
- **planner gateway = model-fallback 而非 retry**:`call_model_json`(`llm_gateway.py:88-110`)遍历 `_candidate_models`(primary + `DJA_MODEL_FALLBACKS` env),任何异常试下一模型。**无同模型重试、无 re-ask、无 schema 校验**(`generator.py:77-82` 只 `isinstance(payload, dict)` 和 `isinstance(operators, list)`)。
- **ReAct 重试靠 system prompt 指令**(`orchestrator.py:233-235,255`:"If ... fails, inspect the returned errors and retry the failed stage with corrected inputs... retry at least once")——无代码级 retry,`max_iters=15` 唯一界。
- **tool arg 验证 = pydantic(结构化)**:`ToolSpec.execute` 调 `self.input_model.model_validate(raw_input)`(`core/tool/contracts.py:129-137`);`invoke_tool_spec` 捕 `ValidationError` 返回 `{ok:False, error_type:"invalid_arguments", validation_errors:[...]}`(`adapters/agentscope/tools.py:50-58`)。即 tool 调用**参数**有 schema 校验,但自由文本 LLM 输出没有。

### tool 注册 / discovery
- runtime-agnostic `ToolSpec` 契约(`core/tool/contracts.py:116-143`):`name/description/input_model: Type[BaseModel]/output_model/executor/tags/effects/confirmation`。
- discovery 扫 `tools/*/registry.py` 的 `TOOL_SPECS`(`core/tool/catalog.py:48-67,87-97`),`lru_cache` per group。
- `$ref`/`$defs` inlining(`adapters/agentscope/schema_utils.py:55-62`):展平 pydantic 的 `$ref` 让模型看到自包含 schema。
- qa-copilot 复用同 adapter 暴露 operator tools(`qa-copilot/operator_tools_adapter.py:97-107`),并 `toolkit.register_mcp_client(HttpStatelessClient(...))` 接 GitHub MCP(`agent_helper.py:432-448`)。

### copilot 现代版在 qa-copilot 微服务
`interactive_recipe/assistant.py` 是 **legacy**(plain `dashscope`,JSON-as-text,无 fc 无流)。现代 copilot 在 `qa-copilot/app_deploy.py`:ReActAgent + 原生 function-calling tools(`retrieve_operators_api`/`get_operator_info`/GitHub MCP)+ 流式。`interactive_recipe/copilot_client.py` 是消费 `$COPILOT_SERVICE_URL/process` SSE 的 HTTP client。

---

## 四、对 newDataAgent 的可借鉴点(映射,带证据)

| newDataAgent 方向 | dja 可借鉴(证据) | 落到 newDataAgent |
|---|---|---|
| **gateway 结构化(阶段A)** | pydantic→原生 function-calling schema adapter `build_agentscope_json_schema`(`adapters/agentscope/tools.py:17-26`,从 `input_model.model_json_schema()` 派生 `tools=`)+ `$ref` 展平(`adapters/agentscope/schema_utils.py:55-62`)+ stream callback `last` sentinel(`orchestrator.py:272-281,619-660`)+ ToolSpec runtime-agnostic 契约(`core/tool/contracts.py:116-143`) | newDataAgent 已有 `conversation_action_json_schema()` discriminated union,**正好包成单 tool + `tools=`**,解析 `tool_use` 而非贪心 `_extract_json`。一份 schema 同时用于验证和喂模型 |
| **per-task YAML(两落点)** | 两文件分离(plan metadata + recipe/纯配置)+ `{name, params}` shape + `sort_keys=False` + per-task workspace dir `.djx/{plan_id}`(`plan_cmd.py:143-147`、`apply_recipe/logic.py:204-234`) | 抄结构,但**加 atomic write**(newDataAgent `dataset_runner._publish:488-493` 已有 temp+replace 范式,比 dja 更安全)、用 `allow_unicode=True`(dja 用 False 使 CJK 不可读)、保持 lean(dja 嵌 ~60 DJ 系统字段别学) |
| **C3 解冻本体** | open `params` + 延迟验证(`process_spec.py:11-13,67-79`)+ dynamic f-string prompt(`prompts.py:134,154`)+ intent→LLM→params(`generator.py:43-82`)+ intent-driven custom-operator scaffold(`tools/dev/develop_operator/scaffold.py`) | 前文 C3(算子加 `classes` 参数 + VLM prompt 动态化 + 编译注入)与 dja 机制一致;**强化**:classes 值集**不冻结**(延迟验证 + runtime 错误当 ReAct 修复信号,而非 compile 期严校验);**引入 intent-driven 算子 scaffold 作为非标类别逃生口**(让"区分车辆类型"生成 task-specific 算子而非失败) |
| **C1 算子选择** | **retrieval-then-LLM**(`capabilities/plan/service.py:56-117` + `generator.py:43-82`):先 retrieve 缩小候选(`auto`=llm→bm25→grep fallback,无 key 时本地),LLM 只从候选选+排序+填参 | 替代 `catalog_matching` 纯关键词表;给确定性边界 + 无 key fallback |
| tool 渲染解耦 | `tool_start`/`tool_end` 事件(ok/error_type/result_preview,`runtime.py:83-133`) | 若要 UI/events 通道并行模型流可借鉴 |

---

## 五、不借鉴 / 反例

- **dja 的 `djx plan` planner gateway**(`utils/llm_gateway.py:53-110`):JSON-as-text + regex fence-strip `_extract_json_text` + model-fallback 当重试 + 无 `response_format`/`tools`——**正是 newDataAgent 要逃的模式,别抄**。dja 自己的 ReAct 路径已经做得更好(原生 function-calling)。
- **无 atomic write**:dja 全是 `mkdir`+`open w`+`yaml.safe_dump`,崩溃留截断 YAML——newDataAgent 已有 atomic 范式,别退化。
- **`allow_unicode=False`**:让 CJK 变 `\u...` 不可读——newDataAgent 用 `allow_unicode=True`。
- **plan 嵌 ~60 DJ 系统字段**:掩盖任务信号——newDataAgent 保持 per-task YAML lean(只 `process` + dataset IO + 几个旋钮,系统默认 apply 时注入,如 `_write_recipe` 对 `project_name`/`max_sample_num` 的处理 `logic.py:215,221`)。

---

## 六、caveat

dja 的本体回避**部分是因为它根本没有分类层**(`attributor` 是质量评分非分类)。newDataAgent 真需要"按类别列表分类"功能,**不能照抄"无本体"**,要抄**机制**(open params、动态 prompt、intent→LLM→params、yaml per-arg options、intent retrieval、intent scaffold)让类别成为 task-spec 数据流过这些机制——这正好是前文 C3 + C1 方向,且多了 dja 的 open-params + 延迟验证 + intent scaffold 三条补强。

---

## 附:关键 file:line 索引(dja)

**本体/类别**
- 延迟验证 + 修复信号:`tools/plan/_shared/process_spec.py:11-13,67-79`
- 开放 params:`tools/plan/build_process_spec/input.py:13`(`ProcessOperatorInput.params: Dict[str,Any]`)
- LLM planner:`capabilities/plan/generator.py:43-82`
- attributor(质量评分非分类):`interactive_recipe/attributor.py:7,13,29-41`
- 动态 prompt:`interactive_recipe/prompts.py:1-6,83-132,134-175`
- per-arg options/min/max:`interactive_recipe/operator_pool.py:58-69`、`configs/all_op_info.yaml`
- intent-driven 算子 scaffold:`tools/dev/develop_operator/scaffold.py:101-109,161,195,206`
- retrieval-by-intent:`tools/retrieve/_shared/logic.py:389-429`(本地 `_lexical_fallback` `:192-208`)

**recipe 文件**
- per-task plan 写:`data_juicer_agents/commands/plan_cmd.py:143-147`
- per-task plan save(tool):`tools/plan/plan_save/logic.py:46-48`
- per-task recipe 写(apply):`tools/apply/apply_recipe/logic.py:204-234`
- recipe load:`tools/apply/apply_recipe/tool.py:41-49`
- PlanModel schema:`tools/plan/_shared/schema.py:410-500`(`ProcessOperator`/`ProcessSpec` `:363-396`)
- LLM 算子生成器:`capabilities/plan/generator.py:43-82`
- retrieval 编排:`capabilities/plan/service.py:56-117`
- retrieval backends:`tools/retrieve/_shared/backend/retriever.py:1-82`
- OperatorPool:`interactive_recipe/operator_pool.py:235-340`(`export_config` `:271-290`)
- catalog 重生成:`interactive_recipe/get_op_info.py:65-197`
- 旧 LLM 建议:`interactive_recipe/assistant.py:38-63`、`prompts.py:52-81`
- 真实 plan 产物:`.djx/plan_7773671344b1.yaml`、`.djx/recipes/plan_*.yaml`

**agent 循环 / LLM**
- ReAct agent 构建:`capabilities/session/orchestrator.py:283-339`
- 一次 reply 调用:`orchestrator.py:443`
- 原生 function-calling toolkit:`capabilities/session/toolkit.py:46-60`
- pydantic→schema adapter:`adapters/agentscope/tools.py:17-26,77-96`
- `$ref` inlining:`adapters/agentscope/schema_utils.py:55-62`
- JSON-as-text planner gateway:`utils/llm_gateway.py:53-110`(fence-strip `:12-21`)
- 流回调:`orchestrator.py:272-281,329-337,619-660`
- 确定性 staged planner(非 ReAct):`capabilities/plan/service.py:73-158`、`generator.py:56-82`
- tool-arg 验证:`core/tool/contracts.py:129-143`、`adapters/agentscope/tools.py:50-58`
- tool discovery catalog:`core/tool/catalog.py:48-67`
- TUI 事件轮询(无 token 流):`tui/app.py:340-422`
- qa-copilot ReAct + 流:`qa-copilot/app_deploy.py:291-358`
- legacy JSON-as-text copilot:`interactive_recipe/assistant.py:16-27,60-63`
- SSE copilot client:`interactive_recipe/copilot_client.py:45-91`
