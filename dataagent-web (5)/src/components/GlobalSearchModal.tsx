import React, { useState, useMemo } from 'react';
import { useApp } from '../context/AppContext';
import { OPERATOR_LEVEL_MAP, OperatorSpec } from '../types';
import { 
  Search, 
  X, 
  BookOpen, 
  Code, 
  Copy, 
  Check, 
  Sparkles, 
  Star, 
  Layers, 
  FileText,
  Tag,
  ArrowRight
} from 'lucide-react';

export const GlobalSearchModal: React.FC = () => {
  const { 
    isSearchModalOpen, 
    setIsSearchModalOpen, 
    operators, 
    setSelectedOperatorForDetail,
    showToast,
    toggleFavoriteOperator,
    userFavorites
  } = useApp();

  const [searchTerm, setSearchTerm] = useState('');
  const [selectedFilter, setSelectedFilter] = useState<string>('all');
  const [copiedCodeId, setCopiedCodeId] = useState<string | null>(null);

  const filteredOperators = useMemo(() => {
    return operators.filter((op) => {
      const matchesSearch = 
        op.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        op.summary.toLowerCase().includes(searchTerm.toLowerCase()) ||
        op.tags.some(t => t.toLowerCase().includes(searchTerm.toLowerCase())) ||
        (op.codeSnippets.python && op.codeSnippets.python.toLowerCase().includes(searchTerm.toLowerCase()));

      if (!matchesSearch) return false;

      if (selectedFilter === 'all') return true;
      if (selectedFilter === 'vision') return op.modality === 'vision';
      if (selectedFilter === 'multimodal') return op.modality === 'multimodal';
      if (selectedFilter === 'l3') return op.level === 'L3';
      if (selectedFilter === 'l2') return op.level === 'L2';
      if (selectedFilter === 'favorites') return userFavorites.includes(op.id);
      return true;
    });
  }, [operators, searchTerm, selectedFilter, userFavorites]);

  if (!isSearchModalOpen) return null;

  const handleCopyCode = (code: string, id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(code);
    setCopiedCodeId(id);
    showToast('算子 Python 代码段已复制到剪贴板！');
    setTimeout(() => setCopiedCodeId(null), 2000);
  };

  const handleSelectOp = (op: OperatorSpec) => {
    setSelectedOperatorForDetail(op);
    setIsSearchModalOpen(false);
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center pt-16 px-4 animate-in fade-in duration-200">
      <div className="bg-white rounded-2xl shadow-2xl border border-emerald-100 w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden">
        
        {/* Search Bar Header */}
        <div className="p-4 border-b border-slate-100 flex items-center space-x-3 bg-slate-50/50">
          <Search className="w-5 h-5 text-emerald-600 shrink-0" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="快捷查询特定算子详细文档、参数定义与代码案例..."
            className="w-full bg-transparent text-sm text-slate-800 focus:outline-none placeholder:text-slate-400 font-medium"
            autoFocus
          />
          {searchTerm && (
            <button
              onClick={() => setSearchTerm('')}
              className="text-slate-400 hover:text-slate-600 p-1"
            >
              <X className="w-4 h-4" />
            </button>
          )}
          <button
            onClick={() => setIsSearchModalOpen(false)}
            className="text-slate-400 hover:text-slate-700 text-xs px-2 py-1 rounded-md border border-slate-200"
          >
            ESC
          </button>
        </div>

        {/* Quick Filter Tags */}
        <div className="px-4 py-2.5 bg-white border-b border-slate-100 flex items-center space-x-2 overflow-x-auto text-xs">
          <span className="text-slate-400 font-medium shrink-0 flex items-center space-x-1">
            <Tag className="w-3 h-3" />
            <span>筛选:</span>
          </span>

          <button
            onClick={() => setSelectedFilter('all')}
            className={`px-2.5 py-1 rounded-lg transition-colors ${
              selectedFilter === 'all'
                ? 'bg-emerald-600 text-white font-medium shadow-2xs'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            全部 ({operators.length})
          </button>

          <button
            onClick={() => setSelectedFilter('vision')}
            className={`px-2.5 py-1 rounded-lg transition-colors ${
              selectedFilter === 'vision'
                ? 'bg-emerald-600 text-white font-medium shadow-2xs'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            视觉 Vision
          </button>

          <button
            onClick={() => setSelectedFilter('multimodal')}
            className={`px-2.5 py-1 rounded-lg transition-colors ${
              selectedFilter === 'multimodal'
                ? 'bg-emerald-600 text-white font-medium shadow-2xs'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            多模态 Multimodal
          </button>

          <button
            onClick={() => setSelectedFilter('l3')}
            className={`px-2.5 py-1 rounded-lg transition-colors ${
              selectedFilter === 'l3'
                ? 'bg-emerald-600 text-white font-medium shadow-2xs'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            L3 多模态大模型算子
          </button>

          <button
            onClick={() => setSelectedFilter('favorites')}
            className={`px-2.5 py-1 rounded-lg transition-colors flex items-center space-x-1 ${
              selectedFilter === 'favorites'
                ? 'bg-amber-500 text-white font-medium shadow-2xs'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            <Star className="w-3 h-3 fill-current" />
            <span>我的收藏 ({userFavorites.length})</span>
          </button>
        </div>

        {/* Search Results List */}
        <div className="p-4 overflow-y-auto space-y-3 flex-1 divide-y divide-slate-100">
          {filteredOperators.length === 0 ? (
            <div className="py-12 text-center text-slate-400 text-sm">
              <BookOpen className="w-8 h-8 mx-auto mb-2 text-slate-300" />
              未检索到匹配的算子案例或代码片段
            </div>
          ) : (
            filteredOperators.map((op) => {
              const levelInfo = OPERATOR_LEVEL_MAP[op.level];
              const isFav = userFavorites.includes(op.id);

              return (
                <div
                  key={op.id}
                  onClick={() => handleSelectOp(op)}
                  className="pt-3 first:pt-0 group cursor-pointer hover:bg-emerald-50/40 p-3 rounded-xl transition-all border border-transparent hover:border-emerald-200"
                >
                  <div className="flex items-start justify-between">
                    
                    <div className="space-y-1.5 flex-1 pr-4">
                      {/* Name & Badges */}
                      <div className="flex items-center space-x-2 flex-wrap gap-y-1">
                        <span className="font-semibold text-slate-900 group-hover:text-emerald-700 text-sm">
                          {op.name}
                        </span>

                        <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${levelInfo.badgeBg} ${levelInfo.badgeText}`}>
                          {op.level} - {levelInfo.title}
                        </span>

                        <span className="px-2 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600">
                          {op.modality}
                        </span>

                        {op.isPublic ? (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-emerald-100 text-emerald-800">
                            公共社区库
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-amber-100 text-amber-800">
                            个人自建库
                          </span>
                        )}
                      </div>

                      {/* Summary */}
                      <p className="text-xs text-slate-600 line-clamp-2 leading-relaxed">
                        {op.summary}
                      </p>

                      {/* Tags & Examples count */}
                      <div className="flex items-center space-x-3 text-[11px] text-slate-400 pt-1">
                        <span className="flex items-center space-x-1 text-emerald-700 font-medium">
                          <BookOpen className="w-3 h-3" />
                          <span>{op.examples.length} 个验证实现样例</span>
                        </span>
                        <span>•</span>
                        <span>p95 延迟: {op.p95LatencyMs}ms</span>
                        <span>•</span>
                        <span>引用量: {op.usageCount} 次</span>
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center space-x-2 shrink-0">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleFavoriteOperator(op.id);
                        }}
                        className={`p-1.5 rounded-lg border transition-colors ${
                          isFav
                            ? 'bg-amber-50 text-amber-600 border-amber-200'
                            : 'bg-white text-slate-400 border-slate-200 hover:text-amber-500'
                        }`}
                        title="收藏算子"
                      >
                        <Star className={`w-3.5 h-3.5 ${isFav ? 'fill-current' : ''}`} />
                      </button>

                      {op.codeSnippets.python && (
                        <button
                          onClick={(e) => handleCopyCode(op.codeSnippets.python!, op.id, e)}
                          className="flex items-center space-x-1 bg-slate-100 hover:bg-emerald-100 text-slate-700 hover:text-emerald-800 px-2.5 py-1 rounded-lg text-xs font-medium transition-colors"
                          title="复制 Python 示例代码"
                        >
                          {copiedCodeId === op.id ? (
                            <Check className="w-3.5 h-3.5 text-emerald-600" />
                          ) : (
                            <Code className="w-3.5 h-3.5" />
                          )}
                          <span className="hidden sm:inline">复制代码</span>
                        </button>
                      )}

                      <div className="p-1 text-slate-400 group-hover:text-emerald-600">
                        <ArrowRight className="w-4 h-4" />
                      </div>
                    </div>

                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer info */}
        <div className="p-3 bg-slate-50 border-t border-slate-100 text-[11px] text-slate-500 flex items-center justify-between">
          <span>提示：按 ↑ ↓ 键切换，按 Enter 打开算子全量实现样例与参数文档</span>
          <span className="font-semibold text-emerald-700">DataAgent 算子支持库 ({operators.length} 款注册算子)</span>
        </div>

      </div>
    </div>
  );
};
