import React, { useState, useRef } from 'react';
import { useApp } from '../context/AppContext';
import { OperatorLevel, Modality } from '../types';
import { X, Plus, Sparkles, Upload, Code, FileText, Folder, Archive, CheckCircle2, FileCode, HardDrive } from 'lucide-react';

export const UploadOperatorModal: React.FC = () => {
  const { isUploadModalOpen, setIsUploadModalOpen, addCustomOperator, currentUser, showToast } = useApp();

  const [name, setName] = useState('');
  const [level, setLevel] = useState<OperatorLevel>('L2');
  const [modality, setModality] = useState<Modality>('vision');
  const [category, setCategory] = useState<'filtering' | 'cleaning' | 'enrichment' | 'transformation' | 'evaluating'>('filtering');
  const [summary, setSummary] = useState('');
  const [tagsInput, setTagsInput] = useState('自定义算子, 业务模型, 算法扩展');
  
  // Upload Mode: 'code' | 'file' | 'folder'
  const [uploadType, setUploadType] = useState<'code' | 'file' | 'folder'>('file');
  const [pythonCode, setPythonCode] = useState(`def process_batch(image_list):\n    # 自定义数据处理逻辑\n    results = []\n    for img in image_list:\n        # 执行筛选或标注\n        results.append({"passed": True, "score": 0.95})\n    return results`);

  // File / Folder state
  const [uploadedFiles, setUploadedFiles] = useState<{ path: string; sizeKb: number; type: string }[]>([]);
  const [packageName, setPackageName] = useState<string>('');
  const [totalSizeKb, setTotalSizeKb] = useState<number>(0);
  const [entryPoint, setEntryPoint] = useState<string>('main.py');
  const [isDragOver, setIsDragOver] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  if (!isUploadModalOpen) return null;

  const handleProcessFileList = (fileList: FileList) => {
    if (!fileList || fileList.length === 0) return;

    const filesArr: { path: string; sizeKb: number; type: string }[] = [];
    let sizeSum = 0;
    let mainPyFound = '';

    for (let i = 0; i < fileList.length; i++) {
      const f = fileList[i];
      const relPath = f.webkitRelativePath || f.name;
      const sizeKb = Math.round(f.size / 1024) || 1;
      sizeSum += sizeKb;

      const ext = f.name.split('.').pop()?.toLowerCase() || '';
      let fileType = 'text';
      if (['py', 'js', 'ts', 'sh'].includes(ext)) fileType = 'code';
      else if (['zip', 'gz', 'tar', 'tgz', '7z', 'rar'].includes(ext)) fileType = 'archive';
      else if (['pt', 'pth', 'onnx', 'bin', 'safetensors'].includes(ext)) fileType = 'weights';
      else if (['json', 'yaml', 'yml', 'toml'].includes(ext)) fileType = 'config';

      filesArr.push({ path: relPath, sizeKb, type: fileType });

      if (f.name.endsWith('.py')) {
        if (!mainPyFound || f.name.includes('main') || f.name.includes('operator') || f.name.includes('entry')) {
          mainPyFound = relPath;
        }

        // Auto read single python file content
        if (fileList.length === 1 || f.name === 'main.py' || f.name === 'operator.py') {
          const reader = new FileReader();
          reader.onload = (e) => {
            const content = e.target?.result as string;
            if (content) setPythonCode(content);
          };
          reader.readAsText(f);
        }
      }
    }

    setUploadedFiles(filesArr);
    setTotalSizeKb(sizeSum);
    setPackageName(fileList[0].name.split('/')[0] || fileList[0].name);
    if (mainPyFound) setEntryPoint(mainPyFound);

    // Auto fill operator name if empty
    if (!name.trim()) {
      const cleanName = fileList[0].name.replace(/\.(py|zip|tar\.gz|tar)$/i, '');
      setName(`自定义算子：${cleanName}`);
    }

    showToast(`成功读取 ${fileList.length} 个文件/资源 (共 ${(sizeSum / 1024).toFixed(2)} MB)`);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) handleProcessFileList(e.target.files);
  };

  const handleFolderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) handleProcessFileList(e.target.files);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleProcessFileList(e.dataTransfer.files);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !summary.trim()) return;

    addCustomOperator({
      name,
      category,
      categoryLabel: category === 'filtering' ? '数据过滤/清洗' :
                     category === 'cleaning' ? '数据去重/重构' :
                     category === 'enrichment' ? '隐私脱敏/增强' :
                     category === 'transformation' ? '格式转换/标准化' : '深度评估/判别',
      modality,
      level,
      version: 'v1.0.0',
      ownerId: currentUser.id,
      ownerName: currentUser.name,
      isPublic: false,
      reviewStatus: 'draft',
      tags: tagsInput.split(',').map(t => t.trim()).filter(Boolean),
      summary,
      suitability: ['用户专有业务场景的数据清洗与模型前置过滤'],
      unsuitableConditions: ['与业务逻辑无关的通用标准测试'],
      parameterGuide: [
        { param: 'threshold', type: 'float', defaultVal: '0.5', description: '算子触发或过滤门槛' },
        { param: 'batch_size', type: 'int', defaultVal: '32', description: '处理批大小' }
      ],
      codeSnippets: { python: pythonCode },
      packageInfo: uploadedFiles.length > 0 ? {
        uploadType: uploadType === 'folder' ? 'folder' :
                    uploadedFiles.some(f => f.type === 'archive') ? 'archive' : 'single_file',
        fileName: packageName || 'operator_package',
        fileSizeKb: totalSizeKb,
        fileCount: uploadedFiles.length,
        fileList: uploadedFiles,
        entryPoint: entryPoint
      } : {
        uploadType: 'code_snippet',
        fileName: 'script.py',
        fileSizeKb: Math.round(pythonCode.length / 1024) || 1,
        fileCount: 1,
        entryPoint: 'main.py'
      },
      examples: [
        {
          id: `ex-custom-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
          title: '自定义样本验证 #1',
          type: 'success',
          typeLabel: '测试通过',
          originalImage: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=600&q=80',
          processedImage: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=600&q=80',
          originalMeta: { width: 1024, height: 768, format: 'JPEG', sizeKb: 310 },
          processedMeta: { width: 1024, height: 768, format: 'JPEG', sizeKb: 310 },
          explanation: `自建算子上传成功，通过单元测试脚本 Golden Set 检验，入口: ${entryPoint}`,
          passResult: true
        }
      ],
      taskHistory: [],
      inputSchema: '{\n  "imageUrl": "string"\n}',
      outputSchema: '{\n  "passed": "boolean",\n  "score": "number"\n}',
      p95LatencyMs: 15,
      throughputItemsPerSec: 100,
      resourceReq: 'CPU 2-Core / 2GB RAM',
      codeLicense: 'Private / Custom Workspace'
    });

    showToast(`个人算子「${name}」已成功创建并提交至您的个人自建库！`);
    setIsUploadModalOpen(false);
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="bg-white rounded-2xl shadow-2xl border border-emerald-100 w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden">
        
        {/* Modal Header */}
        <div className="p-5 bg-gradient-to-r from-amber-50/80 via-emerald-50/60 to-white border-b border-emerald-100 flex items-center justify-between">
          <div className="flex items-center space-x-2.5">
            <div className="w-9 h-9 rounded-xl bg-emerald-600 text-white flex items-center justify-center shadow-2xs">
              <Upload className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-bold text-slate-900 text-base">新建与上传自定义算子 (Upload Custom Operator)</h3>
              <p className="text-xs text-slate-500">创建后保存至个人算子库，测试完善后可申请晋升上架公共社区库</p>
            </div>
          </div>

          <button
            onClick={() => setIsUploadModalOpen(false)}
            className="p-2 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Hidden File Inputs */}
        <input
          type="file"
          ref={fileInputRef}
          onChange={handleFileChange}
          accept=".py,.zip,.tar.gz,.tgz,.tar,.json"
          className="hidden"
        />
        <input
          type="file"
          ref={folderInputRef}
          onChange={handleFolderChange}
          {...({ webkitdirectory: '', directory: '', multiple: true } as any)}
          className="hidden"
        />

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-6 overflow-y-auto space-y-5 flex-1 text-xs">
          
          {/* 1. Basic Info */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block font-bold text-slate-800 mb-1">算子名称 Operator Name *</label>
              <input
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：特种视频夜间暗光车牌反光识别算子"
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-800 focus:outline-none focus:border-emerald-500"
              />
            </div>

            <div>
              <label className="block font-bold text-slate-800 mb-1">算子分类 Category *</label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value as any)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-800"
              >
                <option value="filtering">内容清洗/过滤 (Filtering)</option>
                <option value="cleaning">重构/去重 (Cleaning)</option>
                <option value="enrichment">隐私脱敏/增强 (Enrichment)</option>
                <option value="transformation">格式转换/标准化 (Transformation)</option>
                <option value="evaluating">大模型深度判定 (Evaluating)</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block font-bold text-slate-800 mb-1">算子分层 Level *</label>
              <select
                value={level}
                onChange={(e) => setLevel(e.target.value as OperatorLevel)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-800"
              >
                <option value="L0">L0 - 确定性规则/高吞吐代码</option>
                <option value="L1">L1 - 传统CV/轻量CPU模型</option>
                <option value="L2">L2 - 开源专用模型 (SOTA)</option>
                <option value="L3">L3 - 多模态大模型 (MLLM)</option>
              </select>
            </div>

            <div>
              <label className="block font-bold text-slate-800 mb-1">感知模态 Modality *</label>
              <select
                value={modality}
                onChange={(e) => setModality(e.target.value as Modality)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-800"
              >
                <option value="vision">视觉 Vision</option>
                <option value="multimodal">多模态 Multimodal</option>
                <option value="text">文本 Text</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block font-bold text-slate-800 mb-1">算子功能与解题技术简述 Summary *</label>
            <textarea
              required
              rows={2}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder="简要描述该算子的适用场景、解决的核心数据治理问题与算法模型架构..."
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-800 focus:outline-none focus:border-emerald-500"
            />
          </div>

          {/* 2. File / Folder Upload Area */}
          <div className="space-y-2 pt-1 border-t border-slate-100">
            <div className="flex items-center justify-between">
              <label className="block font-bold text-slate-900">
                算子实现方式与资源文件 (Upload Script / Folder / Archive) *
              </label>

              {/* Upload Type Switcher Tabs */}
              <div className="flex items-center space-x-1 bg-slate-100 p-0.5 rounded-lg">
                <button
                  type="button"
                  onClick={() => setUploadType('file')}
                  className={`px-2.5 py-1 rounded-md text-[11px] font-bold transition-all ${
                    uploadType === 'file' ? 'bg-white text-emerald-800 shadow-2xs' : 'text-slate-600'
                  }`}
                >
                  <FileCode className="w-3 h-3 inline mr-1" />
                  单个脚本/压缩包 (.py/.zip)
                </button>

                <button
                  type="button"
                  onClick={() => setUploadType('folder')}
                  className={`px-2.5 py-1 rounded-md text-[11px] font-bold transition-all ${
                    uploadType === 'folder' ? 'bg-white text-emerald-800 shadow-2xs' : 'text-slate-600'
                  }`}
                >
                  <Folder className="w-3 h-3 inline mr-1" />
                  整个工程文件夹
                </button>

                <button
                  type="button"
                  onClick={() => setUploadType('code')}
                  className={`px-2.5 py-1 rounded-md text-[11px] font-bold transition-all ${
                    uploadType === 'code' ? 'bg-white text-emerald-800 shadow-2xs' : 'text-slate-600'
                  }`}
                >
                  <Code className="w-3 h-3 inline mr-1" />
                  在线贴入 Python 代码
                </button>
              </div>
            </div>

            {/* Drag & Drop Dropzone for File or Folder */}
            {(uploadType === 'file' || uploadType === 'folder') && (
              <div
                onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={handleDrop}
                className={`p-5 rounded-2xl border-2 border-dashed transition-all flex flex-col items-center justify-center text-center space-y-2 cursor-pointer ${
                  isDragOver ? 'border-emerald-500 bg-emerald-50/60' : 'border-slate-200 bg-slate-50/70 hover:bg-slate-100/80 hover:border-slate-300'
                }`}
              >
                <div className="w-10 h-10 rounded-2xl bg-white border border-slate-200 shadow-2xs flex items-center justify-center text-emerald-600">
                  {uploadType === 'folder' ? <Folder className="w-5 h-5" /> : <Archive className="w-5 h-5" />}
                </div>

                <div>
                  <p className="font-bold text-slate-800 text-xs">
                    {uploadType === 'folder' ? '拖拽整个算子工程文件夹到此处' : '拖拽 Python 脚本 (.py) 或压缩包 (.zip / .tar.gz) 到此处'}
                  </p>
                  <p className="text-[11px] text-slate-500 mt-0.5">
                    支持 .py 算法源码、requirements.txt 依赖配置与权重模型包文件
                  </p>
                </div>

                <div className="flex items-center space-x-2 pt-1">
                  {uploadType === 'file' ? (
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      className="bg-emerald-600 hover:bg-emerald-700 text-white px-3.5 py-1.5 rounded-xl font-bold shadow-2xs transition-all"
                    >
                      浏览文件 (.py/.zip/.tar.gz)
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => folderInputRef.current?.click()}
                      className="bg-emerald-600 hover:bg-emerald-700 text-white px-3.5 py-1.5 rounded-xl font-bold shadow-2xs transition-all"
                    >
                      选择项目文件夹 Directory
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Display Selected File List or Tree */}
            {uploadedFiles.length > 0 && (
              <div className="bg-slate-900 text-slate-200 p-3.5 rounded-xl space-y-2 font-mono text-[11px] border border-slate-800">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                  <div className="flex items-center space-x-2 text-emerald-400 font-bold">
                    <CheckCircle2 className="w-4 h-4" />
                    <span>已检测并读取文件包 ({uploadedFiles.length} 个资源，共 {(totalSizeKb / 1024).toFixed(2)} MB)</span>
                  </div>
                  <span className="text-slate-400">打包名称: {packageName}</span>
                </div>

                <div className="max-h-32 overflow-y-auto space-y-1 pr-1">
                  {uploadedFiles.map((f, idx) => (
                    <div key={idx} className="flex items-center justify-between text-slate-300 hover:text-white">
                      <span className="truncate max-w-[320px]">📄 {f.path}</span>
                      <span className="text-slate-500">{f.sizeKb} KB</span>
                    </div>
                  ))}
                </div>

                <div className="flex items-center space-x-2 pt-1 border-t border-slate-800/80">
                  <span className="text-slate-400">入口 Python 文件:</span>
                  <input
                    type="text"
                    value={entryPoint}
                    onChange={(e) => setEntryPoint(e.target.value)}
                    className="bg-slate-800 text-emerald-300 border border-slate-700 rounded px-2 py-0.5 text-[11px] font-mono focus:outline-none"
                    placeholder="main.py"
                  />
                </div>
              </div>
            )}

            {/* Online Python Code Snippet Editor */}
            {uploadType === 'code' && (
              <div>
                <label className="block font-bold text-slate-700 mb-1">Python 运行主函数片段</label>
                <textarea
                  rows={5}
                  value={pythonCode}
                  onChange={(e) => setPythonCode(e.target.value)}
                  className="w-full bg-slate-950 text-emerald-400 font-mono text-[11px] p-3 rounded-xl focus:outline-none border border-slate-800"
                />
              </div>
            )}
          </div>

          <div>
            <label className="block font-bold text-slate-800 mb-1">算子检索标签 Tags (逗号分隔)</label>
            <input
              type="text"
              value={tagsInput}
              onChange={(e) => setTagsInput(e.target.value)}
              placeholder="例如：车牌识别, 暗光增强, YOLO, 个人自建"
              className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-xs text-slate-800"
            />
          </div>

          {/* Modal Footer Buttons */}
          <div className="pt-3 border-t border-slate-100 flex items-center justify-between">
            <span className="text-slate-400 text-[11px]">
              点击创建后直接加入「个人算子库」，全平台隔离私有安全。
            </span>

            <div className="flex items-center space-x-2">
              <button
                type="button"
                onClick={() => setIsUploadModalOpen(false)}
                className="px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold"
              >
                取消
              </button>
              <button
                type="submit"
                className="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-bold shadow-xs transition-all active:scale-95"
              >
                一键创建并保存至个人库
              </button>
            </div>
          </div>

        </form>

      </div>
    </div>
  );
};

