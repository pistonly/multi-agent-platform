// 前端类型。Pydantic 派生类型由 scripts/gen_types.py 从 schemas.py 自动生成；
// 改 schema 后重跑该脚本并提交更新后的 types.generated.ts。
//
// 勿手改 generated 文件里的字段——它是 schemas.py 的 TS 镜像。本文件只保留：
//  - 前端惯用别名（去 Read 后缀 / Payload 命名），保持现有 import 兼容；
//  - 非派生自 Pydantic 的业务类型（无 schema，手维护）。

// generated 模型以命名空间导入，用于下方定义别名；同时 re-export 全部给消费者。
import type * as Schemas from "./types.generated";
export * from "./types.generated";

// 前端惯用别名（generated 用 schemas 的 class 名，带 Read 后缀；前端历史用简短名）。
export type Project = Schemas.ProjectRead;
export type ExperimentSummary = Schemas.ExperimentSummaryRead;
export type ExperimentDetail = Schemas.ExperimentDetailRead;
export type ExperimentBundle = Schemas.ExperimentBundleRead;
export type PlanVersion = Schemas.PlanVersionRead;
export type Agent = Schemas.AgentRead;
export type Review = Schemas.ReviewRead;
export type ReviewItem = Schemas.ReviewItemRead;
export type Comment = Schemas.CommentRead;
export type ExperimentLog = Schemas.ExperimentLogRead;
export type ProjectStatus = Schemas.ProjectStatusRead;
export type ProjectStatusVersion = Schemas.ProjectStatusVersionRead;
export type GlobalStatus = Schemas.GlobalStatusRead;
export type TopicSummary = Schemas.TopicSummaryRead;
export type TopicComment = Schemas.TopicCommentRead;
export type TopicActionItem = Schemas.TopicActionItemRead;
export type TopicDecision = Schemas.TopicDecisionRead;
export type PendingReply = Schemas.PendingReplyRead;
export type PendingPlanRevision = Schemas.PendingPlanRevisionRead;
export type MentionTodo = Schemas.MentionTodoRead;
export type PendingTopicReplyTodo = Schemas.PendingTopicReplyTodoRead;
export type PendingRoundAckTodo = Schemas.PendingRoundAckTodoRead;
export type PendingAdvanceRoundTodo = Schemas.PendingAdvanceRoundTodoRead;
export type StaleOpenTopicTodo = Schemas.StaleOpenTopicTodoRead;
export type TopicActionItemTodo = Schemas.TopicActionItemTodoRead;
export type ExperimentReviewInformational = Schemas.ExperimentReviewInformationalRead;
export type Notification = Schemas.NotificationRead;
export type NotificationList = Schemas.NotificationListRead;
export type TopicProgressList = Schemas.TopicProgressListRead;
export type PlatformFeedback = Schemas.PlatformFeedbackRead;

// Payload 别名（前端历史用 *Payload 后缀；schemas 用 *Create / *Revise / *Update）。
export type ProjectCreatePayload = Schemas.ProjectCreate;
export type ExperimentCreatePayload = Schemas.ExperimentCreate;
export type TopicCreatePayload = Schemas.TopicCreate;
export type TopicResolvePayload = Schemas.TopicResolve;
export type FeedbackCreatePayload = Schemas.PlatformFeedbackCreate;
export type FeedbackUpdatePayload = Schemas.PlatformFeedbackUpdate;

// --- 非派生自 Pydantic 的业务类型（无 schema，手维护） ---

export type ExperimentBlockedOn =
  | "awaiting_non_creator_review"
  | "awaiting_review_for_current_plan_version"
  | "awaiting_result_approval"
  | "open_unreasonable_item"
  | "awaiting_addressed_item_ack"
  | "none";

export interface NotificationStreamEvent {
  type: "notification.created";
  event: string;
  notification_id: string;
}
