import { PipelineVersion, NodePreviewItem } from '../types';

export const INITIAL_PIPELINES: PipelineVersion[] = [
  {
    id: 'pipe-balanced-01',
    name: 'SOTA 均衡通用生产 Pipeline (推荐)',
    version: 'v2.4.0',
    candidateType: 'balanced',
    summary: '兼顾样本保留量、清洗精度与计算成本，作为 90% 模型训练的默认高品质方案。',
    description: '采用 pHash 快速去重 + 人脸高斯模糊脱敏 + 水印中门槛过滤 + CLIP美学分 5.0 过滤。',
    retentionRatePct: 78.5,
    modelScoreEstimate: 88.4,
    avgLatencyMs: 42,
    nodes: [
      {
        id: 'node-1',
        operatorId: 'op-phash-dedup-05',
        operatorName: '感知哈希与特征向量快速近重复去重',
        level: 'L0',
        params: { max_hamming_distance: 5 },
        inputNodeIds: [],
        outputNodeIds: ['node-2'],
        failureStrategy: 'skip'
      },
      {
        id: 'node-2',
        operatorId: 'op-face-anon-02',
        operatorName: '生物特征隐私人脸合规模糊与匿名化',
        level: 'L1',
        params: { blur_kernel_size: 31, mode: 'gaussian' },
        inputNodeIds: ['node-1'],
        outputNodeIds: ['node-3'],
        failureStrategy: 'skip'
      },
      {
        id: 'node-3',
        operatorId: 'op-watermark-01',
        operatorName: '多模态智能水印识别与切除算子',
        level: 'L2',
        params: { confidence_threshold: 0.45, action_mode: 'filter_out' },
        inputNodeIds: ['node-2'],
        outputNodeIds: ['node-4'],
        failureStrategy: 'skip'
      },
      {
        id: 'node-4',
        operatorId: 'op-aesthetic-03',
        operatorName: '视觉图像美学评分与光照质感质量算子',
        level: 'L2',
        params: { min_aesthetic_score: 5.0 },
        inputNodeIds: ['node-3'],
        outputNodeIds: [],
        failureStrategy: 'skip'
      }
    ],
    createdBy: '陈博士 (Data Scientist)',
    createdAt: '2026-07-15',
    isPublic: true,
    reviewStatus: 'approved',
    totalRuns: 1420,
    averagePassRate: 78.5,
    estimatedCostPer1k: 0.35,
    taskFingerprint: 'fg-multimodal-drive-2026'
  },
  {
    id: 'pipe-retention-02',
    name: '保留优先 (宽松覆盖) 候选方案',
    version: 'v1.1.0',
    candidateType: 'retention_first',
    summary: '降低误删风险，保留边缘、弱质与低置信度困难样本，防漏杀长尾场景。',
    description: '只做强重复去重与隐私人脸打码，水印阈值提至 0.8，美学分阈值降至 3.5。',
    retentionRatePct: 92.1,
    modelScoreEstimate: 83.2,
    avgLatencyMs: 25,
    nodes: [
      {
        id: 'node-r1',
        operatorId: 'op-phash-dedup-05',
        operatorName: '感知哈希与特征向量快速近重复去重',
        level: 'L0',
        params: { max_hamming_distance: 3 },
        inputNodeIds: [],
        outputNodeIds: ['node-r2'],
        failureStrategy: 'skip'
      },
      {
        id: 'node-r2',
        operatorId: 'op-face-anon-02',
        operatorName: '生物特征隐私人脸合规模糊与匿名化',
        level: 'L1',
        params: { blur_kernel_size: 21, mode: 'gaussian' },
        inputNodeIds: ['node-r1'],
        outputNodeIds: ['node-r3'],
        failureStrategy: 'skip'
      },
      {
        id: 'node-r3',
        operatorId: 'op-watermark-01',
        operatorName: '多模态智能水印识别与切除算子',
        level: 'L2',
        params: { confidence_threshold: 0.80, action_mode: 'filter_out' },
        inputNodeIds: ['node-r2'],
        outputNodeIds: [],
        failureStrategy: 'skip'
      }
    ],
    createdBy: 'Sarah Lin (Model Trainer)',
    createdAt: '2026-07-16',
    isPublic: true,
    reviewStatus: 'approved',
    totalRuns: 310,
    averagePassRate: 92.1,
    estimatedCostPer1k: 0.18,
    taskFingerprint: 'fg-multimodal-drive-2026'
  },
  {
    id: 'pipe-quality-03',
    name: '质量优先 (严格精筛 + MLLM 校验) 候选方案',
    version: 'v3.0.0',
    candidateType: 'quality_first',
    summary: '极高纯净度与严苛验证，结合 MLLM 开放语义校验，适合蒸馏与金标准测试集。',
    description: '加入 MLLM 开放语义二次判别 + 美学分 >= 6.5 + 严苛水印 0.3 拦截。',
    retentionRatePct: 62.4,
    modelScoreEstimate: 94.1,
    avgLatencyMs: 180,
    nodes: [
      {
        id: 'node-q1',
        operatorId: 'op-phash-dedup-05',
        operatorName: '感知哈希去重',
        level: 'L0',
        params: { max_hamming_distance: 6 },
        inputNodeIds: [],
        outputNodeIds: ['node-q2'],
        failureStrategy: 'skip'
      },
      {
        id: 'node-q2',
        operatorId: 'op-face-anon-02',
        operatorName: '隐私人脸打码',
        level: 'L1',
        params: { blur_kernel_size: 41 },
        inputNodeIds: ['node-q1'],
        outputNodeIds: ['node-q3'],
        failureStrategy: 'skip'
      },
      {
        id: 'node-q3',
        operatorId: 'op-watermark-01',
        operatorName: '水印检测 (严格0.3)',
        level: 'L2',
        params: { confidence_threshold: 0.30 },
        inputNodeIds: ['node-q2'],
        outputNodeIds: ['node-q4'],
        failureStrategy: 'skip'
      },
      {
        id: 'node-q4',
        operatorId: 'op-mllm-verifier-04',
        operatorName: 'Gemini MLLM 图文逻辑校验',
        level: 'L3',
        params: { reasoning_depth: 'standard' },
        inputNodeIds: ['node-q3'],
        outputNodeIds: [],
        failureStrategy: 'skip'
      }
    ],
    createdBy: '陈博士 (Data Scientist)',
    createdAt: '2026-07-17',
    isPublic: true,
    reviewStatus: 'approved',
    totalRuns: 420,
    averagePassRate: 62.4,
    estimatedCostPer1k: 1.85,
    taskFingerprint: 'fg-multimodal-drive-2026'
  }
];

