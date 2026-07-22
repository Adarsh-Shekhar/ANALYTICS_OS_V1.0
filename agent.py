"""
LangGraph Agent Workflow for ANALYTICS_OS_V1.0.

This module defines the multi-step AI agent state graph responsible for:
1. Translating natural language questions into valid DuckDB SQL queries.
2. Executing SQL queries safely in an execution sandbox.
3. Automatically correcting SQL syntax/runtime errors via recursive retries.
4. Recommending chart visualizer configurations based on query results.
5. Synthesizing clear, plain-English business insights from the resulting dataset.
"""

from typing import TypedDict, Annotated, List, Dict, Any, Optional
import operator
import duckdb
import json
import os
import re
from dotenv import load_dotenv

from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_groq import ChatGroq

# Load environment variables (e.g., GROQ_API_KEY) from .env
load_dotenv()


# =====================================================================
# State Definition
# =====================================================================
class AgentState(TypedDict):
    """
    Represents the shared memory state passed between LangGraph nodes.
    """
    question: str
    schema_metadata: Dict[str, Dict[str, str]]
    sample_rows: Dict[str, List[Dict[str, Any]]]
    generated_sql: Optional[str]
    execution_error: Optional[str]
    final_data: Optional[List[Dict[str, Any]]]
    chart_config: Optional[Dict[str, Any]]
    analysis_summary: Optional[str]
    messages: Annotated[List[BaseMessage], operator.add]


# =====================================================================
# LLM Initialization
# =====================================================================
# Initialize Groq LLaMA 3.3 70B model with deterministic (0 temperature) output
llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)


# =====================================================================
# Helper Utilities
# =====================================================================
def extract_sql(text: str) -> str:
    """
    Extracts raw SQL code from an LLM response string, removing markdown code blocks.
    
    Args:
        text (str): Raw string output from the LLM.
        
    Returns:
        str: Cleaned SQL query ready for execution.
    """
    match = re.search(r"```sql\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.replace("```sql", "").replace("```", "").strip()


# =====================================================================
# Agent Node Functions
# =====================================================================
def sql_generator_node(state: AgentState):
    """
    Generates a DuckDB-compliant SQL query based on database schema, sample rows,
    conversation history, and any previous execution error context.
    """
    print("Agent: Generating SQL query...")
    schema = state["schema_metadata"]
    sample_rows = state["sample_rows"]
    
    # Supply previous error logs to prompt if self-correcting
    error_context = ""
    if state.get("execution_error"):
        error_context = (
            f"\nYOUR PREVIOUS ATTEMPT FAILED WITH THIS ERROR: {state['execution_error']}\n"
            "Please fix the syntax or logic error and try again."
        )

    system_instruction = (
        "You are an expert SQL engineer specializing in analytical data retrieval.\n"
        "Your task is to write a single, valid DuckDB SQL query that answers the user's latest question.\n\n"
        "DATABASE SCHEMA DETAILS:\n"
        "{schema}\n\n"
        "SAMPLE DATA ROWS:\n"
        "{sample_rows}\n\n"
        "CRITICAL RULES:\n"
        "1. Return ONLY the raw SQL code. Do NOT wrap it in markdown code blocks.\n"
        "2. If the user asks a follow-up question, use the conversation history to understand the context.\n"
        "3. Only query tables and columns explicitly listed in the schema.{error_context}"
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_instruction),
        MessagesPlaceholder(variable_name="messages")
    ])

    chain = prompt | llm | StrOutputParser()
    
    response_str = chain.invoke({
        "schema": str(schema),
        "sample_rows": str(sample_rows),
        "error_context": error_context,
        "messages": state["messages"]
    })
    
    generated_query = extract_sql(response_str)

    return {
        "generated_sql": generated_query,
        "messages": [AIMessage(content=f"Generated SQL Query: {generated_query}")]
    }


def execute_sql_node(state: AgentState):
    """
    Executes the generated SQL query against the session DuckDB database.
    Captures output datasets on success, or logs execution errors on failure.
    """
    query = state.get("generated_sql", "")
    print(f"Sandbox: Executing query -> {query}")
    
    try:
        # Open in read-only mode to safeguard the persistent DB
        safe_con = duckdb.connect(database='app_data.duckdb', read_only=True)
        result = safe_con.execute(query).df().to_dict(orient="records")
        safe_con.close()
        
        return {
            "final_data": result,
            "execution_error": None
        }
    except Exception as e:
        error_msg = str(e)
        print(f"Sandbox Error encountered: {error_msg}")
        return {
            "execution_error": error_msg,
            "final_data": None
        }


