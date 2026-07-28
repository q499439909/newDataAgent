import React, { useState } from 'react';
import { useApp } from '../context/AppContext';
import { OPERATOR_LEVEL_MAP } from '../types';
import { 
  X, 
  BookOpen, 
  Code, 
  Copy, 
  Check, 
  Sparkles, 
  Layers, 
  History, 
  Sliders, 
  ShieldCheck, 
  Cpu, 
  ExternalLink,
  Tag,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Star,
  Globe,
  Lock,
  MessageSquare,
  Share2,
  Clock,
  Folder
} from 'lucide-react';

export const OperatorDetailModal: React.FC = () => {
  const { 
    selectedOperatorForDetail, 
    setSelectedOperatorForDetail, 
    userFavorites, 
    toggleFavoriteOperator,
    showToast,
    currentUser,
    applyForOperatorPromotion,
    reviewOperatorPromotion
  } = useApp();

  const [activeTab, setActiveTab] = useState<'examples' | 'history' | 'code' | 'params'>('examples');
  const [activeCodeLang, setActiveCodeLang] = useState<'python' | 'typescript' | 'docker'>('python');
  const [copied, setCopied] = useState(false);

  if (!selectedOperatorForDetail) return null;

  const op = selectedOperatorForDetail;
  const levelInfo = OPERATOR_LEVEL_MAP[op.level];
  const isFav = userFavorites.includes(op.id);

  const handleCopyCode = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    showToast('算子调用代码已复制到剪贴板！');
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="bg-white rounded-2xl shadow-2xl border border-emerald-100 w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden">
        
        {/* Modal Header */}
        <div className="p-6 bg-gradient-to-r from-emerald-50/80 via-teal-50/40 to-white border-b border-emerald-100 flex items-start justify-between">
          <div className="space-y-2">
            <div className="flex items-center space-x-2 flex-wrap gap-y-1">
              <span className={`px-2.5 py-0.5 rounded text-[10px] font-bold ${levelInfo.badgeBg} ${levelInfo.badgeText}`}>
                {op.level} - {levelInfo.title}
              </span>

              {op.isPublic ? (
                <span className="px-2.5 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-800 flex items-center space-x-1">
                  <Globe className="w-3 h-3" />
                  <span>公共社区库</span>
                </span>
              ) : (
                <span className="px-2.5 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-900 flex items-center space-x-1">
                  <Lock className="w-3 h-3" />
                  <span>个人自建库</span>
                </span>
              )}

              <span className="text-xs text-slate-400">版本: {op.version}</span>
            </div>

            <h2 className="text-xl font-bold text-slate-900">{op.name}</h2>
            <div className="flex items-center space-x-3 text-xs text-slate-500">
              <span>归属人: <strong className="text-slate-800">{op.ownerName || (op.isPublic ? '平台内置' : '个人自建')}</strong></span>
              <span>•</span>
              <span>检索标签: {op.tags.join(', ')}</span>
            </div>
            <p className="text-xs text-slate-600 leading-relaxed max-w-2xl">{op.summary}</p>
          </div>

          <div className="flex items-center space-x-2">
            <button
              onClick={() => toggleFavoriteOperator(op.id)}
              className={`p-2.5 rounded-xl border transition-colors ${
                isFav
                  ? 'bg-amber-50 text-amber-500 border-amber-200'
                  : 'bg-white text-slate-400 border-slate-200 hover:text-amber-500'
              }`}
            >
              <Star className={`w-4 h-4 ${isFav ? 'fill-current' : ''}`} />
            </button>

            <button
              onClick={() => setSelectedOperatorForDetail(null)}
              className="p-2.5 text-slate-400 hover:text-slate-700 bg-white border border-slate-200 rounded-xl"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Public Promotion & Progress Banner Card for Personal/Owned Operators */}
        {(!op.isPublic || op.ownerId === currentUser.id) && (
          <div className="mx-6 mt-4 p-4 rounded-2xl bg-gradient-to-r from-slate-50 via-emerald-50/40 to-amber-50/40 border border-slate-200/90 shadow-2xs space-y-3">
            
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-200/60 pb-3">
              <div>
                <div className="flex items-center space-x-2 flex-wrap gap-y-1">
                  <span className="font-bold text-slate-900 text-xs flex items-center space-x-1.5">
                    <Share2 className="w-3.5 h-3.5 text-emerald-600" />
                    <span>公共社区库晋升申请与进度</span>
                  </span>
                  
                  {op.reviewStatus === 'pending_review' && (
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-100 text-amber-900 border border-amber-200 flex items-center space-x-1">
                      <Clock className="w-3 h-3 animate-spin" />
                      <span>公共库晋升审核中</span>
                    </span>
                  )}
                  {op.reviewStatus === 'approved' && (
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800 border border-emerald-200 flex items-center space-x-1">
                      <CheckCircle2 className="w-3 h-3 text-emerald-600" />
                      <span>已成功晋升公共库</span>
                    </span>
                  )}
                  {op.reviewStatus === 'rejected' && (
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-red-100 text-red-800 border border-red-200 flex items-center space-x-1">
                      <XCircle className="w-3 h-3 text-red-600" />
                      <span>审核申请已被退回</span>
                    </span>
                  )}
                  {op.reviewStatus === 'draft' && (
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-100 text-slate-700 border border-slate-200">
                      未申请 (个人私有)
                    </span>
                  )}
                </div>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  将经过充分验证的个人自建算子申请提交晋升至全平台公共社区算子库。
                </p>
              </div>

              {/* Action Button: Apply / Resubmit to Public Library */}
              {(op.reviewStatus === 'draft' || op.reviewStatus === 'rejected') && (
                <button
                  onClick={() => applyForOperatorPromotion(op.id)}
                  className="flex items-center space-x-1.5 bg-emerald-600 hover:bg-emerald-700 text-white px-3.5 py-1.5 rounded-xl text-xs font-bold shadow-xs transition-all active:scale-95 shrink-0"
                >
                  <Share2 className="w-3.5 h-3.5" />
                  <span>{op.reviewStatus === 'rejected' ? '修改后重新提交申请' : '申请提交至公共库'}</span>
                </button>
              )}
            </div>

            {/* Application Progress Stepper */}
            <div className="grid grid-cols-4 gap-2 pt-1">
              <div className="flex flex-col items-center text-center space-y-1">
                <div className="w-6 h-6 rounded-full bg-emerald-600 text-white flex items-center justify-center text-[10px] font-bold shadow-2xs">
                  1
                </div>
                <span className="text-[11px] font-bold text-slate-800">个人自建</span>
                <span className="text-[9px] text-slate-400">测试验证中</span>
              </div>

              <div className="flex flex-col items-center text-center space-y-1">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold transition-all ${
                  op.reviewStatus !== 'draft' ? 'bg-emerald-600 text-white shadow-2xs' : 'bg-slate-200 text-slate-500'
                }`}>
                  2
                </div>
                <span className={`text-[11px] font-bold ${op.reviewStatus !== 'draft' ? 'text-slate-800' : 'text-slate-400'}`}>
                  提交申请
                </span>
                <span className="text-[9px] text-slate-400">
                  {op.reviewStatus !== 'draft' ? '已发起申请' : '待提交'}
                </span>
              </div>

              <div className="flex flex-col items-center text-center space-y-1">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold transition-all ${
                  op.reviewStatus === 'approved' ? 'bg-emerald-600 text-white shadow-2xs' :
                  op.reviewStatus === 'pending_review' ? 'bg-amber-500 text-white animate-pulse shadow-2xs' :
                  op.reviewStatus === 'rejected' ? 'bg-red-500 text-white shadow-2xs' : 'bg-slate-200 text-slate-500'
                }`}>
                  3
                </div>
                <span className={`text-[11px] font-bold ${
                  op.reviewStatus === 'pending_review' ? 'text-amber-900' :
                  op.reviewStatus === 'approved' ? 'text-slate-800' :
                  op.reviewStatus === 'rejected' ? 'text-red-900' : 'text-slate-400'
                }`}>
                  架构与合规复核
                </span>
                <span className="text-[9px] text-slate-400">
                  {op.reviewStatus === 'pending_review' ? '正在审核...' :
                   op.reviewStatus === 'approved' ? '复核通过' :
                   op.reviewStatus === 'rejected' ? '已退回' : '未开始'}
                </span>
              </div>

              <div className="flex flex-col items-center text-center space-y-1">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold transition-all ${
                  op.isPublic ? 'bg-emerald-600 text-white shadow-2xs' : 'bg-slate-200 text-slate-500'
                }`}>
                  4
                </div>
                <span className={`text-[11px] font-bold ${op.isPublic ? 'text-emerald-800' : 'text-slate-400'}`}>
                  公共社区库上架
                </span>
                <span className="text-[9px] text-slate-400">
                  {op.isPublic ? '全平台公开' : '未上架'}
                </span>
              </div>
            </div>

            {/* Comment or feedback message */}
            {op.reviewComment && (
              <div className="bg-white p-3 rounded-xl border border-slate-200/80 text-xs text-slate-700 flex items-start space-x-2">
                <MessageSquare className="w-4 h-4 text-emerald-600 mt-0.5 shrink-0" />
                <div className="space-y-0.5">
                  <span className="font-bold text-slate-900">审核节点记录与意见：</span>
                  <p className="text-slate-600 leading-relaxed">{op.reviewComment}</p>
                </div>
              </div>
            )}

            {/* Admin Quick Review inside Modal */}
            {currentUser.role === 'admin' && op.reviewStatus === 'pending_review' && (
              <div className="bg-amber-50 p-3 rounded-xl border border-amber-200 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <span className="text-xs font-bold text-amber-950">管理员操作视角：复核公共库晋升请求</span>
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => reviewOperatorPromotion(op.id, true)}
                    className="px-3 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-bold transition-all"
                  >
                    同意晋升并上架
                  </button>
                  <button
                    onClick={() => reviewOperatorPromotion(op.id, false, '退回修改：请补充包含边界或异常样本的单元测试例。')}
                    className="px-3 py-1 bg-red-600 hover:bg-red-700 text-white rounded-lg text-xs font-bold transition-all"
                  >
                    退回修改
                  </button>
                </div>
              </div>
            )}

          </div>
        )}

        {/* Navigation Tabs */}
        <div className="px-6 py-3 bg-slate-50/80 border-b border-slate-100 flex items-center space-x-2 text-xs font-medium">
          <button
            onClick={() => setActiveTab('examples')}
            className={`flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl transition-all ${
              activeTab === 'examples'
                ? 'bg-white text-emerald-900 font-bold shadow-2xs border border-emerald-200'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <BookOpen className="w-4 h-4 text-emerald-600" />
            <span>实现样例库 ({op.examples.length})</span>
          </button>

          <button
            onClick={() => setActiveTab('history')}
            className={`flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl transition-all ${
              activeTab === 'history'
                ? 'bg-white text-emerald-900 font-bold shadow-2xs border border-emerald-200'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <History className="w-4 h-4 text-emerald-600" />
            <span>已支持过的任务样例 ({op.taskHistory.length})</span>
          </button>

          <button
            onClick={() => setActiveTab('code')}
            className={`flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl transition-all ${
              activeTab === 'code'
                ? 'bg-white text-emerald-900 font-bold shadow-2xs border border-emerald-200'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <Code className="w-4 h-4 text-emerald-600" />
            <span>算子调用代码案例</span>
          </button>

          <button
            onClick={() => setActiveTab('params')}
            className={`flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl transition-all ${
              activeTab === 'params'
                ? 'bg-white text-emerald-900 font-bold shadow-2xs border border-emerald-200'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <Sliders className="w-4 h-4 text-emerald-600" />
            <span>参数调优与 Schema 指南</span>
          </button>
        </div>

        {/* Modal Body Content */}
        <div className="p-6 overflow-y-auto space-y-6 flex-1 bg-white">
          
          {/* TAB 1: Operator Verified Examples */}
          {activeTab === 'examples' && (
            <div className="space-y-6">
              
              <div className="bg-emerald-50/60 p-4 rounded-xl border border-emerald-100 text-xs text-emerald-900 space-y-1">
                <div className="font-bold flex items-center space-x-1">
                  <ShieldCheck className="w-4 h-4 text-emerald-600" />
                  <span>真实 Run 复验绑定的实现样例 (OperatorExampleSet)</span>
                </div>
                <p className="text-emerald-800">
                  展示算子在实际数据生产中的真实表现，包含原图与处理后对比、掩模框定位、分值变化以及保留/剔除决策原因。
                </p>
              </div>

              <div className="space-y-6">
                {op.examples.map((ex) => (
                  <div key={ex.id} className="bg-slate-50 p-4 rounded-2xl border border-slate-200 space-y-4">
                    
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                          ex.type === 'success' ? 'bg-emerald-100 text-emerald-800' :
                          ex.type === 'negative' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800'
                        }`}>
                          {ex.typeLabel}
                        </span>
                        <h4 className="font-bold text-slate-900 text-sm">{ex.title}</h4>
                      </div>

                      <div className="text-xs font-semibold">
                        {ex.passResult ? (
                          <span className="text-emerald-700 flex items-center space-x-1">
                            <CheckCircle2 className="w-3.5 h-3.5" />
                            <span>结论：通过保留</span>
                          </span>
                        ) : (
                          <span className="text-red-600 flex items-center space-x-1">
                            <XCircle className="w-3.5 h-3.5" />
                            <span>结论：拦截剔除 ({ex.filterReason})</span>
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Image comparison side-by-side */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      
                      {/* Original image */}
                      <div className="space-y-1.5">
                        <div className="text-[11px] font-semibold text-slate-500 flex justify-between">
                          <span>处理前原图:</span>
                          <span>{ex.originalMeta.width}x{ex.originalMeta.height} ({ex.originalMeta.sizeKb}KB)</span>
                        </div>
                        <div className="relative aspect-video rounded-xl overflow-hidden bg-slate-950 flex items-center justify-center">
                          <img src={ex.originalImage} alt="原图" className="w-full h-full object-cover" />
                          
                          {/* Annotations */}
                          {ex.annotations?.map((ann, i) => (
                            <div key={i} className="absolute top-2 left-2 bg-amber-500 text-white text-[10px] font-bold px-2 py-0.5 rounded shadow-sm">
                              {ann.label}
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Processed image */}
                      <div className="space-y-1.5">
                        <div className="text-[11px] font-semibold text-slate-500 flex justify-between">
                          <span>算子处理后结果:</span>
                          <span className="text-emerald-700">{ex.scoreChange}</span>
                        </div>
                        <div className="relative aspect-video rounded-xl overflow-hidden bg-slate-950 flex items-center justify-center">
                          <img src={ex.processedImage} alt="处理后" className="w-full h-full object-cover" />
                        </div>
                      </div>

                    </div>

                    {/* Explanation */}
                    <div className="text-xs text-slate-700 bg-white p-3 rounded-xl border border-slate-200/80 leading-relaxed">
                      <span className="font-bold text-slate-900">算子判定与分析依据: </span>
                      {ex.explanation}
                    </div>

                  </div>
                ))}
              </div>

            </div>
          )}

          {/* TAB 2: Task History */}
          {activeTab === 'history' && (
            <div className="space-y-4">
              <h3 className="font-bold text-slate-900 text-sm">已在生产环境中支持过的具体任务记录</h3>
              <p className="text-xs text-slate-500">
                展示该算子在历史真实数据集清洗与训练任务中的吞吐、通过率及模型 Accuracy/mAP 增益。
              </p>

              <div className="divide-y divide-slate-100 border border-slate-200 rounded-xl overflow-hidden">
                {op.taskHistory.map((th) => (
                  <div key={th.id} className="p-4 bg-white hover:bg-slate-50/80 transition-colors space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-slate-900 text-xs">{th.taskName}</span>
                      <span className="text-[10px] text-slate-400 font-mono">{th.date}</span>
                    </div>

                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                      <div className="bg-slate-50 p-2 rounded-lg border border-slate-100">
                        <div className="text-[10px] text-slate-400">处理规模</div>
                        <div className="font-semibold text-slate-800">{th.totalProcessed.toLocaleString()} 张</div>
                      </div>

                      <div className="bg-slate-50 p-2 rounded-lg border border-slate-100">
                        <div className="text-[10px] text-slate-400">通过率</div>
                        <div className="font-semibold text-emerald-700">{th.passRate}%</div>
                      </div>

                      <div className="bg-slate-50 p-2 rounded-lg border border-slate-100">
                        <div className="text-[10px] text-slate-400">模型增益</div>
                        <div className="font-bold text-emerald-800">{th.modelAccuracyGain}</div>
                      </div>

                      <div className="bg-slate-50 p-2 rounded-lg border border-slate-100">
                        <div className="text-[10px] text-slate-400">p95 耗时</div>
                        <div className="font-semibold text-slate-800">{th.p95LatencyMs} ms</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* TAB 3: Code Snippets */}
          {activeTab === 'code' && (
            <div className="space-y-4">
              
              {/* Package Info if uploaded */}
              {op.packageInfo && (
                <div className="bg-slate-900 text-slate-200 p-4 rounded-xl border border-slate-800 space-y-2 text-xs">
                  <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                    <div className="flex items-center space-x-2 font-bold text-emerald-400">
                      <Folder className="w-4 h-4" />
                      <span>已打包算子包含资源 (Upload Package Metadata)</span>
                    </div>
                    <span className="text-slate-400 font-mono text-[11px]">
                      模式: {op.packageInfo.uploadType === 'folder' ? '工程文件夹' : op.packageInfo.uploadType === 'archive' ? '压缩包' : '脚本文件'}
                    </span>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-[11px] font-mono text-slate-300">
                    <div>包文件: <strong className="text-white">{op.packageInfo.fileName}</strong></div>
                    <div>入口 Python: <strong className="text-emerald-400">{op.packageInfo.entryPoint}</strong></div>
                    <div>资源总数: <strong className="text-white">{op.packageInfo.fileCount} 个文件 ({(op.packageInfo.fileSizeKb! / 1024).toFixed(2)} MB)</strong></div>
                  </div>

                  {op.packageInfo.fileList && op.packageInfo.fileList.length > 0 && (
                    <div className="pt-2 border-t border-slate-800/80">
                      <div className="text-[10px] text-slate-400 mb-1 font-semibold uppercase">包含的具体文件树 (File Tree):</div>
                      <div className="max-h-24 overflow-y-auto space-y-1 font-mono text-[11px] bg-slate-950 p-2 rounded-lg border border-slate-800">
                        {op.packageInfo.fileList.map((f, i) => (
                          <div key={i} className="flex justify-between text-slate-400 hover:text-slate-200">
                            <span>📄 {f.path}</span>
                            <span>{f.sizeKb} KB</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => setActiveCodeLang('python')}
                    className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                      activeCodeLang === 'python' ? 'bg-emerald-600 text-white' : 'bg-slate-100 text-slate-600'
                    }`}
                  >
                    Python SDK
                  </button>

                  <button
                    onClick={() => setActiveCodeLang('typescript')}
                    className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                      activeCodeLang === 'typescript' ? 'bg-emerald-600 text-white' : 'bg-slate-100 text-slate-600'
                    }`}
                  >
                    TypeScript
                  </button>
                </div>

                <button
                  onClick={() => handleCopyCode(op.codeSnippets[activeCodeLang] || '')}
                  className="flex items-center space-x-1.5 bg-slate-100 hover:bg-emerald-100 text-slate-700 hover:text-emerald-900 px-3 py-1.5 rounded-xl text-xs font-semibold transition-colors"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>复制代码</span>
                </button>
              </div>

              <div className="bg-slate-950 text-slate-100 p-4 rounded-xl font-mono text-xs overflow-x-auto leading-relaxed border border-slate-800">
                <pre>{op.codeSnippets[activeCodeLang] || op.codeSnippets.python}</pre>
              </div>
            </div>
          )}

          {/* TAB 4: Parameters & Schema */}
          {activeTab === 'params' && (
            <div className="space-y-4 text-xs">
              <h3 className="font-bold text-slate-900 text-sm">参数调节与物理定义</h3>
              
              <div className="border border-slate-200 rounded-xl overflow-hidden divide-y divide-slate-100">
                {op.parameterGuide.map((p, idx) => (
                  <div key={idx} className="p-3 bg-white flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div>
                      <span className="font-bold font-mono text-emerald-800 text-xs">{p.param}</span>
                      <span className="ml-2 text-[10px] text-slate-400">({p.type})</span>
                      <p className="text-slate-600 text-xs mt-0.5">{p.description}</p>
                    </div>
                    <div className="bg-slate-50 px-2.5 py-1 rounded-lg border border-slate-200 text-slate-800 font-mono text-[11px] shrink-0">
                      默认值: {p.defaultVal}
                    </div>
                  </div>
                ))}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
                <div>
                  <div className="font-bold text-slate-800 mb-1">输入协议 Input Schema</div>
                  <pre className="bg-slate-900 text-emerald-400 p-3 rounded-xl font-mono text-[11px]">{op.inputSchema}</pre>
                </div>
                <div>
                  <div className="font-bold text-slate-800 mb-1">输出协议 Output Schema</div>
                  <pre className="bg-slate-900 text-emerald-400 p-3 rounded-xl font-mono text-[11px]">{op.outputSchema}</pre>
                </div>
              </div>
            </div>
          )}

        </div>

      </div>
    </div>
  );
};
