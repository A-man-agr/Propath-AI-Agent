import os
import logging
import datetime
import asyncio
import google.cloud.logging
from google.cloud import datastore
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from mcp.server.fastmcp import FastMCP 

from google.adk import Agent
from google.adk.agents import SequentialAgent
from google.adk.tools.tool_context import ToolContext

# --- 1. Setup Logging ---
try:
    cloud_logging_client = google.cloud.logging.Client()
    cloud_logging_client.setup_logging()
except Exception:
    logging.basicConfig(level=logging.INFO)

load_dotenv()
model_name = os.getenv("MODEL", "gemini-1.5-flash")

# --- 2. Database Setup ---
# For the default database, leaving arguments empty is the most stable 
# way to deploy on Google Cloud. It auto-detects the project and (default) DB.
db = datastore.Client() 

mcp = FastMCP("WorkspaceTools")

# ================= 3. TOOLS =================

@mcp.tool()
def add_task(title: str) -> str:
    """Adds a new task to the workspace."""
    try:
        key = db.key('Task')
        task = datastore.Entity(key=key)
        task.update({
            'title': title, 
            'completed': False, 
            'created_at': datetime.datetime.now()
        })
        db.put(task)
        return f"Success: Task '{title}' saved (ID: {task.key.id})."
    except Exception as e:
        logging.error(f"DB Error in add_task: {e}")
        return f"Database Error: {str(e)}"

@mcp.tool()
def list_tasks() -> str:
    """Lists all current tasks."""
    try:
        query = db.query(kind='Task')
        tasks = list(query.fetch())
        if not tasks: return "Your task list is empty."
        
        res = ["📋 Current Tasks:"]
        for t in tasks:
            status = "✅" if t.get('completed') else "⏳"
            res.append(f"{status} {t.get('title')} (ID: {t.key.id})")
        return "\n".join(res)
    except Exception as e:
        return f"Database Error: {str(e)}"

@mcp.tool()
def complete_task(task_id: str) -> str:
    """Marks a task as complete. Input must be the numeric ID."""
    try:
        numeric_id = int(''.join(filter(str.isdigit, task_id)))
        key = db.key('Task', numeric_id)
        task = db.get(key)
        if task:
            task['completed'] = True
            db.put(task)
            return f"Task {numeric_id} marked as done."
        return f"Task {numeric_id} not found."
    except Exception as e:
        return f"Error processing task ID: {str(e)}"

@mcp.tool()
def add_note(title: str, content: str) -> str:
    """Saves a detailed note for Dr. Abhishek."""
    try:
        key = db.key('Note')
        note = datastore.Entity(key=key)
        note.update({'title': title, 'content': content, 'at': datetime.datetime.now()})
        db.put(note)
        return f"Note '{title}' saved successfully."
    except Exception as e:
        return f"Database Error: {str(e)}"

@mcp.tool()
def save_resume_section(section: str, content: str) -> str:
    """Saves or updates a specific section of the user's resume (e.g., 'Education', 'Experience')."""
    try:
        key = db.key('ResumeSection', section)
        resume_section = datastore.Entity(key=key)
        resume_section.update({'content': content, 'updated_at': datetime.datetime.now()})
        db.put(resume_section)
        return f"Resume section '{section}' saved successfully."
    except Exception as e:
        return f"Database Error: {str(e)}"

@mcp.tool()
def get_resume() -> str:
    """Retrieves all saved resume sections for the user to review or format."""
    try:
        query = db.query(kind='ResumeSection')
        sections = list(query.fetch())
        if not sections: return "No resume sections have been saved yet."
        
        res = ["📄 Current Resume Sections:"]
        for s in sections:
            res.append(f"--- {s.key.name} ---\n{s.get('content')}")
        return "\n\n".join(res)
    except Exception as e:
        return f"Database Error: {str(e)}"

@mcp.tool()
def update_career_goal(goal: str) -> str:
    """Saves the user's overarching career goal."""
    try:
        key = db.key('CareerGoal', 'primary')
        career_goal = datastore.Entity(key=key)
        career_goal.update({'goal': goal, 'updated_at': datetime.datetime.now()})
        db.put(career_goal)
        return f"Career goal updated: '{goal}'"
    except Exception as e:
        return f"Database Error: {str(e)}"

