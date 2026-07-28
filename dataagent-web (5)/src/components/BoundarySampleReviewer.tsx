import React, { useState } from 'react';
import { useApp } from '../context/AppContext';
import { 
  Check, 
  X, 
  CheckCircle2, 
  XCircle, 
  ChevronLeft, 
  ChevronRight, 
  ShieldAlert, 
  HelpCircle,
  Sparkles,
  ArrowRight
} from 'lucide-react';

interface ReviewSample {
  id: string;
  imageUrl: string;
  title: string;
  slice: string;
  confidenceScore: number;
  detectedIssue: string;
  pipelineDecision: 'FILTER_OUT' | 'KEEP' | 'UNCERTAIN';
  userAuditResult?: 'passed' | 'rejected' | 'skipped';
}

const INITIAL_REVIEW_SAMPLES: ReviewSample[] = [
  {
    id: 'rev-001',
    imageUrl: 'https://images.unsplash.com/photo-1529139574466-a303027c1d8b?auto=format&fit=crop&w=600&q=80',
    title: '边界样本 #1：T恤正面包含疑似品牌英文 logo 字体',
    slice: '夜间雨雾行人切片',
    confidenceScore: 0.48,
    detectedIssue: '算子 op-watermark-01 在置信度 0.48 触发疑似文字水印拦截警告',
    pipelineDecision: 'UNCERTAIN'
  },
  {
    id: 'rev-002',
    imageUrl: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?auto=format&fit=crop&w=600&q=80',
    title: '边界样本 #2：远光灯眩光下的水渍遮挡行人',
    slice: '强反射积水路面',
    confidenceScore: 0.52,
    detectedIssue: '算子 op-aesthetic-03 美学清晰度打分 5.1（临界门槛 5.0）',
    pipelineDecision: 'KEEP'
  },
  {
    id: 'rev-003',
    imageUrl: 'https://images.unsplash.com/photo-1552519507-da3b142c6e3d?auto=format&fit=crop&w=600&q=80',
    title: '边界样本 #3：儿童推车推把被塑料遮阳棚覆盖',
    slice: '极罕见儿童推车难例',
    confidenceScore: 0.39,
    detectedIssue: 'MLLM 开放语义校验判定存在形态不确定遮挡',
    pipelineDecision: 'UNCERTAIN'
  }
];

