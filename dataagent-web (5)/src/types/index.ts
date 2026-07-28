export type UserRole = 
  | 'model_trainer'
  | 'data_scientist'
  | 'data_engineer'
  | 'evaluator'
  | 'admin'
  | 'compliance_auditor';

export interface User {
  id: string;
  name: string;
  email: string;
  avatar: string;
  role: UserRole;
  roleName: string;
  department: string;
}

export type Modality = 'vision' | 'text' | 'audio' | 'multimodal' | 'tabular';

export type OperatorLevel = 'L0' | 'L1' | 'L2' | 'L3' | 'L4';

export interface OperatorLevelInfo {
  code: OperatorLevel;
  title: string;
  description: string;
  badgeBg: string;
  badgeText: string;
}

export const OPERATOR_LEVEL_MAP: Record<OperatorLevel, OperatorLevelInfo> = {
  L0: { code: 'L0', title: '确定性规则/代码', description: '无模型依赖，纯算法与元数据处理，超高吞吐', badgeBg: 'bg-emerald-100 dark:bg-emerald-950/60', badgeText: 'text-emerald-800 dark:text-emerald-300' },
  L1: { code: 'L1', title: '传统CV/轻量模型', description: 'CPU高稳定性模型，低延迟处理物理特征', badgeBg: 'bg-teal-100 dark:bg-teal-950/60', badgeText: 'text-teal-800 dark:text-teal-300' },
  L2: { code: 'L2', title: '开源专用模型', description: '人脸/美学/分割/水印等专业领域SOTA模型', badgeBg: 'bg-cyan-100 dark:bg-cyan-950/60', badgeText: 'text-cyan-800 dark:text-cyan-300' },
  L3: { code: 'L3', title: '多模态大模型', description: 'MLLM开放语义理解与低置信度深度判别', badgeBg: 'bg-indigo-100 dark:bg-indigo-950/60', badgeText: 'text-indigo-800 dark:text-indigo-300' },
  L4: { code: 'L4', title: '人工审核/复核', description: '高风险与冲突边界样本由人工仲裁兜底', badgeBg: 'bg-amber-100 dark:bg-amber-950/60', badgeText: 'text-amber-800 dark:text-amber-300' },
};

export type OperatorReviewStatus = 'draft' | 'pending_review' | 'approved' | 'rejected';

export interface OperatorExample {
  id: string;
  title: string;
  type: 'success' | 'negative' | 'boundary_failure';
  typeLabel: string;
  originalImage: string;
  processedImage: string;
  originalMeta: { width: number; height: number; format: string; sizeKb: number };
  processedMeta: { width: number; height: number; format: string; sizeKb: number };
  annotations?: { label: string; bbox?: number[]; confidence?: number; score?: number }[];
  explanation: string;
  scoreChange?: string;
  passResult: boolean;
  filterReason?: string;
}

export interface SupportedTaskHistory {
  id: string;
  taskId: string;
  taskName: string;
  datasetSlice: string;
  totalProcessed: number;
  passRate: number;
  modelAccuracyGain: string;
  p95LatencyMs: number;
  adoptionStatus: 'adopted' | 'evaluated_only' | 'archived';
  date: string;
}

export interface OperatorSpec {
  id: string;
  name: string;
  category: 'cleaning' | 'enrichment' | 'filtering' | 'transformation' | 'evaluating';
  categoryLabel: string;
  modality: Modality;
  level: OperatorLevel;
  version: string;
  ownerId: string;
  ownerName: string;
  isPublic: boolean;
  reviewStatus: OperatorReviewStatus;
  reviewComment?: string;
  starsCount: number;
  usageCount: number;
  tags: string[];
  summary: string;
  suitability: string[];
  unsuitableConditions: string[];
  parameterGuide: { param: string; type: string; defaultVal: string; description: string }[];
  codeSnippets: {
    python?: string;
    typescript?: string;
    docker?: string;
    torch?: string;
  };
  examples: OperatorExample[];
  taskHistory: SupportedTaskHistory[];
  inputSchema: string;
  outputSchema: string;
  p95LatencyMs: number;
  throughputItemsPerSec: number;
  resourceReq: string;
  codeLicense: string;
  modelLicense?: string;
  packageInfo?: {
    uploadType: 'code_snippet' | 'single_file' | 'folder' | 'archive';
    fileName?: string;
    fileSizeKb?: number;
    fileCount?: number;
    fileList?: { path: string; sizeKb: number; type: string }[];
    entryPoint?: string;
  };
  updatedAt: string;
}

