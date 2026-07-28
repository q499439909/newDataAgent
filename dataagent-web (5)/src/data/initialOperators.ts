import { OperatorSpec } from '../types';

export const INITIAL_OPERATORS: OperatorSpec[] = [
  {
    id: 'op-watermark-01',
    name: '多模态智能水印识别与切除算子',
    category: 'filtering',
    categoryLabel: '内容清洗/过滤',
    modality: 'vision',
    level: 'L2',
    version: 'v2.1.0',
    ownerId: 'system',
    ownerName: '平台内置',
    isPublic: true,
    reviewStatus: 'approved',
    starsCount: 342,
    usageCount: 1840,
    tags: ['水印检测', '图像打码/剔除', 'OCR检测', 'YOLOv8-Watermark', '开源专用模型'],
    summary: '基于自研微调 YOLOv8-Watermark 模型与轻量 SegFormer，精准定位台标、动态网名、半透明微缩水印与版权文字。',
    suitability: [
      '电商产品图、影视剧照、新闻抓取图片的版权水印过滤',
      '训练数据中带有平台标志、网名或时间戳的干预图像清洗',
      '包含复杂背景下的微弱半透明水纹定位'
    ],
    unsuitableConditions: [
      '图片本身即为水印/标志图库（可能发生误删）',
      '红外感光或超高噪音扫描文档'
    ],
    parameterGuide: [
      { param: 'confidence_threshold', type: 'float', defaultVal: '0.45', description: '水印置信度门槛，低于此值的疑似水印将忽略' },
      { param: 'iou_threshold', type: 'float', defaultVal: '0.50', description: '重叠框NMS抑制门槛' },
      { param: 'mask_padding_px', type: 'int', defaultVal: '4', description: 'Mask外扩像素点，防止边缘残留' },
      { param: 'action_mode', type: 'string', defaultVal: 'filter_out', description: '处理策略：filter_out(直接过滤删图) | crop_or_inpaint(修复/裁剪)' }
    ],
    codeSnippets: {
      python: `import torch
from dataagent.operators import WatermarkDetector

# 实例化算子
detector = WatermarkDetector(
    model_path="weights/yolov8_watermark_v2.pt",
    conf_thresh=0.45,
    device="cuda:0"
)

def process_batch(images):
    # 返回 bbox, confidence, mask
    results = detector.detect_batch(images)
    clean_images = []
    for img, res in zip(images, results):
        if res.has_watermark and res.confidence > 0.8:
            continue # 丢弃高置信水印图
        clean_images.append(img)
    return clean_images`,
      typescript: `import { WatermarkOperator } from '@dataagent/vision-ops';

const op = new WatermarkOperator({
  confidenceThreshold: 0.45,
  actionMode: 'filter_out'
});

const result = await op.execute({ imageUrl: 'https://storage.dataagent.internal/raw/sample01.jpg' });
console.log('Watermark detected:', result.detected, 'Confidence:', result.confidence);`,
      docker: `docker run --gpus all -v /data:/data dataagent/op-watermark-detector:v2.1.0 --conf 0.45 --input /data/raw --output /data/cleaned`,
      torch: `# PyTorch Model Loading
model = torch.hub.load('dataagent/yolov8-watermark', 'custom', path='weights/yolov8_wm.pt')
preds = model(image_tensor)
`
    },
    examples: [
      {
        id: 'ex-wm-1',
        title: '成功案例：角落半透明台标检测与拦截',
        type: 'success',
        typeLabel: '成功识别',
        originalImage: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=600&q=80',
        processedImage: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=600&q=80',
        originalMeta: { width: 1920, height: 1080, format: 'JPEG', sizeKb: 420 },
        processedMeta: { width: 1920, height: 1080, format: 'JPEG', sizeKb: 420 },
        annotations: [
          { label: '水印: 右上角台标', bbox: [1600, 40, 1880, 120], confidence: 0.94, score: 0.94 }
        ],
        explanation: '在右上角发现置信度 94% 的高清平台 TV 徽标，算子判定为“有强版权水印”，触发 filter_out 策略剔除，成功防止模型学习平台台标特征。',
        scoreChange: '水纹污染值 0.94 -> 已裁切',
        passResult: false,
        filterReason: '检测到高置信度台标水印 (0.94 > 0.45)'
      },
      {
        id: 'ex-wm-2',
        title: '无水印正常人像图：安全保留',
        type: 'success',
        typeLabel: '正常通过',
        originalImage: 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=600&q=80',
        processedImage: 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=600&q=80',
        originalMeta: { width: 1024, height: 1024, format: 'PNG', sizeKb: 890 },
        processedMeta: { width: 1024, height: 1024, format: 'PNG', sizeKb: 890 },
        explanation: '全图无透明网格水印或文本叠加，置信度最高仅 0.08，正常保留。',
        scoreChange: '水印得分: 0.08',
        passResult: true
      },
      {
        id: 'ex-wm-3',
        title: '边界测试：衣服印花误判测试',
        type: 'boundary_failure',
        typeLabel: '边界分析',
        originalImage: 'https://images.unsplash.com/photo-1529139574466-a303027c1d8b?auto=format&fit=crop&w=600&q=80',
        processedImage: 'https://images.unsplash.com/photo-1529139574466-a303027c1d8b?auto=format&fit=crop&w=600&q=80',
        originalMeta: { width: 1200, height: 800, format: 'JPEG', sizeKb: 310 },
        processedMeta: { width: 1200, height: 800, format: 'JPEG', sizeKb: 310 },
        annotations: [
          { label: '疑似文字 (衣服图案)', bbox: [450, 500, 650, 580], confidence: 0.48 }
        ],
        explanation: '模特 T 恤上的品牌英文字母被识别为疑似水印 (0.48)。配合后级多模态文本语义校验节点，二次确认其为主体服装纹理而非图像水印，规避误杀。',
        scoreChange: '粗筛选 0.48 -> L3语义判别修正为合规',
        passResult: true
      }
    ],
    taskHistory: [
      {
        id: 'th-101',
        taskId: 'wo-2026-08',
        taskName: '自动驾驶多模态抓拍清洗批次 #4',
        datasetSlice: 'urban_drive_cameras_100k',
        totalProcessed: 124500,
        passRate: 91.2,
        modelAccuracyGain: '+2.8% mAP',
        p95LatencyMs: 14,
        adoptionStatus: 'adopted',
        date: '2026-07-10'
      },
      {
        id: 'th-102',
        taskId: 'wo-2026-02',
        taskName: '电商服饰全景图美学与清洗',
        datasetSlice: 'fashion_catalog_50k',
        totalProcessed: 52000,
        passRate: 88.5,
        modelAccuracyGain: '+4.1% F1-score',
        p95LatencyMs: 12,
        adoptionStatus: 'adopted',
        date: '2026-06-22'
      }
    ],
    inputSchema: '{\n  "imageUrl": "string",\n  "options": {\n    "confidenceThreshold": 0.45\n  }\n}',
    outputSchema: '{\n  "detected": "boolean",\n  "confidence": "number",\n  "watermarks": [\n    {\n      "label": "string",\n      "bbox": [x1, y1, x2, y2],\n      "confidence": "number"\n    }\n  ],\n  "decision": "KEEP | FILTER_OUT"\n}',
    p95LatencyMs: 12,
    throughputItemsPerSec: 180,
    resourceReq: 'NVIDIA T4 / A10G, 2GB VRAM',
    codeLicense: 'Apache-2.0',
    modelLicense: 'CC-BY-4.0 (Commercial allowed)',
    updatedAt: '2026-07-15'
  },
  {
    id: 'op-face-anon-02',
    name: '生物特征隐私人脸合规模糊与匿名化算子',
    category: 'enrichment',
    categoryLabel: '隐私合规/脱敏',
    modality: 'vision',
    level: 'L1',
    version: 'v1.8.2',
    ownerId: 'system',
    ownerName: '平台内置',
    isPublic: true,
    reviewStatus: 'approved',
    starsCount: 289,
    usageCount: 1210,
    tags: ['人脸检测', 'GDPR合规', '高斯模糊', '与或遮挡', '隐私脱敏'],
    summary: '符合 GDPR 与国家数据安全法的自动人脸检测与保真打码算子，支持高斯模糊、马赛克或几何黑框遮挡。',
    suitability: [
      '街景、商场抓拍等公共场所含路人人脸的数据合规化',
      '车联网驾驶员监控数据对外共享时的隐私脱敏'
    ],
    unsuitableConditions: [
      '人脸识别或微表情识别模型的训练数据集（打码会破坏目标特征）'
    ],
    parameterGuide: [
      { param: 'blur_kernel_size', type: 'int', defaultVal: '31', description: '高斯模糊核大小，数值越大越模糊' },
      { param: 'mode', type: 'string', defaultVal: 'gaussian', description: '打码样式：gaussian | mosaic | black_box' },
      { param: 'min_face_size', type: 'int', defaultVal: '16', description: '最小识别像素尺寸，小于此像素的人脸忽略' }
    ],
    codeSnippets: {
      python: `import cv2
from dataagent.operators import FaceAnonymizer

anonymizer = FaceAnonymizer(mode="gaussian", blur_kernel=31)

def process_frame(img_path):
    img = cv2.imread(img_path)
    faces = anonymizer.detect_faces(img)
    anonymized_img = anonymizer.apply_blur(img, faces)
    return anonymized_img, len(faces)`,
      typescript: `import { FaceAnonymizer } from '@dataagent/privacy-ops';

const anon = new FaceAnonymizer({ mode: 'gaussian', blurKernelSize: 31 });
const outputBuffer = await anon.processImage(inputBuffer);`
    },
    examples: [
      {
        id: 'ex-face-1',
        title: '街景多路人脱敏处理',
        type: 'success',
        typeLabel: '成功脱敏',
        originalImage: 'https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?auto=format&fit=crop&w=600&q=80',
        processedImage: 'https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?auto=format&fit=crop&w=600&q=80',
        originalMeta: { width: 1920, height: 1280, format: 'JPEG', sizeKb: 610 },
        processedMeta: { width: 1920, height: 1280, format: 'JPEG', sizeKb: 590 },
        annotations: [
          { label: '人脸 #1 (高斯模糊)', bbox: [410, 200, 480, 290], confidence: 0.98 },
          { label: '人脸 #2 (高斯模糊)', bbox: [850, 230, 910, 310], confidence: 0.96 }
        ],
        explanation: '成功检测到画面中 2 名路人人脸，应用 31x31 高斯模糊，图像主体结构与背景完全保留，人脸特征不可识别。',
        scoreChange: '隐私风险指数: 100 -> 0 (完全合规)',
        passResult: true
      }
    ],
    taskHistory: [
      {
        id: 'th-103',
        taskId: 'wo-2026-05',
        taskName: '智慧城市路口多视角数据脱敏',
        datasetSlice: 'city_traffic_cam_80k',
        totalProcessed: 80000,
        passRate: 99.8,
        modelAccuracyGain: '合规通过率 100%',
        p95LatencyMs: 8,
        adoptionStatus: 'adopted',
        date: '2026-07-01'
      }
    ],
    inputSchema: '{\n  "imagePath": "string",\n  "mode": "gaussian"\n}',
    outputSchema: '{\n  "anonymizedImagePath": "string",\n  "facesAnonymizedCount": "number"\n}',
    p95LatencyMs: 6,
    throughputItemsPerSec: 320,
    resourceReq: 'CPU 4-Core / GPU Optional',
    codeLicense: 'MIT',
    updatedAt: '2026-07-12'
  },
  {
    id: 'op-aesthetic-03',
    name: '视觉图像美学评分与光照质感质量算子',
    category: 'filtering',
    categoryLabel: '质量评估/筛选',
    modality: 'vision',
    level: 'L2',
    version: 'v3.0.1',
    ownerId: 'system',
    ownerName: '平台内置',
    isPublic: true,
    reviewStatus: 'approved',
    starsCount: 412,
    usageCount: 2310,
    tags: ['AVA数据集', 'CLIP-Aesthetic', '构图评估', '曝光检测', '高清图筛选'],
    summary: '基于 CLIP-ViT-L/14 微调的美学与打光质量评估器，可精准剔除暗光曝光不足、模糊抖动与构图残缺图。',
    suitability: [
      '高保真图像生成模型（Flux, SDXL）训练集的前置高质量图筛选',
      '广告营销及高审美壁纸数据集整理'
    ],
    unsuitableConditions: [
      '夜视仪、特殊监控或暗光极端场景的数据集（会被低分过滤）'
    ],
    parameterGuide: [
      { param: 'min_aesthetic_score', type: 'float', defaultVal: '5.5', description: '美学最低打分 (0-10分)' },
      { param: 'sharpness_threshold', type: 'float', defaultVal: '60.0', description: '拉普拉斯方差清晰度最低要求' },
      { param: 'exposure_min_max', type: 'string', defaultVal: '0.15,0.85', description: '直方图平均灰度极值比例范围' }
    ],
    codeSnippets: {
      python: `from dataagent.operators import AestheticScorer

scorer = AestheticScorer(model_name="clip-aesthetic-vit-l14", min_score=5.5)

def eval_image(img_path):
    score = scorer.predict(img_path)
    is_good = score >= 5.5
    return {"score": score, "pass": is_good}`
    },
    examples: [
      {
        id: 'ex-aes-1',
        title: '高美学得分艺术摄影：7.8分（优秀）',
        type: 'success',
        typeLabel: '高分保留',
        originalImage: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?auto=format&fit=crop&w=600&q=80',
        processedImage: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?auto=format&fit=crop&w=600&q=80',
        originalMeta: { width: 2400, height: 1600, format: 'JPEG', sizeKb: 1200 },
        processedMeta: { width: 2400, height: 1600, format: 'JPEG', sizeKb: 1200 },
        explanation: '对比度充沛、冷暖色彩协调、主体黄金分割对齐，美学打分 7.82 > 5.5，清晰度 184 > 60。',
        scoreChange: '美学得分: 7.82 / 清晰度: 184',
        passResult: true
      },
      {
        id: 'ex-aes-2',
        title: '过曝抖动废图：3.1分（过滤）',
        type: 'negative',
        typeLabel: '低分剔除',
        originalImage: 'https://images.unsplash.com/photo-1513836279014-a89f7a76ae86?auto=format&fit=crop&w=600&q=80',
        processedImage: 'https://images.unsplash.com/photo-1513836279014-a89f7a76ae86?auto=format&fit=crop&w=600&q=80',
        originalMeta: { width: 800, height: 600, format: 'JPEG', sizeKb: 90 },
        processedMeta: { width: 800, height: 600, format: 'JPEG', sizeKb: 90 },
        explanation: '图像严重晃动模糊，亮度灰度方差过小，美学打分仅 3.10，直接排除。',
        scoreChange: '美学得分: 3.10 < 5.5 (触发剔除)',
        passResult: false,
        filterReason: '美学分 3.10 低于门槛 5.5，清晰度不足'
      }
    ],
    taskHistory: [
      {
        id: 'th-104',
        taskId: 'wo-2026-09',
        taskName: 'GenAI 4K高精现实风生成数据清洗',
        datasetSlice: '4k_landscape_and_architecture',
        totalProcessed: 200000,
        passRate: 64.3,
        modelAccuracyGain: '生成图像 FID 下降 -4.2',
        p95LatencyMs: 22,
        adoptionStatus: 'adopted',
        date: '2026-07-16'
      }
    ],
    inputSchema: '{\n  "imageUrl": "string"\n}',
    outputSchema: '{\n  "aestheticScore": "number",\n  "sharpness": "number",\n  "passed": "boolean"\n}',
    p95LatencyMs: 18,
    throughputItemsPerSec: 120,
    resourceReq: 'NVIDIA T4 4GB VRAM',
    codeLicense: 'Apache-2.0',
    modelLicense: 'MIT',
    updatedAt: '2026-07-18'
  },
  {
    id: 'op-mllm-verifier-04',
    name: 'Gemini MLLM 开放语义图文一致性与逻辑判别算子',
    category: 'evaluating',
    categoryLabel: '大模型深度判定',
    modality: 'multimodal',
    level: 'L3',
    version: 'v4.2.0',
    ownerId: 'system',
    ownerName: '平台内置',
    isPublic: true,
    reviewStatus: 'approved',
    starsCount: 560,
    usageCount: 3100,
    tags: ['Gemini 2.5 Flash', 'MLLM', '图文一致性', '常识冲突检测', '低置信度仲裁'],
    summary: '利用 Gemini 多模态大模型进行复杂语义判定，检测图片内容与 Caption/标注文本是否存在矛盾、违反物理常识或隐式不合规。',
    suitability: [
      '复杂 VQA 问答数据集的逻辑错误校验',
      '传统分类器或置信度低于 0.6 的困难边缘样本的第二道智能仲裁'
    ],
    unsuitableConditions: [
      '每秒需要处理数万张图片的纯硬规则批处理（建议用 L0/L1 算子）'
    ],
    parameterGuide: [
      { param: 'prompt_template', type: 'string', defaultVal: 'Check alignment between image and text...', description: '思考指令模板' },
      { param: 'reasoning_depth', type: 'string', defaultVal: 'standard', description: '推理深度：fast | standard | deep_think' },
      { param: 'strictness', type: 'float', defaultVal: '0.8', description: '严格程度系数 (0.0-1.0)' }
    ],
    codeSnippets: {
      python: `from google import genai
from dataagent.operators import MLLMVerifier

verifier = MLLMVerifier(api_key=GEMINI_API_KEY, prompt_template="Strict verify image caption logic.")

def check_pair(image_path, text_caption):
    result = verifier.verify(image_path, text_caption)
    return result.is_consistent, result.explanation`
    },
    examples: [
      {
        id: 'ex-mllm-1',
        title: '成功发现 Caption 误描述：文字写着“白猫”，实际图片为“灰花猫”',
        type: 'success',
        typeLabel: '逻辑纠错',
        originalImage: 'https://images.unsplash.com/photo-1514888286974-6c03e2ca1dba?auto=format&fit=crop&w=600&q=80',
        processedImage: 'https://images.unsplash.com/photo-1514888286974-6c03e2ca1dba?auto=format&fit=crop&w=600&q=80',
        originalMeta: { width: 1024, height: 768, format: 'JPEG', sizeKb: 340 },
        processedMeta: { width: 1024, height: 768, format: 'JPEG', sizeKb: 340 },
        explanation: 'MLLM 识别图像中的猫毛色为“橘带白斑纹”，而关联的描述文本为“一只纯白色波斯猫在雪地上”。提示图文严重不匹配。',
        scoreChange: '一致性得分: 0.12 (严重不匹配)',
        passResult: false,
        filterReason: '图文矛盾：图片动物毛色与 Caption 描述不符'
      }
    ],
    taskHistory: [
      {
        id: 'th-105',
        taskId: 'wo-2026-11',
        taskName: '多模态对话 VQA 精筛与盲测',
        datasetSlice: 'vqa_hard_eval_set_10k',
        totalProcessed: 10000,
        passRate: 82.1,
        modelAccuracyGain: '幻觉率下降 -8.5%',
        p95LatencyMs: 380,
        adoptionStatus: 'adopted',
        date: '2026-07-19'
      }
    ],
    inputSchema: '{\n  "image": "string",\n  "caption": "string"\n}',
    outputSchema: '{\n  "isConsistent": "boolean",\n  "explanation": "string"\n}',
    p95LatencyMs: 320,
    throughputItemsPerSec: 15,
    resourceReq: 'Cloud API / API Key Server Side',
    codeLicense: 'Apache-2.0',
    modelLicense: 'Google Gemini Terms',
    updatedAt: '2026-07-20'
  },
  {
    id: 'op-phash-dedup-05',
    name: '感知哈希与特征向量快速近重复去重算子',
    category: 'cleaning',
    categoryLabel: '去重/多样性',
    modality: 'vision',
    level: 'L0',
    version: 'v1.5.0',
    ownerId: 'system',
    ownerName: '平台内置',
    isPublic: true,
    reviewStatus: 'approved',
    starsCount: 198,
    usageCount: 4200,
    tags: ['pHash', 'DHash', 'Faiss向量索引', '连拍去重', '极速超高吞吐'],
    summary: '结合 pHash 汉明距离与 SSIM 结构相似度，毫秒级快速清洗连续抓拍、重复缩放图与微小微调冗余数据。',
    suitability: [
      '视频抽帧或摄像头高频抓拍中的极度相似连拍剔除',
      '跨网抓取图像库中的海量海报/同款商品重复清洗'
    ],
    unsuitableConditions: [
      '语义相同但画面构图完全不同的图像（应结合向量近邻算法）'
    ],
    parameterGuide: [
      { param: 'max_hamming_distance', type: 'int', defaultVal: '5', description: 'pHash 汉明距离阈值，<=此值判定为极重复' },
      { param: 'ssim_threshold', type: 'float', defaultVal: '0.92', description: 'SSIM 相似度二次确认门槛' }
    ],
    codeSnippets: {
      python: `import imagehash
from PIL import Image

def calculate_phash_distance(img1_path, img2_path):
    h1 = imagehash.phash(Image.open(img1_path))
    h2 = imagehash.phash(Image.open(img2_path))
    return h1 - h2 # 汉明距离`
    },
    examples: [
      {
        id: 'ex-dedup-1',
        title: '连拍重复帧检测（汉明距离=2，SSIM=0.96）',
        type: 'success',
        typeLabel: '去重成功',
        originalImage: 'https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=600&q=80',
        processedImage: 'https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=600&q=80',
        originalMeta: { width: 800, height: 800, format: 'JPEG', sizeKb: 150 },
        processedMeta: { width: 800, height: 800, format: 'JPEG', sizeKb: 150 },
        explanation: '两张连拍图片构图完全一致，仅瞳孔光影微弱变化，算子保留首张，剔除重复第二张。',
        scoreChange: '重复判定: 汉明距离 2 <= 5',
        passResult: false,
        filterReason: '与集合已有样本 img_00189 重复 (SSIM 0.96)'
      }
    ],
    taskHistory: [
      {
        id: 'th-106',
        taskId: 'wo-2026-01',
        taskName: '视频抽帧 1000 万张全量去重',
        datasetSlice: 'video_frames_10m',
        totalProcessed: 10000000,
        passRate: 41.2,
        modelAccuracyGain: '节省 58% 存储与训练计算',
        p95LatencyMs: 0.8,
        adoptionStatus: 'adopted',
        date: '2026-06-15'
      }
    ],
    inputSchema: '{\n  "imagePath": "string"\n}',
    outputSchema: '{\n  "isDuplicate": "boolean",\n  "duplicateOfId": "string"\n}',
    p95LatencyMs: 0.8,
    throughputItemsPerSec: 2500,
    resourceReq: 'CPU Single-Core',
    codeLicense: 'MIT',
    updatedAt: '2026-07-05'
  },
  {
    id: 'op-custom-user-01',
    name: '自建：汽车遮挡号牌与特殊遮挡智能标签算子',
    category: 'enrichment',
    categoryLabel: '业务定制标注',
    modality: 'vision',
    level: 'L2',
    version: 'v1.0.0-draft',
    ownerId: 'user-002',
    ownerName: 'Sarah Lin (Model Trainer)',
    isPublic: false,
    reviewStatus: 'pending_review',
    reviewComment: '已提交社区公开申请，等待管理员复核安全与样本规范',
    starsCount: 18,
    usageCount: 45,
    tags: ['自定义算子', '车牌遮挡', '自动打标', '待社区审核'],
    summary: '针对车联网和违法抓拍场景，自动检测被纸张、泥土或物品人为遮挡的号牌，并打上 `license_plate_occluded` 软标签。',
    suitability: ['交通安防监控特种数据集扩展与困难样本识别'],
    unsuitableConditions: ['常规清晰车牌打标'],
    parameterGuide: [
      { param: 'occlusion_sensitivity', type: 'float', defaultVal: '0.6', description: '遮挡识别敏感度' }
    ],
    codeSnippets: {
      python: `# 自定义算子代码示例
def process(image_path, sensitivity=0.6):
    # 算子逻辑
    return {"occluded": True, "confidence": 0.88}`
    },
    examples: [
      {
        id: 'ex-cust-1',
        title: '车牌被纸板掩盖：识别成功',
        type: 'success',
        typeLabel: '命中打标',
        originalImage: 'https://images.unsplash.com/photo-1552519507-da3b142c6e3d?auto=format&fit=crop&w=600&q=80',
        processedImage: 'https://images.unsplash.com/photo-1552519507-da3b142c6e3d?auto=format&fit=crop&w=600&q=80',
        originalMeta: { width: 1280, height: 720, format: 'JPEG', sizeKb: 280 },
        processedMeta: { width: 1280, height: 720, format: 'JPEG', sizeKb: 280 },
        annotations: [
          { label: '遮挡号牌 (置信度 0.88)', bbox: [500, 420, 680, 490], confidence: 0.88 }
        ],
        explanation: '检测到车头牌照区域存在异物覆盖（遮挡面积 45%），标记 occluded=True。',
        scoreChange: '遮挡概率: 0.88',
        passResult: true
      }
    ],
    taskHistory: [
      {
        id: 'th-107',
        taskId: 'wo-2026-custom',
        taskName: '安防车辆遮挡号牌专项扩增集',
        datasetSlice: 'traffic_anomaly_5k',
        totalProcessed: 5000,
        passRate: 94.0,
        modelAccuracyGain: '+5.2% 遮挡场景 Recall',
        p95LatencyMs: 15,
        adoptionStatus: 'adopted',
        date: '2026-07-20'
      }
    ],
    inputSchema: '{\n  "imagePath": "string"\n}',
    outputSchema: '{\n  "occluded": "boolean",\n  "confidence": "number"\n}',
    p95LatencyMs: 15,
    throughputItemsPerSec: 150,
    resourceReq: 'GPU 2GB VRAM',
    codeLicense: 'Private / Internal',
    updatedAt: '2026-07-21'
  }
];
