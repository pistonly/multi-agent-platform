import { useState } from "react";

interface FileBreadcrumbProps {
  path: string;
  className?: string;
}

export function FileBreadcrumb({ path, className = "" }: FileBreadcrumbProps) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    navigator.clipboard.writeText(path).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className={`flex items-center gap-2 text-xs text-slate-500 ${className}`.trim()}>
      <span className="font-mono">{path}</span>
      <button
        type="button"
        className="text-accent hover:underline"
        onClick={handleCopy}
      >
        {copied ? "已复制" : "复制路径"}
      </button>
    </div>
  );
}
