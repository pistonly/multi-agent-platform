import { MarkdownBody } from "./MarkdownBody";

export function StatusMarkdown({ content }: { content: string }) {
  return <MarkdownBody content={content} />;
}
