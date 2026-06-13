import { memo, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Split streamed markdown into stable top-level blocks (paragraph-ish chunks
 *  separated by blank lines) so completed blocks can be memoized while only
 *  the still-growing tail block re-parses on each incoming token. Blank lines
 *  inside fenced code blocks do NOT split — a fence must stay one block or it
 *  would render as broken half-fences mid-stream. */
export function splitMarkdownBlocks(src: string): string[] {
  const lines = src.split("\n");
  const blocks: string[] = [];
  let cur: string[] = [];
  let fence: string | null = null; // "```" or "~~~" while inside a fence

  for (const line of lines) {
    const m = line.match(/^\s*(`{3,}|~{3,})/);
    if (m) {
      if (fence === null) {
        fence = m[1][0] === "`" ? "```" : "~~~";
      } else if (m[1].startsWith(fence)) {
        fence = null;
      }
    }
    if (fence === null && line.trim() === "") {
      if (cur.length > 0) {
        blocks.push(cur.join("\n"));
        cur = [];
      }
    } else {
      cur.push(line);
    }
  }
  if (cur.length > 0) blocks.push(cur.join("\n"));
  return blocks;
}

/** A single parsed block. memo() makes this render-once for completed blocks:
 *  their content string never changes during the stream, so React skips the
 *  expensive markdown re-parse and only the last block re-renders per token. */
const MarkdownBlock = memo(function MarkdownBlock({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>;
});

/** Live markdown for streaming assistant text. Same prose styling as the
 *  persisted-message rendering in MessageBubble, so the turn doesn't visually
 *  snap when the stream completes and history is refetched. */
export const StreamingMarkdown = memo(function StreamingMarkdown({ content }: { content: string }) {
  const blocks = useMemo(() => splitMarkdownBlocks(content), [content]);
  return (
    <div className="prose prose-chat max-w-none">
      {blocks.map((block, i) => (
        <MarkdownBlock key={i} content={block} />
      ))}
    </div>
  );
});
