import hashlib
import logging
import secrets
import uuid

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole

logger = logging.getLogger(__name__)

TOKEN_PREFIX_LEN = 8

# T02：legacy（空 prefix / 未回填 sha256）候选集上限，防随机 token 触发
# 服务端 N 次 bcrypt 的 CPU DoS 放大。正常部署 legacy agent 数量远小于此值；
# 命中上限仍未匹配时告警提示运维 reissue token 收敛 legacy 池。
LEGACY_CANDIDATE_LIMIT = 256


def hash_token(token: str) -> str:
    return bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()


def verify_token(token: str, token_hash: str) -> bool:
    return bcrypt.checkpw(token.encode(), token_hash.encode())


def token_sha256(token: str) -> str:
    """sha256(token) 十六进制，用于认证快路径等值索引查找。

    token 由 ``secrets.token_urlsafe(32)`` 生成（256-bit 随机），无字典 /
    暴力破解面，sha256 足够（bcrypt 的慢哈希防暴力场景在此不适用）。
    """
    return hashlib.sha256(token.encode()).hexdigest()


def token_prefix(token: str) -> str:
    return token[:TOKEN_PREFIX_LEN]


def _backfill_token_sha256(db: Session, agent: Agent, digest: str) -> None:
    """Legacy agent 首次成功 bcrypt 登录后回填 sha256，此后走快路径。"""
    agent.api_token_sha256 = digest
    db.commit()


def create_agent(
    db: Session,
    name: str,
    role: AgentRole = AgentRole.agent,
    *,
    project_id: uuid.UUID | None = None,
) -> tuple[Agent, str]:
    if role == AgentRole.agent and project_id is None:
        raise ValueError("project_id is required for role=agent")
    if role == AgentRole.admin:
        project_id = None
    token = secrets.token_urlsafe(32)
    agent = Agent(
        name=name,
        api_token_hash=hash_token(token),
        api_token_prefix=token_prefix(token),
        api_token_sha256=token_sha256(token),
        role=role,
        project_id=project_id,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent, token


def get_agent_by_token(db: Session, token: str) -> Agent | None:
    """Resolve an agent from its API token.

    快路径（T01）：sha256(token) 唯一索引等值查找，O(微秒)。所有由
    ``create_agent`` / ``bootstrap`` / ``reissue_agent_token`` 签发的 token
    都同时写入 sha256，直接命中。

    慢路径（legacy，T02）：仅对 ``api_token_sha256 IS NULL`` 的行做 bcrypt
    校验——该集合随每次成功登录的惰性回填单调收缩到空。候选集有硬上限，
    防随机 token 触发 N 次 bcrypt 的 CPU DoS 放大。
    """
    if not token:
        return None

    digest = token_sha256(token)
    agent = db.scalar(select(Agent).where(Agent.api_token_sha256 == digest))
    if agent is not None:
        return agent

    legacy_candidates = 0
    if len(token) >= TOKEN_PREFIX_LEN:
        prefix = token_prefix(token)
        prefix_stmt = select(Agent).where(
            Agent.api_token_prefix == prefix,
            Agent.api_token_sha256.is_(None),
        )
        for legacy in db.scalars(prefix_stmt):
            legacy_candidates += 1
            if verify_token(token, legacy.api_token_hash):
                _backfill_token_sha256(db, legacy, digest)
                return legacy

    # Legacy agents created before api_token_prefix migration (empty prefix).
    # 上限截断：超过 LEGACY_CANDIDATE_LIMIT 的 legacy 池不再无条件全量
    # bcrypt——随机 token 每次请求最多触发 LIMIT 次慢哈希。
    legacy_stmt = (
        select(Agent)
        .where(
            Agent.api_token_prefix == "",
            Agent.api_token_sha256.is_(None),
        )
        .limit(LEGACY_CANDIDATE_LIMIT)
    )
    for legacy in db.scalars(legacy_stmt):
        legacy_candidates += 1
        if verify_token(token, legacy.api_token_hash):
            _backfill_token_sha256(db, legacy, digest)
            return legacy

    if legacy_candidates >= LEGACY_CANDIDATE_LIMIT:
        logger.warning(
            "legacy token candidates hit limit (%d) without a match; "
            "consider reissuing tokens for legacy agents to shrink the "
            "bcrypt-only pool (map auth reissue)",
            legacy_candidates,
        )
    return None


def reissue_agent_token(
    db: Session,
    *,
    project_key: str,
    agent_name: str,
) -> tuple[Agent, str]:
    """Reissue an agent's API token, revoking the previous one (M52C).

    Self-service recovery path for a lost ``.map/agents.local.yaml``:
    the ``project_key`` is the proof of project ownership (same trust
    boundary as ``POST /bootstrap``). The old token becomes invalid
    immediately because the stored hash is replaced atomically.

    Raises ``NotFoundError`` (mapped to 404) when the project key or the
    agent name within that project does not exist — the 404 deliberately
    does not reveal which part was wrong.
    """
    from sqlalchemy import select as _select

    from server.domain.models import Project
    from server.services.errors import NotFoundError

    project = db.scalar(_select(Project).where(Project.project_key == project_key))
    if project is None:
        raise NotFoundError(
            f"project_key '{project_key}' not found. "
            "Check .map/config.yaml project_key, or bootstrap with a new key."
        )

    agent = db.scalar(
        _select(Agent).where(Agent.name == agent_name, Agent.project_id == project.id)
    )
    if agent is None:
        raise NotFoundError(
            f"agent '{agent_name}' not found in project '{project_key}'. "
            "Check the agent_name in .map/agents.yaml "
            "(e.g. multi-agent-platform-host)."
        )

    token = secrets.token_urlsafe(32)
    agent.api_token_hash = hash_token(token)
    agent.api_token_prefix = token_prefix(token)
    agent.api_token_sha256 = token_sha256(token)
    db.commit()
    db.refresh(agent)
    return agent, token
