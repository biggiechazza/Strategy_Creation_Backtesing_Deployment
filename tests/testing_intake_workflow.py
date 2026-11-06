
'''Check intake with fake strategy files, model responses, and repository writes.'''
import importlib
import io
import os
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import MagicMock, patch


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
        self.workflow = importlib.import_module('intake_agent_lc.Workflow')
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
                entrypoint_name='FixtureStrategy',
            ),
        ]
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
            ['Old description', 'New description'],
        )
        for call in calls:
            self.assertIs(call.args[0], conn)
            self.assertEqual(call.kwargs['source_code'], source)

        self.assertIn('Name already taken.', output.getvalue())
        
if __name__ == '__main__':
    unittest.main()