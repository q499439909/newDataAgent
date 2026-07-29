import { AgentTurnResponse, ConversationMessageResponse } from './dataagentClient';
import {
  OperatorSpec,
  PipelineVersion,
  TaskSpec,
  WorkOrder,
  OperatorLevel,
  PipelineCandidateType,
} from '../types';

function asArray(value: any): any[] {
  return Array.isArray(value) ? value : [];
}

function textList(value: any): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === 'string') return item;
      return item?.description || item?.source_text || JSON.stringify(item);
    });
  }
  if (value && typeof value === 'object') {
    return Object.entries(value).map(([key, item]) => `${key}: ${String(item)}`);
  }
  return [];
}

function stageFromTurn(turn: AgentTurnResponse): WorkOrder['currentStage'] {
  const state = turn.state || {};
  const interruptKind = turn.interrupts?.[0]?.value?.kind;
  if (state.terminated) return 'paused';
  if (state.next_action === 'submit_dataset_run') return 'sampling';
  if (interruptKind === 'pipeline_approval' || state.representative_pipelines?.length) return 'processing';
  if (state.retrieval_plan || state.current_agent === 'retrieval') return 'retrieval';
  if (state.sampling_plan) return 'sampling';
  return 'spec';
}

function pipelineType(strategy: string): PipelineCandidateType {
  if (strategy === 'retention_first' || strategy === 'recall_first') return 'retention_first';
  if (strategy === 'quality_first') return 'quality_first';
  return 'balanced';
}

function pipelineName(strategy: string): string {
  return {
    retention_first: '保留优先方案',
    recall_first: '保留优先方案',
    balanced: '均衡方案',
    quality_first: '质量优先方案',
  }[strategy] || strategy || '候选方案';
}

function numberMetric(metrics: Record<string, any>, keys: string[]): number | null {
  for (const key of keys) {
    const value = metrics[key];
    if (value !== undefined && value !== null && value !== '') {
      const numeric = Number(value);
      if (Number.isFinite(numeric)) return numeric;
    }
  }
  return null;
}

function percentMetric(metrics: Record<string, any>, keys: string[]): number | null {
  const value = numberMetric(metrics, keys);
  if (value === null) return null;
  return value <= 1 ? Math.round(value * 100) : value;
}

function levelFromBackend(node: Record<string, any>): OperatorLevel {
  const id = String(node.operator_version_id || node.id || '').toLowerCase();
  const backend = String(node.runtime_backend || '').toLowerCase();
  if (id.includes('human') || id.includes('review')) return 'L4';
  if (backend === 'remote' || id.includes('remote_api') || id.includes('vlm')) return 'L3';
  if (backend === 'cuda' || id.includes('local_cuda') || id.includes('local_model')) return 'L2';
  if (id.includes('datajuicer') || id.includes('face') || id.includes('aesthetic') || id.includes('watermark')) return 'L1';
  return 'L0';
}

export function mapBackendPipeline(payload: Record<string, any>): PipelineVersion {
  const strategy = String(payload.strategy || 'balanced');
  const metrics = payload.metrics || {};
  const retentionRatePct = percentMetric(metrics, ['retention_rate', 'retentionRate', 'retention_rate_pct']);
  const averagePassRate = percentMetric(metrics, ['rule_pass_rate', 'pass_rate', 'average_pass_rate']);
  const modelScoreEstimate = numberMetric(metrics, ['model_score', 'modelScore', 'confidence']);
  const latencySeconds = numberMetric(metrics, ['latency_seconds', 'p95_latency_seconds']);
  const avgLatencyMs = numberMetric(metrics, ['avg_latency_ms', 'p95_latency_ms'])
    ?? (latencySeconds === null ? null : Math.round(latencySeconds * 1000));
  const estimatedCostPer1k = numberMetric(metrics, ['estimated_cost', 'cost_per_1k', 'estimated_cost_per_1k']);
  const hasMetrics = Object.keys(metrics).length > 0;
  const nodes = asArray(payload.nodes).map((node, index) => ({
    id: String(node.id || `node-${index + 1}`),
    operatorId: String(node.operator_version_id || node.operatorId || node.id || `operator-${index + 1}`),
    operatorName: String(node.name || node.operator_version_id || `节点 ${index + 1}`),
    level: levelFromBackend(node),
    params: node.parameters || {},
    inputNodeIds: [],
    outputNodeIds: [],
    failureStrategy: node.required ? 'halt' as const : 'skip' as const,
    executionStatus: 'idle' as const,
  }));

  return {
    id: String(payload.id || crypto.randomUUID()),
    name: pipelineName(strategy),
    version: `v${payload.version || 1}`,
    candidateType: pipelineType(strategy),
    nodes,
    createdBy: String(payload.created_by || 'DataAgent'),
    createdAt: new Date().toISOString().slice(0, 10),
    isPublic: true,
    reviewStatus: payload.approved ? 'approved' : 'draft',
    totalRuns: 0,
    averagePassRate,
    estimatedCostPer1k,
    taskFingerprint: String(payload.task_spec_version_id || ''),
    summary: payload.approved ? '后端已批准的生产流水线' : '主 Agent 生成的候选流水线',
    description: `${pipelineName(strategy)}，包含 ${nodes.length} 个受控算子节点。`,
    retentionRatePct,
    modelScoreEstimate,
    avgLatencyMs,
    metricsSource: hasMetrics ? 'backend' : 'unavailable',
  };
}

