from asyncio import graph

from dotenv import load_dotenv
import ast
import uuid
from pathlib import Path
from typing import Literal

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
class State(MessageState):
    file_path: str | None
    pass



# Node functions

def file_path_node(state):
    # User Provides File Path
    file_path = interrupt('Paste the exact/full file path to the encoded strategy you want to backtest:')
    return {**state, 'file_path': file_path, 'file_error': None,}



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
    if state.get("file_valid"):
        return "examine_file"
    return "file_path"


def examine_file_node(state):







# Graph
graph = StateGraph(State)
graph.add_node("file_path", file_path_node)
graph.add_node("file_check", file_check_node)
graph.add_node('examine_file', examine_file_node)

graph.add_edge(START, "file_path")
graph.add_edge("file_path", "file_check")

graph.add_conditional_edges("file_check",route_file_check,{"examine_file": "examine_file","file_path": "file_path",},)