import { describe, expect, it } from "vitest";
import {
  slugifyTopicTitle,
  topicArchiveCommand,
  topicCloseCommand,
  topicCommentCommand,
  topicCreateCommand,
  topicMigrateCommand,
} from "./topicFsCommands";

describe("topicFsCommands", () => {
  it("slugifies ascii titles and drops other scripts", () => {
    expect(slugifyTopicTitle(" Hello World ")).toBe("hello-world");
    expect(slugifyTopicTitle("一条讨论")).toBe("");
  });

  it("builds create command with quoted title", () => {
    expect(topicCreateCommand('say "hi"', "demo")).toBe(
      'map --persona host topic create --title "say \\"hi\\"" --slug demo',
    );
  });

  it("uses placeholders when slug is missing", () => {
    expect(topicCommentCommand("")).toContain("--topic <slug>");
    expect(topicCloseCommand("demo")).toContain("--topic demo");
    expect(topicArchiveCommand("demo")).toBe("map --persona host topic archive --topic demo");
    expect(topicMigrateCommand("abc-id")).toContain("--id abc-id");
  });
});
