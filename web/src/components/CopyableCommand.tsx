import { useCallback, useState } from "react";

interface CopyableCommandProps {
  command: string;
  label?: string;
}

/**
 * Full-width shell command with click-to-copy. Used for FS write guidance
 * after topic DB write APIs returned 410.
 */
export function CopyableCommand({ command, label }: CopyableCommandProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = command;
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
  }, [command]);

  return (
    <div className="space-y-1">
      {label && <p className="text-xs text-slate-400">{label}</p>}
      <div className="flex items-start gap-2">
        <code className="block min-w-0 flex-1 overflow-x-auto whitespace-pre rounded bg-black/40 px-2 py-1 font-mono text-xs text-slate-200">
          {command}
        </code>
        <button
          type="button"
          onClick={handleCopy}
          className="shrink-0 text-xs text-slate-400 hover:text-accent"
          aria-label={label ? `复制${label}` : "复制命令"}
        >
          {copied ? "已复制" : "复制"}
        </button>
      </div>
    </div>
  );
}
