/** CLI command strings for FS topic writes (Web is read-only after M58). */

export function slugifyTopicTitle(title: string): string {
  return title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function shellQuote(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

export function topicCreateCommand(title: string, slug: string): string {
  const safeTitle = title.trim() || "<title>";
  const safeSlug = slug.trim() || "<slug>";
  return `map --persona host fs topic-create --title ${shellQuote(safeTitle)} --slug ${safeSlug}`;
}

export function topicCommentCommand(slug: string): string {
  const safeSlug = slug.trim() || "<slug>";
  return `map --persona host fs comment --topic ${safeSlug} --body "..."`;
}

export function topicCloseCommand(slug: string): string {
  const safeSlug = slug.trim() || "<slug>";
  return `map --persona host fs close --topic ${safeSlug} --reason <code> --note "..."`;
}

export function topicArchiveCommand(slug: string): string {
  const safeSlug = slug.trim() || "<slug>";
  return `map --persona host fs archive --topic ${safeSlug}`;
}

export function topicMigrateCommand(topicId: string): string {
  return `map --persona host topic migrate --id ${topicId} --slug <slug>`;
}
