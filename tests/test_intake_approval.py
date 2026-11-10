"""Check the real intake approval route without a model service or database."""

import importlib
import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch
from _workflow_isolation_support import load_isolated_workflow


SOURCE = '''from app.models import Signal
raise AssertionError("Review fixture must never execute")
class ReviewStrategy:
    def evaluate(self, current_bar, prior_bars):
        return Signal.HOLD
'''


class IntakeApprovalTests(unittest.TestCase):
    """Exercise approval through the saved application's actual compiled graph."""

    def setUp(self):
        """Replace external setup before importing the workflow under review."""
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.dict(os.environ, {
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
        }))
        self.patches.enter_context(patch("dotenv.load_dotenv", return_value=False))
        self.patches.enter_context(patch(
            "socket.socket.connect",
            side_effect=AssertionError("Unexpected network connection"),
        ))
        self.patches.enter_context(patch(
            "psycopg.connect",
            side_effect=AssertionError("Unexpected database connection"),
        ))
        self.model = MagicMock()
        self.patches.enter_context(patch(
            "langchain.chat_models.init_chat_model", return_value=self.model,
        ))
        self.workflow = load_isolated_workflow(self.patches)
        self.patches.enter_context(patch.object(self.workflow, "model", self.model))

    def test_only_explicit_approve_all_and_model_approval_can_save(self):
        """Check both approval requirements across eight complete graph cases.

        Ordinary, conditional, blank, and nontext replies cannot be upgraded
        into approval by a model reply. An explicit response also requires
        the approved model status. Each case gets its own checkpoints.
        """
        cases = (
            ("exact approval", "approve all", "approved", "saved"),
            ("normalized approval", "  ApPrOvE AlL \t", "approved", "saved"),
            ("ordinary yes", "yes", "approved", "paused"),
            ("conditional approval", "approve all except the exit change", "approved", "paused"),
            ("blank response", "", "approved", "paused"),
            ("nontext response", None, "approved", "paused"),
            ("model requests clarification", "approve all", "needs_response", "paused"),
            ("model rejects", "approve all", "rejected", "rejected"),
        )
        changes = ["Use the completed bar close instead of the next open."]
        for label, answer, decision, expected in cases:
            with self.subTest(case=label):
                graph = self.workflow.graph.compile(
                    checkpointer=self.workflow.InMemorySaver(),
                )
                config = {"configurable": {"thread_id": "approval-review"}}
                conn = object()
                context = {"db_connection": conn}
                model_call = self.model.with_structured_output.return_value.invoke
                self.model.reset_mock()
                replies = [
                    self.workflow.ExamineResult(
                        status="hard_incompatibility",
                        incompatibilities=["Next open fills are unsupported."],
                    ),
                    self.workflow.IncompatibilityResult(
                        status="proposal_ready",
                        incompatibility_summary=["Next open fills are unsupported."],
                        proposed_changes=changes,
                        permission_questions=["Approve the fill timing change?"],
                    ),
                    self.workflow.CompatibilityDecision(status=decision),
                ]
                if expected == "saved":
                    replies.extend([
                        self.workflow.QuestionsResult(questions_resolved=True),
                        self.workflow.FormattingResult(
                            status="ready_for_validation",
                            formatted_source=SOURCE,
                            entrypoint_name="ReviewStrategy",
                        ),
                    ])
                model_call.side_effect = replies
                strategy_file = MagicMock()
                strategy_file.exists.return_value = True
                strategy_file.is_file.return_value = True
                strategy_file.suffix = ".py"
                strategy_file.read_bytes.return_value = SOURCE.encode("utf-8")
                with patch.object(self.workflow, "Path", return_value=strategy_file), patch.object(
                    self.workflow, "create_strategy_with_initial_version",
                    return_value={"strategy_id": 701, "strategy_version_id": 9907},
                ) as create_strategy:
                    result = graph.invoke({}, config, context=context, version="v2")
                    self.assertEqual(len(result.interrupts), 1)
                    self.assertIsInstance(result.interrupts[0].value, str)
                    result = graph.invoke(
                        self.workflow.Command(resume={
                            result.interrupts[0].id: "review_fixture.py",
                        }),
                        config, context=context, version="v2",
                    )
                    self.assertEqual(len(result.interrupts), 1)
                    self.assertEqual(
                        result.interrupts[0].value["type"], "engine_incompatibility",
                    )
                    self.assertEqual(
                        result.interrupts[0].value["proposed_changes"], changes,
                    )
                    result = graph.invoke(
                        self.workflow.Command(resume={result.interrupts[0].id: answer}),
                        config, context=context, version="v2",
                    )
                    if expected == "saved":
                        self.assertEqual(len(result.interrupts), 1)
                        self.assertEqual(
                            result.interrupts[0].value["type"], "strategy_name",
                        )
                        create_strategy.assert_not_called()
                        result = graph.invoke(
                            self.workflow.Command(resume={
                                result.interrupts[0].id: "Approved review strategy",
                            }),
                            config, context=context, version="v2",
                        )
                        self.assertEqual(
                            result.interrupts[0].value["type"], "strategy_description",
                        )
                        result = graph.invoke(
                            self.workflow.Command(resume={result.interrupts[0].id: ""}),
                            config, context=context, version="v2",
                        )
                        self.assertFalse(result.interrupts)
                        self.assertEqual(result.value["incompatibility_status"], "approved")
                        self.assertEqual(result.value["validation_status"], "passed")
                        self.assertEqual(result.value["save_status"], "saved")
                        self.assertEqual(result.value["approved_changes"], [{
                            "proposed_changes": changes, "user_response": answer,
                        }])
                        self.assertEqual(
                            (result.value["strategy_id"], result.value["strategy_version_id"]),
                            (701, 9907),
                        )
                        create_strategy.assert_called_once_with(
                            conn, name="Approved review strategy", description=None,
                            source_code=SOURCE, entrypoint_name="ReviewStrategy",
                        )
                        self.assertEqual(model_call.call_count, 5)
                    else:
                        create_strategy.assert_not_called()
                        self.assertNotEqual(result.value.get("save_status"), "saved")
                        self.assertFalse(result.value.get("approved_changes", []))
                        self.assertIsNone(result.value.get("strategy_id"))
                        self.assertIsNone(result.value.get("strategy_version_id"))
                        self.assertEqual(model_call.call_count, 3)
                        if expected == "paused":
                            self.assertEqual(len(result.interrupts), 1)
                            self.assertEqual(
                                result.interrupts[0].value["type"], "engine_incompatibility",
                            )
                            self.assertEqual(
                                result.interrupts[0].value["proposed_changes"], changes,
                            )
                            result = graph.invoke(
                                self.workflow.Command(resume={
                                    result.interrupts[0].id: "reject",
                                }),
                                config, context=context, version="v2",
                            )
                            self.assertEqual(model_call.call_count, 3)
                            create_strategy.assert_not_called()
                        self.assertFalse(result.interrupts)
                        self.assertEqual(result.value["incompatibility_status"], "rejected")
                    self.assertNotIn("db_connection", result.value)
