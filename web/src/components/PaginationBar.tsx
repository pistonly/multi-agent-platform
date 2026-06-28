interface PaginationBarProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

export function PaginationBar({ page, pageSize, total, onPageChange }: PaginationBarProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const canPrev = page > 1;
  const canNext = page < totalPages;

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-surface-border pt-3 text-sm text-slate-500">
      <span>
        共 {total} 条 · 第 {page}/{totalPages} 页
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          className="btn-secondary py-1 text-xs"
          disabled={!canPrev}
          onClick={() => onPageChange(page - 1)}
        >
          上一页
        </button>
        <button
          type="button"
          className="btn-secondary py-1 text-xs"
          disabled={!canNext}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </button>
      </div>
    </div>
  );
}
