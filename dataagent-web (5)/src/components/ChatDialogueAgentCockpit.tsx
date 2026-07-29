import React, { useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import { ArtifactKind } from './ArtifactPanel';
import { WorkOrderChatMessage } from '../types';
import {
  Activity,
  AlertCircle,
  ArrowRight,
  Bot,
  CheckCircle2,
  FileText,
  GitBranch,
  ListChecks,
  Play,
  RefreshCw,
  Send,
  Sparkles,
} from 'lucide-react';

interface ChatDialogueAgentCockpitProps {
  onOpenArtifact: (kind: ArtifactKind) => void;
}

const nowTime = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const messageId = () => `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const createWelcomeMessage = (): WorkOrderChatMessage => ({
  id: messageId(),
  sender: 'agent',
  agentName: '数据任务规划 Agent（主 Agent）',
  text: '请描述数据源路径、处理目标、约束和验收标准。我会先形成 TaskSpec；如有语义缺口，会在这里继续向你澄清。',
  time: nowTime(),
});

export const ChatDialogueAgentCockpit: React.FC<ChatDialogueAgentCockpitProps> = ({ onOpenArtifact }) => {
  const {
    activeWorkOrder,
    activeWorkOrderChatMessages,
    setActiveWorkOrderChatMessages,
    setWorkOrderChatMessages,
    createNewWorkOrder,
    sendMainAgentMessage,
    approveCurrentTaskSpec,
    approveCurrentPipeline,
    submitCurrentDatasetRun,
    showToast,
  } = useApp();

  const [inputPrompt, setInputPrompt] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const messages = activeWorkOrderChatMessages;

  useEffect(() => {
    if (activeWorkOrder && activeWorkOrderChatMessages.length === 0) {
      setActiveWorkOrderChatMessages([createWelcomeMessage()]);
    }
  }, [activeWorkOrder?.id, activeWorkOrderChatMessages.length]);

  if (!activeWorkOrder) return null;

  const spec = activeWorkOrder.currentTaskSpec;
  const hasAmbiguities = Boolean(spec?.ambiguities?.length);
  const requirementQuestions = activeWorkOrder.requirementClarification?.questions || [];
  const waitingForPipeline = activeWorkOrder.waitingFor === 'pipeline_approval';
  const canSubmitRun = activeWorkOrder.agentTurn?.state?.next_action === 'submit_dataset_run';
  const latestRun = activeWorkOrder.latestRunObservation || activeWorkOrder.agentTurn?.state?.latest_run_observation;
  const hasPipelineArtifact = Boolean(activeWorkOrder.candidatePipelines?.length || waitingForPipeline);
  const hasQualityArtifact = Boolean(activeWorkOrder.nodePreviews?.length || activeWorkOrder.qcReport || latestRun?.qc_report_id || latestRun?.qc_status);
  const hasRunArtifact = Boolean(latestRun || activeWorkOrder.activeRunId || activeWorkOrder.agentTurn?.state?.active_run_id || activeWorkOrder.agentTurn?.state?.next_action?.includes?.('run'));
  const hasFileArtifact = Boolean(latestRun?.dataset_version_id || latestRun?.failed_asset_uris?.length || latestRun?.repair_candidate_uris?.length || latestRun?.evidence_refs?.length);

  const setMessages = (next: WorkOrderChatMessage[], workOrderId = activeWorkOrder.id) => {
    setWorkOrderChatMessages(workOrderId, next);
  };

  const appendAgentMessage = (text: string, baseMessages = messages, actionType?: ArtifactKind) => {
    setMessages([
      ...baseMessages,
      {
        id: messageId(),
        sender: 'agent',
        agentName: '数据任务规划 Agent（主 Agent）',
        text,
        time: nowTime(),
        actionType,
      },
    ]);
  };

  const handleSendMessage = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!inputPrompt.trim()) return;

    const userText = inputPrompt.trim();
    const targetWorkOrder = activeWorkOrder.conversationId
      ? activeWorkOrder
      : await createNewWorkOrder('新的数据任务对话', '通过主 Agent 对话创建的任务');
    const initialWorkOrderId = targetWorkOrder.id;
    setInputPrompt('');

    const messagesWithUser: WorkOrderChatMessage[] = [
      ...(targetWorkOrder.id === activeWorkOrder.id ? messages : []),
      { id: messageId(), sender: 'user', text: userText, time: nowTime() },
    ];
    setMessages(messagesWithUser, initialWorkOrderId);
    setIsGenerating(true);

    try {
      const { reply, workOrder } = await sendMainAgentMessage(userText, (streamEvent) => {
        if (streamEvent.type === 'action' && streamEvent.action) {
          showToast(streamEvent.action.summary || streamEvent.action.stage_label || '主 Agent 正在处理当前请求。');
        }
      }, targetWorkOrder);

      const nextActionType: ArtifactKind | undefined = workOrder?.waitingFor === 'task_spec_confirmation'
        ? 'task_spec'
        : workOrder?.waitingFor === 'pipeline_approval'
          ? 'pipeline'
          : workOrder?.waitingFor === 'run_outcome_resolution'
            ? 'run'
            : undefined;
      const targetWorkOrderId = workOrder?.id || initialWorkOrderId;
      const messagesWithReply: WorkOrderChatMessage[] = [
        ...messagesWithUser,
        {
          id: messageId(),
          sender: 'agent',
          agentName: '数据任务规划 Agent（主 Agent）',
          text: reply || '后端已完成本轮处理。',
          time: nowTime(),
          actionType: nextActionType,
        },
      ];
      setMessages(messagesWithReply, initialWorkOrderId);
      setMessages(messagesWithReply, targetWorkOrderId);
      showToast('主 Agent 本轮状态已同步。');
    } catch (error) {
      setMessages([
        ...messagesWithUser,
        {
          id: messageId(),
          sender: 'agent',
          agentName: '数据任务规划 Agent（主 Agent）',
          text: `后端连接失败，本轮没有写入真实控制面。请确认 Python API 已启动。错误：${error instanceof Error ? error.message : String(error)}`,
          time: nowTime(),
        },
      ], initialWorkOrderId);
    } finally {
      setIsGenerating(false);
    }
  };

  const handleQuickCreateNewTask = async () => {
    await createNewWorkOrder('新的数据任务对话', '通过主 Agent 对话创建的任务');
  };

  const sendClarificationSeed = () => {
    setInputPrompt(requirementQuestions.length
      ? requirementQuestions.map((question, index) => `${index + 1}. ${question}\n答：`).join('\n')
      : '请逐条列出当前需求中仍需我澄清的问题。');
  };

  return (
    <div className="flex h-full min-h-[760px] flex-col overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-200/80 bg-slate-50 p-4">
        <div className="flex min-w-0 items-center space-x-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-emerald-600 text-white shadow-sm">
            <Bot className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center space-x-2">
              <span className="truncate text-sm font-bold text-slate-900">主 Agent 工作台</span>
              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-800">
                LangGraph
              </span>
            </div>
            <p className="truncate text-[11px] text-slate-500">自然语言需求 → TaskSpec → 检索/处理/策略 Agent → Pipeline 审批 → Run/QC 反馈</p>
          </div>
        </div>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto bg-slate-50/40 p-4 sm:p-6">
        <div className="space-y-3 rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="flex min-w-0 items-center space-x-1.5 text-xs font-bold text-emerald-900">
              <Activity className="h-4 w-4 shrink-0 text-emerald-600" />
              <span className="truncate">当前任务: {activeWorkOrder.name}</span>
            </span>
            <span className="shrink-0 font-mono text-[10px] text-slate-400">ID: {activeWorkOrder.id}</span>
          </div>

          <p className="rounded-lg border border-slate-100 bg-slate-50 p-2.5 text-xs leading-relaxed text-slate-600">
            {activeWorkOrder.targetDescription}
          </p>

          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            {activeWorkOrder.mainAgentAction && (
              <span className="rounded-lg border border-emerald-200 bg-emerald-50 px-2 py-1 font-semibold text-emerald-800">
                主 Agent 动作: {activeWorkOrder.mainAgentAction}
              </span>
            )}
            {activeWorkOrder.waitingFor && (
              <span className="rounded-lg border border-amber-200 bg-amber-50 px-2 py-1 font-semibold text-amber-800">
                等待: {activeWorkOrder.waitingFor}
              </span>
            )}
            {activeWorkOrder.activeRunId && (
              <span className="rounded-lg border border-sky-200 bg-sky-50 px-2 py-1 font-semibold text-sky-800">
                Active Run: {activeWorkOrder.activeRunId}
              </span>
            )}
          </div>

          {activeWorkOrder.taskPlan?.length ? (
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
              {activeWorkOrder.taskPlan.slice(0, 8).map(item => (
                <div key={item.id} className="flex items-center justify-between rounded-lg border border-slate-100 bg-slate-50 px-2.5 py-1.5 text-[11px]">
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

          {requirementQuestions.length ? (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
              <div className="mb-2 flex items-center gap-1.5 font-bold">
                <AlertCircle className="h-4 w-4" />
                主 Agent 需要你澄清需求
              </div>
              <div className="space-y-1">
                {requirementQuestions.map((question, index) => <div key={index}>{index + 1}. {question}</div>)}
              </div>
            </div>
          ) : null}

          {spec && (
            <div className="space-y-2 rounded-xl border border-emerald-100 bg-emerald-50/70 p-3 text-[11px] text-slate-700">
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 font-bold text-emerald-900">
                  <FileText className="h-3.5 w-3.5" />
                  TaskSpec {spec.version}
                </span>
                <span className={hasAmbiguities ? 'font-bold text-amber-700' : 'font-bold text-emerald-700'}>
                  {hasAmbiguities ? '待澄清' : spec.status === 'confirmed' ? '已确认' : '待确认'}
                </span>
              </div>
              <div>硬约束: {spec.hardConstraints.length ? spec.hardConstraints.join('; ') : '后端未返回'}</div>
              <div>验收指标: {spec.acceptanceCriteria.length ? spec.acceptanceCriteria.join('; ') : '后端未返回'}</div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {activeWorkOrder.waitingFor === 'task_spec_confirmation' && (
              <button
                type="button"
                onClick={approveCurrentTaskSpec}
                disabled={hasAmbiguities}
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white transition-all hover:bg-emerald-700 disabled:opacity-50"
              >
                <CheckCircle2 className="h-3.5 w-3.5" />
                确认 TaskSpec
              </button>
            )}

            {waitingForPipeline && (activeWorkOrder.candidatePipelines || []).slice(0, 3).map(pipe => (
              <button
                key={pipe.id}
                type="button"
                onClick={() => approveCurrentPipeline(pipe.id)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white transition-all hover:bg-emerald-700"
              >
                <CheckCircle2 className="h-3.5 w-3.5" />
                批准 {pipe.name}
              </button>
            ))}

            {canSubmitRun && (
              <button
                type="button"
                onClick={submitCurrentDatasetRun}
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white transition-all hover:bg-emerald-700"
              >
                <Play className="h-3.5 w-3.5" />
                提交全量执行
              </button>
            )}

            {requirementQuestions.length ? (
              <button
                type="button"
                onClick={sendClarificationSeed}
                className="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 transition-all hover:bg-amber-50"
              >
                回答澄清问题
              </button>
            ) : null}

            {spec && (
              <button
                type="button"
                onClick={() => onOpenArtifact('task_spec')}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition-all hover:bg-slate-50"
              >
                <FileText className="h-3.5 w-3.5" />
                查看 TaskSpec
              </button>
            )}

            {hasPipelineArtifact && (
              <button
                type="button"
                onClick={() => onOpenArtifact('pipeline')}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition-all hover:bg-slate-50"
              >
                <GitBranch className="h-3.5 w-3.5" />
                查看 Pipeline
                <ArrowRight className="h-3 w-3" />
              </button>
            )}

            {hasQualityArtifact && (
              <button
                type="button"
                onClick={() => onOpenArtifact('quality')}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition-all hover:bg-slate-50"
              >
                <ListChecks className="h-3.5 w-3.5" />
                查看边界/QC
              </button>
            )}

            {hasRunArtifact && (
              <button
                type="button"
                onClick={() => onOpenArtifact('run')}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition-all hover:bg-slate-50"
              >
                <Play className="h-3.5 w-3.5" />
                查看运行产物
              </button>
            )}

            {hasFileArtifact && (
              <button
                type="button"
                onClick={() => onOpenArtifact('files')}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition-all hover:bg-slate-50"
              >
                查看文件
              </button>
            )}
          </div>
        </div>

        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] space-y-3 rounded-2xl p-4 text-xs leading-relaxed ${
              msg.sender === 'user'
                ? 'rounded-br-none bg-emerald-600 text-white shadow-sm'
                : 'rounded-bl-none border border-slate-200/80 bg-white text-slate-800 shadow-sm'
            }`}>
              {msg.sender === 'agent' && (
                <div className="mb-1 flex items-center justify-between border-b border-slate-100 pb-2 text-[11px] font-semibold text-emerald-700">
                  <span className="flex items-center space-x-1.5">
                    <Sparkles className="h-3.5 w-3.5 text-emerald-600" />
                    <span>{msg.agentName}</span>
                  </span>
                  <span className="text-[10px] font-normal text-slate-400">{msg.time}</span>
                </div>
              )}
              <p className="whitespace-pre-wrap">{msg.text}</p>
              {msg.actionType && (
                <button
                  type="button"
                  onClick={() => onOpenArtifact(msg.actionType as ArtifactKind)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-[11px] font-bold text-emerald-800 hover:bg-emerald-100"
                >
                  查看 {msg.actionType === 'task_spec' ? 'TaskSpec' : msg.actionType === 'pipeline' ? 'Pipeline' : msg.actionType === 'run' ? '运行产物' : '产物'}
                  <ArrowRight className="h-3 w-3" />
                </button>
              )}
              {msg.sender === 'user' && <div className="text-right text-[10px] text-emerald-100">{msg.time}</div>}
            </div>
          </div>
        ))}

        {isGenerating && (
          <div className="flex justify-start">
            <div className="flex items-center space-x-2 rounded-2xl border border-slate-200 bg-white p-3.5 text-xs text-slate-500 shadow-sm">
              <RefreshCw className="h-4 w-4 animate-spin text-emerald-600" />
              <span>主 Agent 正在分析本轮输入并同步后端状态...</span>
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center space-x-2 overflow-x-auto border-t border-slate-100 bg-slate-50 px-4 py-2 text-[11px]">
        <span className="shrink-0 font-medium text-slate-400">快捷建议:</span>
        <button
          onClick={() => setInputPrompt('请逐条澄清当前 TaskSpec 中的待确认项，不要直接默认确认。')}
          className="shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-slate-600 transition-colors hover:bg-emerald-50 hover:text-emerald-800"
        >
          要求逐条澄清
        </button>
        <button
          onClick={() => setInputPrompt('请查看当前候选 Pipeline 是否已有后端真实试跑 metrics；没有的话不要展示估算值。')}
          className="shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-slate-600 transition-colors hover:bg-emerald-50 hover:text-emerald-800"
        >
          检查试跑指标
        </button>
      </div>

      <form onSubmit={handleSendMessage} className="flex items-center space-x-2 border-t border-slate-200 bg-white p-3">
        <input
          type="text"
          value={inputPrompt}
          onChange={(event) => setInputPrompt(event.target.value)}
          placeholder="输入需求、补充澄清答案，或要求主 Agent 继续规划..."
          className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-xs text-slate-800 focus:border-emerald-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={isGenerating || !inputPrompt.trim()}
          className="inline-flex items-center space-x-1 rounded-xl bg-emerald-600 px-4 py-2.5 text-xs font-bold text-white shadow-sm transition-all hover:bg-emerald-700 disabled:opacity-50"
        >
          <Send className="h-3.5 w-3.5" />
          <span>发送</span>
        </button>
        <button
          type="button"
          onClick={handleQuickCreateNewTask}
          className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
        >
          新会话
        </button>
      </form>
    </div>
  );
};
