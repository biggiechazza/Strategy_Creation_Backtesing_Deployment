
'''Check intake with fake strategy files, model responses, and repository writes.'''
import ast
from collections import deque
import importlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import MagicMock, patch
from _workflow_isolation_support import load_isolated_workflow


class IntakeWorkflowTests(unittest.TestCase):
    '''Exercise terminal input, graph routing, and static validation together.'''

    def setUp(self):
        '''Mock external setup before importing intake and isolate each conversation.

        A fresh saver prevents the fixed thread ID from reusing an earlier test.
        Cleanup restores the patches even when a test fails.
        '''
        self.mocks = ExitStack()
        self.addCleanup(self.mocks.close)
        self.mocks.enter_context(patch.dict(os.environ, {
            'LANGSMITH_TRACING': 'false',
            'LANGCHAIN_TRACING_V2': 'false',
        }))
        self.mocks.enter_context(patch('dotenv.load_dotenv', return_value=False))
        self.mocks.enter_context(patch(
            'socket.socket.connect', side_effect=AssertionError('Unexpected network call')
        ))
        self.mocks.enter_context(patch(
            'psycopg.connect', side_effect=AssertionError('Unexpected database connection')
        ))

        self.model = MagicMock()
        self.mocks.enter_context(patch(
            'langchain.chat_models.init_chat_model', return_value=self.model
        ))
        self.workflow = load_isolated_workflow(self.mocks)
        self.mocks.enter_context(patch.object(self.workflow, 'model', self.model))

        graph = self.workflow.graph.compile(
            checkpointer=self.workflow.InMemorySaver()
        )
        self.mocks.enter_context(patch.object(self.workflow, 'intake_graph', graph))

    def test_terminal_intake_saves_after_static_validation(self):
        '''Check terminal answers reach one save and the returned IDs are shown.

        The fixture passes static checks but raises if executed. File access
        is mocked while the file check node and static validator still run.
        '''
        source = '''from app.models import Signal
raise AssertionError("Fixture strategy source must never execute")
class FixtureStrategy:
    def evaluate(self, current_bar, prior_bars):
        return Signal.HOLD
'''
        self.model.with_structured_output.return_value.invoke.side_effect = [
            self.workflow.ExamineResult(status='ready'),
            self.workflow.FormattingResult(
                status='ready_for_validation',
                formatted_source=source,
                entrypoint_name='FixtureStrategy',
            ),
        ]
        strategy_file = MagicMock()
        strategy_file.exists.return_value = True
        strategy_file.is_file.return_value = True
        strategy_file.suffix = '.py'
        strategy_file.read_bytes.return_value = source.encode('utf-8')
        conn = object()
        saved = {'strategy_id': 1001, 'strategy_version_id': 2001}
        output = io.StringIO()

        with patch.object(self.workflow, 'Path', return_value=strategy_file), \
                patch('builtins.input', side_effect=['fixture.py', 'Example', '']), \
                patch.object(self.workflow, 'create_strategy_with_initial_version',
                    return_value=saved) as create_strategy, \
                redirect_stdout(output):
            state = self.workflow.run_intake(conn)

        self.assertEqual(state['validation_status'], 'passed')
        self.assertEqual(state['save_status'], 'saved')
        self.assertEqual(state['strategy_version_id'], 2001)
        self.assertNotIn('db_connection', state)
        create_strategy.assert_called_once_with(
            conn, name='Example', description=None,
            source_code=source, entrypoint_name='FixtureStrategy',
        )
        self.assertIn('Strategy ID: 1001', output.getvalue())
        self.assertIn('Strategy version ID: 2001', output.getvalue())

    def test_clarification_answers_survive_resumes(self):
        '''Check both answer rounds reach question review and formatting.

        Completed question tasks should be reused on resume. Extra model calls
        would consume the scripted responses and fail this test.
        '''
        source = '''from app.models import Signal
raise AssertionError("Fixture strategy source must never execute")
class FixtureStrategy:
    def evaluate(self, current_bar, prior_bars):
        return Signal.HOLD
'''
        first_question = 'Which time zone?'
        second_question = 'Which session?'
        model_call = self.model.with_structured_output.return_value.invoke
        model_call.side_effect = [
            self.workflow.ExamineResult(
                status='needs_clarification',
                questions=[first_question],
                clarifiable_issues=['Time zone and session need clarification.'],
            ),
            self.workflow.QuestionsResult(
                questions_resolved=False, questions=[first_question],
            ),
            self.workflow.QuestionsResult(
                questions_resolved=False, questions=[second_question],),
            self.workflow.QuestionsResult(questions_resolved=True),
            self.workflow.FormattingResult(
                status='ready_for_validation',
                formatted_source=source,
                entrypoint_name='FixtureStrategy',),]
        strategy_file = MagicMock()
        strategy_file.exists.return_value = True
        strategy_file.is_file.return_value = True
        strategy_file.suffix = '.py'
        strategy_file.read_bytes.return_value = source.encode('utf-8')
        conn = object()
        saved = {'strategy_id': 1001, 'strategy_version_id': 2001}

        with patch.object(self.workflow, 'Path', return_value=strategy_file), \
                patch('builtins.input', side_effect=['fixture.py', 'UTC', 'RTH', 'Example', '']), \
                patch.object(self.workflow, 'create_strategy_with_initial_version',
                    return_value=saved) as create_strategy, \
                redirect_stdout(io.StringIO()):
            state = self.workflow.run_intake(conn)

        self.assertEqual(state['save_status'], 'saved')
        self.assertEqual(state['user_answers'], [
            {
                'questions': [first_question],
                'answers': [{'question': first_question, 'answer': 'UTC'}],},{'questions': [second_question],'answers': [{'question': second_question, 'answer': 'RTH'}],},])
        self.assertEqual(model_call.call_count, 5)
        create_strategy.assert_called_once_with(
            conn, name='Example', description=None,
            source_code=source, entrypoint_name='FixtureStrategy',)

        for call in model_call.call_args_list[-2:]:
            message = call.args[0][-1].content
            self.assertIn('UTC', message)
            self.assertIn('RTH', message)

    def test_duplicate_name_collects_new_metadata(self):
        '''Check a duplicate name requests new metadata before another save attempt.

        Examination and formatting should run once. The second repository call
        must use the replacement name and description supplied through the terminal.
        '''
        source = '''from app.models import Signal
raise AssertionError("Fixture strategy source must never execute")
class FixtureStrategy:
    def evaluate(self, current_bar, prior_bars):
        return Signal.HOLD
'''
        model_call = self.model.with_structured_output.return_value.invoke
        model_call.side_effect = [
            self.workflow.ExamineResult(status='ready'),
            self.workflow.FormattingResult(
                status='ready_for_validation',
                formatted_source=source,
                entrypoint_name='FixtureStrategy',),]

        
        strategy_file = MagicMock()
        strategy_file.exists.return_value = True
        strategy_file.is_file.return_value = True
        strategy_file.suffix = '.py'
        strategy_file.read_bytes.return_value = source.encode('utf-8')
        conn = object()
        saved = {'strategy_id': 1002, 'strategy_version_id': 2002}
        duplicate = self.workflow.Duplicate_Name_Prob('Name already taken.')
        output = io.StringIO()

        with patch.object(self.workflow, 'Path', return_value=strategy_file), \
                patch('builtins.input', side_effect=[
                    'fixture.py', 'Taken', 'Old description', 'New name', 'New description',
                ]), \
                patch.object(self.workflow, 'create_strategy_with_initial_version',
                    side_effect=[duplicate, saved]) as create_strategy, \
                redirect_stdout(output):
            state = self.workflow.run_intake(conn)

        self.assertEqual(state['save_status'], 'saved')
        self.assertEqual(state['strategy_name'], 'New name')
        self.assertEqual(state['strategy_description'], 'New description')
        self.assertEqual(state['strategy_id'], 1002)
        self.assertEqual(state['strategy_version_id'], 2002)
        self.assertEqual(model_call.call_count, 2)
        self.assertEqual(create_strategy.call_count, 2)

        calls = create_strategy.call_args_list
        self.assertEqual([call.kwargs['name'] for call in calls], ['Taken', 'New name'])
        self.assertEqual(
            [call.kwargs['description'] for call in calls],
            ['Old description', 'New description'],)


        
        for call in calls:
            self.assertIs(call.args[0], conn)
            self.assertEqual(call.kwargs['source_code'], source)

        self.assertIn('Name already taken.', output.getvalue())

    def test_rejection_and_validation_stops_do_not_save(self):
        '''Check rejection and validation endings stop before any repository call.

        Each case needs fresh checkpoints because subtests share the same setup.
        None of these fixtures should reach the name or description prompts.
        '''
        source = '''from app.models import Signal
raise AssertionError("Fixture strategy source must never execute")
class FixtureStrategy:
    def evaluate(self, current_bar, prior_bars):
        return Signal.HOLD'''
        def formatted(text):
            '''Build a complete formatting reply for the static validator.'''
            return self.workflow.FormattingResult(
                status='ready_for_validation',
                formatted_source=text,
                entrypoint_name='FixtureStrategy',)

        ready = self.workflow.ExamineResult(status='ready')
        hard = self.workflow.ExamineResult(
            status='hard_incompatibility',
            incompatibilities=['Next open fills are unsupported.'],
        )
        proposal = self.workflow.IncompatibilityResult(
            status='proposal_ready',
            incompatibility_summary=['Next open fills are unsupported.'],
            proposed_changes=['Use the completed bar close.'],
            permission_questions=['Approve close fills?'],
        )
        no_proposal = self.workflow.IncompatibilityResult(
            status='no_viable_proposal',
            incompatibility_summary=['No compatible replacement could be specified.'],
        )
        blocked_source = source.replace('class FixtureStrategy:', 'class FixtureStrategy(object):')
        invalid_source = source.replace('return Signal.HOLD', 'return "hold"')
        corrections = [ready]

        for attempt in range(4):
            corrections.append(formatted(invalid_source))
            if attempt < 3:
                corrections.append(self.workflow.QuestionsResult(questions_resolved=True))

        cases = (
            (
                'rejected', [hard, proposal], ['fixture.py', 'reject'],
                {'incompatibility_status': 'rejected'},
                'compatibility changes were rejected',
            ),
            (
                'no_proposal', [hard, no_proposal], ['fixture.py'],
                {'incompatibility_status': 'no_viable_proposal'},
                'no complete compatibility proposal',
            ),
            (
                'blocked', [ready, formatted(blocked_source)], ['fixture.py'],
                {'validation_status': 'blocked'},
                'required static checks could not be completed',
            ),
            (
                'corrections_exhausted', corrections, ['fixture.py'],
                {'validation_status': 'failed', 'validation_failure_count': 4},
                'after 3 correction attempts',
            ),
        )
        strategy_file = MagicMock()
        strategy_file.exists.return_value = True
        strategy_file.is_file.return_value = True
        strategy_file.suffix = '.py'
        strategy_file.read_bytes.return_value = source.encode('utf-8')
        model_call = self.model.with_structured_output.return_value.invoke

        for name, replies, answers, expected, message in cases:
            with self.subTest(name=name):
                model_call.reset_mock()
                model_call.side_effect = replies
                graph = self.workflow.graph.compile(
                    checkpointer=self.workflow.InMemorySaver()
                )
                output = io.StringIO()

                with patch.object(self.workflow, 'intake_graph', graph), \
                        patch.object(self.workflow, 'Path', return_value=strategy_file), \
                        patch('builtins.input', side_effect=answers) as terminal, \
                        patch.object(self.workflow, 'create_strategy_with_initial_version') as create_strategy, \
                        redirect_stdout(output):
                    state = self.workflow.run_intake(object())

                for key, value in expected.items():
                    self.assertEqual(state[key], value)
                create_strategy.assert_not_called()
                self.assertIsNone(state.get('strategy_id'))
                self.assertIsNone(state.get('strategy_version_id'))
                self.assertEqual(terminal.call_count, len(answers))
                self.assertEqual(model_call.call_count, len(replies))
                self.assertIn(message, output.getvalue())

    def test_database_error_stops_without_retry_or_success(self):
        '''Check an uncertain save stops once and does not expose raw connection details.

        A driver error does not prove the transaction rolled back. The graph
        should report an unconfirmed save without returning saved IDs.
        '''
        source = '''from app.models import Signal
raise AssertionError("Fixture strategy source must never execute")
class FixtureStrategy:
    def evaluate(self, current_bar, prior_bars):
        return Signal.HOLD
'''
        self.model.with_structured_output.return_value.invoke.side_effect = [
            self.workflow.ExamineResult(status='ready'),
            self.workflow.FormattingResult(
                status='ready_for_validation',
                formatted_source=source,
                entrypoint_name='FixtureStrategy',
            ),
        ]
        strategy_file = MagicMock()
        strategy_file.exists.return_value = True
        strategy_file.is_file.return_value = True
        strategy_file.suffix = '.py'
        strategy_file.read_bytes.return_value = source.encode('utf-8')
        conn = object()
        private_detail = 'private connection detail'
        output = io.StringIO()

        with patch.object(self.workflow, 'Path', return_value=strategy_file), \
                patch('builtins.input', side_effect=['fixture.py', 'Example', '']), \
                patch.object(self.workflow, 'create_strategy_with_initial_version',
                    side_effect=self.workflow.Error(private_detail)) as create_strategy, \
                redirect_stdout(output):
            state = self.workflow.run_intake(conn)

        self.assertEqual(state['validation_status'], 'passed')
        self.assertEqual(state['save_status'], 'failed')
        self.assertIsNone(state['strategy_id'])
        self.assertIsNone(state['strategy_version_id'])
        create_strategy.assert_called_once_with(
            conn, name='Example', description=None,
            source_code=source, entrypoint_name='FixtureStrategy',
        )
        self.assertNotIn(private_detail, state['save_error'])
        self.assertNotIn(private_detail, output.getvalue())
        self.assertIn('could not be confirmed', output.getvalue())
        self.assertIn('Check the database before retrying.', output.getvalue())
        self.assertNotIn('Strategy saved after passing static validation.', output.getvalue())


