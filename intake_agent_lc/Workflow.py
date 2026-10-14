
from dotenv import load_dotenv
import ast
import uuid
from pathlib import Path
from typing import Literal, TypedDict
from System_Prompt import system_prompt_node_3, system_prompt_formatting, system_prompt_incompatibility, system_prompt_compatibility_decision, system_prompt_questions


from pydantic import BaseModel, Field

from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage, SystemMessage
from langchain.tools import tool

from langgraph.func import task
from langgraph.graph import MessagesState, StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt, Command

load_dotenv()

model = init_chat_model('gpt-6-luna')

# Shared Memory & Classes
class State(MessagesState):
# Path to users
    file_path: str | None
    file_valid: bool | None
    file_error: str | None

# Original user strategy code
    source_code: str | None

# Node 3 examine_file_node
    examination_status: str | None
    questions: list
    clarifiable_issues: list
    incompatibilities: list
    formatting_notes: list

# Node 5 questions_node
    user_answers: list
    questions_resolved: bool | None

# Node 6 Incompatibility & potential changes
    approved_changes: list
    incompatibility_status: str | None
    incompatibility_questions: list
    incompatibility_response: object | None

# Node 4 Formatting
    format_status: str | None
    formatted_source: str | None
    entrypoint_name: str | None
    changes_made: list

# Node 7 Validation
    validation_status: Literal['passed', 'failed', 'blocked'] | None
    checks_completed: list[str]
    checks_skipped: list[dict[str, str]]
    validation_errors: list



class QuestionsResult(BaseModel):

    questions_resolved: bool = Field(
        description='True only when all material clarification questions have been adequately answered.')

    questions: list[str] = Field(
        default_factory=list,
        description='Remaining or follow-up questions. Empty when all questions are resolved.')



class IncompatibilityResult(BaseModel):

    incompatibility_summary: list[str] = Field(
        default_factory = list,
        description = 'Exact engine incompatibilities that prevent faithful adaptation.')

    proposed_changes: list[str] = Field(
        default_factory = list,
        description = 'Minimum necessary changes to strategy behavior required for engine compatibility.')

    permission_questions: list[str] = Field(
        default_factory = list,
        description = 'Questions asking the user for explicit approval of each required behavioral change.')



class FormattingResult(BaseModel):
    status: Literal[
        'ready_for_validation',
        'needs_clarification',
        'hard_incompatibility'] = Field(description = 'Determines where the graph routes after or during formatting.')

    formatted_source: str | None = Field(
        default = None,
        description = 'Complete formatted Python strategy source when formatting can be completed.')

    entrypoint_name: str | None = Field(
        default = None,
        description = 'Exact class name of the formatted strategy entrypoint.')

    questions: list[str] = Field(
        default_factory = list,
        description = 'Any new material questions discovered during formatting.')

    incompatibilities: list[str] = Field(
        default_factory = list,
        description = 'Any engine incompatibilities discovered during formatting.')

    changes_made: list[str] = Field(
        default_factory = list,
        description = 'Changes made while adapting the strategy to the engine contract.')



# Only used during Node 3 examine_file_node
class ExamineResult(BaseModel):
    status: Literal['ready', 'needs_clarification', 'hard_incompatibility', 'not_strategy'] = Field(description = 'The routing outcome produced by examination.')

    questions: list[str] = Field(
        default_factory = list,
        description='Material questions the user must answer before formatting.')

    clarifiable_issues: list[str] = Field(
        default_factory = list,
        description='Ambiguities that can be resolved through user clarification.')

    incompatibilities: list[str] = Field(
        default_factory = list,
        description='Engine incompatibilities identified in the strategy, or it is not a identifiable trading strategy.')

    formatting_notes: list[str] = Field(
        default_factory = list,
        description='Source established facts the formatting node must preserve.')



class CompatibilityDecision(BaseModel):
    status: Literal[
        'approved',
        'rejected',
        'needs_response'] = Field(description='Whether the user explicitly approved the proposed compatibility changes.')



class ValidationResult(BaseModel):
    validation_status: Literal['passed', 'failed', 'blocked']

    checks: list[str] = Field(default_factory=list)

    checks_skipped: list[dict[str, str]] = Field(default_factory=list)

    validation_errors: list[dict[str, str | None]] = Field(default_factory=list)


