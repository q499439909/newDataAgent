import React, { useState } from 'react';
import { useApp } from '../context/AppContext';
import { X, Sparkles, MessageSquarePlus, ArrowRight, Bot, Zap, PlusCircle } from 'lucide-react';

interface NewWorkOrderModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: () => void;
}

export const NewWorkOrderModal: React.FC<NewWorkOrderModalProps> = ({
  isOpen,
  onClose,
  onCreated,
}) => {
  const { createNewWorkOrder, showToast } = useApp();
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!isOpen) return null;

  const handleInstantCreate = async (
    title: string = '新需求对话工单',
    desc: string = '已开启 Agent 交互，通过自然语言生成 TaskSpec'
  ) => {
    setIsSubmitting(true);
    try {
      await createNewWorkOrder(title, desc);
      showToast(`已成功创建对话工单「${title}」，正在为您跳转与 Agent 交互！`);
      onCreated();
      onClose();
    } catch (err) {
      console.error(err);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="bg-white border border-slate-200 text-slate-800 w-full max-w-lg rounded-3xl shadow-2xl overflow-hidden flex flex-col">
        
        {/* Modal Header */}
        <div className="p-5 border-b border-slate-200 flex items-center justify-between bg-slate-50">
          <div className="flex items-center space-x-2.5">
            <div className="p-2 rounded-xl bg-emerald-50 text-emerald-700 border border-emerald-200">
              <MessageSquarePlus className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-bold text-base text-slate-900">新建 Agent 对话工单</h3>
              <p className="text-xs text-slate-500">无需手动填写复杂表单，直接与 Agent 对话生成</p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-2 rounded-xl text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 space-y-5 text-xs text-slate-600">
          
          {/* Main Action Banner */}
          <div className="p-5 rounded-2xl bg-gradient-to-br from-emerald-50 to-teal-50 border border-emerald-200/80 space-y-3.5">
            <div className="flex items-start space-x-3">
              <div className="p-2.5 rounded-xl bg-emerald-600 text-white shrink-0 shadow-md shadow-emerald-600/20">
                <Bot className="w-6 h-6" />
              </div>
              <div className="space-y-1">
                <h4 className="font-bold text-sm text-slate-900">自然语言交互生成 TaskSpec</h4>
                <p className="text-slate-600 text-[11px] leading-relaxed">
                  开启对话后，您可以直接向数据任务规划主 Agent 表达数据源、处理目标、脱敏格式和验收标准；主 Agent 会生成 TaskSpec，并调度专业 Agent 完成候选 Pipeline 规划。
                </p>
              </div>
            </div>

            <button
              onClick={() => handleInstantCreate('新需求对话工单', '用户通过对话交互直接生成 TaskSpec')}
              disabled={isSubmitting}
              className="w-full py-3 px-4 rounded-xl bg-emerald-600 hover:bg-emerald-500 active:scale-[0.99] text-white font-bold text-xs flex items-center justify-center space-x-2 shadow-md shadow-emerald-600/20 transition-all cursor-pointer disabled:opacity-50"
            >
              <PlusCircle className="w-4 h-4" />
              <span>{isSubmitting ? '正在创建对话...' : '一键新建对话工单 (立即开始)'}</span>
            </button>
          </div>

          {/* Quick Scenario Options */}
          <div className="space-y-2">
            <label className="text-slate-500 font-bold text-[11px] uppercase tracking-wider block flex items-center space-x-1">
              <Zap className="w-3.5 h-3.5 text-emerald-600" />
              <span>常用场景方向 (点击直接开启相关对话)</span>
            </label>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              <button
                type="button"
                onClick={() => handleInstantCreate('具身智能 RGB-D 数据工单', '清洗与对齐机械臂多角度 RGB-D 深度图与 3D 点云')}
                className="p-3 rounded-xl bg-slate-50 hover:bg-emerald-50/60 border border-slate-200 hover:border-emerald-300 text-left transition-all group cursor-pointer"
              >
                <div className="font-bold text-slate-800 group-hover:text-emerald-900">🤖 具身智能 3D 抓取数据</div>
                <div className="text-slate-500 text-[10px] mt-0.5">3D 点云与机械臂抓取对齐</div>
              </button>

              <button
                type="button"
                onClick={() => handleInstantCreate('医疗多模态问答脱敏工单', '过滤 50,000 份包含患者隐秘信息的医学报告与伪影')}
                className="p-3 rounded-xl bg-slate-50 hover:bg-emerald-50/60 border border-slate-200 hover:border-emerald-300 text-left transition-all group cursor-pointer"
              >
                <div className="font-bold text-slate-800 group-hover:text-emerald-900">🩺 医疗多模态问答脱敏</div>
                <div className="text-slate-500 text-[10px] mt-0.5">隐私脱敏与图文报告对齐</div>
              </button>

              <button
                type="button"
                onClick={() => handleInstantCreate('自动驾驶雨夜高召回工单', '自动清洗标注雨夜极低对比度环境中的关键行人样本')}
                className="p-3 rounded-xl bg-slate-50 hover:bg-emerald-50/60 border border-slate-200 hover:border-emerald-300 text-left transition-all group cursor-pointer"
              >
                <div className="font-bold text-slate-800 group-hover:text-emerald-900">🚗 自动驾驶雨夜难例切片</div>
                <div className="text-slate-500 text-[10px] mt-0.5">极端漫反射与低对比度过滤</div>
              </button>

              <button
                type="button"
                onClick={() => handleInstantCreate('通用视觉多模态清洗工单', '人脸生物特征去标识化与版权水印水印智能清洗')}
                className="p-3 rounded-xl bg-slate-50 hover:bg-emerald-50/60 border border-slate-200 hover:border-emerald-300 text-left transition-all group cursor-pointer"
              >
                <div className="font-bold text-slate-800 group-hover:text-emerald-900">🖼️ 通用视觉水印人脸脱敏</div>
                <div className="text-slate-500 text-[10px] mt-0.5">版权水印与生物识别去标识</div>
              </button>
            </div>
          </div>

        </div>

        {/* Modal Footer */}
        <div className="p-4 border-t border-slate-200 bg-slate-50 flex items-center justify-between text-[11px] text-slate-500">
          <div className="flex items-center space-x-1">
            <Bot className="w-3.5 h-3.5 text-emerald-600" />
            <span>数据任务规划 Agent（主 Agent）实时在线待命</span>
          </div>
          <button
            onClick={onClose}
            className="px-3.5 py-1.5 rounded-lg text-slate-600 hover:bg-slate-200/80 transition-colors font-medium cursor-pointer"
          >
            取消
          </button>
        </div>

      </div>
    </div>
  );
};