def visualization_node(state: AgentState):
    """
    Determines the appropriate chart type (bar, line, pie, or none) and axis mappings
    for rendering frontend visualizations using Chart.js.
    """
    question = state["question"]
    data = state["final_data"]
    
    system_instruction = (
        "You are an expert data visualization AI.\n"
        "Your task is to analyze the user's question and the provided data, and determine the best way to graph it.\n"
        "Output your response STRICTLY as a valid JSON object. Do not wrap it in markdown code blocks.\n\n"
        "JSON FORMAT EXPECTED:\n"
        "{{\n"
        '  "chart_type": "bar" | "line" | "pie" | "none",\n'
        '  "x_axis_key": "name_of_column_for_x_axis",\n'
        '  "y_axis_key": "name_of_column_for_y_axis",\n'
        '  "title": "A short, descriptive title for the chart"\n'
        "}}\n\n"
        "RULES:\n"
        "1. If the data is just a single number (e.g., 'What is the total revenue?'), set chart_type to 'none'.\n"
        "2. If the data involves a time series (dates), prefer 'line'.\n"
        "3. If comparing categories, prefer 'bar' or 'pie'."
    )
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_instruction),
        ("human", "Question: {question}\nData: {data}")
    ])
    
    chain = prompt | llm | StrOutputParser()
    
    response_str = chain.invoke({
        "question": question,
        "data": str(data)
    })
    
    try:
        clean_json = response_str.strip().strip("```json").strip("```").strip()
        chart_config = json.loads(clean_json)
    except json.JSONDecodeError:
        chart_config = {"chart_type": "none", "error": "Could not generate chart configuration"}
        
    return {
        "chart_config": chart_config,
        "messages": [AIMessage(content=f"Generated Chart Config: {chart_config}")]
    }


def analyst_node(state: AgentState):
    """
    Summarizes dataset findings into a high-level 2-3 sentence business summary.
    """
    question = state["question"]
    data = state["final_data"]
    
    if state.get("execution_error") or not data:
        return {"analysis_summary": "No data available to analyze."}

    system_instruction = (
        "You are an expert data analyst.\n"
        "Review the user's question and the extracted JSON data.\n"
        "Write a brief, insightful, 2-3 sentence summary of the findings.\n"
        "Point out the most important numbers, trends, or outliers.\n"
        "Do NOT explain how you got the data or mention SQL. Just provide the business insight."
    )
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_instruction),
        ("human", "Question: {question}\nData: {data}")
    ])
    
    chain = prompt | llm | StrOutputParser()
    
    summary = chain.invoke({
        "question": question,
        "data": str(data)
    })
    
    return {
        "analysis_summary": summary.strip(),
        "messages": [AIMessage(content=f"Analysis: {summary.strip()}")]
    }


# =====================================================================
# Graph Routing Logic
# =====================================================================
def should_continue(state: AgentState) -> str:
    """
    Determines whether to retry SQL generation (on execution error) or proceed
    to visualization and analysis.
    """
    if state.get("execution_error"):
        return "generate"
    return "visualize"


# =====================================================================
# StateGraph Construction & Compilation
# =====================================================================
workflow = StateGraph(AgentState)

# Register state nodes
workflow.add_node("sql_generator", sql_generator_node)
workflow.add_node("execution_sandbox", execute_sql_node)
workflow.add_node("visualizer", visualization_node)
workflow.add_node("analyst", analyst_node)

# Flow connections
workflow.add_edge(START, "sql_generator")
workflow.add_edge("sql_generator", "execution_sandbox")

workflow.add_conditional_edges(
    "execution_sandbox",
    should_continue,
    {
        "generate": "sql_generator",
        "visualize": "visualizer"
    }
)

workflow.add_edge("visualizer", "analyst")
workflow.add_edge("analyst", END)

# In-memory checkpointer for multi-turn conversation persistence
memory = MemorySaver()

# Compile the executable agent app
app = workflow.compile(checkpointer=memory)