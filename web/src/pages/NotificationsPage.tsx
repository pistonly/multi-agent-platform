import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "../api/client";
import type { Notification } from "../api/types";

function notificationHref(n: Notification): string | null {
  const p = n.payload_json;
  if (!p) return null;
  if (n.event === "agent.mentioned") {
    const expId = p.experiment_id as string | undefined;
    if (expId) return `/experiments/${expId}`;
    const topicId = p.topic_id as string | undefined;
    return topicId ? `/topics/${topicId}` : null;
  }
  if (n.event.startsWith("experiment.") || n.event === "plan.revised" || n.event === "review.submitted") {
    const id = (p.id ?? p.experiment_id) as string | undefined;
    return id ? `/experiments/${id}` : null;
  }
  if (n.event === "comment.created") {
    const id = p.experiment_id as string | undefined;
    return id ? `/experiments/${id}` : null;
  }
  if (n.event.startsWith("topic.")) {
    const id = (p.topic_id ?? p.id) as string | undefined;
    return id ? `/topics/${id}` : null;
  }
  return null;
}

export function NotificationsPage() {
  const queryClient = useQueryClient();
  const queryKey = ["notifications"] as const;

  const query = useQuery({
    queryKey,
    queryFn: () => fetchNotifications({ limit: 100 }),
    refetchInterval: 30_000,
  });

  const markOne = useMutation({
    mutationFn: markNotificationRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  });

  const markAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  });

  if (query.isLoading) return <p className="text-slate-400">加载通知…</p>;
  if (query.error) return <p className="text-red-400">通知加载失败</p>;

  const { items, unread_count } = query.data!;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">通知收件箱</h1>
          <p className="mt-1 text-sm text-slate-400">
            {unread_count > 0 ? `${unread_count} 条未读` : "全部已读"}
          </p>
        </div>
        {unread_count > 0 && (
          <button
            type="button"
            className="btn-secondary"
            disabled={markAll.isPending}
            onClick={() => markAll.mutate()}
          >
            全部标为已读
          </button>
        )}
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-slate-500">暂无通知</p>
      ) : (
        <ul className="space-y-2">
          {items.map((n) => {
            const href = notificationHref(n);
            const unread = !n.read_at;
            return (
              <li
                key={n.id}
                className={`card flex flex-wrap items-start justify-between gap-3 ${unread ? "border-accent/30 bg-accent-muted/10" : ""}`}
              >
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                    <span>{new Date(n.created_at).toLocaleString()}</span>
                    <span className="badge bg-surface text-slate-400">{n.event}</span>
                    {unread && <span className="badge bg-accent-muted text-accent">未读</span>}
                  </div>
                  <p className="text-sm text-slate-200">{n.summary}</p>
                  {href && (
                    <Link to={href} className="mt-1 inline-block text-xs text-accent hover:underline">
                      查看详情 →
                    </Link>
                  )}
                </div>
                {unread && (
                  <button
                    type="button"
                    className="btn-secondary py-1 text-xs"
                    disabled={markOne.isPending}
                    onClick={() => markOne.mutate(n.id)}
                  >
                    标为已读
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
