import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchTodos } from "../api/client";
import { PhaseBadge } from "../components/PhaseStepper";

export function TodosPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["todos"],
    queryFn: fetchTodos,
    refetchInterval: 120_000,
  });

  if (isLoading) return <p className="text-slate-400">加载待办…</p>;
  if (!data) return null;

  const empty =
    data.my_open_experiments.length === 0 &&
    data.pending_reviews.length === 0 &&
    data.pending_replies.length === 0 &&
    (data.pending_topic_replies?.length ?? 0) === 0 &&
    (data.action_items?.length ?? 0) === 0 &&
    data.my_open_topics.length === 0 &&
    data.mentions.length === 0;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-white">待办</h1>
      {empty && <p className="text-slate-500">暂无待办，一切就绪 🎉</p>}

      {data.mentions.length > 0 && (
        <Section title={`@提及我（${data.mentions.length}）`}>
          {data.mentions.map((m) => {
            const to = m.experiment_id
              ? `/experiments/${m.experiment_id}`
              : m.topic_id
                ? `/topics/${m.topic_id}`
                : "/todos";
            return (
              <Row key={m.id} to={to}>
                <div>
                  <div className="text-accent hover:underline">
                    {m.author_name ?? m.author_agent_id.slice(0, 8)} 提及了你
                  </div>
                  <div className="text-xs text-slate-400">{m.excerpt}</div>
                </div>
                <span className="text-xs text-slate-500">
                  {new Date(m.created_at).toLocaleDateString()}
                </span>
              </Row>
            );
          })}
        </Section>
      )}

      {(data.pending_topic_replies?.length ?? 0) > 0 && (
        <Section title={`话题待回复（${data.pending_topic_replies.length}）`}>
          {data.pending_topic_replies.map((r) => (
            <Row key={r.comment_id} to={`/topics/${r.topic_id}`}>
              <div>
                <div className="text-accent hover:underline">{r.topic_title}</div>
                <div className="text-xs text-slate-400">
                  {r.author_name ?? r.author_agent_id.slice(0, 8)}：{r.excerpt}
                </div>
              </div>
              <span className="text-xs text-slate-500">
                {new Date(r.created_at).toLocaleDateString()}
              </span>
            </Row>
          ))}
        </Section>
      )}

      {(data.action_items?.length ?? 0) > 0 && (
        <Section title={`行动项（${data.action_items.length}）`}>
          {data.action_items.map((item) => (
            <Row key={item.id} to={`/topics/${item.topic_id}`}>
              <div>
                <div className="text-accent hover:underline">{item.title}</div>
                <div className="text-xs text-slate-400">{item.topic_title}</div>
              </div>
              <div className="text-right text-xs text-slate-500">
                {item.due_at ? `截止 ${new Date(item.due_at).toLocaleDateString()}` : item.status}
              </div>
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
              <PhaseBadge phase={e.phase} />
            </Row>
          ))}
        </Section>
      )}

      {data.my_open_topics.length > 0 && (
        <Section title={`我发起的话题（${data.my_open_topics.length}）`}>
          {data.my_open_topics.map((t) => (
            <Row key={t.id} to={`/topics/${t.id}`}>
              <span className="text-accent hover:underline">{t.title}</span>
              <span className="text-xs text-slate-500">{t.comment_count} 评论</span>
            </Row>
          ))}
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card">
      <h2 className="mb-3 text-lg font-semibold text-white">{title}</h2>
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