ORIGINAL_SOURCE = '''from app.models import Signal
raise AssertionError("Gap review strategy fixture must never execute")
class OriginalStrategy:
    def evaluate(self, current_bar, prior_bars):
        return Signal.HOLD
'''
BROKEN_SOURCE = ORIGINAL_SOURCE.replace(
    "OriginalStrategy", "BrokenDraftStrategy",
).replace("return Signal.HOLD", 'return "hold"')
CORRECTED_SOURCE = ORIGINAL_SOURCE.replace("OriginalStrategy", "CorrectedStrategy")
FEEDBACK_MARKER = "Validation feedback (last failed draft and errors):"


class SchemaScript:
    """Bind each scripted reply to its exact expected schema and system prompt."""

    def __init__(self):
        self.steps = deque()
        self.calls = []

    def add(self, schema, reply, system_prompt, check=None):
        """Queue a response contract and optional checks on the outgoing user message."""
        if not isinstance(reply, schema):
            raise AssertionError("The fixture reply does not match its expected schema")
        self.steps.append((schema, reply, system_prompt, check))

    def with_structured_output(self, schema):
        """Reject a wrong schema before it can receive a plausible scripted reply."""
        if not self.steps:
            raise AssertionError("Unexpected extra structured model call")
        expected, reply, system_prompt, check = self.steps[0]
        if schema is not expected:
            raise AssertionError(
                f"Expected schema {expected.__name__}, got {getattr(schema, '__name__', schema)}"
            )
        return SchemaInvocation(self, schema)