export const BoundarySampleReviewer: React.FC<{ onComplete: () => void }> = ({ onComplete }) => {
  const { showToast, addAuditLog } = useApp();
  const [samples, setSamples] = useState<ReviewSample[]>(INITIAL_REVIEW_SAMPLES);
  const [currentIndex, setCurrentIndex] = useState<number>(0);

  const currentSample = samples[currentIndex];

  const handleVote = (vote: 'passed' | 'rejected' | 'skipped') => {
    setSamples(prev => prev.map((s, idx) => {
      if (idx === currentIndex) {
        return { ...s, userAuditResult: vote };
      }
      return s;
    }));

    showToast(
      vote === 'passed' ? '标记为合格，保留进入数据集' : 
      vote === 'rejected' ? '标记为不合格，从数据集剔除' : '已跳过此争议样本'
    );

    if (currentIndex < samples.length - 1) {
      setCurrentIndex(prev => prev + 1);
    } else {
      showToast('恭喜！20~50 张边界/争议样本已全部完成审核，已生成正式 ReviewSet！');
      addAuditLog('SUBMIT_REVIEWSET', 'ReviewSet', 'revset-2026-drive', '完成 100% 边界样本确认');
      onComplete();
    }
  };

  const completedCount = samples.filter(s => s.userAuditResult).length;
  const progressPct = Math.round((completedCount / samples.length) * 100);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      
      {/* Top Banner */}
      <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-2xs flex items-center justify-between">
        <div>
          <div className="flex items-center space-x-2">
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-100 text-amber-900">
              HITL 质检
            </span>
          </div>
          <h2 className="text-lg font-bold text-slate-900 mt-1">边界样本质检</h2>
          <p className="text-xs text-slate-500">判断样本是否合格，审核结论用于指导生成 ReviewSet</p>
        </div>

        {/* Progress Bar */}
        <div className="text-right space-y-1">
          <div className="text-xs font-bold text-emerald-800">
            已审核 {completedCount} / {samples.length} ({progressPct}%)
          </div>
          <div className="w-36 h-2 bg-slate-100 rounded-full overflow-hidden border border-slate-200">
            <div
              className="h-full bg-emerald-600 transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      </div>

      {/* Main Inspector Card */}
      {currentSample && (
        <div className="bg-white rounded-2xl border border-emerald-100 shadow-md overflow-hidden grid grid-cols-1 md:grid-cols-12">
          
          {/* Image Display Left */}
          <div className="md:col-span-7 bg-slate-950 p-6 flex flex-col items-center justify-center relative min-h-[360px]">
            <img
              src={currentSample.imageUrl}
              alt={currentSample.title}
              className="max-h-[380px] w-auto object-contain rounded-xl shadow-xl"
            />

            <div className="absolute top-3 left-3 bg-slate-900/80 backdrop-blur-sm text-white px-3 py-1 rounded-lg text-xs font-mono">
              样本 #{currentIndex + 1} / {samples.length}
            </div>

            <div className="absolute bottom-3 right-3 bg-emerald-600 text-white px-2.5 py-1 rounded-lg text-xs font-bold">
              切片: {currentSample.slice}
            </div>
          </div>

          {/* Right Details & Decision Action Controls */}
          <div className="md:col-span-5 p-6 flex flex-col justify-between space-y-6 bg-slate-50/50">
            
            <div className="space-y-4">
              <div>
                <span className="text-[10px] font-bold text-amber-700 uppercase tracking-wider bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
                  争议归因
                </span>
                <h3 className="font-bold text-slate-900 text-sm mt-1.5 leading-snug">
                  {currentSample.title}
                </h3>
              </div>

              <div className="bg-white p-3.5 rounded-xl border border-slate-200 space-y-2 text-xs">
                <div className="flex justify-between text-slate-500">
                  <span>模型/算子置信度:</span>
                  <span className="font-bold text-amber-600">{currentSample.confidenceScore}</span>
                </div>
                <div className="text-slate-700 leading-relaxed bg-slate-50 p-2.5 rounded-lg border border-slate-100">
                  {currentSample.detectedIssue}
                </div>
              </div>

              {currentSample.userAuditResult && (
                <div className="p-3 rounded-xl border bg-emerald-50 text-emerald-900 border-emerald-200 text-xs font-semibold flex items-center space-x-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  <span>您已标记为: {currentSample.userAuditResult.toUpperCase()}</span>
                </div>
              )}
            </div>

            {/* Quick Binary Decision Action Buttons */}
            <div className="space-y-3 pt-4 border-t border-slate-200/80">
              <div className="grid grid-cols-2 gap-3">
                <button
                  onClick={() => handleVote('passed')}
                  className="flex items-center justify-center space-x-2 bg-emerald-600 hover:bg-emerald-700 text-white py-3 px-4 rounded-xl font-bold text-xs shadow-sm transition-all active:scale-95"
                >
                  <Check className="w-4 h-4" />
                  <span>合格 (保留)</span>
                </button>

                <button
                  onClick={() => handleVote('rejected')}
                  className="flex items-center justify-center space-x-2 bg-red-600 hover:bg-red-700 text-white py-3 px-4 rounded-xl font-bold text-xs shadow-sm transition-all active:scale-95"
                >
                  <X className="w-4 h-4" />
                  <span>不合格 (剔除)</span>
                </button>
              </div>

              <button
                onClick={() => handleVote('skipped')}
                className="w-full text-center text-xs text-slate-500 hover:text-slate-800 py-1.5"
              >
                暂时不确定，跳过此样本
              </button>
            </div>

          </div>

        </div>
      )}

      {/* Pagination Controls */}
      <div className="flex items-center justify-between text-xs text-slate-500 pt-2">
        <button
          disabled={currentIndex === 0}
          onClick={() => setCurrentIndex(prev => prev - 1)}
          className="flex items-center space-x-1 disabled:opacity-30 hover:text-emerald-700"
        >
          <ChevronLeft className="w-4 h-4" />
          <span>上一张</span>
        </button>

        <span>键盘快捷键：[A] 合格 | [D] 不合格 | [Space] 跳过</span>

        <button
          disabled={currentIndex === samples.length - 1}
          onClick={() => setCurrentIndex(prev => prev + 1)}
          className="flex items-center space-x-1 disabled:opacity-30 hover:text-emerald-700"
        >
          <span>下一张</span>
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

    </div>
  );
};
