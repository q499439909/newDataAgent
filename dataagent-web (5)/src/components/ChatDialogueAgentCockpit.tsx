import React, { useState } from 'react';
import { useApp } from '../context/AppContext';
import {
  Activity,
  ArrowRight,
  Bot,
  CheckCircle2,
  FileCheck,
  Play,
  RefreshCw,
  Send,
  Sparkles,
} from 'lucide-react';

interface ChatDialogueAgentCockpitProps {
  onSelectPipelineComparison: () => void;
  onSelectBoundaryReview: () => void;
}

type ChatMessage = {
  sender: 'user' | 'agent';
  agentName?: string;
  text: string;
  time: string;
  actionType?: 'task_spec' | 'pipeline';
};

export const ChatDialogueAgentCockpit: React.FC<ChatDialogueAgentCockpitProps> = ({
  onSelectPipelineComparison,
  onSelectBoundaryReview,
}) => {
  const {
    activeWorkOrder,
    createNewWorkOrder,
    sendMainAgentMessage,
    approveCurrentTaskSpec,
    approveCurrentPipeline,
    submitCurrentDatasetRun,
    showToast,
  } = useApp();

  const [inputPrompt, setInputPrompt] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      sender: 'agent',
      agentName: '数据任务规划 Agent（主 Agent）',
      text: '请描述数据源路径、处理目标、约束和验收标准。我会先形成 TaskSpec；如有歧义会要求澄清，再调度检索 Agent 与数据处理 Agent 生成候选 Pipeline。',
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ]);

  if (!activeWorkOrder) return null;

  const spec = activeWorkOrder.currentTaskSpec;
  const hasAmbiguities = Boolean(spec?.ambiguities?.length);
  const waitingForPipeline = activeWorkOrder.waitingFor === 'pipeline_approval';
  const canSubmitRun = activeWorkOrder.agentTurn?.state?.next_action === 'submit_dataset_run';

  const appendAgentMessage = (text: string, actionType?: ChatMessage['actionType']) => {
    setMessages(prev => [
      ...prev,
      {
        sender: 'agent',
        agentName: '数据任务规划 Agent（主 Agent）',
        text,
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        actionType,
      },
    ]);
  };

  const handleSendMessage = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!inputPrompt.trim()) return;

    const userText = inputPrompt.trim();
    setInputPrompt('');
    setMessages(prev => [
      ...prev,
      {
        sender: 'user',
        text: userText,
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      },
    ]);
    setIsGenerating(true);

    try {
      const { reply, workOrder } = await sendMainAgentMessage(userText, (streamEvent) => {
        if (streamEvent.type === 'action' && streamEvent.action) {
          showToast(streamEvent.action.summary || streamEvent.action.stage_label || '主 Agent 正在处理当前请求。');
        }
      });
      appendAgentMessage(
        reply || '后端已完成本轮处理。',
        workOrder?.waitingFor === 'task_spec_confirmation'
          ? 'task_spec'
          : workOrder?.waitingFor === 'pipeline_approval'
            ? 'pipeline'
            : undefined,
      );
      showToast('主 Agent 本轮状态已同步。');
    } catch (error) {
      appendAgentMessage(
        `后端连接失败，本轮没有写入真实控制面。请确认 Python API 已启动。错误：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setIsGenerating(false);
    }
  };

  const handleQuickCreateNewTask = () => {
    createNewWorkOrder('新的数据任务对话', '通过主 Agent 对话创建的任务');
  };

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-sm flex flex-col h-[760px] overflow-hidden">
      <div className="p-4 bg-slate-50 border-b border-slate-200/80 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="w-9 h-9 rounded-xl bg-emerald-600 text-white flex items-center justify-center shadow-sm">
            <Bot className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <span className="font-bold text-slate-900 text-sm">主 Agent 工作台</span>
              <span className="px-2 py-0.5 text-[10px] bg-emerald-100 text-emerald-800 rounded-full font-semibold">
                LangGraph
              </span>
            </div>
            <p className="text-[11px] text-slate-500">自然语言需求 → TaskSpec → 检索/处理/策略 Agent → Pipeline 审批</p>
          </div>
        </div>
      </div>

      <div className="flex-1 p-6 overflow-y-auto space-y-4 bg-slate-50/40">
        <div className="bg-white p-4 rounded-xl border border-slate-200/80 shadow-sm space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-emerald-900 flex items-center space-x-1.5">
              <Activity className="w-4 h-4 text-emerald-600" />
              <span>当前任务: {activeWorkOrder.name}</span>
            </span>
            <span className="text-[10px] font-mono text-slate-400">ID: {activeWorkOrder.id}</span>
          </div>
          <p className="text-xs text-slate-600 leading-relaxed bg-slate-50 p-2.5 rounded-lg border border-slate-100">
            {activeWorkOrder.targetDescription}
          </p>

          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            {activeWorkOrder.mainAgentAction && (
              <span className="px-2 py-1 rounded-lg bg-emerald-50 text-emerald-800 border border-emerald-200 font-semibold">
                主 Agent 动作: {activeWorkOrder.mainAgentAction}
              </span>
            )}
            {activeWorkOrder.waitingFor && (
              <span className="px-2 py-1 rounded-lg bg-amber-50 text-amber-800 border border-amber-200 font-semibold">
                等待: {activeWorkOrder.waitingFor}
              </span>
            )}
          </div>

          {activeWorkOrder.taskPlan?.length ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
              {activeWorkOrder.taskPlan.slice(0, 6).map(item => (
                <div key={item.id} className="flex items-center justify-between rounded-lg bg-slate-50 border border-slate-100 px-2.5 py-1.5 text-[11px]">
                  <span className="truncate text-slate-600">{item.label}</span>
                  <span className={`ml-2 shrink-0 font-bold ${
                    item.status === 'completed' ? 'text-emerald-700' :
                    item.status === 'in_progress' || item.status === 'ready' ? 'text-amber-700' :
                    'text-slate-400'
                  }`}>
                    {item.status}
                  </span>
                </div>
              ))}
            </div>
          ) : null}

          {spec && (
            <div className="rounded-xl border border-emerald-100 bg-emerald-50/70 p-3 text-[11px] text-slate-700 space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-bold text-emerald-900 flex items-center gap-1.5">
                  <FileCheck className="w-3.5 h-3.5" />
                  TaskSpec {spec.version}
                </span>
                <span className={hasAmbiguities ? 'text-amber-700 font-bold' : 'text-emerald-700 font-bold'}>
                  {hasAmbiguities ? '待澄清' : spec.status === 'confirmed' ? '已确认' : '待确认'}
                </span>
              </div>
              <div>硬约束: {spec.hardConstraints.length ? spec.hardConstraints.join('; ') : '暂无'}</div>
              <div>验收指标: {spec.acceptanceCriteria.length ? spec.acceptanceCriteria.join('; ') : '暂无'}</div>
              {hasAmbiguities && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-2 text-amber-800 space-y-1">
                  <div className="font-bold">需要你补充确认：</div>
                  {spec.ambiguities.map((item, index) => (
                    <div key={index}>{index + 1}. {item}</div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {activeWorkOrder.waitingFor === 'task_spec_confirmation' && (
              <button
                type="button"
                onClick={approveCurrentTaskSpec}
                disabled={hasAmbiguities}
                className="flex items-center space-x-1 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg text-xs font-bold transition-all"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>确认 TaskSpec</span>
              </button>
            )}
            {waitingForPipeline && (activeWorkOrder.candidatePipelines || []).slice(0, 3).map(pipe => (
              <button
                key={pipe.id}
                type="button"
                onClick={() => approveCurrentPipeline(pipe.id)}
                className="flex items-center space-x-1 bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-1.5 rounded-lg text-xs font-bold transition-all"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>批准 {pipe.name}</span>
              </button>
            ))}
            {canSubmitRun && (
              <button
                type="button"
                onClick={submitCurrentDatasetRun}
                className="flex items-center space-x-1 bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-1.5 rounded-lg text-xs font-bold transition-all"
              >
                <Play className="w-3.5 h-3.5" />
                <span>提交全量数据运行</span>
              </button>
            )}
            <button
              type="button"
              onClick={onSelectPipelineComparison}
              className="flex items-center space-x-1 bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all"
            >
              <span>查看候选方案状态</span>
              <ArrowRight className="w-3 h-3" />
            </button>
            <button
              type="button"
              onClick={onSelectBoundaryReview}
              className="flex items-center space-x-1 bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all"
            >
              <span>边界样本复核</span>
            </button>
          </div>
        </div>

        {messages.map((msg, index) => (
          <div key={index} className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] rounded-2xl p-4 text-xs leading-relaxed space-y-3 ${
              msg.sender === 'user'
                ? 'bg-emerald-600 text-white rounded-br-none shadow-sm'
                : 'bg-white text-slate-800 border border-slate-200/80 rounded-bl-none shadow-sm'
            }`}>
              {msg.sender === 'agent' && (
                <div className="flex items-center justify-between text-[11px] text-emerald-700 font-semibold border-b border-slate-100 pb-2 mb-1">
                  <span className="flex items-center space-x-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-emerald-600" />
                    <span>{msg.agentName}</span>
                  </span>
                  <span className="text-[10px] text-slate-400 font-normal">{msg.time}</span>
                </div>
              )}
              <p className="whitespace-pre-wrap">{msg.text}</p>
              {msg.sender === 'user' && (
                <div className="text-[10px] text-emerald-100 text-right">{msg.time}</div>
              )}
            </div>
          </div>
        ))}

        {isGenerating && (
          <div className="flex justify-start">
            <div className="bg-white p-3.5 rounded-2xl border border-slate-200 text-xs text-slate-500 flex items-center space-x-2 shadow-sm">
              <RefreshCw className="w-4 h-4 text-emerald-600 animate-spin" />
              <span>主 Agent 正在分析本轮输入并同步后端状态...</span>
            </div>
          </div>
        )}
      </div>

      <div className="px-4 py-2 bg-slate-50 border-t border-slate-100 flex items-center space-x-2 overflow-x-auto text-[11px]">
        <span className="text-slate-400 font-medium shrink-0">快捷建议:</span>
        <button
          onClick={() => setInputPrompt('请根据当前 TaskSpec 中的待澄清项逐条提问，不要直接默认确认。')}
          className="bg-white hover:bg-emerald-50 text-slate-600 hover:text-emerald-800 border border-slate-200 rounded-lg px-2.5 py-1 shrink-0 transition-colors"
        >
          要求逐条澄清
        </button>
        <button
          onClick={() => setInputPrompt('请查看当前候选 Pipeline 是否已有真实试跑 metrics；没有的话不要展示估算值。')}
          className="bg-white hover:bg-emerald-50 text-slate-600 hover:text-emerald-800 border border-slate-200 rounded-lg px-2.5 py-1 shrink-0 transition-colors"
        >
          检查试跑指标
        </button>
      </div>

      <form onSubmit={handleSendMessage} className="p-3 bg-white border-t border-slate-200 flex items-center space-x-2">
        <input
          type="text"
          value={inputPrompt}
          onChange={(event) => setInputPrompt(event.target.value)}
          placeholder="输入需求、补充澄清答案，或要求主 Agent 继续规划..."
          className="flex-1 bg-slate-50 border border-slate-200 focus:border-emerald-500 rounded-xl px-4 py-2.5 text-xs text-slate-800 focus:outline-none"
        />
        <button
          type="submit"
          disabled={isGenerating || !inputPrompt.trim()}
          className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white px-4 py-2.5 rounded-xl text-xs font-bold transition-all shadow-sm flex items-center space-x-1"
        >
          <Send className="w-3.5 h-3.5" />
          <span>发送</span>
        </button>
        <button
          type="button"
          onClick={handleQuickCreateNewTask}
          className="bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 px-3 py-2.5 rounded-xl text-xs font-semibold"
        >
          新会话
        </button>
      </form>
    </div>
  );
};