# Node functions

# Node 1
def file_path_node(state):
    # User Provides File Path
    file_path = interrupt('Paste the exact/full file path to the encoded strategy you want to backtest:')
    return {**state, 'file_path': file_path, 'file_error': None,}


# Node 2
def file_check_node(state):
    # Check if path exists, is python, and is a file
    file_path = state.get("file_path")
    path = Path(file_path)
    if not path.exists():
        return {
            "file_valid": False,
            "file_error": f"File not found: {path}"}
    elif not path.is_file():
        return {
            "file_valid": False,
            "file_error": f"Path is not a file: {path}"}
    elif path.suffix.lower() != ".py":
        return {
            "file_valid": False,
            "file_error": "File must be a Python .py file."}

    # Then check the code has valid syntax 
    try:
        source_code = path.read_bytes().decode("utf-8")
        ast.parse(source_code)
        compile(source_code, str(path), 'exec')
    except UnicodeDecodeError:
        return {
            "file_valid": False,
            "file_error": "File is not valid UTF-8."}
    except SyntaxError as error:
        return {
            "file_valid": False,
            "file_error": f"Invalid Python syntax: {error}"}
    # all else
    except Exception as error:
        return {
            "file_valid": False,
            "file_error": f"Unexpected error: {error}"}

    # If nothing happens then onto the next node
    return {
        "file_valid": True,
        "file_error": None,
        "source_code": source_code}






# Routing decisions begin
# NOT a node, if the file wasn't valid return to the file_path node
def route_file_check(state):
    if state.get('file_valid') == True and state.get('file_error') == None:
        return 'examine_file'
    return 'file_path'






# Node 3, three potential nodes after: questions_node or code_formatting_node or hard_incompatibility_node
def examine_file_node(state):
    source_code = state.get('source_code')

    # Model looks at the source code from the user file and determines the appropriate routing and questions and produces reasons for its actions.
    result = model.with_structured_output(ExamineResult).invoke([
        SystemMessage(content = system_prompt_node_3), HumanMessage(content = f'Examine the following strategy source:\n\n{source_code}')])

    # Check if the model determined the file is not a strategy, and raise an error if so
    if result.status == 'not_strategy':
        print(result.incompatibilities)
        raise ValueError('The uploaded file contains no identifiable trading strategy.')

    # Model returns this exact output
    return {
        'examination_status': result.status,
        'questions': result.questions,
        'clarifiable_issues': result.clarifiable_issues,
        'incompatibilities': result.incompatibilities,
        'formatting_notes': result.formatting_notes,}


# Grab 'examination_status' from the models output and tell the graph which node to use initially.
def route_examination(state):
    status = state.get('examination_status')
    if status == 'needs_clarification':
        return 'questions'
    elif status == 'hard_incompatibility':
        return 'incompatibility'
    elif status == 'ready':
        return 'formatting'

    # Temporary for agent evals
    else:
        print('Unexpected examination status')








# PATH 1 after examine_file, Node 4: Direct to Formatting
def code_formatting_node(state):

    source_code = state.get('source_code')

    formatting_notes = state.get('formatting_notes', [])
    
    user_answers = state.get('user_answers', [])
 
    approved_changes = state.get('approved_changes', [])
    
    result = model.with_structured_output(FormattingResult).invoke([SystemMessage(content = system_prompt_formatting),
        HumanMessage(content = f'''Original strategy source:{source_code}Examination notes:{formatting_notes}User clarification answers:{user_answers}User-approved compatibility changes:{approved_changes}''')])

    return {
        'format_status': result.status,
        'formatted_source': result.formatted_source,
        'entrypoint_name': result.entrypoint_name,
        'questions': result.questions,
        'incompatibilities': result.incompatibilities,
        'changes_made': result.changes_made,}

# Router for formatting node
def route_formatting(state):
    status = state.get('format_status')

