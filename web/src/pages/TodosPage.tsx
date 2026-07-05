import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  dismissAllMentions,
  dismissMention,
  dismissTopic,
  fetchWork,
} from "../api/client";
import { PhaseBadge } from "../components/PhaseStepper";
import { AgentBadge } from "../components/AgentBadge";
import { withCommentAnchor } from "../utils/commentAnchor";
import { blockedOnMessage } from "../utils/experimentCapabilities";
import { sumTodos } from "../utils/todoCount";
import { formatElapsed, wakeBadge } from "../utils/actionItemWake";

export function TodosPage() {
  const queryClient = useQueryClient();
  const { data: work, isLoading } = useQuery({
    queryKey: ["work"],
    queryFn: fetchWork,
    refetchInterval: 120_000,
  });

  const dismissOne = useMutation({
    mutationFn: (mentionId: string) => dismissMention(mentionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["work"] }),
  });

  const dismissMany = useMutation({
    mutationFn: () => dismissAllMentions(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["work"] }),
  });

  const dismissOneTopic = useMutation({
    mutationFn: (topicId: string) => dismissTopic(topicId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["work"] }),
  });

  if (isLoading) return <p className="text-slate-400">加载待办…</p>;
  if (!work) return null;

  const data = work.todos;

  const empty = sumTodos(data) === 0;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-white">待办</h1>
      {empty && <p className="text-slate-500">暂无待办，一切就绪 🎉</p>}

      {data.mentions.length > 0 && (
        <Section
          title={`@提及我（${data.mentions.length}）`}
          action={
            <button
              type="button"
              className="text-xs text-slate-400 hover:text-accent disabled:opacity-50"
              onClick={() => dismissMany.mutate()}
              disabled={dismissMany.isPending}
              title="将所有未处理的 @ 提及标记为已读"
            >
              {dismissMany.isPending ? "清除中…" : "全部清除"}
            </button>
          }
        >
          {data.mentions.map((m) => {
            const baseHref = m.experiment_id
              ? `/experiments/${m.experiment_id}`
              : m.topic_id
                ? `/topics/${m.topic_id}`
                : "/todos";
            // source_id == comment id that triggered the mention (see server
            // mention_service). Attach as anchor so the destination page
            // scrolls/highlights the relevant comment.
            const to = withCommentAnchor(baseHref, m.source_id);
            return (
              <Row key={m.id} to={to}>
                <div>
                  <div className="flex flex-wrap items-center gap-2 text-accent hover:underline">
                    <AgentBadge
                      agentId={m.author_agent_id}
                      fallbackName={m.author_name}
                      compact
                    />
                    <span>提及了你</span>
                  </div>
                  <div className="text-xs text-slate-400">{m.excerpt}</div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-slate-500">
                    {new Date(m.created_at).toLocaleDateString()}
                  </span>
                  <button
                    type="button"
                    className="rounded px-2 py-0.5 text-xs text-slate-400 hover:bg-surface-border hover:text-white disabled:opacity-50"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      dismissOne.mutate(m.id);
                    }}
                    disabled={dismissOne.isPending}
                    title="标记为已读,从待办中移除"
                    aria-label="标记为已读"
                  >
                    ✕
                  </button>
                </div>
              </Row>
            );
          })}
        </Section>
      )}

      {(data.pending_topic_replies?.length ?? 0) > 0 && (
        <Section title={`话题待回复（${data.pending_topic_replies.length}）`}>
          {data.pending_topic_replies.map((r) => (
            <Row key={r.comment_id} to={withCommentAnchor(`/topics/${r.topic_id}`, r.comment_id)}>
              <div>
                <div className="text-accent hover:underline">{r.topic_title}</div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                  <AgentBadge
                    agentId={r.author_agent_id}
                    fallbackName={r.author_name}
                    compact
                  />
                  <span>：{r.excerpt}</span>
                </div>
              </div>
              <span className="text-xs text-slate-500">
                {new Date(r.created_at).toLocaleDateString()}
              </span>
            </Row>
          ))}
        </Section>
      )}

      {(data.pending_round_acks?.length ?? 0) > 0 && (
        <Section title={`Round Summary 待 ack（${data.pending_round_acks.length}）`}>
          {data.pending_round_acks.map((r) => (
            <Row key={`${r.topic_id}:${r.summary_comment_id ?? "pending"}`} to={`/topics/${r.topic_id}`}>
              <div>
                <div className="text-accent hover:underline">{r.topic_title}</div>
                {r.summary_excerpt && (
                  <div className="text-xs text-slate-400">{r.summary_excerpt}</div>
                )}
              </div>
              <span className="text-xs text-slate-500">{r.discussion_round}</span>
            </Row>
          ))}
        </Section>
      )}

      {(data.pending_advance_rounds?.length ?? 0) > 0 && (
        <Section title={`话题待推进轮次（${data.pending_advance_rounds.length}）`}>
          {data.pending_advance_rounds.map((r) => (
            <Row key={r.topic_id} to={`/topics/${r.topic_id}`}>
              <div>
                <div className="text-accent hover:underline">{r.topic_title}</div>
                <div className="text-xs text-slate-400">participant ack 已齐，待 host advance-round</div>
              </div>
              <span className="text-xs text-slate-500">{r.discussion_round}</span>
            </Row>
          ))}
        </Section>
      )}

      {(data.action_items?.length ?? 0) > 0 && (
        <Section title={`待唤醒行动项（${data.action_items.length}）`}>
          {data.action_items.map((item) => {
            const badge = wakeBadge(item);
            const elapsed = formatElapsed(item.first_open_at);
            return (
              <Row key={item.id} to={`/topics/${item.topic_id}`}>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-accent hover:underline">{item.title}</span>
                    <span
                      className={`badge text-[10px] ${badge.className}`}
                      title={badge.title}
                    >
                      {badge.label}
                    </span>
                  </div>
                  <div className="text-xs text-slate-400">
                    {item.topic_title}
                    <span className="mx-2 text-slate-600">·</span>
                    开放 {elapsed}
                    {item.due_at ? (
                      <>
                        <span className="mx-2 text-slate-600">·</span>
                        截止 {new Date(item.due_at).toLocaleDateString()}
                      </>
                    ) : null}
                  </div>
                </div>
              </Row>
            );
          })}
        </Section>
      )}

      {data.pending_plan_revisions.length > 0 && (
        <Section title={`计划待修订（${data.pending_plan_revisions.length}）`}>
          {data.pending_plan_revisions.map((r) => (
            <Row key={r.experiment_id} to={`/experiments/${r.experiment_id}`}>
              <div>
                <div className="text-accent hover:underline">{r.experiment_title}</div>
                <div className="text-xs text-slate-400">
                  v{r.current_plan_version} · {r.open_unreasonable_count} 条 open unreasonable
                </div>
              </div>
              <span className="badge bg-amber-900/40 text-amber-200">待修订 plan</span>
            </Row>
          ))}
        </Section>
      )}

      {data.pending_reviews.length > 0 && (
        <Section title={`待评审（${data.pending_reviews.length}）`}>
          {data.pending_reviews.map((e) => (
            <Row key={e.id} to={`/experiments/${e.id}`}>
              <span className="text-accent hover:underline">{e.title}</span>
              <PhaseBadge phase={e.phase} />
            </Row>
          ))}
        </Section>
      )}

      {data.pending_result_reviews.length > 0 && (
        <Section title={`结果待审批（${data.pending_result_reviews.length}）`}>
          {data.pending_result_reviews.map((e) => (
            <Row key={e.id} to={`/experiments/${e.id}`}>
              <span className="text-accent hover:underline">{e.title}</span>
              <PhaseBadge phase={e.phase} />
            </Row>
          ))}
        </Section>
      )}

      {data.pending_replies.length > 0 && (
        <Section title={`待回复（${data.pending_replies.length}）`}>
          {data.pending_replies.map((r) => (
            <Row key={r.item_id} to={`/experiments/${r.experiment_id}`}>
              <div>
                <div className="text-accent hover:underline">{r.experiment_title}</div>
                <div className="text-xs text-slate-400">{r.content}</div>
              </div>
              <span className="badge bg-amber-900/40 text-amber-200">{r.status}</span>
            </Row>
          ))}
        </Section>
      )}

      {data.my_open_experiments.length > 0 && (
        <Section title={`我发起的实验（${data.my_open_experiments.length}）`}>
          {data.my_open_experiments.map((e) => (
            <Row key={e.id} to={`/experiments/${e.id}`}>
              <span className="text-accent hover:underline">{e.title}</span>
              <div className="flex items-center gap-2">
                {blockedOnMessage(e.blocked_on) && (
                  <span className="badge bg-amber-900/40 text-amber-200" title={e.blocked_on ?? undefined}>
                    {blockedOnMessage(e.blocked_on)}
                  </span>
                )}
                <PhaseBadge phase={e.phase} />
              </div>
            </Row>
          ))}
        </Section>
      )}

      {data.my_open_topics.length > 0 && (
        <Section title={`我发起的话题（${data.my_open_topics.length}，参考 topic-progress）`}>
          {data.my_open_topics.map((t) => (
            <Row key={t.id} to={`/topics/${t.id}`}>
              <span className="text-accent hover:underline">{t.title}</span>
              <div className="flex items-center gap-3">
                <span className="text-xs text-slate-500">{t.comment_count} 评论</span>
                <button
                  type="button"
                  className="rounded px-2 py-0.5 text-xs text-slate-400 hover:bg-surface-border hover:text-white disabled:opacity-50"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    dismissOneTopic.mutate(t.id);
                  }}
                  disabled={dismissOneTopic.isPending}
                  title="从待办中隐藏;有新动态时自动重新出现"
                  aria-label="标记为已处理"
                >
                  ✕
                </button>
              </div>
            </Row>
          ))}
        </Section>
      )}
    </div>
  );
}

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-white">{title}</h2>
        {action}
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function Row({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="flex items-center justify-between gap-3 border-b border-surface-border py-2 last:border-0"
    >
      {children}
    </Link>
  );
}
