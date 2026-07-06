import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { extractMentionNames } from "./mentionParse";

const repoFixtures = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../tests/fixtures",
);

describe("extractMentionNames", () => {
  it("skips inline code", () => {
    const body = "工具 `pytest` 与 `@host` 不应匹配";
    expect(extractMentionNames(body)).toEqual([]);
  });

  it("skips fenced code", () => {
    const body = "```\n@multi-agents-platform-host\n`@host`\n```\n块外 @foo-bar";
    expect(extractMentionNames(body)).toEqual(["foo-bar"]);
  });

  it("finds mentions outside code", () => {
    const body = "请 @reviewer-agent 参与";
    expect(extractMentionNames(body)).toEqual(["reviewer-agent"]);
  });

  it("golden comment_seq=4 fixture has no mentions", () => {
    const body = readFileSync(join(repoFixtures, "mention_comment_seq_4.md"), "utf-8");
    expect(extractMentionNames(body)).toEqual([]);
  });

  it("golden comment_seq=5 fixture has no mentions", () => {
    const body = readFileSync(join(repoFixtures, "mention_comment_seq_5.md"), "utf-8");
    expect(extractMentionNames(body)).toEqual([]);
  });
});
