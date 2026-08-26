"""End-to-end collaboration scenario driver (demo / E2E validation).

Drives a scripted host → participant → host → ... → reviewer flow by spawning
the per-persona Agent runtime (``PersonaAgentClient`` with
``integration="manual"``) and sending one turn per step. Each step's prompt
tells the agent what to do; the agent reads the relevant Skill under
``.cursor/skills/`` and uses ``map --persona <name>`` CLI to act. The
orchestrator never mutates MAP state directly — it only reads MAP (via
``MapCommandClient``) between steps to discover ``topic_id`` / ``experiment_id``
and to branch when a decision step picks the "no" path.

This is a demo / E2E entry, NOT a replacement for ``simple-waker``. The waker
remains the default production path (reactive, polled, multi-topic); this
script is a linear, single-scenario conductor for end-to-end validation and
demos. Architecture boundaries from AGENTS.md still hold:

  - waker / orchestrator = conductor (no business judgment)
  - Skill = behavior definition
  - spawned Agent = actor (reads Skill, uses `map` CLI, writes back to MAP)
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from cli.agent_client import PersonaAgentClient, WakeUpEvent
from cli.errors import WorkerError
from cli.map_command_client import MapCommandClient
from cli.runtime_chat import (
    default_runtime_home,
    default_state_file,
    ensure_waker_not_running,
    load_runtime_state,
    persona_agent_state,
    resolve_session_id,
)
from cli.wake_backend import sync_runtime_skills

e2e_app = typer.Typer(help="End-to-end collaboration scenario driver (demo / E2E)")

PERSONAS: tuple[str, ...] = ("host", "participant", "reviewer")

_DEFAULT_TOPIC_TITLE = "E2E demo: collaboration lifecycle"
_DEFAULT_SUBJECT = (
    "Validate that host / participant / reviewer personas can drive a topic "
    "through discussion -> experiment -> plan review -> execution -> result "
    "verification -> closure using the map CLI and Skills."
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    name: str
    persona: str
    status: str
    text: str


@dataclass
class Scenario:
    subject: str
    topic_title: str
    project_root: Path
    run_dir: Path
    plan_file: Path
    log_file: Path
    new_session: bool = False
    model: str | None = None
    topic_id: str | None = None
    topic_slug: str | None = None
    experiment_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _echo(msg: str = "") -> None:
    typer.echo(msg)


def _echo_err(msg: str) -> None:
    typer.echo(msg, err=True)


def build_prompt(persona: str, body: str) -> str:
    """Wrap a step body with the standard E2E preamble + trailing summary cue."""
    return (
        f"You are the **{persona}** persona of the MAP (Multi-Agent Platform) "
        "project. An end-to-end collaboration demo is being driven by an "
        "external orchestrator; this turn is ONE step of that demo. "
        f"Use `map --persona {persona}` CLI and the Skills under "
        "`.cursor/skills/` (start with `map-project-collab` "
        "(§ Waker 模式) → your persona Skill: topic-host / "
        "topic-participant / experiment-host / experiment-reviewer). Do not "
        "wait for further prompts within this turn — gather state yourself "
        "via the `map` CLI and act.\n\n"
        f"{body}\n\n"
        "When done, end your turn with a one-line summary starting with "
        "`E2E:` so the orchestrator can record it."
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class E2EDriver:
    def __init__(
        self,
        scenario: Scenario,
        clients: dict[str, PersonaAgentClient],
    ) -> None:
        self.scenario = scenario
        self.clients = clients
        self.map_host = MapCommandClient(persona="host", project_root=scenario.project_root)
        self.steps: list[StepResult] = []

    # ---- single step execution ----

    async def step(self, name: str, persona: str, body: str) -> StepResult:
        _echo(f"\n=== step {len(self.steps) + 1}: {name} (persona={persona}) ===")
        prompt = build_prompt(persona, body)
        text_parts: list[str] = []

        def on_event(ev: WakeUpEvent) -> None:
            if ev.get("type") == "text":
                chunk = str(ev.get("content") or "")
                text_parts.append(chunk)
                sys.stdout.write(chunk)
                sys.stdout.flush()

        status = await self.clients[persona].wake_up(
            prompt, on_event=on_event, event_source="e2e"
        )
        result = StepResult(name=name, persona=persona, status=status, text="".join(text_parts))
        self.steps.append(result)
        _echo("")  # newline after streamed text
        if status != "ok":
            _echo_err(f"[error] step {name} returned status={status}")
        return result

    # ---- read-only state queries between steps ----

    def find_topic_by_title(self) -> str | None:
        rows = self.map_host.topic_list_open()
        for row in rows:
            if str(row.get("title") or "") == self.scenario.topic_title:
                tid = str(row.get("id") or "") or None
                if tid:
                    slug = str(row.get("slug") or "") or None
                    if slug:
                        self.scenario.topic_slug = slug
                    return tid
        return None

    def find_experiment_for_topic(self) -> str | None:
        if not self.scenario.topic_id:
            return None
        rows = self.map_host.experiment_list()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("topic_id") or "") == self.scenario.topic_id:
                eid = str(row.get("id") or "") or None
                if eid:
                    return eid
        return None

    def experiment_phase(self) -> str | None:
        if not self.scenario.experiment_id:
            return None
        data = self.map_host.experiment_status(self.scenario.experiment_id)
        if isinstance(data, dict):
            return str(data.get("phase") or "") or None
        return None

    def topic_status(self) -> str | None:
        if not self.scenario.topic_id:
            return None
        data = self.map_host.topic_show(self.scenario.topic_id)
        if isinstance(data, dict):
            return str(data.get("status") or "") or None
        return None

    # ---- per-step prompt bodies ----

    def _ctx(self) -> str:
        lines = [f"subject: {self.scenario.subject}", f"topic_title: {self.scenario.topic_title}"]
        if self.scenario.topic_id:
            lines.append(f"topic_id: {self.scenario.topic_id}")
        if self.scenario.topic_slug:
            lines.append(f"topic_slug: {self.scenario.topic_slug}")
        if self.scenario.experiment_id:
            lines.append(f"experiment_id: {self.scenario.experiment_id}")
        return "\n".join(lines)

    def _prompt_create_topic(self) -> str:
        return (
            f"{self._ctx()}\n\n"
            "Task: open a new discussion topic on the subject above, using the "
            "exact topic_title above. Use the topic-host Skill and "
            "`map --persona host fs topic-create --title \"<topic_title>\" "
            "--slug <kebab-case-slug> --participants host,participant`. "
            "After creating, confirm the topic_id in your E2E summary line."
        )

    def _prompt_participant_comment(self, round_label: str) -> str:
        return (
            f"{self._ctx()}\n\n"
            f"Task: this is {round_label} of the discussion. Read the topic "
            "thread with `map --persona participant fs show --topic <topic_slug>`, "
            "then post a substantive comment that advances the discussion "
            "(question, counterpoint, or supporting evidence) with "
            "`map --persona participant fs comment --topic <topic_slug> "
            "--body \"...\"`. Use the topic-participant Skill."
        )

    def _prompt_host_reply(self, round_label: str) -> str:
        return (
            f"{self._ctx()}\n\n"
            f"Task: this is {round_label} of the discussion. Read the latest "
            "participant comment with `map --persona host fs show --topic "
            "<topic_slug>`, then reply with `map --persona host fs comment "
            "--topic <topic_slug> --body \"...\"` (threading is carried by "
            "section references inside the file, not --parent). Use the "
            "topic-host Skill."
        )

    def _prompt_host_decide_experiment(self) -> str:
        return (
            f"{self._ctx()}\n\n"
            "Task: make a judgment call. There has been at least one round of "
            "discussion.\n"
            "  Option A — continue the discussion (post another reply).\n"
            "  Option B — open an experiment to test a concrete hypothesis "
            "derived from the discussion.\n\n"
            "For this demo, prefer Option B so the rest of the lifecycle can "
            "be showcased; but make the judgment yourself based on discussion "
            "quality.\n\n"
            "If you choose Option B:\n"
            f"  1. Write a plan markdown file at {self.scenario.plan_file} "
            "(hypothesis, steps, success criteria).\n"
            "  2. `map --persona host experiment create --title \"<title>\" "
            f"--plan-file {self.scenario.plan_file} --topic-id <topic_id> "
            "--submit-for-review`\n"
            "  3. Use the experiment-host Skill (.cursor/skills/experiment-host/SKILL.md).\n"
            "  4. Confirm the experiment_id in your E2E summary line.\n\n"
            "If you choose Option A, just post the reply and say so in your "
            "E2E summary; the orchestrator will end the demo."
        )

    def _prompt_reviewer_review_plan(self) -> str:
        return (
            f"{self._ctx()}\n\n"
            "Task: an experiment plan has been submitted for review. Review it:\n"
            "  1. `map --persona reviewer experiment show --id <experiment_id>` "
            "to read the plan.\n"
            "  2. Use the experiment-reviewer Skill to submit your review via "
            "the appropriate `map --persona reviewer experiment review ...` "
            "command.\n\n"
            "For this demo, prefer 'approve with minor comments' so the flow "
            "can proceed to execution; but make the judgment yourself based on "
            "plan quality."
        )

    def _prompt_host_decide_start(self) -> str:
        return (
            f"{self._ctx()}\n\n"
            "Task: the reviewer has reviewed your plan. Decide the next step:\n"
            "  - If the review is acceptable: first approve the experiment "
            "with `map --persona host experiment approve --id "
            "<experiment_id>` (review submission does not auto-approve), then "
            "start the experiment with "
            "`map --persona host experiment start --id <experiment_id>`, then "
            f"write a brief execution log to {self.scenario.log_file} "
            "(1-2 paragraphs describing what was 'run' — this is a demo, so a "
            "plausible simulated log is fine), then submit the result with "
            "`map --persona host experiment complete --id <experiment_id> "
            f"--summary \"<summary>\" --file {self.scenario.log_file}`.\n"
            "  - If rejected with revisions: revise the plan and resubmit.\n\n"
            "For this demo, prefer starting + completing so the verification "
            "step can be showcased; but make the judgment yourself.\n\n"
            "Use the experiment-host Skill."
        )

    def _prompt_reviewer_verify_result(self) -> str:
        return (
            f"{self._ctx()}\n\n"
            "Task: the experiment result is pending verification. Verify it:\n"
            "  1. `map --persona reviewer experiment logs --id <experiment_id>` "
            "to read the execution log.\n"
            "  2. `map --persona reviewer experiment accept-result --id "
            "<experiment_id> --summary \"<summary>\" --file <review.md>` "
            "(or `reject-result` if the result is inadequate).\n\n"
            "For this demo, prefer 'accept-result' so the flow can proceed to "
            "closure; but make the judgment yourself.\n\n"
            "Use the experiment-reviewer Skill."
        )

    def _prompt_host_decide_close(self) -> str:
        return (
            f"{self._ctx()}\n\n"
            "Task: the experiment has been verified and accepted. Decide "
            "whether to close the topic or keep it open for further work.\n"
            "  - To close: `map --persona host fs close --topic <topic_slug> "
            "--note \"<conclusion and action items>\"` (the close_note carries "
            "the resolution).\n"
            "  - To keep open: post a summary comment and stop.\n\n"
            "For this demo, prefer closing the topic to wrap up the lifecycle; "
            "but make the judgment yourself."
        )

    # ---- scenario runner ----

    async def run_scenario(self) -> None:
        try:
            await self._run_steps()
        finally:
            self.write_run_log()

    async def _run_steps(self) -> None:
        # Step 1: host creates the topic.
        await self.step("create-topic", "host", self._prompt_create_topic())
        self.scenario.topic_id = self.find_topic_by_title()
        if not self.scenario.topic_id:
            self._end("topic not found after create-topic step")
            return

        # Steps 2-4: two rounds of host ↔ participant discussion.
        await self.step(
            "participant-comment-1", "participant",
            self._prompt_participant_comment("round 1"),
        )
        await self.step(
            "host-reply-1", "host",
            self._prompt_host_reply("round 1"),
        )
        await self.step(
            "participant-comment-2", "participant",
            self._prompt_participant_comment("round 2"),
        )

        # Step 5: host decides whether to open an experiment.
        await self.step(
            "host-decide-experiment", "host",
            self._prompt_host_decide_experiment(),
        )
        self.scenario.experiment_id = self.find_experiment_for_topic()
        if not self.scenario.experiment_id:
            self._end("host chose not to open an experiment; demo ends here")
            return

        # Step 6: reviewer reviews the plan.
        await self.step(
            "reviewer-review-plan", "reviewer",
            self._prompt_reviewer_review_plan(),
        )
        # Review submission clears pending reviews but does NOT auto-approve:
        # the host must explicitly `experiment approve` (review_service.
        # assert_approve_eligibility) before the phase moves to 'approved'.
        phase = self.experiment_phase()
        if phase != "review":
            self._end(
                f"experiment phase={phase!r} after review (expected 'review' — "
                "host approval still pending); demo ends here"
            )
            return

        # Step 7: host decides whether to start + complete the experiment.
        await self.step(
            "host-decide-start", "host",
            self._prompt_host_decide_start(),
        )
        phase = self.experiment_phase()
        if phase != "result_review":
            self._end(
                f"experiment phase={phase!r} after start (expected 'result_review'); "
                "demo ends here"
            )
            return

        # Step 8: reviewer verifies the result.
        await self.step(
            "reviewer-verify-result", "reviewer",
            self._prompt_reviewer_verify_result(),
        )
        phase = self.experiment_phase()
        if phase != "done":
            self._end(
                f"experiment phase={phase!r} after verify (expected 'done'); "
                "demo ends here"
            )
            return

        # Step 9: host decides whether to close the topic.
        await self.step(
            "host-decide-close", "host",
            self._prompt_host_decide_close(),
        )
        status = self.topic_status()
        self._end(f"final topic status: {status!r}")

    def _end(self, msg: str) -> None:
        _echo(f"\n[end] {msg}")

    # ---- run log ----

    def write_run_log(self) -> None:
        log_file = self.scenario.run_dir / "run.log"
        lines = [
            f"E2E run log — finished {datetime.now(timezone.utc).isoformat()}",
            f"subject: {self.scenario.subject}",
            f"topic_title: {self.scenario.topic_title}",
            f"topic_id: {self.scenario.topic_id}",
            f"experiment_id: {self.scenario.experiment_id}",
            "",
            "Steps:",
        ]
        for i, s in enumerate(self.steps, 1):
            summary = ""
            m = re.search(r"E2E:\s*(.+)$", s.text, re.MULTILINE)
            if m:
                summary = m.group(1).strip()[:200]
            lines.append(
                f"  {i}. [{s.persona}] {s.name} — status={s.status} — {summary}"
            )
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _echo(f"\nrun log: {log_file}")


# ---------------------------------------------------------------------------
# Client setup
# ---------------------------------------------------------------------------


def _build_client(
    *,
    persona: str,
    scenario: Scenario,
    state_file: Path,
    runtime_home: Path,
) -> PersonaAgentClient:
    """Create a per-persona PersonaAgentClient that resumes its prior session
    (or starts fresh when ``scenario.new_session`` is set). State is shared
    with `map runtime chat` so the same session can be inspected/resumed
    interactively for debugging."""
    state = load_runtime_state(state_file)
    agent_state = persona_agent_state(state, persona)
    resume_id = resolve_session_id(
        agent_state, session_id=None, new_session=scenario.new_session
    )
    if scenario.new_session:
        agent_state.pop("claude_session_id", None)
        agent_state.pop("runtime_session_id", None)
    elif resume_id:
        agent_state["claude_session_id"] = resume_id
        agent_state["runtime_session_id"] = resume_id

    sync_runtime_skills(project_root=scenario.project_root, runtime_home=runtime_home)
    runtime_home.mkdir(parents=True, exist_ok=True)

    def save_state() -> None:
        from cli.bridge_state import save_bridge_state

        save_bridge_state(state_file, state)

    extra_env: dict[str, str] = {
        "HOME": str(runtime_home),
        "MAP_RUNTIME_CHAT_PERSONA": persona,
    }

    client = PersonaAgentClient(
        persona=persona,
        state=agent_state,
        save_state_fn=save_state,
        project_root=scenario.project_root,
        extra_env=extra_env,
        model=scenario.model,
        integration="manual",
    )
    return client


async def _run_e2e_async(scenario: Scenario, ignore_waker: bool) -> None:
    # Refuse to run if any persona's waker is active (would conflict over the
    # same Claude session), unless --ignore-waker is set.
    for persona in PERSONAS:
        ensure_waker_not_running(persona=persona, ignore_waker=ignore_waker)

    state_files = {
        p: default_state_file(scenario.project_root, p) for p in PERSONAS
    }
    runtime_homes = {
        p: default_runtime_home(scenario.project_root, p) for p in PERSONAS
    }
    clients = {
        p: _build_client(
            persona=p,
            scenario=scenario,
            state_file=state_files[p],
            runtime_home=runtime_homes[p],
        )
        for p in PERSONAS
    }

    driver = E2EDriver(scenario=scenario, clients=clients)
    try:
        await driver.run_scenario()
    finally:
        for client in clients.values():
            await client.disconnect()


def run_e2e(**kwargs: Any) -> None:
    try:
        asyncio.run(_run_e2e_async(**kwargs))
    except WorkerError as exc:
        _echo_err(f"Error: {exc}")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        _echo("\nInterrupted.")
        raise typer.Exit(130) from None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@e2e_app.command("run")
def e2e_run(
    subject: str = typer.Option(
        _DEFAULT_SUBJECT,
        "--subject",
        help="Discussion subject for the demo topic.",
    ),
    topic_title: str = typer.Option(
        _DEFAULT_TOPIC_TITLE,
        "--topic-title",
        help="Exact title used for the demo topic (orchestrator matches by this).",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Repo root containing .map/ (default: search upward from cwd).",
    ),
    log_dir: Path | None = typer.Option(
        None,
        "--log-dir",
        help="Where to write the per-run E2E log dir (default: .map/e2e-logs/<ts>).",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Optional Claude model override passed to the Agent runtime.",
    ),
    new_session: bool = typer.Option(
        False,
        "--new-session",
        help="Start a fresh Claude session per persona instead of resuming state.",
    ),
    ignore_waker: bool = typer.Option(
        False,
        "--ignore-waker",
        help="Allow running while a simple-waker is active (may conflict over sessions).",
    ),
) -> None:
    """Drive a scripted host ↔ participant ↔ reviewer collaboration flow."""
    from cli.runtime_chat import resolve_project_root

    root = resolve_project_root(project_root)
    run_dir = log_dir or (root / ".map" / "e2e-logs" / _now_ts())
    run_dir.mkdir(parents=True, exist_ok=True)

    scenario = Scenario(
        subject=subject,
        topic_title=topic_title,
        project_root=root,
        run_dir=run_dir,
        plan_file=run_dir / "plan.md",
        log_file=run_dir / "execution-log.md",
        new_session=new_session,
        model=model,
    )

    _echo(
        f"MAP E2E collaboration driver\n"
        f"  project_root: {root}\n"
        f"  run_dir:      {run_dir}\n"
        f"  topic_title:  {topic_title}\n"
        f"  new_session:  {new_session}"
    )

    run_e2e(scenario=scenario, ignore_waker=ignore_waker)


__all__ = ["e2e_app", "run_e2e"]