# See if the model outputted any issues or questions
    if status == 'hard_incompatibility':
        return 'incompatibility'
    elif status == 'needs_clarification':
        return 'questions'

    # Only approve the move to validation if the new formatted source is valid
    elif status == 'ready_for_validation':
        formatted_source = state.get('formatted_source')
        entrypoint_name = state.get('entrypoint_name')

        if not isinstance(formatted_source, str) or not formatted_source.strip():
            raise ValueError('Formatting returned an empty or invalid formatted_source.')

        if not isinstance(entrypoint_name, str) or not entrypoint_name.strip():
            raise ValueError('Formatting returned an empty or invalid entrypoint_name.')
        return 'validation'






# questions_node Helper Function---------------------------------
@task
def evaluate_questions(source_code, formatting_notes, questions,
                       user_answers, clarifiable_issues, approved_changes):

    result = model.with_structured_output(QuestionsResult).invoke([
        SystemMessage(content=system_prompt_questions),
        HumanMessage(content=f'''Original strategy source:
{source_code}

Examination notes:
{formatting_notes}

Identified clarifiable issues:
{clarifiable_issues}

Current clarification questions:
{questions}

All previous user answers:
{user_answers}

Approved compatibility changes:
{approved_changes}''')])

    return result.model_dump()



# PATH 2 after examine_file, Node 5: Questions
def questions_node(state):

    # Grab previous user answers
    previous_answers = state.get('user_answers', [])

    # Evaluate all clarification requirements and previous answers
    result = QuestionsResult.model_validate(
        evaluate_questions(
            state.get('source_code'),
            state.get('formatting_notes', []),
            state.get('questions', []),
            previous_answers,
            state.get('clarifiable_issues', []),
            state.get('approved_changes', [])).result())

    # All questions have been resolved
    if result.questions_resolved:

        if result.questions:
            raise ValueError(
                'Questions marked resolved but additional questions were returned.')

        return {
            'questions': [],
            'questions_resolved': True}

    # Unresolved questions require another user response
    if not result.questions:
        raise ValueError(
            'Questions remain unresolved but no follow-up questions were provided.')

    # Ask outstanding questions and collect answers
    answers = interrupt({
        'type': 'clarification_questions',
        'questions': result.questions})

    # Preserve all previous clarification rounds
    updated_answers = previous_answers + [{
        'questions': result.questions,
        'answers': answers}]

    return {
        'questions': result.questions,
        'user_answers': updated_answers,
        'questions_resolved': False}

# Router for questions_node
def route_questions(state):

    if state.get('questions_resolved') is True:
        return 'formatting'

    elif state.get('questions_resolved') is False:
        return 'questions'

    raise ValueError('Unexpected questions_resolved value.')





# INCOMPATIBILITY NODE HELPER FUNCTIONS ---------------------------------------
@task
def generate_proposal(source_code, incompatibilities):

    result = model.with_structured_output(IncompatibilityResult).invoke([
        SystemMessage(content = system_prompt_incompatibility),
        HumanMessage(content = f'''Original strategy source:{source_code}Detected engine incompatibilities:{incompatibilities}''')])
    return result.model_dump()



@task
def evaluate_response(proposed_changes, permission_questions, user_response):

    decision = model.with_structured_output(CompatibilityDecision).invoke([
        SystemMessage(content = system_prompt_compatibility_decision),
        HumanMessage(content = f'''Proposed changes:{proposed_changes}Permission questions:{permission_questions}User response:{user_response}''')])
    return decision.model_dump()



