import ReactMarkdown from "react-markdown";

export function StatusMarkdown({ content }: { content: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  );
}
