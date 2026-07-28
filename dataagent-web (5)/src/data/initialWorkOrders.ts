import { WorkOrder } from '../types';
import { INITIAL_PIPELINES, MOCK_NODE_PREVIEWS } from './initialPipelines';

export const INITIAL_WORK_ORDERS: WorkOrder[] = [
  {
    id: 'wo-2026-drive-01',
    name: '自动驾驶复杂气候场景斑马线与行人高召回数据集生产',
    targetDescription: '生产 50,000 张雨雪雾恶劣天气下的路口斑马线与夜间行人数据集。要求绝对无台标水印，人脸全部高斯模糊脱敏，训练 mAP 提升 >= +3.0%，难例 Recall >= 85%。',
    currentStage: 'evaluating',
    owner: 'Sarah Lin (Model Trainer)',
    createdAt: '2026-07-26 14:20',
    revisionChain: ['rev-01-spec', 'rev-02-retrieval', 'rev-03-pipeline', 'rev-04-feedback-rework'],
    currentTaskSpec: {
      id: 'ts-drive-01',
      name: '自动驾驶极端气候人行横道/行人数据集',
      version: 'v1.2',
      createdBy: 'Sarah Lin (Model Trainer)',
      hardConstraints: [
        '必须符合 GDPR 人脸打码要求 (高斯模糊核 >= 21)',
        '100% 清除所有网约车、平台及摄像头强水印',
        '严禁同一场景的连拍冗余图 (汉明距离 > 5)',
        '训练/测试集绝对物理隔离，近重复泄露率 < 0.1%'
      ],
      semanticConstraints: [
        '优先包含夜间、强降雨、水雾漫反射等低对比度极端天气场景',
        '覆盖儿童、拉推车行人等长尾低频体态样本'
      ],
      outputRequirements: [
        '目标规模：30,000 张已洗全量训练样本 + 5,000 张验证集',
        '输出格式：COCO JSON + Yolo Darknet 标注格式',
        '附带每张图片的算子清洗判定血缘链 Manifest'
      ],
      acceptanceCriteria: [
        '模型在夜间雨雾测试集上的 mAP50 >= 82.5% (+3.0% vs 基线)',
        '硬规则逻辑通过率 >= 98%',
        '人工抽检准确率 >= 99%'
      ],
      ambiguities: [
        '需确认：遮阳伞遮挡的行人是否归入普通行人或不确定分类（用户已选择“标记为普通行人”）。'
      ],
      status: 'confirmed',
      targetMetrics: [
        { metric: '雨雾夜间行人 mAP50', baselineValue: '79.2%', targetValue: '82.5% (+3.3%)' },
        { metric: '硬规则数据通过率', baselineValue: '85.0%', targetValue: '>= 98.0%' },
        { metric: '标注污染与假阳率', baselineValue: '12.4%', targetValue: '<= 1.5%' }
      ]
    },
    retrievalPlan: {
      id: 'rp-drive-01',
      sources: ['Milvus-DriveCam-Collection-01', 'OSS-Raw-Traffic-Bucket-North', 'LabelCenter-NightRain-2025'],
      queries: [
        'Text-Vector: "heavy rain night crosswalk pedestrians with umbrellas"',
        'Tag-Filter: weather=rain AND time=night AND object=pedestrian',
        'Image-Vector: Nearest neighbors of failure case #0182 (dim headlight reflection)'
      ],
      targetCount: 65000,
      estimatedRecallRate: 94.2,
      estimatedCost: 120.0
    },
    candidatePool: {
      id: 'cp-drive-01',
      totalAssets: 62400,
      duplicateCount: 8400,
      sliceCoverage: [
        { sliceName: '夜间暴雨斑马线', count: 18200, sufficiency: 'sufficient' },
        { sliceName: '大雾视线受阻', count: 14100, sufficiency: 'sufficient' },
        { sliceName: '强反射积水路面', count: 12500, sufficiency: 'sufficient' },
        { sliceName: '极罕见儿童推车难例', count: 2100, sufficiency: 'marginal' }
      ]
    },
    selectedPipelineId: 'pipe-balanced-01',
    candidatePipelines: INITIAL_PIPELINES,
    nodePreviews: MOCK_NODE_PREVIEWS,
    qcReport: {
      id: 'qc-drive-01',
      overallScore: 92.4,
      passRate: 88.2,
      hardRulePassRate: 99.1,
      semanticAccuracy: 96.5,
      duplicateRate: 0.05,
      failedSampleCount: 1420,
      failureReasons: [
        { code: 'WATERMARK_DETECTED', label: '强水印/台标拦截', count: 980, percentage: 69.0 },
        { code: 'BLUR_EXCESSIVE', label: '严重镜头水渍模糊', count: 320, percentage: 22.5 },
        { code: 'DUPLICATE_PHASH', label: '连拍高相似去重', count: 120, percentage: 8.5 }
      ],
      recommendation: 'pass'
    },
    modelFeedback: {
      id: 'mf-drive-01',
      modelName: 'YOLOv9-Perception-DriveNet-v4',
      overallMetric: 81.1,
      metricName: '雨雾夜间行人 mAP50',
      baselineMetric: 79.2,
      sliceMetrics: [
        { sliceName: '普通夜间路口', metric: 88.4, delta: +4.2 },
        { sliceName: '雨天车灯反光', metric: 82.1, delta: +2.9 },
        { sliceName: '大雾儿童推车难例', metric: 68.2, delta: +0.4 } // RETRIEVAL_GAP!
      ],
      failedClusters: [
        { clusterName: '极其罕见儿童推车遮挡切片', count: 340, description: '样本覆盖度不足，模型在儿童推车难例上的置信度较低，缺失约 800 张代表样本' }
      ],
      attributionCode: 'HARD_SAMPLE_GAP',
      attributionLabel: '难例切片样本覆盖不足 (HARD_SAMPLE_GAP)',
      recommendedAction: '触发 Loop Supervisor 定向返工：保持当前数据处理 Pipeline 不变，定向对大雾儿童推车切片追加多路向量近邻扩展召回 1,200 张并重新采样合并。'
    }
  },
  {
    id: 'wo-2026-mllm-02',
    name: '多模态 LLM 电商 OCR 细粒度图文对齐洗数据工程',
    targetDescription: '抽取 100,000 件商品详情页中的长图，去除促销弹窗与广告牛皮癣，提取并精配 Markdown 结构化商品属性图文对。',
    currentStage: 'processing',
    owner: 'Alex Wang (Data Engineer)',
    createdAt: '2026-07-25 10:15',
    revisionChain: ['rev-01-spec', 'rev-02-pipeline'],
    currentTaskSpec: {
      id: 'ts-mllm-02',
      name: '电商详情页复杂 OCR 与图文对齐规范',
      version: 'v1.0',
      createdBy: 'Alex Wang (Data Engineer)',
      hardConstraints: ['过滤包含微信号与外部二维码的图片', '长图分段裁切并保留完整表格文本'],
      semanticConstraints: ['商品图与文本描述相关度大于 0.85'],
      outputRequirements: ['JSONL 格式多模态对齐指令数据集'],
      acceptanceCriteria: ['OCR 识别字符准确率 >= 99.2%'],
      ambiguities: ['牛皮癣文字是否需要打码替代：统一完全擦除并修补背景'],
      status: 'confirmed',
      targetMetrics: [
        { metric: '图文语义对齐度 Score', baselineValue: '72.0%', targetValue: '>= 90.0%' }
      ]
    },
    candidatePipelines: INITIAL_PIPELINES,
    nodePreviews: MOCK_NODE_PREVIEWS
  },
  {
    id: 'wo-2026-medical-03',
    name: '医疗 CT 影像肺部结节 3D 脏数据算子清洗与脱敏',
    targetDescription: '对 8,000 例 DICOM 格式的胸部 3D CT 扫描数据进行严格的 DICOM Header 患者隐私脱敏、伪影去除与窗宽窗位归一化。',
    currentStage: 'completed',
    owner: 'dr.chen (Data Scientist)',
    createdAt: '2026-07-22 18:30',
    revisionChain: ['rev-01-spec', 'rev-02-completed'],
    currentTaskSpec: {
      id: 'ts-med-03',
      name: '医疗 DICOM 3D CT 脱敏与质检规范',
      version: 'v2.0',
      createdBy: 'dr.chen',
      hardConstraints: ['彻底剥离 DICOM 包含的 PatientID 与医院标识'],
      semanticConstraints: ['保留 0.5mm 微小肺结节特征形态'],
      outputRequirements: ['NIfTI .nii.gz 归一化数据集'],
      acceptanceCriteria: ['隐私泄露风险校验 0 容忍'],
      ambiguities: [],
      status: 'confirmed',
      targetMetrics: [
        { metric: 'DICOM 隐私清除率', baselineValue: '95.0%', targetValue: '100.0%' }
      ]
    }
  },
  {
    id: 'wo-2026-audio-04',
    name: '方言语音合成 (TTS) 长尾噪声数据自动化高保真剔除',
    targetDescription: '清洗 500 小时粤语与四川话真实麦克风录音，剔除过爆、电磁杂音与重叠说话，保留高清语音片段。',
    currentStage: 'sampling',
    owner: 'Sarah Lin (Model Trainer)',
    createdAt: '2026-07-10 09:00',
    revisionChain: ['rev-01-spec'],
    currentTaskSpec: {
      id: 'ts-audio-04',
      name: '方言 TTS 声学环境与信噪比质量规范',
      version: 'v1.0',
      createdBy: 'Sarah Lin',
      hardConstraints: ['信噪比 SNR >= 25dB'],
      semanticConstraints: ['消除多发音人环境干扰'],
      outputRequirements: ['16kHz WAV 音频 + 文本音素 Alignment'],
      acceptanceCriteria: ['MOS 听感评估基线提升 >= +0.4'],
      ambiguities: [],
      status: 'confirmed',
      targetMetrics: [
        { metric: '平均信噪比 SNR', baselineValue: '18.2dB', targetValue: '>= 28.0dB' }
      ]
    }
  }
];
