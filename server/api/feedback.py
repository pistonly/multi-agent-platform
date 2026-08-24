"""Platform feedback API — retired in v0.15 M62 (dead-letter box teardown).

全链路废弃定案见话题 v015-feedback-deprecation-design（round2 全票）：
自托管收件人错位（部署者≠上游开发者）+ 替代通道已存在（MAP 话题 /
GitHub issue）。CLI 侧已引导性拒绝（exit 2），此为直连 API 消费者的
第二道门。`platform_feedback` 表与 11 条历史数据只读保留（M58 先例），
人类友好索引 = 实验 m62-feedback-deprecation 清账处置表（log-r1.md）。
"""

from fastapi import APIRouter, Depends, HTTPException, status

from server.api.deps import get_current_agent
from server.domain.models import Agent

feedback_router = APIRouter(prefix="/feedback", tags=["feedback"])

_HINT = (
    "bug / tool improvement → run `map feedback` (generates a prefilled GitHub issue link); "
    "dogfood feedback (inside a MAP project) → ask the host to open a MAP topic. "
    "Historical feedback records are preserved read-only in the platform DB."
)


def _feedback_retired_410(action: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": "feedback_retired",
            "message": f"Platform feedback `{action}` was retired in v0.15 M62 (dead-letter box teardown)",
            "hint": _HINT,
        },
    )


@feedback_router.post("", status_code=status.HTTP_410_GONE, include_in_schema=False)
def submit_feedback(agent: Agent = Depends(get_current_agent)) -> None:
    raise _feedback_retired_410("submit")


@feedback_router.get("", status_code=status.HTTP_410_GONE, include_in_schema=False)
def list_feedback(agent: Agent = Depends(get_current_agent)) -> None:
    raise _feedback_retired_410("list")


@feedback_router.get("/{feedback_id}", status_code=status.HTTP_410_GONE, include_in_schema=False)
def get_feedback(agent: Agent = Depends(get_current_agent)) -> None:
    raise _feedback_retired_410("get")


@feedback_router.patch("/{feedback_id}", status_code=status.HTTP_410_GONE, include_in_schema=False)
def update_feedback(agent: Agent = Depends(get_current_agent)) -> None:
    raise _feedback_retired_410("update")
