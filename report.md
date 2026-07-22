# Project Overview: ANALYTICS_OS_V1.0

This project is an LLM-powered data analytics assistant. It features a retro "pixel-art" themed frontend, a FastAPI backend, and an intelligent LangGraph-based AI agent that translates user questions into SQL, executes them against an uploaded dataset using DuckDB, visualizes the results, and provides analytical summaries.

## Core Architecture

The architecture is divided into three main components:
1. **Frontend (`frontend/index.html`)**: A single-page application built with HTML, CSS (TailwindCSS), and vanilla JavaScript.
2. **Backend API (`main.py`)**: A FastAPI server that handles file ingestion, schema extraction, and chat interactions.
3. **AI Agent (`agent.py`)**: A LangGraph workflow that orchestrates the Large Language Model (LLM) to process natural language data queries.

---

## Detailed Component Breakdown

### 1. The AI Agent (`agent.py`)
The AI agent acts as the brain of the application. It is built using LangChain and LangGraph and leverages `ChatGroq` (specifically the `llama-3.3-70b-versatile` model) to understand user queries and generate SQL.

**State Management:**
The state of the agent during a conversation is managed using a `TypedDict` called `AgentState`. It stores:
- The user's question and conversation history (`messages`).
- Database schema metadata and sample rows.
- The generated SQL query and any execution errors.
- The final queried data, chart configurations, and the final analysis summary.

**Workflow Nodes:**
The LangGraph workflow consists of four specialized, sequential nodes:
- **`sql_generator`**: Takes the user's question, conversation history, and database schema to generate a valid DuckDB SQL query. It has error recovery capabilities; if a previous query failed, the error message is injected into the prompt so the LLM can attempt to fix it.
- **`execution_sandbox`**: Safely executes the generated SQL against a read-only DuckDB connection (`app_data.duckdb`). If the query fails, it returns the error to the state so the `sql_generator` can try again.
- **`visualizer`**: An AI node that analyzes the user's question and the successfully retrieved data to determine the best chart type (bar, line, pie, or none) and configures the X and Y axes.
- **`analyst`**: Takes the final data and user question, and writes a brief 2-3 sentence business insight/summary of the findings in plain English, abstracting away the technical SQL details.

**Routing Logic:**
The workflow uses conditional edges for its routing logic. If `execution_sandbox` encounters an error, the state loops back to `sql_generator`. If successful, the flow proceeds to the `visualizer`, then the `analyst`, and finally terminates. It utilizes LangGraph's `MemorySaver` to persist conversation history across chat requests.

### 2. The Backend API (`main.py`)
The backend is built with FastAPI and serves as the bridge between the frontend application and the LangGraph AI agent.

**Endpoints:**
- `GET /`: Serves the static `frontend/index.html` file to the user.
- `POST /ingest-file/`: Handles dataset uploads. It accepts a file and a unique `session_id`. 
  - Supported formats include `.csv`, `.xlsx`, `.xls`, `.sql`, `.sqlite`, and `.db`. 
  - It creates a unique, isolated DuckDB database file for the user's session (e.g., `session_{session_id}.duckdb`) and loads the uploaded data into it. 
  - It extracts the schema (tables and column definitions) and a few sample rows, returning this metadata to the frontend to populate the UI.
- `POST /ask/`: Receives the user's question and `session_id`. 
  - It connects to the session's specific DuckDB database to extract the schema and sample data. 
  - It then invokes the LangGraph workflow (`agent.py`) with the `thread_id` set to the `session_id` to maintain conversation memory context. 
  - It returns the generated SQL, the queried data, chart configurations, and the analysis summary back to the frontend.

### 3. The Frontend (`frontend/index.html`)
The frontend is a single-page HTML application styled with TailwindCSS (via CDN) to look like a retro 90s operating system named "ANALYTICS_OS_V1.0".

**Key Features:**
- **UI Design**: Uses a custom pixel-art aesthetic with specific color palettes, fonts (`JetBrains Mono` and `Space Mono`), and retro UI elements (pixelated windows, titlebars, and buttons with interactive active states).
- **Session Management**: Automatically generates a pseudo-UUID (`session_id`) on page load to keep user sessions isolated on the backend.
- **Data Ingestion View**: Features a drag-and-drop zone for uploading files. Once a file is processed, it displays an interactive, expandable tree view of the uploaded database schema (tables and columns).
- **Chat Interface**: A retro terminal-like chat log where users can ask questions. It visually distinguishes between system messages, user queries, and AI responses.
- **Dynamic Chart Rendering**: Includes custom JavaScript logic (`renderChartBlock`) to dynamically render basic bar charts purely using HTML `div` elements and Tailwind utility classes based on the `chart_config` and data returned by the AI agent.

---

## Technology Stack

- **Python**: Core programming language for backend and AI logic.
- **FastAPI**: Asynchronous web framework for building the API endpoints.
- **DuckDB**: In-process SQL OLAP database management system, chosen for its speed in querying the uploaded datasets.
- **LangChain & LangGraph**: Frameworks for developing applications powered by language models, used specifically for managing prompts, LLM interactions, stateful memory, and complex multi-node agentic workflows.
- **Groq (Llama 3)**: The LLM provider and model used for text generation, SQL generation, and data analysis.
- **Pandas**: Used for parsing Excel files during data ingestion.
- **HTML / CSS / JavaScript**: Vanilla web technologies for the frontend, utilizing TailwindCSS via CDN for rapid styling without a build step.

## Application Flow Summary

1. **Initialization**: User visits the page; a unique `session_id` is generated.
2. **Ingestion**: User uploads a file (e.g., CSV). The backend creates a session-specific DuckDB database, loads the data, and returns the schema to the UI.
3. **Querying**: User types a question (e.g., "What are the top 5 sales?").
4. **Agent Processing**: The LangGraph agent receives the question and schema, generates DuckDB SQL, safely executes it, decides how to visualize the result, and writes a human-readable summary.
5. **Response**: The frontend receives the agent's payload and renders the summary, the raw SQL, and dynamically draws a chart if applicable.