export function mapAgentTurnToWorkOrder(
  turn: AgentTurnResponse,
  existing?: WorkOrder | null,
): WorkOrder {
  const state = turn.state || {};
  const specPayload = state.task_spec || {};
  const interrupt = turn.interrupts?.[0]?.value;
  const objective = String(specPayload.objective || state.requirement || existing?.name || '新的数据生产任务');
  const pipelines = asArray(state.representative_pipelines || state.pipeline_variants).map(mapBackendPipeline);
  const taskSpec: TaskSpec | undefined = specPayload.id ? {
    id: String(specPayload.id),
    name: objective,
    version: `v${specPayload.version || 1}`,
    createdBy: String(specPayload.created_by || state.owner_id || 'DataAgent'),
    hardConstraints: [
      ...textList(specPayload.constraints),
      ...textList(specPayload.hard_constraints),
    ],
    semanticConstraints: textList(specPayload.semantic_requirements),
    outputRequirements: textList(specPayload.output_actions),
    acceptanceCriteria: [
      `硬规则违规率 <= ${specPayload.acceptance?.hard_rule_violation_rate ?? 0}`,
      `边界样本复核量 ${specPayload.acceptance?.boundary_review_size ?? 20}`,
    ],
    ambiguities: textList(specPayload.ambiguities),
    status: specPayload.confirmed ? 'confirmed' : 'draft',
    targetMetrics: Object.entries(specPayload.acceptance?.model_metrics || {}).map(([metric, value]) => ({
      metric,
      baselineValue: '-',
      targetValue: String(value),
    })),
    clauseTraces: asArray(specPayload.clause_traces),
  } : existing?.currentTaskSpec;

  return {
    id: turn.work_order_id,
    name: objective.slice(0, 48),
    targetDescription: objective,
    currentStage: stageFromTurn(turn),
    owner: String(state.owner_id || existing?.owner || 'local-user'),
    createdAt: existing?.createdAt || new Date().toISOString().replace('T', ' ').slice(0, 16),
    revisionChain: [
      ...(existing?.revisionChain || []),
      ...(state.trace || []).slice(-3),
    ].filter(Boolean),
    currentTaskSpec: taskSpec,
    selectedPipelineId: state.selected_pipeline_id || existing?.selectedPipelineId,
    candidatePipelines: pipelines.length ? pipelines : existing?.candidatePipelines,
    nodePreviews: existing?.nodePreviews,
    retrievalPlan: state.retrieval_plan || existing?.retrievalPlan,
    candidatePool: existing?.candidatePool,
    qcReport: existing?.qcReport,
    modelFeedback: existing?.modelFeedback,
    backendThreadId: turn.thread_id,
    agentTurn: turn,
    taskPlan: state.task_plan || existing?.taskPlan,
    mainAgentAction: state.main_agent_action || existing?.mainAgentAction,
    waitingFor: interrupt?.kind || null,
    latestRunObservation: state.latest_run_observation || existing?.latestRunObservation || null,
    observedRunIds: asArray(state.observed_run_ids || existing?.observedRunIds).map(String),
    resolvedRunIds: asArray(state.resolved_run_ids || existing?.resolvedRunIds).map(String),
    activeRunId: state.active_run_id || existing?.activeRunId || null,
    agentObservations: asArray(state.agent_observations || existing?.agentObservations),
    requirementClarification: interrupt?.kind === 'requirement_clarification'
      ? {
          questions: asArray(interrupt.questions).map(String),
          summary: interrupt.summary ? String(interrupt.summary) : undefined,
          allowedActions: asArray(interrupt.allowed_actions).map(String),
        }
      : existing?.requirementClarification || null,
  };
}

