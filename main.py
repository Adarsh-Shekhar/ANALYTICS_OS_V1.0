"""
FastAPI Server for ANALYTICS_OS_V1.0.

Provides HTTP API endpoints for:
- Serving the Single Page Application (SPA) frontend.
- Ingesting heterogeneous datasets (CSV, Excel, SQL scripts, SQLite DBs) into isolated DuckDB sessions.
- Interfacing with the LangGraph data analytics agent to execute SQL queries and return visualization payloads.
"""

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
import duckdb
import os
import tempfile
import re
import pandas as pd

from agent import app as langgraph_app
from langchain_core.messages import HumanMessage

app = FastAPI(
    title="ANALYTICS_OS_V1.0 API",
    description="Backend API for AI-powered analytics agent with session-isolated DuckDB processing.",
    version="1.0.0"
)


@app.get("/", summary="Serve Retro Dashboard SPA")
async def root():
    """
    Serves the main frontend index.html user interface.
    """
    return FileResponse("frontend/index.html")


# =====================================================================
# Endpoint 1: Universal Dataset Ingestion (Session-Aware)
# =====================================================================
@app.post("/ingest-file/", summary="Ingest dataset and build schema metadata")
async def ingest_and_extract_schema(
    file: UploadFile = File(...),
    session_id: str = Form(...)
):
    """
    Accepts uploaded dataset files (.csv, .xlsx, .xls, .sql, .db, .sqlite),
    ingests them into a session-specific DuckDB database, and returns schema metadata.
    """
    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, file.filename)
    
    # Isolate user storage using session ID
    db_file = f"session_{session_id}.duckdb"
    
    # Save incoming stream to temporary buffer file
    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())
        
    try:
        ext = file.filename.split('.')[-1].lower()
        base_name = re.sub(r'\W+', '_', file.filename.split('.')[0].lower())
        
        # Connect to DuckDB session instance
        with duckdb.connect(database=db_file, read_only=False) as con:
            
            # 1. Ingest CSV files
            if ext == 'csv':
                con.execute(
                    f"CREATE OR REPLACE TABLE {base_name} AS SELECT * FROM read_csv('{file_path}', auto_detect=true)"
                )
                extracted_tables = [base_name]

            # 2. Ingest Excel spreadsheets (.xlsx / .xls)
            elif ext in ['xlsx', 'xls']:
                df = pd.read_excel(file_path)
                con.execute(f"CREATE OR REPLACE TABLE {base_name} AS SELECT * FROM df")
                extracted_tables = [base_name]

            # 3. Execute SQL scripts (.sql)
            elif ext == 'sql':
                with open(file_path, 'r', encoding='utf-8') as f:
                    sql_script = f.read()
                con.execute(sql_script)
                tables_raw = con.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()
                extracted_tables = [row[0] for row in tables_raw]

            # 4. Attach & convert SQLite / DB files (.sqlite / .db)
            elif ext in ['db', 'sqlite']:
                con.execute("INSTALL sqlite; LOAD sqlite;")
                con.execute(f"ATTACH '{file_path}' AS sqlite_db (TYPE SQLITE);")
                tables_raw = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                
                extracted_tables = []
                for row in tables_raw:
                    t_name = row[0]
                    con.execute(f"CREATE OR REPLACE TABLE {t_name} AS SELECT * FROM sqlite_db.{t_name}")
                    extracted_tables.append(t_name)
            else:
                return {"status": "error", "message": "Unsupported file format."}
            
            # Inspect schemas and fetch sample data rows for agent context
            full_schema = {}
            full_sample_data = {}
            
            for t_name in extracted_tables:
                schema_raw = con.execute(f"DESCRIBE {t_name}").fetchall()
                full_schema[t_name] = {row[0]: row[1] for row in schema_raw}
                full_sample_data[t_name] = con.execute(f"SELECT * FROM {t_name} LIMIT 1").df().to_dict(orient="records")

        return {
            "status": "success",
            "session_id": session_id,
            "uploaded_file": file.filename,
            "tables_loaded": extracted_tables,
            "schema": full_schema
        }
        
    finally:
        # Clean up temporary uploaded file from disk
        if os.path.exists(file_path):
            os.remove(file_path)


# =====================================================================
# Endpoint 2: Conversational Data Querying (Session-Aware)
# =====================================================================
class QueryRequest(BaseModel):
    question: str
    session_id: str


@app.post("/ask/", summary="Execute natural language query via LangGraph agent")
async def ask_data_agent(request: QueryRequest):
    """
    Passes user query and dataset schema context to the LangGraph AI agent.
    Returns generated SQL, query result dataset, chart configurations, and business summary.
    """
    db_file = f"session_{request.session_id}.duckdb"
    
    # Ensure database exists for given session
    if not os.path.exists(db_file):
        return {"status": "error", "message": "Session data not found. Please upload a file first."}
    
    # Refresh current table schema and sample data metadata
    with duckdb.connect(database=db_file, read_only=False) as con:
        tables_raw = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        all_tables = [row[0] for row in tables_raw]
        
        full_schema = {}
        full_sample_data = {}
        
        for t_name in all_tables:
            schema_raw = con.execute(f"DESCRIBE {t_name}").fetchall()
            full_schema[t_name] = {row[0]: row[1] for row in schema_raw}
            full_sample_data[t_name] = con.execute(f"SELECT * FROM {t_name} LIMIT 3").df().to_dict(orient="records")

    # Construct initial state with updated message history
    initial_state = {
        "question": request.question,
        "schema_metadata": full_schema,
        "sample_rows": full_sample_data,
        "messages": [HumanMessage(content=request.question)]
    }

    # Attach session checkpoint configuration and recursion guards
    config = {
        "configurable": {"thread_id": request.session_id},
        "recursion_limit": 5
    }
    
    try:
        final_state = langgraph_app.invoke(initial_state, config=config)
    except Exception as e:
        print(f"Graph Execution Failed: {str(e)}")
        return {
            "status": "error",
            "message": "The AI encountered an issue processing the SQL execution. Please rephrase your question.",
            "chart_config": {"chart_type": "none"}
        }
    
    return {
        "status": "success",
        "question": request.question,
        "generated_sql": final_state.get("generated_sql"),
        "final_data": final_state.get("final_data"),
        "chart_config": final_state.get("chart_config"),
        "analysis_summary": final_state.get("analysis_summary")
    }