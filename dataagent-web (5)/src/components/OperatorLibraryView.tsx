import React, { useState } from 'react';
import { useApp } from '../context/AppContext';
import { OperatorSpec, OPERATOR_LEVEL_MAP } from '../types';
import { 
  BookOpen, 
  Search, 
  Plus, 
  Star, 
  Code, 
  CheckCircle2, 
  Clock, 
  Layers, 
  Share2, 
  Lock, 
  Globe, 
  ShieldCheck, 
  ArrowRight,
  Filter,
  Check,
  Tag,
  ThumbsUp,
  AlertCircle
} from 'lucide-react';

export const OperatorLibraryView: React.FC = () => {
  const { 
    operators, 
    currentUser, 
    userFavorites, 
    toggleFavoriteOperator, 
    setSelectedOperatorForDetail,
    setIsUploadModalOpen,
    setIsSearchModalOpen,
    applyForOperatorPromotion,
    reviewOperatorPromotion,
    showToast
  } = useApp();

  const [activeTab, setActiveTab] = useState<'all' | 'personal' | 'public' | 'favorites' | 'pending'>('all');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [filterQuery, setFilterQuery] = useState('');

  const pendingOperators = operators.filter(op => op.reviewStatus === 'pending_review');

  const filteredOperators = operators.filter(op => {
    // Tab filter
    if (activeTab === 'personal') {
      // Personal operators always exist in personal library even after approved & public
      if (op.ownerId !== currentUser.id) return false;
    } else if (activeTab === 'public') {
      if (!op.isPublic) return false;
    } else if (activeTab === 'favorites') {
      if (!userFavorites.includes(op.id)) return false;
    } else if (activeTab === 'pending') {
      if (op.reviewStatus !== 'pending_review') return false;
    }

    // Category filter
    if (selectedCategory !== 'all' && op.category !== selectedCategory) return false;

    // Search query
    if (filterQuery) {
      const q = filterQuery.toLowerCase();
      const matchName = op.name.toLowerCase().includes(q);
      const matchTag = op.tags.some(t => t.toLowerCase().includes(q));
      const matchSummary = op.summary.toLowerCase().includes(q);
      if (!matchName && !matchTag && !matchSummary) return false;
    }

    return true;
  });

  return (
    <div className="space-y-6">
      
      {/* Top Header & Search Bar */}
      <div className="bg-white p-6 rounded-2xl border border-emerald-100 shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <span className="px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800">
              可组合与可评测算子 Asset Registry
            </span>
            <span className="text-xs text-slate-400">已注册算子: {operators.length} 款</span>
          </div>
          <h2 className="text-lg font-bold text-slate-900 mt-1">
            算子能力中心
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            提供已验证的算子列表与评测结论，支持自定义上传与社区晋升。
          </p>
        </div>

        <div className="flex items-center space-x-2">
          {/* Quick Search trigger button */}
          <button
            onClick={() => setIsSearchModalOpen(true)}
            className="flex items-center space-x-2 bg-slate-50 hover:bg-emerald-50 text-slate-700 hover:text-emerald-800 border border-slate-200 px-3.5 py-2 rounded-xl text-xs font-semibold transition-all"
          >
            <Search className="w-3.5 h-3.5 text-slate-400" />
            <span>搜索算子</span>
            <kbd className="px-1.5 py-0.5 text-[10px] bg-white border rounded text-slate-400 font-mono">⌘K</kbd>
          </button>
        </div>
      </div>

      {/* Filter Nav Tabs */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 bg-white p-2.5 rounded-2xl border border-slate-200/80 shadow-2xs">
        
        {/* Main Tabs */}
        <div className="flex items-center space-x-1 bg-slate-100 p-1 rounded-xl">
          <button
            onClick={() => setActiveTab('all')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              activeTab === 'all'
                ? 'bg-white text-emerald-900 font-bold shadow-2xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            全部 ({operators.length})
          </button>

          <button
            onClick={() => setActiveTab('public')}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              activeTab === 'public'
                ? 'bg-white text-emerald-900 font-bold shadow-2xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <Globe className="w-3.5 h-3.5 text-emerald-600" />
            <span>公共库</span>
          </button>

          <button
            onClick={() => setActiveTab('personal')}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              activeTab === 'personal'
                ? 'bg-white text-emerald-900 font-bold shadow-2xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <Lock className="w-3.5 h-3.5 text-amber-600" />
            <span>个人库</span>
          </button>

          <button
            onClick={() => setActiveTab('favorites')}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              activeTab === 'favorites'
                ? 'bg-amber-500 text-white font-bold shadow-2xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <Star className="w-3.5 h-3.5 fill-current" />
            <span>收藏 ({userFavorites.length})</span>
          </button>

          <button
            onClick={() => setActiveTab('pending')}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              activeTab === 'pending'
                ? 'bg-amber-600 text-white font-bold shadow-2xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5 text-amber-500" />
            <span>待审核社区队列</span>
            {pendingOperators.length > 0 && (
              <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-red-500 text-white font-bold animate-pulse">
                {pendingOperators.length}
              </span>
            )}
          </button>
        </div>

        {/* Local Filter Input */}
        <div className="w-full sm:w-64 relative">
          <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
          <input
            type="text"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder="本页筛选..."
            className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-9 pr-3 py-1.5 text-xs text-slate-800 focus:outline-none focus:border-emerald-500"
          />
        </div>

      </div>

      {/* Dedicated Header for Personal Library with New Operator Action Button */}
      {activeTab === 'personal' && (
        <div className="bg-gradient-to-r from-amber-50/80 via-emerald-50/40 to-white p-4.5 rounded-2xl border border-amber-200/70 shadow-2xs flex flex-col sm:flex-row sm:items-center justify-between gap-3 animate-in fade-in duration-200">
          <div className="space-y-0.5">
            <h3 className="text-xs font-bold text-slate-900 flex items-center space-x-1.5">
              <Lock className="w-4 h-4 text-amber-600" />
              <span>我的个人自建算子库 (Personal Asset Library)</span>
            </h3>
            <p className="text-xs text-slate-600">
              这里沉淀了您创建与开发的专有算子。测试验证完善后可点击算子卡片「申请提交至公共库」，查看评审进度。
            </p>
          </div>

          <button
            onClick={() => setIsUploadModalOpen(true)}
            className="flex items-center space-x-1.5 bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded-xl text-xs font-bold shadow-2xs transition-all active:scale-95 shrink-0"
          >
            <Plus className="w-4 h-4" />
            <span>新建个人算子</span>
          </button>
        </div>
      )}

      {/* Dedicated Header for Admin Pending Review Queue */}
      {activeTab === 'pending' && (
        <div className="bg-gradient-to-r from-slate-900 via-slate-800 to-amber-950 p-5 rounded-2xl text-white shadow-md space-y-2 animate-in fade-in duration-200">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center space-x-2">
              <ShieldCheck className="w-5 h-5 text-amber-400" />
              <h3 className="font-bold text-sm">管理员视角：待审核算子上架页面 (Pending Review Audit)</h3>
            </div>
            <span className="text-xs text-amber-300 font-mono bg-amber-950/80 px-2.5 py-0.5 rounded-full border border-amber-700/60">
              所有人的申请都在管理员这里: {pendingOperators.length} 条待审核
            </span>
          </div>
          <p className="text-xs text-slate-300 leading-relaxed max-w-3xl">
            全平台所有人提交的算子公共库晋升申请都在管理员这里进行审核。通过审核后，算子将正式上架至公共社区库，供所有用户调用。
          </p>
        </div>
      )}

      {/* Operators Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filteredOperators.map((op) => {
          const levelInfo = OPERATOR_LEVEL_MAP[op.level];
          const isFav = userFavorites.includes(op.id);
          const isOwner = op.ownerId === currentUser.id;

          return (
            <div
              key={op.id}
              onClick={() => setSelectedOperatorForDetail(op)}
              className="bg-white rounded-2xl border border-slate-200/90 hover:border-emerald-300 p-5 space-y-4 cursor-pointer hover:shadow-md transition-all flex flex-col justify-between group"
            >
              <div className="space-y-3">
                
                {/* Header info */}
                <div className="flex items-start justify-between">
                  <div className="space-y-1">
                    <div className="flex items-center space-x-2 flex-wrap gap-y-1">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${levelInfo.badgeBg} ${levelInfo.badgeText}`}>
                        {op.level} - {levelInfo.title}
                      </span>

                      {op.isPublic ? (
                        <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-100 text-emerald-800 flex items-center space-x-1">
                          <Globe className="w-3 h-3" />
                          <span>公共社区</span>
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-100 text-amber-900 flex items-center space-x-1">
                          <Lock className="w-3 h-3" />
                          <span>个人自建</span>
                        </span>
                      )}
                    </div>

                    <h3 className="font-bold text-slate-900 text-base group-hover:text-emerald-700 transition-colors">
                      {op.name}
                    </h3>
                  </div>

                  {/* Star Favorite Button */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleFavoriteOperator(op.id);
                    }}
                    className={`p-2 rounded-xl border transition-all ${
                      isFav
                        ? 'bg-amber-50 text-amber-500 border-amber-200'
                        : 'bg-slate-50 text-slate-400 border-slate-200 hover:text-amber-500'
                    }`}
                  >
                    <Star className={`w-4 h-4 ${isFav ? 'fill-current' : ''}`} />
                  </button>
                </div>

                {/* Summary */}
                <p className="text-xs text-slate-600 line-clamp-2 leading-relaxed">
                  {op.summary}
                </p>

                {/* Tags */}
                <div className="flex items-center space-x-1.5 flex-wrap gap-y-1">
                  {op.tags.slice(0, 3).map((tag, idx) => (
                    <span key={idx} className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px]">
                      #{tag}
                    </span>
                  ))}
                  {op.tags.length > 3 && (
                    <span className="text-[10px] text-slate-400">+{op.tags.length - 3}</span>
                  )}
                </div>

                {/* Examples count & Task History badge */}
                <div className="bg-emerald-50/60 p-2.5 rounded-xl border border-emerald-100 space-y-1">
                  <div className="flex items-center justify-between text-xs text-emerald-900 font-bold">
                    <span className="flex items-center space-x-1">
                      <BookOpen className="w-3.5 h-3.5 text-emerald-600" />
                      <span>{op.examples.length} 个验证实现样例</span>
                    </span>
                    <span className="text-[11px] text-emerald-700 font-normal">
                      支持过 {op.taskHistory.length} 个任务
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-500 line-clamp-1">
                    最近支持: {op.taskHistory[0]?.taskName || '全量生产清洗'}
                  </p>
                </div>

              </div>

              {/* Card Footer: Promotion status & Detail trigger */}
              <div className="pt-3 border-t border-slate-100 flex items-center justify-between text-xs">
                
                <div className="text-slate-500">
                  <span>归属人: </span>
                  <span className="font-semibold text-slate-800">{op.ownerName.split(' ')[0]}</span>
                </div>

                {/* Promotion Action for Owner or Admin */}
                {!op.isPublic && isOwner && op.reviewStatus !== 'pending_review' && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      applyForOperatorPromotion(op.id);
                    }}
                    className="flex items-center space-x-1 text-emerald-700 hover:text-emerald-900 font-bold hover:underline"
                  >
                    <Share2 className="w-3.5 h-3.5" />
                    <span>申请晋升公共库</span>
                  </button>
                )}

                {op.reviewStatus === 'pending_review' && (
                  <span className="text-[11px] font-semibold text-amber-700 flex items-center space-x-1">
                    <Clock className="w-3 h-3" />
                    <span>公共晋升审核中...</span>
                  </span>
                )}

                {/* Admin Review Action if user is Admin */}
                {currentUser.role === 'admin' && op.reviewStatus === 'pending_review' && (
                  <div className="flex items-center space-x-1">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        reviewOperatorPromotion(op.id, true);
                      }}
                      className="px-2 py-1 bg-emerald-600 text-white rounded text-[10px] font-bold"
                    >
                      通过
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        reviewOperatorPromotion(op.id, false);
                      }}
                      className="px-2 py-1 bg-red-600 text-white rounded text-[10px] font-bold"
                    >
                      退回
                    </button>
                  </div>
                )}

                <div className="flex items-center space-x-1 text-emerald-700 font-bold group-hover:translate-x-1 transition-transform">
                  <span>实现详情</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </div>

              </div>

            </div>
          );
        })}
      </div>

      {/* Empty State */}
      {filteredOperators.length === 0 && (
        <div className="bg-white rounded-2xl border border-slate-200 p-12 text-center space-y-4 my-4 shadow-2xs">
          <div className="w-12 h-12 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center mx-auto text-slate-400">
            {activeTab === 'pending' ? <ShieldCheck className="w-6 h-6 text-amber-500" /> : <Filter className="w-6 h-6" />}
          </div>

          <div className="space-y-1">
            <h3 className="font-bold text-slate-800 text-sm">
              {activeTab === 'pending' ? '当前暂无待审核的算子上架申请' : '未找到符合条件的算子'}
            </h3>
            <p className="text-xs text-slate-500 max-w-md mx-auto">
              {activeTab === 'pending' 
                ? '所有提交公共库申请的算子均会在管理员此页面汇总呈现。您可以点击下方按钮，快捷生成一条模拟审核申请。'
                : '请尝试清空搜索关键字或切换类别筛选条件。'}
            </p>
          </div>

          {activeTab === 'pending' && (
            <button
              onClick={() => {
                // Find a non-public operator or create a mock pending operator
                const unpromoted = operators.find(o => !o.isPublic && o.reviewStatus !== 'pending_review');
                if (unpromoted) {
                  applyForOperatorPromotion(unpromoted.id);
                  showToast(`已成功为「${unpromoted.name}」发起公共库晋升申请！`);
                } else {
                  showToast('模拟成功发起一条用户算子上架申请！');
                }
              }}
              className="inline-flex items-center space-x-1.5 bg-amber-600 hover:bg-amber-700 text-white px-4 py-2 rounded-xl text-xs font-bold shadow-2xs transition-all active:scale-95"
            >
              <Plus className="w-4 h-4" />
              <span>模拟提交一条「开发者/成员算子」上架申请</span>
            </button>
          )}
        </div>
      )}

    </div>
  );
};
