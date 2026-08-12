"""Tests for v0.10 ExperimentMode.direct — plan mode fast path.

Covers state machine transitions, phase owner routing, capabilities
(actions/blocked_on), and schema serialization for direct mode.
"""

import pytest

from map_types.enums import ExperimentMode, ExperimentPhase, PhaseOwner
from map_types.schemas import ExperimentCreate, ExperimentSummaryRead, PlanInput
from server.domain.state_machine import StateMachineError, validate_phase_transition
from server.services.phase_owner_resolver import is_informational_only, owner_for


# ─── State Machine ───────────────────────────────────────────────────────


class TestDirectModeStateMachine:
    def test_direct_draft_to_running(self):
        validate_phase_transition(
            ExperimentPhase.draft, ExperimentPhase.running, mode="direct"
        )

    def test_direct_running_to_done(self):
        validate_phase_transition(
            ExperimentPhase.running, ExperimentPhase.done, mode="direct"
        )

    def test_direct_draft_to_cancelled(self):
        validate_phase_transition(
            ExperimentPhase.draft, ExperimentPhase.cancelled, mode="direct"
        )

    def test_direct_running_to_cancelled(self):
        validate_phase_transition(
            ExperimentPhase.running, ExperimentPhase.cancelled, mode="direct"
        )

    def test_direct_rejects_draft_to_review(self):
        with pytest.raises(StateMachineError):
            validate_phase_transition(
                ExperimentPhase.draft, ExperimentPhase.review, mode="direct"
            )

    def test_direct_rejects_running_to_result_review(self):
        with pytest.raises(StateMachineError):
            validate_phase_transition(
                ExperimentPhase.running,
                ExperimentPhase.result_review,
                mode="direct",
            )

    def test_direct_rejects_review_to_approved(self):
        with pytest.raises(StateMachineError):
            validate_phase_transition(
                ExperimentPhase.review,
                ExperimentPhase.approved,
                mode="direct",
            )

    def test_direct_rejects_approved_to_running(self):
        with pytest.raises(StateMachineError):
            validate_phase_transition(
                ExperimentPhase.approved,
                ExperimentPhase.running,
                mode="direct",
            )

    def test_direct_rejects_result_review_to_done(self):
        with pytest.raises(StateMachineError):
            validate_phase_transition(
                ExperimentPhase.result_review,
                ExperimentPhase.done,
                mode="direct",
            )


class TestStandardModeBackwardCompat:
    """Ensure standard mode transitions are unchanged."""

    def test_standard_draft_to_review(self):
        validate_phase_transition(
            ExperimentPhase.draft, ExperimentPhase.review, mode="standard"
        )

    def test_standard_rejects_draft_to_running(self):
        with pytest.raises(StateMachineError):
            validate_phase_transition(
                ExperimentPhase.draft,
                ExperimentPhase.running,
                mode="standard",
            )

    def test_default_mode_is_standard(self):
        """No mode parameter → standard behavior."""
        validate_phase_transition(ExperimentPhase.draft, ExperimentPhase.review)
        with pytest.raises(StateMachineError):
            validate_phase_transition(ExperimentPhase.draft, ExperimentPhase.running)


# ─── Phase Owner Resolver ────────────────────────────────────────────────


class TestDirectModePhaseOwner:
    def test_direct_running_owner_is_participant(self):
        assert owner_for(ExperimentPhase.running, mode="direct") == PhaseOwner.participant

    def test_direct_draft_owner_is_host(self):
        assert owner_for(ExperimentPhase.draft, mode="direct") == PhaseOwner.host

    def test_direct_done_owner_is_host(self):
        assert owner_for(ExperimentPhase.done, mode="direct") == PhaseOwner.host

    def test_standard_running_owner_is_host(self):
        assert owner_for(ExperimentPhase.running, mode="standard") == PhaseOwner.host

    def test_default_running_owner_is_host(self):
        assert owner_for(ExperimentPhase.running) == PhaseOwner.host


class TestDirectModeInformationalOnly:
    def test_direct_running_is_informational_for_host(self):
        """Host has no actions and is blocked → informational_only."""
        assert is_informational_only(
            ExperimentPhase.running,
            actions=[],
            blocked_on="waiting_for_executor",
            mode="direct",
        )

    def test_standard_running_not_informational_for_host(self):
        """In standard mode, host owns running → not informational_only."""
        assert not is_informational_only(
            ExperimentPhase.running,
            actions=[],
            blocked_on="waiting_for_executor",
            mode="standard",
        )

    def test_direct_running_with_actions_not_informational(self):
        """If host has actions, not informational even in direct mode."""
        assert not is_informational_only(
            ExperimentPhase.running,
            actions=["cancel"],
            blocked_on="waiting_for_executor",
            mode="direct",
        )


# ─── Schemas ─────────────────────────────────────────────────────────────


class TestExperimentCreateSchema:
    def test_create_with_direct_mode(self):
        payload = ExperimentCreate(
            title="Test",
            plan=PlanInput(content_md="---\ntitle: test\n---\nbody"),
            mode=ExperimentMode.direct,
        )
        assert payload.mode == ExperimentMode.direct

    def test_create_default_is_standard(self):
        payload = ExperimentCreate(
            title="Test",
            plan=PlanInput(content_md="---\ntitle: test\n---\nbody"),
        )
        assert payload.mode == ExperimentMode.standard

    def test_summary_read_has_mode(self):
        """ExperimentSummaryRead should have a mode field with default."""
        # Verify the field exists on the schema
        assert "mode" in ExperimentSummaryRead.model_fields
        # Default should be standard
        field = ExperimentSummaryRead.model_fields["mode"]
        assert field.default == ExperimentMode.standard
