
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

model = init_chat_model('gpt-6-luna', temperature = 0.1)

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
    status: Literal['ready', 'needs_clarification', 'hard_incompatibility'] = Field(description = 'The routing outcome produced by examination.')

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
    except UnicodeDecodeError:
        return {
            "file_valid": False,
            "file_error": "File is not valid UTF-8."}
    except SyntaxError as error:
        return {
            "file_valid": False,
            "file_error": f"Invalid Python syntax: {error}"}

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
    source_code = state.get("source_code")

    # Model looks at the source code from the user file and determines the appropriate routing and questions and produces reasons for its actions.
    result = model.with_structured_output(ExamineResult).invoke([
        SystemMessage(content = system_prompt_node_3), HumanMessage(content = f'Examine the following strategy source:\n\n{source_code}')])

    # Model returns this exact schema output
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
            state.get('approved_changes', [])
        ).result()
    )

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




# Validation Node, Formatting outputs here
def validation_node(state):
    pass 





    
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
