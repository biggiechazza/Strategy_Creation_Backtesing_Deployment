from asyncio import graph

from System_Prompt import system_prompt_node_3
from dotenv import load_dotenv
import ast
import uuid
from pathlib import Path
from typing import Literal, TypedDict

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage, SystemMessage
from langchain.tools import tool

from langgraph.graph import MessagesState, StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt, Command

load_dotenv()

model = init_chat_model('gpt-6-luna', temperature = 0.1)

# Shared Memory
class State(MessagesState):
    file_path: str | None
    pass


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


# NOT a node, if the file wasn't valid return to the file_path node
def route_file_check(state):
    if state.get('file_valid') == True and state.get('file_error') == None:
        return 'examine_file'
    return 'file_path'

# Routing decisions begin
# Also not a node
class RoutingResult(BaseModel):
    status: Literal['ready', 'needs_clarification', 'hard_incompatibility'] = Field(description = 'The routing outcome produced by examination.')

    questions: list[str] = Field(
        default_factory=list,
        description='Material questions the user must answer before formatting.')

    clarifiable_issues: list[str] = Field(
        default_factory=list,
        description='Ambiguities that can be resolved through user clarification.')

    incompatibilities: list[str] = Field(
        default_factory=list,
        description='Engine incompatibilities identified in the strategy, or it is not a identifiable trading strategy.')

    formatting_notes: list[str] = Field(
        default_factory=list,
        description='Source established facts the formatting node must preserve.')



# Node 3, three potential paths after: questions_node or code_formatting_node or hard_incompatibility_node
def examine_file_node(state):
    source_code = state.get("source_code")

    # Model looks at the source code from the user file and determines the appropriate routing and questions and produces reasons for its actions.
    result = model.with_structured_output(RoutingResult).invoke([
        SystemMessage(content = system_prompt_node_3), HumanMessage(content = f'Examine the following strategy source:\n\n{source_code}')])

    # Model returns this exact schema output
    return {
        'examination_status': result.status,
        'questions': result.questions,
        'clarifiable_issues': result.clarifiable_issues,
        'incompatibilities': result.incompatibilities,
        'formatting_notes': result.formatting_notes,}


# Grab 'examination_status' from the models output and tell the graph which path to take.
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


# PATH 1 Node 4: Direct to Formatting
def code_formatting_node(state):
    pass

# PATH 2 Node 4: Questions
def questions_node(state):
    pass

# PATH 3 Node 4: Hard Incompatibility
def hard_incompatibility_node(state):
    pass


# Graph
graph = StateGraph(State)
graph.add_node("file_path", file_path_node)
graph.add_node("file_check", file_check_node)
graph.add_node('examine_file', examine_file_node)
graph.add_node("questions", questions_node)
graph.add_node("incompatibility", hard_incompatibility_node)
graph.add_node("formatting", code_formatting_node)

graph.add_edge(START, "file_path")
graph.add_edge("file_path", "file_check")

graph.add_conditional_edges("file_check",route_file_check,{"examine_file": "examine_file","file_path": "file_path",},)
graph.add_conditional_edges('examine_file',route_examination,{'questions': 'questions', 'incompatibility': 'incompatibility', 'formatting': 'formatting',},)