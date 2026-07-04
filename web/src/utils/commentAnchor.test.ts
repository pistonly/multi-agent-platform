import { describe, expect, it } from "vitest";
import {
  commentDomId,
  parseCommentAnchor,
  withCommentAnchor,
} from "./commentAnchor";

const UUID = "682125ed-7c34-4291-8dee-c357abccd2bd";
const OTHER_UUID = "dfef307a-f70d-4daf-9cbc-6a7c4b5c51c0";

describe("parseCommentAnchor", () => {
  it("reads ?anchor=<id> from the search string (with leading ?)", () => {
    expect(parseCommentAnchor(`?anchor=${UUID}`, "")).toBe(UUID);
  });

  it("reads ?anchor=<id> from the search string (no leading ?)", () => {
    expect(parseCommentAnchor(`anchor=${UUID}`, "")).toBe(UUID);
  });

  it("reads #comment-<id> from the hash fragment", () => {
    expect(parseCommentAnchor("", `#comment-${UUID}`)).toBe(UUID);
  });

  it("prefers the query string over the hash when both are present", () => {
    expect(parseCommentAnchor(`?anchor=${UUID}`, `#comment-${OTHER_UUID}`)).toBe(UUID);
  });

  it("returns null when neither form is present", () => {
    expect(parseCommentAnchor("", "")).toBeNull();
    expect(parseCommentAnchor("?foo=bar", "#section")).toBeNull();
  });

  it("ignores anchor values with illegal characters", () => {
    expect(parseCommentAnchor("?anchor=hello world", "")).toBeNull();
    expect(parseCommentAnchor("?anchor=<script>", "")).toBeNull();
  });

  it("returns null for empty / blank anchor values", () => {
    expect(parseCommentAnchor("?anchor=", "")).toBeNull();
    expect(parseCommentAnchor("?anchor=%20", "")).toBeNull();
    expect(parseCommentAnchor("", "#comment-")).toBeNull();
  });

  it("returns null when the anchor value is unreasonably long", () => {
    const huge = "a".repeat(200);
    expect(parseCommentAnchor(`?anchor=${huge}`, "")).toBeNull();
  });

  it("supports hash fragments with extra junk after the id", () => {
    expect(parseCommentAnchor("", `#comment-${UUID} extra`)).toBe(UUID);
  });
});

describe("commentDomId", () => {
  it("prefixes the comment id with 'comment-'", () => {
    expect(commentDomId(UUID)).toBe(`comment-${UUID}`);
  });
});

describe("withCommentAnchor", () => {
  it("appends ?anchor=<id> when no anchor exists yet", () => {
    expect(withCommentAnchor(`/topics/abc`, UUID)).toBe(`/topics/abc?anchor=${UUID}#comment-${UUID}`);
  });

  it("preserves existing query parameters and only adds anchor", () => {
    expect(withCommentAnchor(`/topics/abc?page=2`, UUID)).toBe(
      `/topics/abc?page=2&anchor=${UUID}#comment-${UUID}`,
    );
  });

  it("replaces the existing anchor when one is already present", () => {
    expect(withCommentAnchor(`/topics/abc?anchor=${OTHER_UUID}`, UUID)).toBe(
      `/topics/abc?anchor=${UUID}#comment-${UUID}`,
    );
  });

  it("is a no-op when the href already carries the same anchor id", () => {
    expect(withCommentAnchor(`/topics/abc?anchor=${UUID}#comment-${UUID}`, UUID)).toBe(
      `/topics/abc?anchor=${UUID}#comment-${UUID}`,
    );
  });

  it("returns the href unchanged when commentId is falsy or invalid", () => {
    expect(withCommentAnchor(`/topics/abc`, null)).toBe(`/topics/abc`);
    expect(withCommentAnchor(`/topics/abc`, "")).toBe(`/topics/abc`);
    expect(withCommentAnchor(`/topics/abc`, "hello world")).toBe(`/topics/abc`);
  });

  it("round-trips through parseCommentAnchor (click @ -> open URL -> parse)", () => {
    // Scenario (b) of the experiment acceptance criteria: a user clicks an @
    // mention in the todo / notification list, the front-end builds the link
    // via `withCommentAnchor`, and a fresh page load later parses the anchor
    // back via `parseCommentAnchor`.
    const baseHref = `/topics/${OTHER_UUID}`;
    const href = withCommentAnchor(baseHref, UUID);
    expect(href.startsWith(baseHref)).toBe(true);
    const [pathAndSearch, hash = ""] = href.split("#");
    const [path, search = ""] = pathAndSearch.split("?");
    expect(path).toBe(baseHref);
    expect(parseCommentAnchor(search, hash)).toBe(UUID);
  });

  it("builds an href that survives a plain #comment-<id> hash (no query)", () => {
    // Scenario (a) of the experiment acceptance criteria: a user pastes a URL
    // that only carries the hash fragment (the form produced by the hook's
    // hash-mirror fallback).
    const search = "";
    const hash = `#comment-${UUID}`;
    expect(parseCommentAnchor(search, hash)).toBe(UUID);
  });
});