export function mergeConversationResponseIntoWorkOrder(
  response: ConversationMessageResponse,
  existing?: WorkOrder | null,
): WorkOrder | null {
  if (!response.turn) return existing || null;
  const mapped = mapAgentTurnToWorkOrder(response.turn, existing);
  return {
    ...mapped,
    conversationId: response.conversation_id || existing?.conversationId,
  };
}

export function mapBackendOperator(payload: Record<string, any>): OperatorSpec {
  const tags = asArray(payload.capability_tags || payload.tags).map(String);
  const category = String(payload.category || 'filtering').toLowerCase();
  const normalizedTags = tags.map(tag => tag.toLowerCase());
  const profiles = asArray(payload.supported_runtime_profiles).map(profile => String(profile.backend || '').toLowerCase());
  const impl = String(payload.implementation?.implementation_type || payload.implementation_type || '').toLowerCase();
  const providerId = String(payload.provider?.provider_id || '').toLowerCase();
  const hasModel = Boolean(payload.model_requirement);
  const level: OperatorLevel = (() => {
    if (normalizedTags.some(tag => tag.includes('human') || tag.includes('review'))) return 'L4';
    if (
      profiles.includes('remote')
      || normalizedTags.includes('remote')
      || normalizedTags.includes('api')
      || normalizedTags.includes('multimodal')
      || normalizedTags.includes('commercial_model')
      || payload.id?.toLowerCase?.().includes('vlm')
    ) return 'L3';
    if (
      profiles.includes('cuda')
      || normalizedTags.includes('gpu')
      || normalizedTags.includes('local_model')
      || impl === 'model'
      || hasModel
    ) return 'L2';
    if (
      providerId === 'datajuicer'
      || impl === 'external_service'
      || normalizedTags.some(tag => ['face', 'aesthetic', 'watermark', 'classification', 'visual_understanding'].includes(tag))
    ) return 'L1';
    return 'L0';
  })();
  return {
    id: String(payload.id || payload.operator_version_id || crypto.randomUUID()),
    name: String(payload.name || payload.display_name || payload.id || '后端算子'),
    category: ['cleaning', 'enrichment', 'filtering', 'transformation', 'evaluating'].includes(category)
      ? category as OperatorSpec['category']
      : 'filtering',
    categoryLabel: String(payload.category || '过滤'),
    modality: tags.includes('text') ? 'text' : tags.includes('audio') ? 'audio' : 'vision',
    level,
    version: `v${payload.version || 1}`,
    ownerId: String(payload.created_by || 'system'),
    ownerName: String(payload.created_by || 'DataAgent'),
    isPublic: true,
    reviewStatus: String(payload.status || '').includes('DRAFT') ? 'draft' : 'approved',
    starsCount: 0,
    usageCount: 0,
    tags,
    summary: String(payload.description || payload.summary || '后端注册的受控数据处理算子。'),
    suitability: tags,
    unsuitableConditions: [],
    parameterGuide: Object.entries(payload.parameter_schema?.properties || {}).map(([param, schema]: [string, any]) => ({
      param,
      type: String(schema?.type || 'string'),
      defaultVal: String(schema?.default ?? '-'),
      description: String(schema?.description || ''),
    })),
    codeSnippets: {},
    examples: [],
    taskHistory: [],
    inputSchema: JSON.stringify(payload.input_schema || {}, null, 2),
    outputSchema: JSON.stringify(payload.output_schema || {}, null, 2),
    p95LatencyMs: Number(payload.runtime_profile?.p95_latency_ms || 0),
    throughputItemsPerSec: Number(payload.runtime_profile?.throughput_items_per_sec || 0),
    resourceReq: String(payload.runtime_profile?.resource_requirement || payload.runtime_backend || 'CPU'),
    codeLicense: String(payload.license || 'internal'),
    updatedAt: new Date().toISOString().slice(0, 10),
  };
}