export type PipelineCandidateType = 'retention_first' | 'balanced' | 'quality_first';

export interface PipelineNode {
  id: string;
  operatorId: string;
  operatorName: string;
  level: OperatorLevel;
  params: Record<string, any>;
  inputNodeIds: string[];
  outputNodeIds: string[];
  failureStrategy: 'skip' | 'halt' | 'fallback';
  executionStatus?: 'idle' | 'running' | 'completed' | 'failed';
}

export interface PipelineVersion {
  id: string;
  name: string;
  version: string;
  candidateType: PipelineCandidateType;
  nodes: PipelineNode[];
  createdBy: string;
  createdAt: string;
  isPublic: boolean;
  reviewStatus: OperatorReviewStatus;
  totalRuns: number;
  averagePassRate: number | null;
  estimatedCostPer1k: number | null;
  taskFingerprint: string;
  summary: string;
  description: string;
  retentionRatePct: number | null;
  modelScoreEstimate: number | null;
  avgLatencyMs: number | null;
  metricsSource?: 'backend' | 'mock' | 'unavailable';
}

export interface NodePreviewItem {
  id: string;
  imageId: string;
  imageTitle: string;
  originalUrl: string;
  originalSizeKb: number;
  originalDims: string;
  nodeResults: {
    nodeId: string;
    nodeName: string;
    operatorLevel: OperatorLevel;
    outputUrl?: string;
    status: 'passed' | 'filtered' | 'transformed';
    score?: number;
    labels?: string[];
    reason?: string;
    bbox?: number[];
  }[];
}

export interface TaskSpec {
  id: string;
  name: string;
  version: string;
  createdBy: string;
  hardConstraints: string[];
  semanticConstraints: string[];
  outputRequirements: string[];
  acceptanceCriteria: string[];
  ambiguities: string[];
  status: 'draft' | 'confirmed' | 'revision_requested';
  targetMetrics: { metric: string; targetValue: string; baselineValue: string }[];
}

export interface RetrievalPlan {
  id: string;
  sources: string[];
  queries: string[];
  targetCount: number;
  estimatedRecallRate: number;
  estimatedCost: number;
}

export interface CandidatePool {
  id: string;
  totalAssets: number;
  duplicateCount: number;
  sliceCoverage: { sliceName: string; count: number; sufficiency: 'sufficient' | 'marginal' | 'gap' }[];
}

export interface QCReport {
  id: string;
  overallScore: number;
  passRate: number;
  hardRulePassRate: number;
  semanticAccuracy: number;
  duplicateRate: number;
  failedSampleCount: number;
  failureReasons: { code: string; label: string; count: number; percentage: number }[];
  recommendation: 'pass' | 'rework_retrieval' | 'rework_processing' | 'rework_sampling';
}

export interface ModelFeedback {
  id: string;
  modelName: string;
  overallMetric: number;
  metricName: string;
  baselineMetric: number;
  sliceMetrics: { sliceName: string; metric: number; delta: number }[];
  failedClusters: { clusterName: string; count: number; description: string }[];
  attributionCode: 'RETRIEVAL_GAP' | 'PROCESSING_FALSE_NEG' | 'PROCESSING_FALSE_POS' | 'LABEL_QUALITY_ISSUE' | 'DISTRIBUTION_GAP' | 'HARD_SAMPLE_GAP' | 'TARGET_MET';
  attributionLabel: string;
  recommendedAction: string;
}

export interface WorkOrder {
  id: string;
  name: string;
  targetDescription: string;
  currentStage: 'spec' | 'retrieval' | 'processing' | 'sampling' | 'evaluating' | 'completed' | 'paused';
  currentTaskSpec?: TaskSpec;
  retrievalPlan?: RetrievalPlan;
  candidatePool?: CandidatePool;
  selectedPipelineId?: string;
  candidatePipelines?: PipelineVersion[];
  nodePreviews?: NodePreviewItem[];
  qcReport?: QCReport;
  modelFeedback?: ModelFeedback;
  revisionChain: string[];
  owner: string;
  createdAt: string;
  isArchived?: boolean;
  backendThreadId?: string;
  conversationId?: string;
  agentTurn?: any;
  taskPlan?: { id: string; label: string; status: string }[];
  mainAgentAction?: string;
  waitingFor?: string | null;
}

export interface AuditLog {
  id: string;
  timestamp: string;
  actor: string;
  action: string;
  targetType: string;
  targetId: string;
  details: string;
}