export const MOCK_NODE_PREVIEWS: NodePreviewItem[] = [
  {
    id: 'prev-101',
    imageId: 'img-sample-01',
    imageTitle: '样本 #101 - 街景路口摄像头抓拍图像',
    originalUrl: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=600&q=80',
    originalSizeKb: 420,
    originalDims: '1920 x 1080',
    nodeResults: [
      {
        nodeId: 'node-1',
        nodeName: '感知哈希去重',
        operatorLevel: 'L0',
        status: 'passed',
        score: 0.05,
        reason: '与库中无近重复 (汉明距离=12 > 5)'
      },
      {
        nodeId: 'node-2',
        nodeName: '人脸高斯模糊',
        operatorLevel: 'L1',
        status: 'transformed',
        outputUrl: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=600&q=80',
        labels: ['路人人脸#1', '路人人脸#2'],
        bbox: [160, 100, 240, 180],
        reason: '成功在 [160,100] 应用 31x31 高斯掩模脱敏'
      },
      {
        nodeId: 'node-3',
        nodeName: '水印检测与过滤',
        operatorLevel: 'L2',
        status: 'filtered',
        score: 0.94,
        reason: '右上角拦截到 0.94 高置信台标水印 (0.94 > 0.45)'
      }
    ]
  },
  {
    id: 'prev-102',
    imageId: 'img-sample-02',
    imageTitle: '样本 #102 - 高清自然风光与建筑样本',
    originalUrl: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?auto=format&fit=crop&w=600&q=80',
    originalSizeKb: 1100,
    originalDims: '2400 x 1600',
    nodeResults: [
      {
        nodeId: 'node-1',
        nodeName: '感知哈希去重',
        operatorLevel: 'L0',
        status: 'passed',
        reason: '唯一哈希码'
      },
      {
        nodeId: 'node-2',
        nodeName: '人脸高斯模糊',
        operatorLevel: 'L1',
        status: 'passed',
        reason: '无路人人脸特征'
      },
      {
        nodeId: 'node-3',
        nodeName: '水印检测',
        operatorLevel: 'L2',
        status: 'passed',
        score: 0.02,
        reason: '未检测到水印'
      },
      {
        nodeId: 'node-4',
        nodeName: '美学评分',
        operatorLevel: 'L2',
        status: 'passed',
        score: 7.82,
        reason: '美学分 7.82 >= 5.0 (优良质量)'
      }
    ]
  }
];