@mcp.tool()
def add_job_application(company: str, role: str) -> str:
    """Saves a new job application."""
    try:
        key = db.key('JobApplication')
        job = datastore.Entity(key=key)
        job.update({'company': company, 'role': role, 'status': 'Applied', 'date': datetime.datetime.now()})
        db.put(job)
        return f"Success: Applied for '{role}' at '{company}' (ID: {job.key.id})."
    except Exception as e:
        return f"Database Error: {str(e)}"

@mcp.tool()
def list_job_applications() -> str:
    """Lists all current job applications."""
    try:
        query = db.query(kind='JobApplication')
        jobs = list(query.fetch())
        if not jobs: return "No job applications found."
        
        res = ["🏢 Current Job Applications:"]
        for j in jobs:
            res.append(f"[{j.get('status')}] {j.get('role')} at {j.get('company')} (ID: {j.key.id})")
        return "\n".join(res)
    except Exception as e:
        return f"Database Error: {str(e)}"

@mcp.tool()
def update_job_status(app_id: str, status: str) -> str:
    """Updates the status of a job application. Input must be the numeric ID and the new status."""
    try:
        numeric_id = int(''.join(filter(str.isdigit, app_id)))
        key = db.key('JobApplication', numeric_id)
        job = db.get(key)
        if job:
            job['status'] = status
            db.put(job)
            return f"Job Application {numeric_id} status updated to {status}."
        return f"Job Application {numeric_id} not found."
    except Exception as e:
        return f"Error processing job ID: {str(e)}"

# ================= 4. AGENTS =================

def add_prompt_to_state(tool_context: ToolContext, prompt: str):
    """Internal tool to bridge user intent across the agent workflow."""
    tool_context.state["PROMPT"] = prompt
    return {"status": "ok"}

def manager_instruction(ctx):
    user_prompt = ctx.state.get("PROMPT", "Welcome the user.")
    return f"""
You are the ProPath AI Manager. 
Always start with a polite, professional greeting.
Analyze the user's request and delegate tasks to your specialized sub-agents:
- Career Advisor: For setting and reviewing career goals.
- Resume Builder: For formatting and saving resume sections.
- Task Manager: For checking off to-do items and notes.
- Job Tracker: For logging and updating job applications.

User request: {user_prompt}
"""

def career_instruction(ctx):
    return "You are the Career Advisor. Focus exclusively on providing actionable career progression advice and updating the user's primary career goals."

def resume_instruction(ctx):
    return "You are the Resume Builder. Focus exclusively on extracting, formatting, and saving professional resume sections to the database."

def task_instruction(ctx):
    return "You are the Task Manager. Focus exclusively on tracking actionable to-do items and managing daily productivity."

def job_instruction(ctx):
    return "You are the Job Tracker. Focus exclusively on recording job applications, tracking status, and helping the user organize their interviews."

def root_instruction(ctx):
    # Pulls the prompt directly from the API call
    raw_input = ctx.state.get("user_input", "Hello")
    return f"""
1. Save this user input using 'add_prompt_to_state': {raw_input}
2. Hand off control to the 'workflow' agent.
"""

career_advisor = Agent(name="career_advisor", model=model_name, instruction=career_instruction, tools=[update_career_goal])
resume_builder = Agent(name="resume_builder", model=model_name, instruction=resume_instruction, tools=[save_resume_section, get_resume])
task_manager = Agent(name="task_manager", model=model_name, instruction=task_instruction, tools=[add_task, list_tasks, complete_task, add_note])
job_tracker = Agent(name="job_tracker", model=model_name, instruction=job_instruction, tools=[add_job_application, list_job_applications, update_job_status])

propath_manager = Agent(
    name="propath_manager",
    model=model_name,
    instruction=manager_instruction,
    sub_agents=[career_advisor, resume_builder, task_manager, job_tracker]
)

workflow = SequentialAgent(
    name="workflow",
    sub_agents=[propath_manager]
)

root_agent = Agent(
    name="root",
    model=model_name,
    instruction=root_instruction,
    tools=[add_prompt_to_state],
    sub_agents=[workflow]
)

# ================= 5. API =================

app = FastAPI()

class UserRequest(BaseModel):
    prompt: str

@app.post("/api/v1/workspace/chat")
async def chat(request: UserRequest):
    try:
        final_reply = ""
        # Inject user_input into the agent state
        async for event in root_agent.run_async({"user_input": request.prompt}):
            if hasattr(event, 'text') and event.text:
                final_reply = event.text

        return {
            "status": "success",
            "reply": final_reply if final_reply else "Request processed."
        }

    except Exception as e:
        logging.error(f"Chat Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)