# PATH 3 after examine_file, Node 5: Hard Incompatibility
def hard_incompatibility_node(state):

    # Grab incompatibilities and previous approved changes from state
    incompatibilities = state.get('incompatibilities', [])
    source_code = state.get('source_code')
    previous_approved_changes = state.get('approved_changes', [])

    if not source_code or not incompatibilities:
        raise ValueError('Missing strategy source or engine incompatibilities.')

    # Generate the checkpointed proposal
    proposal = IncompatibilityResult.model_validate(
        generate_proposal(source_code, incompatibilities).result())

    # Do not request approval for an incomplete proposal
    if not proposal.proposed_changes or not proposal.permission_questions:
        raise ValueError('Incomplete compatibility proposal.')



    while True:
    # Continue asking for confirmation until approval or rejection is clear from the user
        user_response = interrupt({
            'type': 'engine_incompatibility',
            'incompatibilities': proposal.incompatibility_summary,
            'proposed_changes': proposal.proposed_changes,
            'permission_questions': proposal.permission_questions,
    # Keywords temporary
            'instructions': 'Reply with exactly "approve all" to approve every change, or "reject" to reject them. Other responses require clarification.'})

        response_text = (
            user_response.strip().casefold()
            if isinstance(user_response, str)else '')

    # Explicit rejection requires no model
        if response_text == 'reject' or response_text == 'Rejected':
            status = 'rejected'
            break
    
        decision = CompatibilityDecision.model_validate(
            evaluate_response(
                proposal.proposed_changes,
                proposal.permission_questions,
                user_response).result())

    # Approval requires user approval and model approval
        if response_text == 'approve all' and decision.status == 'approved':
            status = 'approved'
            break

        if decision.status == 'rejected':
            status = 'rejected'
            break

    
    # Preserve previous approvals unless the new proposal was approved
    approved_changes = list(previous_approved_changes)

    if status == 'approved':
        already_approved = any(
            isinstance(change, dict)
            and change.get('proposed_changes') == proposal.proposed_changes
            for change in approved_changes)

        if not already_approved:
            approved_changes.append({
                'proposed_changes': proposal.proposed_changes,
                'user_response': user_response})

    return {
        'approved_changes': approved_changes,
        'incompatibility_status': status,
        'incompatibility_questions': proposal.permission_questions,
        'incompatibility_response': user_response}

# Router for incompatibility_node
def route_incompatibility(state):
    status = state.get('incompatibility_status')

    if status == 'approved':
        return 'questions'

    elif status == 'rejected':
        return END

    raise ValueError(f'Unexpected incompatibility status: {status}')




# Validation Node, Formatting node outputs here if 'ready'
# No model call
def validation_node(state):

    validation_errors = []
    checks_completed = []
    checks_skipped = []

# Get state info relevant to validation
    formatted_source = state.get('formatted_source')
    entrypoint_name = state.get('entrypoint_name')
    format_status = state.get('format_status')
    questions = state.get('questions')
    incompatibilities = state.get('incompatibilities')

    checks = [formatted_source, entrypoint_name, format_status, questions, incompatibilities]

    # Check formatted source
    if not isinstance(formatted_source, str) or not formatted_source.strip():
        validation_errors.append({
            'code': 'INVALID_SOURCE',
            'explanation': 'Formatted source must be a nonempty string.',
            'location': None})

    # Check entrypoint name
    if not isinstance(entrypoint_name, str) or not entrypoint_name.strip():
        validation_errors.append({
            'code': 'INVALID_ENTRYPOINT',
            'explanation': 'Entrypoint name must be a nonempty string.',
            'location': None})

    # Check formatting readiness
    if format_status != 'ready_for_validation':
        validation_errors.append({
            'code': 'INVALID_FORMAT_STATUS',
            'explanation': 'Formatting is not ready for validation.',
            'location': None})

    # Check unresolved questions
    if not isinstance(questions, list) or questions:
        validation_errors.append({
            'code': 'UNRESOLVED_QUESTIONS',
            'explanation': 'Questions must be an empty list.',
            'location': None})

    # Check unresolved incompatibilities
    if not isinstance(incompatibilities, list) or incompatibilities:
        validation_errors.append({
            'code': 'UNRESOLVED_INCOMPATIBILITIES',
            'explanation': 'Incompatibilities must be an empty list.',
            'location': None})

# ------
    # Validate code without executing it
    parsed_tree = None
    if isinstance(formatted_source, str) and formatted_source.strip():
        try:
            parsed_tree = ast.parse(formatted_source)
            checks.append('ast_parse')

        except (SyntaxError, ValueError) as error:
            checks.append('ast_parse') 
            validation_errors.append({
                'code': 'SYNTAX_ERROR',
                'explanation': str(error),
                'location': f'Line {error.lineno}' if isinstance(error, SyntaxError) else None
            })
            checks_skipped.append({
                'check': 'compile',
                'reason': 'Source parsing failed.'})
        else:
            try:
                compile(formatted_source, '<formatted_strategy>', 'exec')

            except (SyntaxError, ValueError) as error:
                validation_errors.append({
                    'code': 'COMPILATION_ERROR',
                    'explanation': str(error),
                    'location': f'Line {error.lineno}' if isinstance(error, SyntaxError) else None})
                

            finally:
                checks.append('compile')

    else:
        checks_skipped.extend([
            {'check': 'ast_parse', 'reason': 'Invalid or empty source.'},
            {'check': 'compile', 'reason': 'Invalid or empty source.'}])


