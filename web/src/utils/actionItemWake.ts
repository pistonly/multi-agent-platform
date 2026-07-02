import type { TopicActionItemTodo } from "../api/types";

/**
 * Plan §3 三段式触发阈值（小时）。
 * 必须与 server/services/action_item_service.py 的
 * WAKE_STAGE_THRESHOLDS 保持一致——见该模块的哨兵测试
 * `test_threshold_constants_match_plan_section_three`。
 */
export const WAKE_STAGE_THRESHOLDS_HOURS: readonly (readonly [number, number])[] =
  [
    [1, 24],
    [2, 72],
  ] as const;

export const WAKE_REPEAT_DAYS = 7;
export const WAKE_MAX_COUNT_BEFORE_STALE = 4;

export type WakeStage = "pre-wake" | "stage-1" | "stage-2" | "repeat" | "stale";

export interface WakeBadge {
  label: string;
  className: string;
  title: string;
}

const STAGE_BADGES: Record<number, WakeBadge> = {
  0: {
    label: "未唤醒",
    className: "bg-slate-700 text-slate-200",
    title: "尚未触发任何唤醒",
  },
  1: {
    label: "唤醒 1/4",
    className: "bg-blue-900/60 text-blue-200",
    title: "T+24h 第一次唤醒",
  },
  2: {
    label: "唤醒 2/4",
    className: "bg-blue-900/60 text-blue-200",
    title: "T+72h 第二次唤醒",
  },
  3: {
    label: "唤醒 3/4",
    className: "bg-amber-900/60 text-amber-200",
    title: "7d 间隔重复唤醒",
  },
  4: {
    label: "唤醒 4/4",
    className: "bg-amber-900/60 text-amber-200",
    title: "最后一次唤醒，下次将进入 stale",
  },
};

const STALE_BADGE: WakeBadge = {
  label: "stale · admin notified",
  className: "bg-red-900/60 text-red-200",
  title: "已停止唤醒 assignee，admin 已收到通知",
};

export function isStale(item: Pick<TopicActionItemTodo, "stale_at">): boolean {
  return item.stale_at !== null && item.stale_at !== undefined;
}

/**
 * Badge 仅反映 wake_count + stale_at；first_open_at 的"距今"由 `formatElapsed`
 * 单独渲染（plan §6 把两件事拆开）。
 */
export function wakeBadge(item: TopicActionItemTodo): WakeBadge {
  if (isStale(item)) return STALE_BADGE;
  const stage = STAGE_BADGES[item.wake_count];
  if (stage) return stage;
  // wake_count > 4（理论上 mark_stale 之后不再递增，但 UI 兜底）
  return {
    label: `唤醒 ${item.wake_count}`,
    className: "bg-amber-900/60 text-amber-200",
    title: `wake_count=${item.wake_count}`,
  };
}

/**
 * `first_open_at` 距今的人读文本。`now` 仅测试注入；默认取调用时 wall clock。
 *
 * - < 1h：`${minutes} 分钟`
 * - < 24h：`${hours} 小时`
 * - < 30d：`${days} 天`
 * - 否则：`${days} 天`
 */
export function formatElapsed(
  firstOpenAt: string | null | undefined,
  now: Date = new Date(),
): string {
  if (!firstOpenAt) return "尚未开始计时";
  const opened = new Date(firstOpenAt);
  if (Number.isNaN(opened.getTime())) return "尚未开始计时";
  const diffMs = now.getTime() - opened.getTime();
  if (diffMs < 0) return "刚刚开放";
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 60) return `${Math.max(1, minutes)} 分钟`;
  const hours = Math.floor(diffMs / 3_600_000);
  if (hours < 24) return `${hours} 小时`;
  const days = Math.floor(diffMs / 86_400_000);
  return `${days} 天`;
}
