import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StreamingMarkdown, splitMarkdownBlocks } from "../components/StreamingMarkdown";

describe("splitMarkdownBlocks", () => {
  it("splits paragraphs on blank lines", () => {
    expect(splitMarkdownBlocks("para one\n\npara two\n\npara three")).toEqual([
      "para one",
      "para two",
      "para three",
    ]);
  });

  it("keeps a fenced code block with internal blank lines as one block", () => {
    const src = "intro\n\n```python\na = 1\n\nb = 2\n```\n\noutro";
    expect(splitMarkdownBlocks(src)).toEqual([
      "intro",
      "```python\na = 1\n\nb = 2\n```",
      "outro",
    ]);
  });

  it("treats an unterminated fence (still streaming) as one open block", () => {
    const src = "text\n\n```sh\necho hi\n\necho bye";
    expect(splitMarkdownBlocks(src)).toEqual(["text", "```sh\necho hi\n\necho bye"]);
  });

  it("handles tilde fences and empty input", () => {
    expect(splitMarkdownBlocks("~~~\nx\n\ny\n~~~")).toEqual(["~~~\nx\n\ny\n~~~"]);
    expect(splitMarkdownBlocks("")).toEqual([]);
  });
});

describe("StreamingMarkdown", () => {
  it("renders markdown blocks as rich text", () => {
    render(<StreamingMarkdown content={"# Title\n\nSome **bold** text"} />);
    expect(screen.getByRole("heading", { name: "Title" })).toBeInTheDocument();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
  });
});