# ------
        # Check that the entrypoint class is defined at module scope
    if parsed_tree is None:
        checks_skipped.append({
            'check': 'entrypoint_class',
            'reason': 'Source parsing did not succeed.'})

    elif not isinstance(entrypoint_name, str) or not entrypoint_name.strip():
        checks_skipped.append({
            'check': 'entrypoint_class',
            'reason': 'Invalid entrypoint name.'})

    else:
        checks.append('entrypoint_class')

        class_found = any(
            isinstance(node, ast.ClassDef)
            and node.name == entrypoint_name
            for node in parsed_tree.body)

        if not class_found:
            validation_errors.append({
                'code': 'MISSING_ENTRYPOINT_CLASS',
                'explanation': 'No module-level class matches entrypoint_name.',
                'location': None})

# ------
    # Check the evaluate() method without executing code
    if parsed_tree is None or not isinstance(entrypoint_name, str) or not entrypoint_name.strip():
        checks_skipped.append({
            'check': 'evaluate_signature',
            'reason': 'Valid parsed source and entrypoint name required.'})

    else:
        entrypoint_class = next(
            (node for node in parsed_tree.body
             if isinstance(node, ast.ClassDef)
             and node.name == entrypoint_name),
            None)

        if entrypoint_class is None:
            checks_skipped.append({
                'check': 'evaluate_signature',
                'reason': 'Entrypoint class not found.'})

        else:
            evaluate_method = next(
                (node for node in entrypoint_class.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == 'evaluate'),
                None)

            if evaluate_method is None:
                checks_skipped.append({
                    'check': 'evaluate_signature',
                    'reason': 'Method not directly declared; inheritance or dynamic assignment requires further verification.'})

            else:
                checks.append('evaluate_signature')

                if isinstance(evaluate_method, ast.AsyncFunctionDef):
                    validation_errors.append({
                        'code': 'ASYNC_EVALUATE',
                        'explanation': 'evaluate() must be synchronous.',
                        'location': f'Line {evaluate_method.lineno}'})

                else:
                    positional_args = (evaluate_method.args.posonlyargs + evaluate_method.args.args)
                    required_positional = (
                        len(positional_args) - len(evaluate_method.args.defaults))

                    # Instance binding supplies self; the engine supplies two market-data arguments.
                    accepts_engine_arguments = (
                        required_positional <= 3
                        and (
                            len(positional_args) >= 3
                            or evaluate_method.args.vararg is not None))

                    if (
                        not accepts_engine_arguments
                        or any(default is None for default in evaluate_method.args.kw_defaults)):
                        validation_errors.append({
                            'code': 'INVALID_EVALUATE_SIGNATURE',
                            'explanation': 'evaluate() must accept self and two positional market-data arguments without additional required keyword-only arguments.',
                            'location': f'Line {evaluate_method.lineno}'})

                        # -----
        # Screen for prohibited operations without executing code

    # If parsing failed, skip this check because there is no AST to inspect
    if parsed_tree is None:
        checks_skipped.append({
            'check': 'prohibited_operations_scan',
            'reason': 'Source parsing failed.'})

    else:
        # Record that the security scan is being performed
        checks.append('prohibited_operations_scan')

        # Modules that strategies are not permitted to import
        prohibited_modules = {
            'os', 'sys', 'subprocess', 'socket',
            'requests', 'httpx', 'urllib',
            'sqlite3', 'psycopg', 'psycopg2',
            'sqlalchemy', 'pathlib', 'importlib',
            'ctypes', 'builtins'}

        # Built-in functions that could access files or execute arbitrary code
        prohibited_functions = {
            'open', 'exec', 'eval', 'compile',
            '__import__', 'input'}

        # Methods associated with external data loading, file access, or databases
        prohibited_methods = {
            'read_csv', 'read_excel', 'read_parquet',
            'read_sql', 'read_json', 'loadtxt',
            'genfromtxt', 'to_sql', 'urlopen',
            'read_text', 'read_bytes',
            'write_text', 'write_bytes'}

        # Walk through every part of the strategy's syntax tree
        for node in ast.walk(parsed_tree):
            violation = None

            # Check imported modules against the prohibited module names
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    modules = [node.module or '']

                # Examine each import and identify restricted modules
                for module in modules:
                    if module.split('.')[0] in prohibited_modules:
                        violation = f'Prohibited import: {module}'
                    elif module == 'app' or (
                        module.startswith('app.')
                        and module != 'app.models'
                    ):
                        violation = f'Prohibited application access: {module}'

                # Prevent relative imports that could access other project files
                if isinstance(node, ast.ImportFrom) and node.level:
                    violation = 'Relative imports are not permitted.'

            # Check whether the strategy calls a prohibited function or method
            elif isinstance(node, ast.Call):

                # Check direct function calls such as open() or exec()
                if isinstance(node.func, ast.Name):
                    if node.func.id in prohibited_functions:
                        violation = f'Prohibited function: {node.func.id}'

                # Check method calls such as read_csv() or read_text()
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr in prohibited_methods:
                        violation = f'Prohibited operation: {node.func.attr}'

            # Save any detected violation with its explanation and line number
            if violation:
                validation_errors.append({
                    'code': 'PROHIBITED_OPERATION',
                    'explanation': violation,
                    'location': f'Line {node.lineno}'})
    # STEP 7: Validate strategy imports against the approved allowlist

    # If parsing failed, we cannot inspect the strategy's imports
    if parsed_tree is None:
        checks_skipped.append({
            'check': 'dependency_policy',
            'reason': 'Source parsing failed.'
        })

    else:
        # Record that dependency validation was performed
        checks_completed.append('dependency_policy')

        # Approved standard-library modules and application exceptions
        allowed_modules = {
            'math',
            'statistics',
            'datetime',
            'collections',
            'collections.abc',
            'itertools',
            'functools',
            'decimal',
            'fractions',
            'typing',
            'enum',
            'operator',
            'app.models',
            '__future__'}

        # Examine every import statement in the generated strategy
        for node in ast.walk(parsed_tree):

            # Handle regular imports: import math, import os, etc.
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in allowed_modules:
                        validation_errors.append({
                            'code': 'UNAPPROVED_IMPORT',
                            'explanation': f'Import not permitted: {alias.name}',
                            'location': f'Line {node.lineno}'})

            # Handle from-import statements: from math import sqrt
            elif isinstance(node, ast.ImportFrom):

                # Relative imports are not permitted
                if node.level > 0:
                    validation_errors.append({
                        'code': 'UNAPPROVED_IMPORT',
                        'explanation': 'Relative imports are not permitted.',
                        'location': f'Line {node.lineno}'})

                # The imported module must appear in the allowlist
                elif node.module not in allowed_modules:
                    validation_errors.append({
                        'code': 'UNAPPROVED_IMPORT',
                        'explanation': f'Import not permitted: {node.module}',
                        'location': f'Line {node.lineno}'})

                # Prevent wildcard imports that obscure imported names
                elif any(alias.name == '*' for alias in node.names):
                    validation_errors.append({
                        'code': 'WILDCARD_IMPORT',
                        'explanation': 'Wildcard imports are not permitted.',
                        'location': f'Line {node.lineno}'})

        # STEP 8: Check whether the strategy constructor requires arguments

    # We need valid source and an entrypoint name before inspecting the class
    if parsed_tree is None or not isinstance(entrypoint_name, str) or not entrypoint_name.strip():
        checks_skipped.append({
            'check': 'constructor_signature_static',
            'reason': 'Valid source and entrypoint name required.'
        })

    else:
        # Find the entrypoint class in the generated source
        entrypoint_class = next(
            (node for node in parsed_tree.body
             if isinstance(node, ast.ClassDef)
             and node.name == entrypoint_name),
            None
        )

        if entrypoint_class is None:
            checks_skipped.append({
                'check': 'constructor_signature_static',
                'reason': 'Entrypoint class not found.'
            })

        # Inheritance, class decorators, and metaclasses may change construction
        elif entrypoint_class.bases or entrypoint_class.keywords or entrypoint_class.decorator_list:
            checks_skipped.append({
                'check': 'constructor_signature_static',
                'reason': 'Class construction may depend on inheritance, decorators, or metaclasses.'
            })

        else:
            # Locate explicitly declared constructors
            constructors = [
                node for node in entrypoint_class.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == '__init__'
            ]

            # Check whether constructor behavior may have been modified
            custom_construction = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == '__new__'
                for node in entrypoint_class.body
            )

            # Handle constructor definitions we cannot reliably inspect
            if (
                custom_construction
                or len(constructors) > 1
                or (constructors and constructors[0].decorator_list)
            ):
                checks_skipped.append({
                    'check': 'constructor_signature_static',
                    'reason': 'Constructor requires additional verification.'
                })

            else:
                checks_completed.append('constructor_signature_static')

                # No __init__ means the class normally inherits object.__init__
                if constructors:
                    constructor = constructors[0]

                    # Count positional parameters, including the instance parameter
                    positional = (
                        constructor.args.posonlyargs
                        + constructor.args.args
                    )

                    # Default values make trailing positional arguments optional
                    required_positional = (
                        len(positional) - len(constructor.args.defaults)
                    )

                    # Check for required keyword-only arguments
                    required_keywords = any(
                        default is None
                        for default in constructor.args.kw_defaults
                    )

                    # Identify constructor signatures that cannot work normally
                    if (
                        isinstance(constructor, ast.AsyncFunctionDef)
                        or not positional
                        or required_positional > 1
                        or required_keywords
                    ):
                        validation_errors.append({
                            'code': 'INVALID_CONSTRUCTOR',
                            'explanation': 'Strategy constructor has an invalid signature or requires arguments.',
                            'location': f'Line {constructor.lineno}'
                        })
        # STEP 9: Check for unsupported engine behavior and invalid signals

    # Without a parsed AST, these checks cannot run
    if parsed_tree is None:
        checks_skipped.extend([
            {
                'check': 'unsupported_engine_operations',
                'reason': 'Source parsing failed.'
            },
            {
                'check': 'signal_returns_static',
                'reason': 'Source parsing failed.'
            }
        ])

    else:
        # PART 1: Detect direct use of unsupported engine operations
        checks_completed.append('unsupported_engine_operations')

        # Order-related functions and methods unavailable to strategies
        prohibited_order_calls = {
            'submit_order',
            'place_order',
            'cancel_order',
            'market_order',
            'limit_order',
            'stop_order',
            'partial_exit',
            'reverse_position'
        }

        # Application objects that strategies must not access directly
        prohibited_access = {'engine', 'account', 'broker'}

        # Examine all operations in the generated source
        for node in ast.walk(parsed_tree):

            # Look for calls to unsupported order functions or methods
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    function_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    function_name = node.func.id
                else:
                    function_name = None

                if function_name in prohibited_order_calls:
                    validation_errors.append({
                        'code': 'UNSUPPORTED_ORDER_OPERATION',
                        'explanation': f'Unsupported order operation: {function_name}',
                        'location': f'Line {node.lineno}'
                    })

            # Detect direct references to restricted application objects
            if isinstance(node, ast.Attribute):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == 'self'
                    and node.attr in prohibited_access
                ):
                    validation_errors.append({
                        'code': 'RESTRICTED_ENGINE_ACCESS',
                        'explanation': f'Direct access to self.{node.attr} is not permitted.',
                        'location': f'Line {node.lineno}'
                    })

        # PART 2: Inspect explicit return statements in evaluate()

        # Find the declared entrypoint class
        entrypoint_class = next(
            (node for node in parsed_tree.body
             if isinstance(node, ast.ClassDef)
             and node.name == entrypoint_name),
            None
        )

        # Find evaluate() inside that class
        evaluate_method = None

        if entrypoint_class is not None:
            evaluate_method = next(
                (node for node in entrypoint_class.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == 'evaluate'),
                None
            )

        # Skip the return check if the method cannot be inspected
        if evaluate_method is None:
            checks_skipped.append({
                'check': 'signal_returns_static',
                'reason': 'A directly declared evaluate() method was not found.'
            })

        else:
            checks_completed.append('signal_returns_static')

            # Only these four Signal members are supported by the engine
            allowed_signals = {'BUY', 'SELL', 'EXIT', 'HOLD'}

            # Examine statements in evaluate(), including conditional branches
            pending = list(evaluate_method.body)

            while pending:
                current = pending.pop()

                # Ignore nested functions because their returns belong elsewhere
                if isinstance(current, (
                    ast.FunctionDef, ast.AsyncFunctionDef,
                    ast.ClassDef, ast.Lambda
                )):
                    continue

                if isinstance(current, ast.Return):
                    value = current.value

                    # Explicit literal returns cannot be application Signal objects
                    if value is None or isinstance(value, ast.Constant):
                        validation_errors.append({
                            'code': 'INVALID_SIGNAL_RETURN',
                            'explanation': 'evaluate() returns a literal or None instead of an application Signal.',
                            'location': f'Line {current.lineno}'
                        })

                    # Detect references to unsupported Signal members
                    elif (
                        isinstance(value, ast.Attribute)
                        and isinstance(value.value, ast.Name)
                        and value.value.id == 'Signal'
                        and value.attr not in allowed_signals
                    ):
                        validation_errors.append({
                            'code': 'INVALID_SIGNAL_MEMBER',
                            'explanation': f'Unsupported Signal member: {value.attr}',
                            'location': f'Line {current.lineno}'
                        })

                # Continue inspecting nested statements and branches
                pending.extend(ast.iter_child_nodes(current))

        # STEP 10: Determine validation outcome and return findings

    # Record required runtime checks that cannot run without
    # a verified, restricted execution environment
    checks_skipped.extend([
        {
            'check': 'runtime_constructor',
            'reason': 'Restricted execution environment not implemented.'
        },
        {
            'check': 'runtime_evaluate_and_signal',
            'reason': 'Cannot verify callable behavior or Signal returns without restricted execution.'
        },
        {
            'check': 'runtime_smoke_tests',
            'reason': 'Synthetic bar tests, exceptions, and timeouts not yet implemented.'
        },
        {
            'check': 'full_behavior_verification',
            'reason': 'Current static checks cannot establish all engine behavior and lookahead restrictions.'
        }
    ])

    # A detected defect takes priority over checks that were skipped
    if validation_errors:
        validation_status = 'failed'

    # No detected defects, but required checks remain incomplete
    elif checks_skipped:
        validation_status = 'blocked'

    # Every required check must be completed successfully to pass
    else:
        validation_status = 'passed'

    # Package all findings using the existing Pydantic schema
    result = ValidationResult(
        validation_status=validation_status,
        validation_errors=validation_errors,
        checks_completed=checks_completed,
        checks_skipped=checks_skipped
    )

    # Return the structured results to LangGraph state
    return result.model_dump()

# Graph
graph = StateGraph(State)
graph.add_node("file_path", file_path_node)
graph.add_node("file_check", file_check_node)
graph.add_node('examine_file', examine_file_node)
graph.add_node("questions", questions_node)
graph.add_node("incompatibility", hard_incompatibility_node)
graph.add_node("formatting", code_formatting_node)
graph.add_node("validation", validation_node)


graph.add_edge(START, "file_path")
graph.add_edge("file_path", "file_check")


graph.add_conditional_edges("file_check",route_file_check,{"examine_file": "examine_file","file_path": "file_path",},)
graph.add_conditional_edges('examine_file',route_examination,{'questions': 'questions', 'incompatibility': 'incompatibility', 'formatting': 'formatting',},)
graph.add_conditional_edges('formatting',route_formatting,{'incompatibility': 'incompatibility','questions': 'questions','validation': 'validation',})
graph.add_conditional_edges('questions',route_questions,{'questions': 'questions','formatting': 'formatting'})
graph.add_conditional_edges('incompatibility',route_incompatibility,{'questions': 'questions','incompatibility': 'incompatibility',END: END,})
