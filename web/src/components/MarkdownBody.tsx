import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MentionHighlight } from "./MentionHighlight";

interface MarkdownBodyProps {
  content: string;
  className?: string;
}

const markdownComponents: Components = {
  // Code / pre nodes own their children; plain `text` nodes skip fenced & inline code.
  text: ({ children }) =>
    typeof children === "string" ? <MentionHighlight text={children} /> : children,
};

export function MarkdownBody({ content, className = "" }: MarkdownBodyProps) {
  return (
    <div className={`markdown-body overflow-x-auto ${className}`.trim()}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
