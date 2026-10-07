from dotenv import load_dotenv
import requests
from dataclasses import dataclass
from pathlib import Path


from langchain.agents import create_agent
from langchain.tools import tool, ToolRuntime
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver



load_dotenv()

# Tools
@tool('read_user_file', description = 'Check if provided file path exists, and return its contents as a string.')
def read_user_file(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f'File not found: {path}')
    elif not path.is_file():
        raise ValueError(f'Path is not a file: {path}')
    return path.read_bytes().decode('utf-8')



# Formats & Context

# Harness/Model
intake_agent = create_agent(model = init_chat_model("gpt-6-luna", temperature = 0.1), tools = [])

message = {'role': "user", 'content': [{'type': 'text', 'text': }]}

