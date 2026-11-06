
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


if __name__ == '__main__':
    unittest.main()