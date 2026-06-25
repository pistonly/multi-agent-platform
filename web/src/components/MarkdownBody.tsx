import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownBodyProps {
  content: string;
  className?: string;
}

export function MarkdownBody({ content, className = "" }: MarkdownBodyProps) {
  return (
    <div className={`markdown-body overflow-x-auto ${className}`.trim()}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}