class SchemaInvocation:
    """Check the actual messages before consuming the expected model reply."""

    def __init__(self, script, schema):
        self.script = script
        self.schema = schema

    def invoke(self, messages):
        expected, reply, system_prompt, check = self.script.steps.popleft()
        if self.schema is not expected:
            raise AssertionError("Structured response contract changed before invocation")
        if len(messages) != 2:
            raise AssertionError("Expected one system and one user message")
        if getattr(messages[0], "type", None) != "system":
            raise AssertionError("The first model message must be the system message")
        if getattr(messages[1], "type", None) != "human":
            raise AssertionError("The second model message must be the user input")
        if messages[0].content != system_prompt:
            raise AssertionError("The node used the wrong system prompt")
        self.script.calls.append((expected, messages))
        if check is not None:
            check(messages[1].content)
        return reply


class IntakeWorkflowGapTests(unittest.TestCase):
    """Exercise saved application definitions with fresh production initialization."""

    def setUp(self):
        """Load the saved production graph without changing cached Workflow state.

        A private module gets its own model, graph, and saver from the saved
        source. Cleanup removes only that module and its package attribute.
        """
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.dict(os.environ, {
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
        }))
        self.patches.enter_context(patch("dotenv.load_dotenv", return_value=False))
        self.patches.enter_context(patch(
            "psycopg.connect", side_effect=AssertionError("Unexpected database connection"),
        ))
        self.patches.enter_context(patch(
            "socket.socket.connect", side_effect=AssertionError("Unexpected network connection"),
        ))
        self.model = SchemaScript()
        self.patches.enter_context(patch(
            "langchain.chat_models.init_chat_model", return_value=self.model,
        ))
        package = importlib.import_module("intake_agent_lc")
        module_attribute = "_workflow_test_instance"
        module_name = f"{package.__name__}.{module_attribute}"
        if module_name in sys.modules or hasattr(package, module_attribute):
            raise AssertionError("An earlier isolated workflow was not cleaned up")
        source = Path(__file__).resolve().parents[1] / "intake_agent_lc" / "Workflow.py"
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise AssertionError("Cannot load the saved Workflow source")
        self.workflow = importlib.util.module_from_spec(spec)
        self.patches.callback(sys.modules.pop, module_name, None)
        sys.modules[module_name] = self.workflow
        self.patches.enter_context(patch.object(
            package, module_attribute, self.workflow, create=True,
        ))
        spec.loader.exec_module(self.workflow)
        self.graph = self.workflow.intake_graph

    def fake_strategy_file(self):
        """Provide original source text without reading or executing an uploaded file."""
        strategy_file = MagicMock()
        strategy_file.exists.return_value = True
        strategy_file.is_file.return_value = True
        strategy_file.suffix = ".py"
        strategy_file.read_bytes.return_value = ORIGINAL_SOURCE.encode("utf-8")
        return strategy_file

    def feedback(self, message):
        """Decode the final diagnostic dictionary without depending on its whitespace."""
        self.assertIn(FEEDBACK_MARKER, message)
        return ast.literal_eval(message.rsplit(FEEDBACK_MARKER, 1)[1].strip())

    def test_failed_draft_reaches_both_prompts_and_corrected_source_is_saved(self):
        """Check a real validation failure can lead to one corrected successful save.

        The failed draft differs from the original upload, so an assertion
        cannot pass merely because the original source appears in the prompt.
        Earlier user answers must survive the correction round as well.
        """
        w = self.workflow
        validation = w.validation_node({
            "format_status": "ready_for_validation",
            "formatted_source": BROKEN_SOURCE,
            "entrypoint_name": "BrokenDraftStrategy",
            "questions": [],
            "incompatibilities": [],
        })
        self.assertEqual(validation["validation_status"], "failed")
        self.assertTrue(validation["validation_errors"])
        expected_feedback = {
            "formatted_source": BROKEN_SOURCE,
            "entrypoint_name": "BrokenDraftStrategy",
            "validation_errors": validation["validation_errors"],
        }

        def original_context(message):
            """Check source context remains the original upload."""
            self.assertIn(ORIGINAL_SOURCE, message)

        def first_format(message):
            """The initial formatting call has answers but no failed-draft diagnostics."""
            original_context(message)
            self.assertIn("UTC", message)
            self.assertIsNone(self.feedback(message))

        def correction_context(message):
            """Both correction stages must receive the exact failed draft and diagnostics."""
            original_context(message)
            self.assertIn("UTC", message)
            self.assertEqual(self.feedback(message), expected_feedback)

        self.model.add(
            w.ExamineResult,
            w.ExamineResult(
                status="needs_clarification",
                questions=["Which time zone?"],
                clarifiable_issues=["The time zone needs clarification."],
            ),
            w.system_prompt_node_3, original_context,
        )
        self.model.add(
            w.QuestionsResult,
            w.QuestionsResult(questions_resolved=False, questions=["Which time zone?"]),
            w.system_prompt_questions, original_context,
        )
        self.model.add(
            w.QuestionsResult, w.QuestionsResult(questions_resolved=True),
            w.system_prompt_questions, original_context,
        )
        self.model.add(
            w.FormattingResult,
            w.FormattingResult(
                status="ready_for_validation", formatted_source=BROKEN_SOURCE,
                entrypoint_name="BrokenDraftStrategy",
            ),
            w.system_prompt_formatting, first_format,
        )
        self.model.add(
            w.QuestionsResult, w.QuestionsResult(questions_resolved=True),
            w.system_prompt_questions, correction_context,
        )
        self.model.add(
            w.FormattingResult,
            w.FormattingResult(
                status="ready_for_validation", formatted_source=CORRECTED_SOURCE,
                entrypoint_name="CorrectedStrategy",
            ),
            w.system_prompt_formatting, correction_context,
        )
        conn = object()
        with patch.object(w, "Path", return_value=self.fake_strategy_file()), patch(
            "builtins.input",
            side_effect=["review_fixture.py", "UTC", "Corrected review", ""],
        ) as terminal, patch.object(
            w, "create_strategy_with_initial_version",
            return_value={"strategy_id": 731, "strategy_version_id": 9713},
        ) as save, redirect_stdout(io.StringIO()):
            state = w.run_intake(conn)
        self.assertIs(w.intake_graph, self.graph)
        self.assertEqual(state["save_status"], "saved")
        self.assertEqual(state["validation_status"], "passed")
        self.assertEqual(state["validation_failure_count"], 1)
        self.assertEqual(state["validation_feedback"], expected_feedback)
        self.assertEqual(state["formatted_source"], CORRECTED_SOURCE)
        self.assertEqual(state["entrypoint_name"], "CorrectedStrategy")
        self.assertEqual(state["user_answers"], [{
            "questions": ["Which time zone?"],
            "answers": [{"question": "Which time zone?", "answer": "UTC"}],
        }])
        save.assert_called_once_with(
            conn, name="Corrected review", description=None,
            source_code=CORRECTED_SOURCE, entrypoint_name="CorrectedStrategy",
        )
        self.assertEqual(terminal.call_count, 4)
        self.assertEqual([schema for schema, messages in self.model.calls], [
            w.ExamineResult, w.QuestionsResult, w.QuestionsResult,
            w.FormattingResult, w.QuestionsResult, w.FormattingResult,
        ])
        self.assertFalse(self.model.steps)

    def test_compatibility_uses_each_expected_schema_and_passes_approval_context(self):
        """Check all five response schemas and important compatibility message inputs."""
        w = self.workflow
        reason = "The fixture requests unsupported next open fills."
        change = "Use completed close fills for this review proposal."
        permission = "Approve every proposed fill timing change?"
        approved = [{"proposed_changes": [change], "user_response": "approve all"}]

        def source_context(message):
            """Examination and proposal generation need the actual uploaded source."""
            self.assertIn(ORIGINAL_SOURCE, message)

        def proposal_context(message):
            """The proposal must receive the incompatibility identified by examination."""
            source_context(message)
            self.assertIn(reason, message)

        def decision_context(message):
            """Classification must receive the proposal, permission question, and response."""
            self.assertIn(change, message)
            self.assertIn(permission, message)
            self.assertIn("approve all", message)

        def approved_context(message):
            """Questions and formatting must receive the recorded approval."""
            source_context(message)
            self.assertIn(str(approved), message)

        self.model.add(
            w.ExamineResult,
            w.ExamineResult(status="hard_incompatibility", incompatibilities=[reason]),
            w.system_prompt_node_3, source_context,
        )
        self.model.add(
            w.IncompatibilityResult,
            w.IncompatibilityResult(
                status="proposal_ready", incompatibility_summary=[reason],
                proposed_changes=[change], permission_questions=[permission],
            ),
            w.system_prompt_incompatibility, proposal_context,
        )
        self.model.add(
            w.CompatibilityDecision, w.CompatibilityDecision(status="approved"),
            w.system_prompt_compatibility_decision, decision_context,
        )
        self.model.add(
            w.QuestionsResult, w.QuestionsResult(questions_resolved=True),
            w.system_prompt_questions, approved_context,
        )
        self.model.add(
            w.FormattingResult,
            w.FormattingResult(
                status="ready_for_validation", formatted_source=CORRECTED_SOURCE,
                entrypoint_name="CorrectedStrategy",
            ),
            w.system_prompt_formatting, approved_context,
        )
        conn = object()
        with patch.object(w, "Path", return_value=self.fake_strategy_file()), patch(
            "builtins.input",
            side_effect=["review_fixture.py", "approve all", "Approved review", ""],
        ), patch.object(
            w, "create_strategy_with_initial_version",
            return_value={"strategy_id": 733, "strategy_version_id": 9733},
        ) as save, redirect_stdout(io.StringIO()):
            state = w.run_intake(conn)
        self.assertEqual(state["save_status"], "saved")
        self.assertEqual(state["approved_changes"], approved)
        save.assert_called_once_with(
            conn, name="Approved review", description=None,
            source_code=CORRECTED_SOURCE, entrypoint_name="CorrectedStrategy",
        )
        self.assertEqual([schema for schema, messages in self.model.calls], [
            w.ExamineResult, w.IncompatibilityResult, w.CompatibilityDecision,
            w.QuestionsResult, w.FormattingResult,
        ])
        self.assertFalse(self.model.steps)

    def test_production_graph_checkpoints_file_retry_and_resumes_to_metadata(self):
        """Use the production graph and saver across an invalid path and a valid retry."""
        w = self.workflow
        self.assertIs(self.graph.checkpointer, w.checkpointer)
        self.assertIsInstance(w.checkpointer, w.InMemorySaver)
        self.model.add(
            w.ExamineResult, w.ExamineResult(status="ready"),
            w.system_prompt_node_3,
        )
        self.model.add(
            w.FormattingResult,
            w.FormattingResult(
                status="ready_for_validation", formatted_source=ORIGINAL_SOURCE,
                entrypoint_name="OriginalStrategy",
            ),
            w.system_prompt_formatting,
        )
        config = {"configurable": {"thread_id": "production-checkpoint-gap-review"}}
        context = {"db_connection": object()}
        strategy_file = self.fake_strategy_file()
        strategy_file.exists.side_effect = [False, True]
        with patch.object(w, "Path", return_value=strategy_file), patch.object(
            w, "create_strategy_with_initial_version",
        ) as save:
            first = self.graph.invoke({}, config, context=context, version="v2")
            self.assertEqual(len(first.interrupts), 1)
            self.assertIsInstance(first.interrupts[0].value, str)
            self.assertEqual(self.graph.get_state(config).next, ("file_path",))
            retry = self.graph.invoke(
                w.Command(resume={first.interrupts[0].id: "missing_review.py"}),
                config, context=context, version="v2",
            )
            self.assertEqual(len(retry.interrupts), 1)
            self.assertIsInstance(retry.interrupts[0].value, str)
            saved_retry = self.graph.get_state(config)
            self.assertEqual(saved_retry.next, ("file_path",))
            self.assertEqual(saved_retry.values["file_path"], "missing_review.py")
            self.assertFalse(saved_retry.values["file_valid"])
            self.assertIn("File not found", saved_retry.values["file_error"])
            self.assertFalse(self.model.calls)
            metadata = self.graph.invoke(
                w.Command(resume={retry.interrupts[0].id: "valid_review.py"}),
                config, context=context, version="v2",
            )
            self.assertEqual(len(metadata.interrupts), 1)
            self.assertEqual(metadata.interrupts[0].value["type"], "strategy_name")
            saved_metadata = self.graph.get_state(config)
            self.assertEqual(saved_metadata.next, ("collect_strategy",))
            self.assertEqual(saved_metadata.values["file_path"], "valid_review.py")
            self.assertIsNone(saved_metadata.values["file_error"])
            self.assertEqual(saved_metadata.values["source_code"], ORIGINAL_SOURCE)
            self.assertEqual(saved_metadata.values["validation_status"], "passed")
            save.assert_not_called()
        self.assertIs(w.intake_graph, self.graph)
        self.assertFalse(self.model.steps)


if __name__ == '__main__':
    unittest.main()
