import { useCallback, useState } from "react";

interface CopyableIdProps {
  id: string;
  label?: string;
  /** How many characters to show before the ellipsis. Default: 8. */
  truncateLength?: number;
  /** Show the full UUID instead of truncated. Default: false. */
  full?: boolean;
  className?: string;
}

/**
 * Displays a UUID with a click-to-copy button.
 *
 * Shows a truncated id by default (e.g. ``a1b2c3d4…``) with a small
 * copy icon. On click, copies the full UUID to clipboard and shows a
 * brief "已复制" feedback.
 *
 * Follows the existing project styling pattern:
 * ``font-mono text-xs text-slate-500`` for muted mono text.
 */
export function CopyableId({
  id,
  label,
  truncateLength = 8,
  full = false,
  className = "",
}: CopyableIdProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Fallback for non-secure contexts
      const textarea = document.createElement("textarea");
      textarea.value = id;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      try {
        document.execCommand("copy");
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      } finally {
        document.body.removeChild(textarea);
      }
    }
  }, [id]);

  const display = full ? id : `${id.slice(0, truncateLength)}…`;

  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      {label && <span className="text-slate-500">{label}:</span>}
      <span className="font-mono text-xs text-slate-500" title={id}>
        {display}
      </span>
      <button
        type="button"
        onClick={handleCopy}
        className="text-slate-500 transition-colors hover:text-accent"
        title="复制完整 ID"
        aria-label={`复制 ${label ?? "ID"}`}
      >
        {copied ? (
          <svg className="h-3 w-3" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M13.5 4.5L6 12l-3.5-3.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : (
          <svg className="h-3 w-3" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="5" y="5" width="8" height="8" rx="1.5" />
            <path d="M3 11V3.5A1.5 1.5 0 014.5 2H11" strokeLinecap="round" />
          </svg>
        )}
      </button>
      {copied && <span className="text-xs text-emerald-400">已复制</span>}
    </span>
  );
}
