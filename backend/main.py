import asyncio
import os
import threading
import subprocess
import tempfile
import signal
from concurrent.futures import ThreadPoolExecutor
import psutil
import json
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent))
from functools import lru_cache
import json
import random
import litellm
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict
from datetime import datetime, timedelta, date, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Request, Query, Request
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import openai
from dotenv import load_dotenv

from strategies.base import BaseStrategy
from models.chat import ChatModel
from parsers.endpoint_parser import EndpointParser
from services.onecompiler_service import OneCompilerService
from auth import verify_password, get_password_hash, create_access_token, verify_token, generate_reset_token, send_password_reset_email
from agent import OpenAIAgent
import question_generation_helpers as qgh

from pydantic import BaseModel, Field, AliasChoices
from database.config import get_db
from database.sqlalchemy_models import User, PasswordResetToken, Project, Code, Submission, SubmissionFeedback, SubmissionEvaluation, CodeData, ExperienceData, MCQAData, NasaTLIData, UserMCQASkillResponse, UserCodeSkillResponse, SkillCheckAssignment, ReportSkillCheckQuestion, ComprehensionQuestion, NavigationEvent, AssistantLog, CodePreference, TaskEvent
from database.models import (
    UserCreate,
    UserResponse,
    PasswordResetRequest,
    PasswordResetConfirm,
    PasswordResetTokenCreate,
    CodeCreate,
    SubmissionCreate,
    SubmissionFeedbackCreate,
    SubmissionFeedback as SubmissionFeedbackModel,
    SubmissionEvaluationCreate,
    SubmissionEvaluation as SubmissionEvaluationModel,
    UserMCQASkillResponseCreate,
    UserCodeSkillResponseCreate,
    ReportSkillCheckQuestionCreate,
    ComprehensionQuestionCreate,
    ComprehensionQuestionResponse,
    GenerateComprehensionQuestionsRequest,
    SaveTutorialQuestionsRequest,
    EvaluateSubmissionRequest,
    NavigationEventCreate,
    TaskEventCreate,
    ProjectCreate,
)
from database.crud import CodeCRUD, SubmissionCRUD, SubmissionFeedbackCRUD, SubmissionEvaluationCRUD, UserMCQASkillResponseCRUD, UserCodeSkillResponseCRUD, ReportSkillCheckQuestionCRUD, NavigationEventCRUD, TaskEventCRUD, ProjectCRUD
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func, distinct

# Load environment variables from .env file
load_dotenv()

# No rate limiting needed - OneCompiler handles execution security

def setup_environment():
    """Set up environment variables if .env file doesn't exist."""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    
    if not os.path.exists(env_path):
        print("🔧 No .env file found. Let's create one!")
        print("Please enter your OpenAI API key:")
        
        api_key = input("OPENAI_API_KEY: ").strip()
        
        if not api_key:
            print("❌ No API key provided. Exiting.")
            exit(1)
        
        # Create .env file
        env_content = f"""# OpenAI API Configuration
OPENAI_API_KEY={api_key}

# AI Agent Configuration (optional)
# Model to use for the AI agent (default: gpt-4o)
AGENT_MODEL=gpt-4o

# RapidAPI Configuration (optional)
# Get your API key from: https://rapidapi.com/onecompiler/api/onecompiler-apis
RAPIDAPI_KEY=

# Execution Mode (optional)
# Set to True for local development (uses Python exec() - UNSAFE, only for development)
# Set to False for production (uses OneCompiler API - secure remote execution)
USE_LOCAL_EXECUTION=True

# Server Configuration (optional)
HOST=0.0.0.0
PORT=4828
DEBUG=True

# Email Configuration (Brevo)
# Get your API key from: https://app.brevo.com/settings/keys/api
BREVO_API_KEY=
FROM_EMAIL=noreply@helpfulcoding.com
FROM_NAME=Helpful Coding
RESET_LINK_BASE_URL=http://localhost:4827/reset-password

# Authentication Configuration
SECRET_KEY=your-secret-key-change-this-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=30
"""
        
        try:
            with open(env_path, 'w') as f:
                f.write(env_content)
            print("✅ .env file created successfully!")
            # Reload environment variables
            load_dotenv()
        except Exception as e:
            print(f"❌ Error creating .env file: {e}")
            exit(1)

# Set up environment if needed
setup_environment()

app = FastAPI(
    title="AI Coding Assistant Backend",
    description="Backend API for the AI Coding Assistant with authentication and code execution capabilities",
    version="1.0.0",
    tags_metadata=[
        {
            "name": "Authentication",
            "description": "User authentication endpoints for signup and login",
        },
        {
            "name": "Code Execution",
            "description": "Endpoints for executing and validating Python code",
        },
        {
            "name": "Tasks",
            "description": "Endpoints for managing coding tasks and test cases",
        },
        {
            "name": "Code",
            "description": "Endpoints for logging and managing user code snapshots",
        },
        {
            "name": "Chat",
            "description": "AI chat endpoints for code assistance",
        },
        {
            "name": "Submissions",
            "description": "Endpoints for logging project submissions",
        },
    ]
)
# Serve repository assets (e.g., images) with a stable URL: /assets/{path}
@app.get("/assets/{file_path:path}")
async def serve_asset(file_path: str):
    try:
        backend_dir = os.path.dirname(__file__)
        repo_root = os.path.abspath(os.path.join(backend_dir, ".."))
        abs_path = os.path.join(repo_root, file_path)
        if not os.path.exists(abs_path):
            return JSONResponse(status_code=404, content={"error": "Asset not found"})
        # Basic safe-guard to prevent directory traversal outside repo
        if not os.path.abspath(abs_path).startswith(repo_root):
            return JSONResponse(status_code=403, content={"error": "Forbidden"})
        
        # Determine content type based on file extension
        lower = file_path.lower()
        content_type = "application/octet-stream"
        if lower.endswith('.png'):
            content_type = "image/png"
        elif lower.endswith('.jpg') or lower.endswith('.jpeg'):
            content_type = "image/jpeg"
        elif lower.endswith('.gif'):
            content_type = "image/gif"
        elif lower.endswith('.svg'):
            content_type = "image/svg+xml"
        elif lower.endswith('.html') or lower.endswith('.htm'):
            content_type = "text/html; charset=utf-8"
        elif lower.endswith('.css'):
            content_type = "text/css; charset=utf-8"
        elif lower.endswith('.js'):
            content_type = "text/javascript; charset=utf-8"
        elif lower.endswith('.json'):
            content_type = "application/json; charset=utf-8"
        elif lower.endswith('.txt'):
            content_type = "text/plain; charset=utf-8"
        elif lower.endswith('.mp4'):
            content_type = "video/mp4"
        
        # Create FileResponse with cache prevention headers
        response = FileResponse(abs_path, media_type=content_type)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4827",
        "http://127.0.0.1:4827",
        "http://localhost:3000",  # Legacy port support
        "http://127.0.0.1:3000",  # Legacy port support
        "https://vibe-code.umiacs.umd.edu",  # Production frontend
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Initialize chat model
try:
    chat_model = ChatModel()
    print("✅ Backend initialized successfully with Autocomplete strategy")
except ValueError as e:
    print(f"❌ Error: {e}")
    print("Please create a .env file in the backend directory with your OpenAI API key")
    exit(1)

from agent_endpoints import router as agent_router

# Store active Python processes
active_processes = {}

# Initialize endpoint parser
endpoint_parser = EndpointParser()

# Initialize OneCompiler service
rapidapi_key = os.getenv("RAPIDAPI_KEY")
onecompiler_service = OneCompilerService(rapidapi_key=rapidapi_key)

# Include agent router
app.include_router(agent_router)

# Python test file parser and executor
import re
import ast

def load_json_test_file(content: str, filename: str, test_type_prefix: str = "") -> List[Dict[str, Any]]:
    """Load test cases from a JSON file"""
    try:
        test_cases_raw = json.loads(content)
        
        # Convert to the expected format
        test_cases = []
        for test in test_cases_raw:
            # Add prefix to title based on test type
            original_title = test.get("title", "Uncategorized")
            prefixed_title = f"{test_type_prefix}: {original_title}" if test_type_prefix else original_title
            
            # Check if this is a frontend_interactive test
            if test.get("type") == "frontend_interactive":
                # Preserve the original structure for frontend interactive tests
                test_case = {
                    "title": prefixed_title,
                    "name": test.get("name", "Unknown Test"),
                    "description": test.get("description", ""),
                    "public": test.get("public", False),
                    "type": test.get("type"),  # Preserve type
                    "setup": test.get("setup"),  # Preserve setup
                    "steps": test.get("steps")  # Preserve steps
                }
            else:
                # Legacy format for backend tests
                test_case = {
                    "title": prefixed_title,
                    "name": test.get("name", "Unknown Test"),
                    "description": test.get("description", ""),
                    "public": test.get("public", False),
                    "metadata": {
                        "type": "endpoint",
                        "endpoint": test.get("endpoint", ""),
                        "input": test.get("input", {}),
                        "expected": test.get("expected")
                    }
                }
            test_cases.append(test_case)
        
        return test_cases
        
    except Exception as e:
        print(f"Error loading JSON test file {filename}: {e}")
        return []

@app.get("/")
async def root():
    return {"message": "AI Coding Assistant Backend is running!"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "Backend is operational"}


def _slugify(name: str) -> str:
    import re
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


# Shared kiosk / conference accounts that may submit more than once per task.
# Keep in sync with MULTI_SUBMISSION_ACCOUNT_IDENTIFIERS in interface/app/config/tasks.ts
MULTI_SUBMISSION_ACCOUNT_IDENTIFIERS = frozenset({"hcomp_2026_user"})


def _multi_submission_user_ids(db: Session) -> set:
    identifiers = [s.strip().lower() for s in MULTI_SUBMISSION_ACCOUNT_IDENTIFIERS if s and s.strip()]
    if not identifiers:
        return set()
    rows = (
        db.query(User.id)
        .filter(
            or_(
                func.lower(User.username).in_(identifiers),
                func.lower(User.email).in_(identifiers),
            )
        )
        .all()
    )
    return {int(row[0]) for row in rows if row and row[0] is not None}


def _is_multi_submission_user(user: Optional[User]) -> bool:
    if not user:
        return False
    identifiers = {s.strip().lower() for s in MULTI_SUBMISSION_ACCOUNT_IDENTIFIERS if s and s.strip()}
    if not identifiers:
        return False
    username = (user.username or "").strip().lower()
    email = (user.email or "").strip().lower()
    return username in identifiers or email in identifiers


def _is_kiosk_reset_code(code_record: Optional[Code]) -> bool:
    if not code_record:
        return False
    if getattr(code_record, "mode", None) == "kiosk_reset":
        return True
    meta = getattr(code_record, "code_metadata", None) or {}
    if isinstance(meta, dict) and meta.get("event") == "kiosk_reset":
        return True
    payload = code_record.code if isinstance(code_record.code, dict) else {}
    return bool(payload.get("__kiosk_reset"))


def _reset_kiosk_in_progress_workspace(db: Session, user_id: int, current_project_id: Optional[int] = None) -> None:
    """Mark in-progress editor state as reset so the next kiosk visitor loads starter files."""
    project_ids = {
        int(row[0])
        for row in db.query(Code.project_id).filter(Code.user_id == user_id).distinct().all()
        if row and row[0] is not None
    }
    if current_project_id is not None:
        project_ids.add(int(current_project_id))
    for project_id in project_ids:
        CodeCRUD.create(
            db,
            CodeCreate(
                user_id=user_id,
                project_id=project_id,
                code={"__kiosk_reset": "1"},
                mode="kiosk_reset",
                metadata={"event": "kiosk_reset"},
            ),
        )


INJECTED_TASK_DESCRIPTIONS: Dict[str, str] = {
    "zic_zac_zoe": (
        "<p>In this task, you'll create a variation of tic-tac-toe on a 5x5 grid with two human players: "
        "Player A and Player B. You'll start with a blank board, helper JavaScript functions, and logic to "
        "connect the HTML, JS, and CSS, which you must update to fulfill a list of given requirements.</p>"
    ),
}


def _resolve_task_description(
    task_name: Optional[str],
    *,
    fallback_description: str = "",
    task_meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Prefer hardcoded overrides for selected tasks before metadata/DB fallbacks."""
    task_slug = _slugify(task_name) if task_name else ""
    injected = INJECTED_TASK_DESCRIPTIONS.get(task_slug)
    if injected:
        return injected
    if isinstance(task_meta, dict):
        meta_description = task_meta.get("description")
        if isinstance(meta_description, str) and meta_description.strip():
            return meta_description
    return fallback_description or ""


def _resolve_project_from_task_id(db: Session, task_id: str) -> Optional[Project]:
    """Unified function to resolve Project from task_id. Always use this instead of project_id."""
    if not task_id:
        return None
    normalized_task_id = _slugify(task_id)
    for project in db.query(Project).all():
        if _slugify(project.name) == normalized_task_id:
            return project
    return None


@lru_cache(maxsize=1)
def _load_dummy_task_metadata() -> Dict[str, Dict[str, Any]]:
    """
    Load fallback metadata from data/tasks.json keyed by slugified name.
    Ensures we preserve important dates if legacy DB rows are missing them.
    """
    try:
        repo_root = Path(__file__).resolve().parent.parent
        data_path = repo_root / "data" / "tasks.json"
        if not data_path.exists():
            print(f"[tasks-db] tasks.json not found at {data_path}")
            return {}

        payload = json.loads(data_path.read_text(encoding="utf-8"))
        tasks = payload.get("tasks", [])
        lookup: Dict[str, Dict[str, Any]] = {}

        for task in tasks:
            name = task.get("name")
            if not name:
                continue
            slug = _slugify(name)
            lookup[slug] = {
                "code_start_date": task.get("code_start_date"),
                "voting_start_date": task.get("voting_start_date"),
                "voting_end_date": task.get("voting_end_date"),
                "description": _resolve_task_description(
                    name,
                    fallback_description=task.get("description", ""),
                ),
                "example": task.get("example", ""),
                "requirements": task.get("requirements", []),
            }
        return lookup
    except Exception as exc:
        print(f"[tasks-db] Failed to load dummy task metadata: {exc}")
        return {}


def _load_task_definition_from_json(task_slug: str) -> Optional[Dict[str, Any]]:
    """
    Load the full task definition (including files) from tasks.json using the slugified name.
    Provides a fallback when the DB is missing or has incomplete records.
    """
    try:
        repo_root = Path(__file__).resolve().parent.parent
        data_path = repo_root / "data" / "tasks.json"
        if not data_path.exists():
            print(f"[tasks-db] tasks.json not found at {data_path}")
            return None

        payload = json.loads(data_path.read_text(encoding="utf-8"))
        tasks = payload.get("tasks", [])
        for task in tasks:
            name = task.get("name")
            if name and _slugify(name) == task_slug:
                return task
    except Exception as exc:
        print(f"[tasks-db] Failed to load task definition for slug '{task_slug}': {exc}")
    return None


def _normalize_project_files(raw_files: Any) -> List[Dict[str, Any]]:
    """
    Ensure project.files is a list of file configs.
    Handles None, JSON strings, or already-parsed lists.
    """
    if isinstance(raw_files, list):
        return raw_files
    if isinstance(raw_files, str):
        try:
            parsed = json.loads(raw_files)
            if isinstance(parsed, list):
                return parsed
            print(f"[tasks-db] Parsed project.files string but got {type(parsed)} instead of list")
        except Exception as exc:
            print(f"[tasks-db] Could not parse project.files JSON string: {exc}")
    return []


def _resolve_project_file_content(file_config: Dict[str, Any]) -> str:
    """
    Resolve starter file content from project file config.
    Supports:
    - Path strings relative to repo root
    - Inline code content
    """
    content = file_config.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return ""
    raw = content.strip()
    repo_root = Path(__file__).resolve().parent.parent
    candidate_path = repo_root / raw
    if candidate_path.exists() and candidate_path.is_file():
        try:
            return candidate_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"[comprehension/context] failed reading starter file '{candidate_path}': {exc}")
            return ""
    return raw


def _build_target_selection_context(project: Project) -> Dict[str, Any]:
    """
    Build context used by requirement-aware compare target selection.
    """
    project_files = _normalize_project_files(project.files)
    starter_code = {"html": "", "css": "", "js": ""}
    for file_cfg in project_files:
        filename = str(file_cfg.get("name", "")).strip().lower()
        content = _resolve_project_file_content(file_cfg)
        if not content:
            continue
        if filename.endswith(".html"):
            starter_code["html"] += content + "\n\n"
        elif filename.endswith(".css"):
            starter_code["css"] += content + "\n\n"
        elif filename.endswith(".js") or filename.endswith(".javascript"):
            starter_code["js"] += content + "\n\n"

    requirements = project.requirements if isinstance(project.requirements, list) else []
    return {
        "requirements": requirements,
        "starter_code": starter_code,
    }


_SYNC_LOCK = threading.Lock()
_SYNC_COMPLETED = False


def _sync_project_dates_from_dummy(db: Session) -> None:
    """
    Populate missing project date fields by syncing from tasks.json once.
    This writes the data into the database so future reads don't require fallbacks.
    """
    global _SYNC_COMPLETED
    if _SYNC_COMPLETED:
        return

    with _SYNC_LOCK:
        if _SYNC_COMPLETED:
            return

        dummy_meta = _load_dummy_task_metadata()
        if not dummy_meta:
            _SYNC_COMPLETED = True
            return

        updated = False

        for project in db.query(Project).all():
            meta = dummy_meta.get(_slugify(project.name))
            if not meta:
                continue

            def parse_date(value: Optional[str]) -> Optional[date]:
                if not value:
                    return None
                try:
                    return date.fromisoformat(value)
                except ValueError:
                    print(f"[tasks-db] Skipping invalid date '{value}' for project '{project.name}'")
                    return None

            code_start = project.code_start_date or parse_date(meta.get("code_start_date"))
            voting_start = project.voting_start_date or parse_date(meta.get("voting_start_date"))
            voting_end = project.voting_end_date or parse_date(meta.get("voting_end_date"))

            if (
                code_start != project.code_start_date or
                voting_start != project.voting_start_date or
                voting_end != project.voting_end_date
            ):
                project.code_start_date = code_start
                project.voting_start_date = voting_start
                project.voting_end_date = voting_end
                updated = True

        if updated:
            db.commit()

        _SYNC_COMPLETED = True


def build_rating_summary(feedback_entries: List[SubmissionFeedback]) -> Dict[str, Any]:
    if not feedback_entries:
        return {"average": None, "count": 0, "perMetric": {}}

    # Filter to only the most recent feedback per voter_id
    # Entries should be ordered by created_at desc, so first occurrence per voter is most recent
    most_recent_by_voter: Dict[int, SubmissionFeedback] = {}
    for entry in feedback_entries:
        voter_id = entry.voter_id
        # Only keep the first entry we see for each voter (most recent due to ordering)
        if voter_id not in most_recent_by_voter:
            most_recent_by_voter[voter_id] = entry

    # Now calculate averages using only the most recent feedback per voter
    per_metric_totals: Dict[str, float] = defaultdict(float)
    per_metric_counts: Dict[str, int] = defaultdict(int)
    averaged_scores_total = 0.0
    averaged_scores_count = 0

    for entry in most_recent_by_voter.values():
        scores = entry.scores or {}
        numeric_scores: List[float] = []
        for key, value in scores.items():
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            numeric_scores.append(score)
            per_metric_totals[key] += score
            per_metric_counts[key] += 1

        if numeric_scores:
            averaged_scores_total += sum(numeric_scores) / len(numeric_scores)
            averaged_scores_count += 1

    average = (
        averaged_scores_total / averaged_scores_count if averaged_scores_count > 0 else None
    )
    per_metric_average = {
        key: (per_metric_totals[key] / per_metric_counts[key])
        for key in per_metric_totals.keys()
        if per_metric_counts[key] > 0
    }

    return {
        "average": round(average, 2) if average is not None else None,
        "count": averaged_scores_count,
        "perMetric": {key: round(value, 2) for key, value in per_metric_average.items()},
    }


class CodeLogRequest(BaseModel):
    user_id: int = Field(..., alias="userId", validation_alias=AliasChoices("userId", "user_id"))
    project_id: Optional[int] = Field(None, alias="projectId", validation_alias=AliasChoices("projectId", "project_id"))
    task_id: Optional[str] = Field(None, alias="taskId", validation_alias=AliasChoices("taskId", "task_id"))
    code: Dict[str, str]
    mode: Optional[str] = "regular"
    event: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default=None, validation_alias=AliasChoices("metadata", "code_metadata"))

    class Config:
        populate_by_name = True


class SubmissionRequest(BaseModel):
    user_id: int = Field(..., alias="userId", validation_alias=AliasChoices("userId", "user_id"))
    project_id: Optional[int] = Field(None, alias="projectId", validation_alias=AliasChoices("projectId", "project_id"))
    task_id: Optional[str] = Field(None, alias="taskId", validation_alias=AliasChoices("taskId", "task_id"))
    title: str
    description: Optional[str] = None
    code: Dict[str, Any]
    image: Optional[str] = None
    comprehension_answers: Optional[Dict[str, Any]] = Field(None, alias="comprehensionAnswers")
    evaluation_id: Optional[int] = Field(None, alias="evaluationId")
    forced_timeout: bool = Field(False, alias="forcedTimeout")

    class Config:
        populate_by_name = True


class SubmissionFeedbackRequest(BaseModel):
    voter_id: int = Field(..., alias="voterId", validation_alias=AliasChoices("voterId", "voter_id"))
    scores: Dict[str, int] = Field(default_factory=dict)
    comment: Optional[str] = None
    is_saved: Optional[bool] = Field(default=None, alias="isSaved", validation_alias=AliasChoices("isSaved", "is_saved"))
    is_reported: Optional[bool] = Field(default=None, alias="isReported", validation_alias=AliasChoices("isReported", "is_reported"))
    report_type: Optional[str] = Field(default=None, alias="reportType", validation_alias=AliasChoices("reportType", "report_type"))
    report_rationale: Optional[str] = Field(default=None, alias="reportRationale", validation_alias=AliasChoices("reportRationale", "report_rationale"))

    class Config:
        populate_by_name = True


@app.get("/api/tasks-db", tags=["Tasks"])
async def list_tasks_from_db(
    user_id: Optional[int] = Query(default=None, alias="userId"),
    db: Session = Depends(get_db)
):
    try:
        _sync_project_dates_from_dummy(db)
        projects = db.query(Project).order_by(Project.id.asc()).all()
        
        # Batch fetch user data to avoid N+1 queries
        submissions_by_project = {}
        latest_code_by_project = {}
        
        if user_id:
            # Fetch only project_id for submissions (we only need to check existence)
            submission_rows = db.query(Submission.project_id).filter(
                Submission.user_id == user_id
            ).all()
            submissions_by_project = {row[0] for row in submission_rows}
            
            # Fetch only needed columns for codes: project_id, code (JSON), created_at, id
            # We need code to check if it's non-empty, and created_at/id for ordering
            all_user_codes = db.query(
                Code.project_id,
                Code.code,
                Code.created_at,
                Code.id
            ).filter(
                Code.user_id == user_id
            ).order_by(Code.project_id, Code.created_at.desc(), Code.id.desc()).all()
            
            # Build lookup dictionary keeping only the latest code per project
            # Since we ordered by created_at desc and id desc, first occurrence per project_id is latest
            # Store just the code content (JSON) since that's all we need to check
            for project_id, code_content, created_at, code_id in all_user_codes:
                if project_id not in latest_code_by_project:
                    latest_code_by_project[project_id] = code_content
        
        # Load dummy metadata once outside the loop (it's cached, but this is clearer)
        dummy_meta = _load_dummy_task_metadata()
        
        tasks = []
        for p in projects:
            # Determine status based on user's code and submissions
            status = "not-started"
            if user_id:
                # Check if user has a submission (completed) - O(1) lookup
                if p.id in submissions_by_project:
                    status = "completed"
                elif p.id in latest_code_by_project:
                    # Check if user has saved code (in-progress)
                    # latest_code_by_project now stores just the code JSON content
                    user_code = latest_code_by_project[p.id]
                    if user_code:  # Check if code content exists and is non-empty
                        # Check if code is different from starter files (has edits)
                        # For now, if code exists, consider it in-progress
                        status = "in-progress"
            
            # Get example from dummy metadata if available
            task_meta = dummy_meta.get(_slugify(p.name), {})
            example = p.example if p.example is not None else task_meta.get("example", "")
            requirements = p.requirements if isinstance(p.requirements, list) else task_meta.get("requirements", [])
            if not isinstance(requirements, list):
                requirements = []
            
            # Prefer tasks.json description when present so content updates are reflected
            # without requiring manual DB row edits for every wording change.
            description = _resolve_task_description(
                p.name,
                fallback_description=p.description or "",
                task_meta=task_meta,
            )
            task_title = p.title or p.name
            if p.label and p.label.lower() == "replication" and task_title:
                prefix = f"Create your own version of {task_title}: "
                description_stripped = description.strip()
                # Try to prepend to the first paragraph if it starts with <p
                if re.match(r'^\s*<p', description_stripped, re.IGNORECASE):
                    # Insert the prefix right after the opening <p> tag
                    description = re.sub(
                        r'^(\s*<p[^>]*>)',
                        rf'\1{prefix}',
                        description_stripped,
                        flags=re.IGNORECASE
                    )
                else:
                    # Prepend a paragraph with the prefix
                    description = f"<p><strong>{prefix}</strong></p>{description_stripped}"
            
            tasks.append({
                "id": _slugify(p.name),
                "name": p.name,
                "title": task_title,  # Use title if available, fallback to name
                "label": p.label or "",  # Include label (replication or open-ended)
                "description": description,
                "example": example,  # Include example from tasks.json
                "projectId": p.id,
                "codeStartDate": p.code_start_date.isoformat() if p.code_start_date else None,
                "votingStartDate": p.voting_start_date.isoformat() if p.voting_start_date else None,
                "votingEndDate": p.voting_end_date.isoformat() if p.voting_end_date else None,
                # Optional fields to keep UI happy with defaults
                "requirements": requirements,
                "videoDemo": None,
                "tags": [],
                "difficulty": "Beginner",
                "appType": "Widget",
                "estimatedTime": "30 min",
                "preview": "📦",
                "status": status,
                "saved": False,
            })
        response = JSONResponse(content={"tasks": tasks})
        # Prevent caching
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/clear-cache", tags=["Tasks"])
async def clear_cache():
    """Clear the LRU cache for task metadata (development/debugging only)"""
    try:
        _load_dummy_task_metadata.cache_clear()
        response = JSONResponse(content={"message": "Cache cleared successfully"})
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# Skill Check Question Selection Configuration
# Customize which questions are loaded by ID
# Note: 
# - debug coding questions: IDs 1-44 (inclusive)
# - normal coding questions: IDs 45 onward
# - ux questions: IDs 1-118 (inclusive)
# - frontend questions: IDs 119 onward
SKILL_CHECK_QUESTION_IDS = {
    "pre_test": {
        "experience": list(range(1, 11)),  # IDs 1-10
        "frontend": list(range(119, 129)),  # IDs 119-128 (10 questions)
        "ux": list(range(1, 11)),        # IDs 1-10
    },
    "post_test": {
        # Post-test mirrors pre-test except experience questions are omitted.
        "experience": [],
        "frontend": list(range(119, 129)),  # IDs 119-128 (10 questions)
        "ux": list(range(1, 11)),        # IDs 1-10
    }
}


def _select_code_tasks_for_assignment(db: Session) -> tuple[list[str], list[str], list[str], list[str]]:
    """
    Select coding tasks for pre-test and post-test assignments.
    Ensures pre and post use the same base task names but different variants (_1 vs _2).
    
    Returns:
        (code_normal_pre, code_normal_post, code_debug_pre, code_debug_post)
        as lists of CodeData.task_name strings.
    """
    # Load all coding questions
    all_code_data = db.query(CodeData).all()
    if not all_code_data:
        return [], [], [], []

    # Extract base names from task names (number, paren, prefix, string_shift)
    base_names = ["number", "paren", "prefix", "string_shift"]

    # Randomly select 2 base names for debug, 2 for normal
    random.shuffle(base_names)
    debug_base_names = base_names[:2]
    normal_base_names = base_names[2:]

    # Filter against actual CodeData entries to ensure tasks exist
    code_data_map = {q.task_name: q for q in all_code_data}

    code_normal_pre: list[str] = []
    code_normal_post: list[str] = []
    code_debug_pre: list[str] = []
    code_debug_post: list[str] = []

    # For normal tasks: same base names, different variants for pre vs post
    for base_name in normal_base_names:
        # Randomly assign which variant goes to pre and which to post
        variants = ["1", "2"]
        random.shuffle(variants)
        pre_variant = variants[0]
        post_variant = variants[1]
        
        pre_task = f"{base_name}_{pre_variant}"
        post_task = f"{base_name}_{post_variant}"
        
        # Only add if both tasks exist in the database
        if pre_task in code_data_map and post_task in code_data_map:
            code_normal_pre.append(pre_task)
            code_normal_post.append(post_task)

    # For debug tasks: same base names, different variants for pre vs post
    for base_name in debug_base_names:
        # Randomly assign which variant goes to pre and which to post
        variants = ["1", "2"]
        random.shuffle(variants)
        pre_variant = variants[0]
        post_variant = variants[1]
        
        pre_task = f"{base_name}_{pre_variant}"
        post_task = f"{base_name}_{post_variant}"
        
        # Only add if both tasks exist in the database
        if pre_task in code_data_map and post_task in code_data_map:
            code_debug_pre.append(pre_task)
            code_debug_post.append(post_task)

    return code_normal_pre, code_normal_post, code_debug_pre, code_debug_post


def _select_ux_questions_for_assignment(db: Session) -> tuple[list[str], list[str]]:
    """
    Select UX questions for pre-test and post-test assignments.
    Ensures pre and post use the same base tags but different variants (_1 vs _2).
    
    Returns:
        (ux_pre, ux_post) as lists of MCQAData.name strings.
    """
    # Load all UX questions
    all_ux_questions = db.query(MCQAData).filter(MCQAData.type == "ux").all()
    if not all_ux_questions:
        return [], []
    
    # Extract base tags from question names
    # UX tags: choices, memory, mobile, design_protocol, error, aesthetics, object, cognitive_ease, visual_order, excitement
    base_tags = ["choices", "memory", "mobile", "design_protocol", "error", 
                 "aesthetics", "object", "cognitive_ease", "visual_order", "excitement"]
    
    # Create a map of base_tag -> {name: question}
    questions_by_base = {}
    for q in all_ux_questions:
        if not q.name:
            continue
        # Extract base tag (everything before the last underscore and number)
        # e.g., "choices_1" -> "choices", "design_protocol_2" -> "design_protocol"
        if '_' in q.name:
            parts = q.name.rsplit('_', 1)
            if len(parts) == 2 and parts[1] in ['1', '2']:
                base_tag = parts[0]
                if base_tag not in questions_by_base:
                    questions_by_base[base_tag] = {}
                questions_by_base[base_tag][q.name] = q
    
    ux_pre: list[str] = []
    ux_post: list[str] = []
    
    # For each base tag, randomly assign variants to pre and post
    for base_tag in base_tags:
        if base_tag not in questions_by_base:
            continue
        
        tag_questions = questions_by_base[base_tag]
        variant_1 = f"{base_tag}_1"
        variant_2 = f"{base_tag}_2"
        
        # Only proceed if both variants exist
        if variant_1 in tag_questions and variant_2 in tag_questions:
            # Randomly assign which variant goes to pre and which to post
            variants = [variant_1, variant_2]
            random.shuffle(variants)
            ux_pre.append(variants[0])
            ux_post.append(variants[1])
    
    return ux_pre, ux_post


def _select_frontend_questions_for_assignment(db: Session) -> tuple[list[str], list[str]]:
    """
    Select frontend questions for pre-test and post-test assignments.
    Ensures pre and post use the same base tags but different variants (_1 vs _2).
    
    Returns:
        (frontend_pre, frontend_post) as lists of MCQAData.name strings.
    """
    # Load all frontend questions
    all_frontend_questions = db.query(MCQAData).filter(MCQAData.type == "frontend").all()
    if not all_frontend_questions:
        return [], []
    
    # Extract base tags from question names
    # Frontend tags: html_knowledge, html_recall, html_trace_code, html_change_code,
    #                css_knowledge, css_recall, css_trace_code, css_change_code,
    #                js_knowledge, js_recall, js_trace_code, js_change_code
    base_tags = [
        "html_knowledge", "html_recall", "html_trace_code", "html_change_code",
        "css_knowledge", "css_recall", "css_trace_code", "css_change_code",
        "js_knowledge", "js_recall", "js_trace_code", "js_change_code"
    ]
    
    # Create a map of base_tag -> {name: question}
    questions_by_base = {}
    for q in all_frontend_questions:
        if not q.name:
            continue
        # Extract base tag (everything before the last underscore and number)
        # e.g., "html_knowledge_1" -> "html_knowledge", "js_change_code_2" -> "js_change_code"
        if '_' in q.name:
            parts = q.name.rsplit('_', 1)
            if len(parts) == 2 and parts[1] in ['1', '2']:
                base_tag = parts[0]
                if base_tag not in questions_by_base:
                    questions_by_base[base_tag] = {}
                questions_by_base[base_tag][q.name] = q
    
    frontend_pre: list[str] = []
    frontend_post: list[str] = []
    
    # For each base tag, randomly assign variants to pre and post
    for base_tag in base_tags:
        if base_tag not in questions_by_base:
            continue
        
        tag_questions = questions_by_base[base_tag]
        variant_1 = f"{base_tag}_1"
        variant_2 = f"{base_tag}_2"
        
        # Only proceed if both variants exist
        if variant_1 in tag_questions and variant_2 in tag_questions:
            # Randomly assign which variant goes to pre and which to post
            variants = [variant_1, variant_2]
            random.shuffle(variants)
            frontend_pre.append(variants[0])
            frontend_post.append(variants[1])
    
    return frontend_pre, frontend_post


def _build_skill_check_assignment_names_split_pre_post(db: Session) -> tuple[
    list[str], list[str], list[str], list[str],
    list[str], list[str], list[str], list[str],
]:
    """
    Build question names for pre-test and post-test with random variant split.

    For each base tag (frontend, ux, code_normal, code_debug), we randomly assign
    one variant (_1 or _2) to pre-test and the other to post-test.
    Returns (frontend_pre, frontend_post, ux_pre, ux_post, code_normal_pre, code_normal_post, code_debug_pre, code_debug_post).
    """
    frontend_base_tags = [
        "html_knowledge", "html_recall", "html_trace_code", "html_change_code",
        "css_knowledge", "css_recall", "css_trace_code", "css_change_code",
        "js_knowledge", "js_recall", "js_trace_code", "js_change_code"
    ]
    ux_base_tags = [
        "choices", "memory", "error",
        "object", "visual_order", "excitement"
    ]
    code_normal_tags = [
        'paren'
    ]
    code_debug_tags = [
        'string_shift'
    ]

    def _split_variants(base_tags: list[str]) -> tuple[list[str], list[str]]:
        pre_names, post_names = [], []
        for base in base_tags:
            v1, v2 = f"{base}_1", f"{base}_2"
            chosen = random.choice((v1, v2))
            other = v2 if chosen == v1 else v1
            pre_names.append(chosen)
            post_names.append(other)
        return pre_names, post_names

    frontend_pre, frontend_post = _split_variants(frontend_base_tags)
    ux_pre, ux_post = _split_variants(ux_base_tags)
    code_normal_pre, code_normal_post = _split_variants(code_normal_tags)
    code_debug_pre, code_debug_post = _split_variants(code_debug_tags)

    return (
        frontend_pre, frontend_post,
        ux_pre, ux_post,
        code_normal_pre, code_normal_post,
        code_debug_pre, code_debug_post,
    )


def _build_identical_skill_check_assignment_names(db: Session) -> tuple[list[str], list[str], list[str], list[str]]:
    """
    Build a single set of question names (same for pre and post). Used as fallback when
    no assignment exists (e.g. anonymous or retake). Delegates to split builder and
    returns pre-test lists.
    """
    (
        frontend_pre, _,
        ux_pre, _,
        code_normal_pre, _,
        code_debug_pre, _,
    ) = _build_skill_check_assignment_names_split_pre_post(db)
    return frontend_pre, ux_pre, code_normal_pre, code_debug_pre


def _load_questions_from_jsonl(
    frontend_count: int,
    ux_count: int,
    coding_count: int,
    debugging_count: int
) -> list[dict]:
    """
    Load questions from JSONL files and randomly select the specified counts.
    
    Args:
        frontend_count: Number of frontend MCQA questions (0-15)
        ux_count: Number of UX MCQA questions (0-15)
        coding_count: Number of coding from scratch questions (0-5)
        debugging_count: Number of debugging questions (0-5)
    
    Returns:
        List of question dictionaries
    """
    frontend_group: list[dict] = []
    ux_group: list[dict] = []
    coding_group: list[dict] = []
    debugging_group: list[dict] = []
    backend_dir = os.path.dirname(__file__)
    repo_root = os.path.abspath(os.path.join(backend_dir, ".."))
    
    # Load MCQA questions from JSONL (try full version first, then fallback)
    mcqa_file = os.path.join(repo_root, "data", "mcqa_data_full.jsonl")
    if not os.path.exists(mcqa_file):
        mcqa_file = os.path.join(repo_root, "data", "mcqa_data.jsonl")
    if os.path.exists(mcqa_file):
        all_mcqa = []
        with open(mcqa_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_mcqa.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        
        # Filter and sample frontend questions
        frontend_questions = [q for q in all_mcqa if q.get("type") == "frontend" and q.get("name") != "sanity_frontend"]
        if frontend_count > 0 and frontend_questions:
            selected_frontend = random.sample(frontend_questions, min(frontend_count, len(frontend_questions)))
            for q in selected_frontend:
                choices_dict = {}
                choices = q.get("choices", [])
                if len(choices) >= 1:
                    choices_dict["choiceA"] = choices[0]
                if len(choices) >= 2:
                    choices_dict["choiceB"] = choices[1]
                if len(choices) >= 3:
                    choices_dict["choiceC"] = choices[2]
                if len(choices) >= 4:
                    choices_dict["choiceD"] = choices[3]
                
                question_id = f"frontend_{q.get('name', 'unknown')}"
                frontend_group.append({
                    "id": question_id,
                    "type": "frontend",
                    "question_type": "mcqa",
                    "question": q.get("question", ""),
                    "answer": q.get("answer", ""),
                    "choices": choices,
                    **choices_dict,
                })
        
        # Filter and sample UX questions
        ux_questions = [q for q in all_mcqa if q.get("type") == "ux"]
        if ux_count > 0 and ux_questions:
            selected_ux = random.sample(ux_questions, min(ux_count, len(ux_questions)))
            for q in selected_ux:
                choices_dict = {}
                choices = q.get("choices", [])
                if len(choices) >= 1:
                    choices_dict["choiceA"] = choices[0]
                if len(choices) >= 2:
                    choices_dict["choiceB"] = choices[1]
                if len(choices) >= 3:
                    choices_dict["choiceC"] = choices[2]
                if len(choices) >= 4:
                    choices_dict["choiceD"] = choices[3]
                
                question_id = f"ux_{q.get('name', 'unknown')}"
                ux_group.append({
                    "id": question_id,
                    "type": "ux",
                    "question_type": "mcqa",
                    "question": q.get("question", ""),
                    "answer": q.get("answer", ""),
                    "choices": choices,
                    **choices_dict,
                })
    
    # Load coding questions from JSONL
    code_file = os.path.join(repo_root, "data", "code_data_full.jsonl")
    if os.path.exists(code_file):
        all_code = []
        with open(code_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_code.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        
        # Sample coding from scratch questions (normal type)
        if coding_count > 0 and all_code:
            selected_coding = random.sample(all_code, min(coding_count, len(all_code)))
            for q in selected_coding:
                task_name = q.get("task_name", "unknown")
                coding_group.append({
                    "id": f"code_normal_{task_name}",
                    "type": "coding",
                    "question_type": "coding",
                    "task_id": task_name,
                    "python_code": q.get("blank_code_py", ""),
                    "js_code": q.get("blank_code_js", ""),
                    "test_cases": q.get("test_cases_py", ""),
                    "test_cases_py": q.get("test_cases_py", ""),
                    "test_cases_js": q.get("test_cases_js", ""),
                    "docstring_py": q.get("docstring_py", ""),
                    "docstring_js": q.get("docstring_js", ""),
                    "code_type": "normal",
                    "arguments": q.get("arguments", []),  # Include arguments field for retake mode
                    "solution": q.get("solution", ""),  # Include solution field for retake mode
                })
        
        # Sample debugging questions (debug type - use model_code)
        if debugging_count > 0 and all_code:
            # Remove already selected questions to avoid duplicates
            remaining_code = [q for q in all_code if q.get("task_name") not in [q.get("task_id") for q in coding_group]]
            if remaining_code:
                selected_debugging = random.sample(remaining_code, min(debugging_count, len(remaining_code)))
                for q in selected_debugging:
                    task_name = q.get("task_name", "unknown")
                    debugging_group.append({
                        "id": f"code_debug_{task_name}",
                        "type": "coding",
                        "question_type": "coding",
                        "task_id": task_name,
                        "python_code": q.get("model_code_py", ""),
                        "js_code": q.get("model_code_js", ""),
                        "test_cases": q.get("test_cases_py", ""),
                        "test_cases_py": q.get("test_cases_py", ""),
                        "test_cases_js": q.get("test_cases_js", ""),
                        "docstring_py": q.get("docstring_py", ""),
                        "docstring_js": q.get("docstring_js", ""),
                        "code_type": "debug",
                        "arguments": q.get("arguments", []),  # Include arguments field for retake mode
                        "solution": q.get("solution", ""),  # Include solution field for retake mode
                    })
    
    # Shuffle within each group, then concatenate in required order
    random.shuffle(frontend_group)
    random.shuffle(ux_group)
    random.shuffle(coding_group)
    random.shuffle(debugging_group)

    return frontend_group + ux_group + coding_group + debugging_group


def _select_random_questions_for_retake(db: Session) -> tuple[list[str], list[str], list[str], list[str]]:
    """
    Randomly sample questions for retake mode using the same strategy as pre-test.
    Groups by base tags/names and randomly picks between _1 and _2 variants.
    No assignment checking, no sanity questions.
    
    Returns:
        (frontend_names, ux_names, code_normal_names, code_debug_names)
    """
    # Frontend questions: use same strategy as pre-test (group by base tags, pick random variant)
    all_frontend_questions = db.query(MCQAData).filter(
        MCQAData.type == "frontend",
        MCQAData.name != "sanity_frontend"
    ).all()
    
    frontend_names: list[str] = []
    if all_frontend_questions:
        # Extract base tags from question names (same as pre-test strategy)
        base_tags = [
            "html_knowledge", "html_recall", "html_trace_code", "html_change_code",
            "css_knowledge", "css_recall", "css_trace_code", "css_change_code",
            "js_knowledge", "js_recall", "js_trace_code", "js_change_code"
        ]
        
        # Create a map of base_tag -> {name: question}
        questions_by_base = {}
        for q in all_frontend_questions:
            if not q.name:
                continue
            if '_' in q.name:
                parts = q.name.rsplit('_', 1)
                if len(parts) == 2 and parts[1] in ['1', '2']:
                    base_tag = parts[0]
                    if base_tag not in questions_by_base:
                        questions_by_base[base_tag] = {}
                    questions_by_base[base_tag][q.name] = q
        
        # For each base tag, randomly pick one variant (_1 or _2)
        for base_tag in base_tags:
            if base_tag not in questions_by_base:
                continue
            
            tag_questions = questions_by_base[base_tag]
            variant_1 = f"{base_tag}_1"
            variant_2 = f"{base_tag}_2"
            
            # If both variants exist, randomly pick one
            if variant_1 in tag_questions and variant_2 in tag_questions:
                frontend_names.append(random.choice([variant_1, variant_2]))
            # If only one variant exists, use it
            elif variant_1 in tag_questions:
                frontend_names.append(variant_1)
            elif variant_2 in tag_questions:
                frontend_names.append(variant_2)
    
    # UX questions: use same strategy as pre-test (group by base tags, pick random variant)
    all_ux_questions = db.query(MCQAData).filter(
        MCQAData.type == "ux",
        MCQAData.name != "sanity_ux"
    ).all()
    
    ux_names: list[str] = []
    if all_ux_questions:
        # Extract base tags from question names (same as pre-test strategy)
        base_tags = ["choices", "memory", "mobile", "design_protocol", "error", 
                     "aesthetics", "object", "cognitive_ease", "visual_order", "excitement"]
        
        # Create a map of base_tag -> {name: question}
        questions_by_base = {}
        for q in all_ux_questions:
            if not q.name:
                continue
            if '_' in q.name:
                parts = q.name.rsplit('_', 1)
                if len(parts) == 2 and parts[1] in ['1', '2']:
                    base_tag = parts[0]
                    if base_tag not in questions_by_base:
                        questions_by_base[base_tag] = {}
                    questions_by_base[base_tag][q.name] = q
        
        # For each base tag, randomly pick one variant (_1 or _2)
        for base_tag in base_tags:
            if base_tag not in questions_by_base:
                continue
            
            tag_questions = questions_by_base[base_tag]
            variant_1 = f"{base_tag}_1"
            variant_2 = f"{base_tag}_2"
            
            # If both variants exist, randomly pick one
            if variant_1 in tag_questions and variant_2 in tag_questions:
                ux_names.append(random.choice([variant_1, variant_2]))
            # If only one variant exists, use it
            elif variant_1 in tag_questions:
                ux_names.append(variant_1)
            elif variant_2 in tag_questions:
                ux_names.append(variant_2)
    
    # Coding questions: use same strategy as pre-test (group by base names, pick random variant)
    all_code_data = db.query(CodeData).all()
    code_normal_names: list[str] = []
    code_debug_names: list[str] = []
    
    if all_code_data:
        # Extract base names from task names (same as pre-test strategy)
        base_names = ["number", "paren", "prefix", "string_shift"]
        
        # Filter against actual CodeData entries to ensure tasks exist
        code_data_map = {q.task_name: q for q in all_code_data}
        
        # Select 2 base names for debug, 2 for normal (keep consistent order)
        # Use first 2 for normal, last 2 for debug to maintain consistent ordering
        normal_base_names = base_names[:2]
        debug_base_names = base_names[2:]
        
        # For normal tasks: randomly pick one variant (_1 or _2) for each base name
        for base_name in normal_base_names:
            variant_1 = f"{base_name}_1"
            variant_2 = f"{base_name}_2"
            
            # If both variants exist, randomly pick one
            if variant_1 in code_data_map and variant_2 in code_data_map:
                code_normal_names.append(random.choice([variant_1, variant_2]))
            # If only one variant exists, use it
            elif variant_1 in code_data_map:
                code_normal_names.append(variant_1)
            elif variant_2 in code_data_map:
                code_normal_names.append(variant_2)
        
        # For debug tasks: randomly pick one variant (_1 or _2) for each base name
        for base_name in debug_base_names:
            variant_1 = f"{base_name}_1"
            variant_2 = f"{base_name}_2"
            
            # If both variants exist, randomly pick one
            if variant_1 in code_data_map and variant_2 in code_data_map:
                code_debug_names.append(random.choice([variant_1, variant_2]))
            # If only one variant exists, use it
            elif variant_1 in code_data_map:
                code_debug_names.append(variant_1)
            elif variant_2 in code_data_map:
                code_debug_names.append(variant_2)
    
    return frontend_names, ux_names, code_normal_names, code_debug_names


def _get_or_create_skill_check_assignment(db: Session, user_id: int) -> SkillCheckAssignment:
    """
    Get an existing skill check assignment for a user or create a new one.
    """
    assignment = (
        db.query(SkillCheckAssignment)
        .filter(SkillCheckAssignment.user_id == user_id)
        .first()
    )
    if assignment:
        # Keep existing assignment; do not recompute random split (would change pre/post every time).
        return assignment

    (
        frontend_pre, frontend_post,
        ux_pre, ux_post,
        code_normal_pre, code_normal_post,
        code_debug_pre, code_debug_post,
    ) = _build_skill_check_assignment_names_split_pre_post(db)

    def _insert_deterministic(section_names: list[str], question_name: str, preferred_index: int) -> list[str]:
        updated = list(section_names)
        insert_position = max(0, min(preferred_index, len(updated)))
        updated.insert(insert_position, question_name)
        return updated

    # Sanity checks at fixed indices in each section.
    desired_frontend_pre = _insert_deterministic(frontend_pre, "sanity_frontend", preferred_index=3)
    desired_frontend_post = _insert_deterministic(frontend_post, "sanity_frontend", preferred_index=3)
    desired_ux_pre = _insert_deterministic(ux_pre, "sanity_ux", preferred_index=7)
    desired_ux_post = _insert_deterministic(ux_post, "sanity_ux", preferred_index=7)

    assignment = SkillCheckAssignment(
        user_id=user_id,
        frontend_pre_test=desired_frontend_pre,
        frontend_post_test=desired_frontend_post,
        ux_pre_test=desired_ux_pre,
        ux_post_test=desired_ux_post,
        code_pre_test=code_normal_pre,
        code_post_test=code_normal_post,
        debug_pre_test=code_debug_pre,
        debug_post_test=code_debug_post,
        sanity_ux_phase=None,
        sanity_frontend_phase=None,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def _build_ordered_question_ids(
    phase: str,
    assignment: Optional[SkillCheckAssignment],
    db: Session
) -> list[str]:
    """
    Build the ordered list of question IDs in the exact same order as get_skill_check_questions.
    
    Order:
    1. Experience questions (pre-test only)
    2. Frontend questions from assignment
    3. UX questions from assignment
    4. Normal coding questions from assignment
    5. Debug coding questions from assignment
    
    Returns:
        List of question IDs in the order they appear in the questions endpoint
    """
    config_key = "pre_test" if phase == "pre-test" else "post_test"
    question_ids_config = SKILL_CHECK_QUESTION_IDS[config_key]
    ordered_question_ids = []
    
    # Section 1: Experience questions (pre-test only)
    if phase == "pre-test":
        experience_questions = db.query(ExperienceData).filter(
            ExperienceData.id.in_(question_ids_config["experience"])
        ).order_by(ExperienceData.id).all()
        for q in experience_questions:
            ordered_question_ids.append(f"exp_{q.id}")
    
    # Section 2: Frontend questions (from assignment, in assignment order)
    if assignment:
        frontend_names = assignment.frontend_pre_test if phase == "pre-test" else assignment.frontend_post_test
        frontend_names = frontend_names or []
    else:
        # Fallback: deterministic fixed selection
        frontend_names, _, _, _ = _build_identical_skill_check_assignment_names(db)
    
    if frontend_names:
        frontend_questions_raw = db.query(MCQAData).filter(
            MCQAData.type == "frontend",
            MCQAData.name.in_(frontend_names)
        ).all()
        frontend_questions_map = {q.name: q for q in frontend_questions_raw}
        frontend_questions = [frontend_questions_map[name] for name in frontend_names if name in frontend_questions_map]
        
        # Check if we should inject sanity_frontend question in this phase
        should_inject_sanity_frontend = False
        if assignment and assignment.sanity_frontend_phase and assignment.sanity_frontend_phase == phase:
            has_sanity = any(q.name == "sanity_frontend" for q in frontend_questions)
            if not has_sanity:
                should_inject_sanity_frontend = True
        
        # Inject sanity_frontend at a random position if needed
        if should_inject_sanity_frontend:
            sanity_frontend_q = db.query(MCQAData).filter(
                MCQAData.type == "frontend",
                MCQAData.name == "sanity_frontend"
            ).first()
            if sanity_frontend_q:
                insert_position = random.randint(0, len(frontend_questions))
                frontend_questions.insert(insert_position, sanity_frontend_q)
        
        for q in frontend_questions:
            question_id = f"frontend_{q.name}" if q.name else f"frontend_{q.id}"
            ordered_question_ids.append(question_id)
    
    # Section 3: UX questions (from assignment, in assignment order)
    if assignment:
        ux_names = assignment.ux_pre_test if phase == "pre-test" else assignment.ux_post_test
        ux_names = ux_names or []
    else:
        # Fallback: deterministic fixed selection
        _, ux_names, _, _ = _build_identical_skill_check_assignment_names(db)
    
    if ux_names:
        ux_questions_raw = db.query(MCQAData).filter(
            MCQAData.type == "ux",
            MCQAData.name.in_(ux_names)
        ).all()
        ux_questions_map = {q.name: q for q in ux_questions_raw}
        ux_questions = [ux_questions_map[name] for name in ux_names if name in ux_questions_map]
        
        # Check if we should inject sanity_ux question in this phase
        should_inject_sanity_ux = False
        if assignment and assignment.sanity_ux_phase and assignment.sanity_ux_phase == phase:
            has_sanity = any(q.name == "sanity_ux" for q in ux_questions)
            if not has_sanity:
                should_inject_sanity_ux = True
        
        # Inject sanity_ux at a random position if needed
        if should_inject_sanity_ux:
            sanity_ux_q = db.query(MCQAData).filter(
                MCQAData.type == "ux",
                MCQAData.name == "sanity_ux"
            ).first()
            if sanity_ux_q:
                insert_position = random.randint(0, len(ux_questions))
                ux_questions.insert(insert_position, sanity_ux_q)
        
        for q in ux_questions:
            question_id = f"ux_{q.name}" if q.name else f"ux_{q.id}"
            ordered_question_ids.append(question_id)
    
    # Section 4 & 5: Code questions (normal first, then debug)
    if assignment:
        if phase == "pre-test":
            code_normal_names = assignment.code_pre_test or []
            code_debug_names = assignment.debug_pre_test or []
        else:
            code_normal_names = assignment.code_post_test or []
            code_debug_names = assignment.debug_post_test or []
    else:
        # Fallback: deterministic fixed selection (no persistent assignment)
        _, _, code_normal_names, code_debug_names = _build_identical_skill_check_assignment_names(db)
    
    # Load all code tasks that actually exist
    selected_task_names = list(set(code_normal_names + code_debug_names))
    code_questions = db.query(CodeData).filter(
        CodeData.task_name.in_(selected_task_names)
    ).all() if selected_task_names else []
    code_data_map = {q.task_name: q for q in code_questions}
    
    # Add code normal questions in order (only if they exist)
    for task_name in code_normal_names:
        if task_name in code_data_map:
            ordered_question_ids.append(f"code_normal_{task_name}")
    
    # Add code debug questions in order (only if they exist)
    for task_name in code_debug_names:
        if task_name in code_data_map:
            ordered_question_ids.append(f"code_debug_{task_name}")
    
    return ordered_question_ids


@app.get("/api/skill-check/questions", tags=["Tasks"])
async def get_skill_check_questions(
    request: Request,
    mode: str = Query(..., description="Skill check mode: 'pre-test', 'post-test', or 'retake'"),
    user_id: Optional[int] = Query(None, description="User ID for skill check assignment (ignored for retake mode)"),
    frontend_count: Optional[int] = Query(None, description="Number of frontend MCQA questions for retake (0-15)"),
    ux_count: Optional[int] = Query(None, description="Number of UX MCQA questions for retake (0-15)"),
    coding_count: Optional[int] = Query(None, description="Number of coding from scratch questions for retake (0-5)"),
    debugging_count: Optional[int] = Query(None, description="Number of debugging questions for retake (0-5)"),
    db: Session = Depends(get_db),
):
    """
    Load skill check questions based on mode.
    
    Pre-test and Post-test:
    - Pre-test includes configured questions from experience_data
    - Post-test excludes experience questions
    - Fixed frontend/UX question names (same in both phases)
    - Fixed coding set: normal `paren_1`; debug `string_shift_2`, `prefix_2`
    
    Retake:
    - 10 randomly sampled frontend questions (no sanity questions)
    - 10 randomly sampled UX questions (no sanity questions)
    - 3 randomly sampled coding questions (normal)
    - 3 randomly sampled coding questions (debug)
    - No experience/NASA TLI questions
    - No assignment checking
    """
    try:
        if mode not in ["pre-test", "post-test", "retake"]:
            return JSONResponse(
                status_code=400,
                content={"error": "Mode must be 'pre-test', 'post-test', or 'retake'"}
            )
        
        # Handle retake mode separately (no assignment, random sampling)
        # IMPORTANT: All questions are sampled/decided in one go before any are returned
        # This ensures the entire question set is fixed for the retake session
        if mode == "retake":
            # Check if count parameters were actually sent in the request URL
            # This distinguishes between "parameter not sent" vs "parameter sent as None/0"
            query_params = dict(request.query_params)
            has_frontend_param = "frontend_count" in query_params
            has_ux_param = "ux_count" in query_params
            has_coding_param = "coding_count" in query_params
            has_debugging_param = "debugging_count" in query_params
            has_any_param = has_frontend_param or has_ux_param or has_coding_param or has_debugging_param
            
            # Convert counts to integers (FastAPI query params should already be int, but be safe)
            frontend_count_int = None
            ux_count_int = None
            coding_count_int = None
            debugging_count_int = None
            
            try:
                if frontend_count is not None:
                    frontend_count_int = int(frontend_count)
                elif has_frontend_param:
                    # Parameter was sent but is None/empty, treat as 0
                    frontend_count_int = 0
                    
                if ux_count is not None:
                    ux_count_int = int(ux_count)
                elif has_ux_param:
                    ux_count_int = 0
                    
                if coding_count is not None:
                    coding_count_int = int(coding_count)
                elif has_coding_param:
                    coding_count_int = 0
                    
                if debugging_count is not None:
                    debugging_count_int = int(debugging_count)
                elif has_debugging_param:
                    debugging_count_int = 0
            except (ValueError, TypeError) as e:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Invalid question count parameter: {str(e)}"}
                )
            
            # If ANY count parameter was in the URL, use JSONL loading
            # This ensures we use the new logic when the modal is used
            has_any_count = has_any_param or (frontend_count_int is not None or ux_count_int is not None or 
                            coding_count_int is not None or debugging_count_int is not None)
            
            if has_any_count:
                try:
                    questions = _load_questions_from_jsonl(
                        frontend_count_int if frontend_count_int is not None else 0,
                        ux_count_int if ux_count_int is not None else 0,
                        coding_count_int if coding_count_int is not None else 0,
                        debugging_count_int if debugging_count_int is not None else 0
                    )
                    return {
                        "questions": questions,
                        "total": len(questions),
                        "mode": mode
                    }
                except Exception as e:
                    return JSONResponse(
                        status_code=500,
                        content={"error": f"Failed to load questions from JSONL: {str(e)}"}
                    )
            
            # Retake mode REQUIRES count parameters - fail if not provided
            # This ensures we always use JSONL files and never fall back to old database logic
            return JSONResponse(
                status_code=400,
                content={"error": "Retake mode requires question count parameters (frontend_count, ux_count, coding_count, debugging_count). These must be provided when using the retake feature."}
            )
            
        
        config_key = "pre_test" if mode == "pre-test" else "post_test"
        question_ids = SKILL_CHECK_QUESTION_IDS[config_key]

        # For logged-in users, use (or create) a persistent skill check assignment
        assignment: Optional[SkillCheckAssignment] = None
        if user_id is not None:
            assignment = _get_or_create_skill_check_assignment(db, user_id)

        questions = []
        
        # Load experience questions only for pre-test
        if mode == "pre-test":
            experience_questions = db.query(ExperienceData).filter(
                ExperienceData.id.in_(question_ids["experience"])
            ).order_by(ExperienceData.id).all()  # Explicit ordering for consistency
            
            for q in experience_questions:
                questions.append({
                    "id": f"exp_{q.id}",
                    "type": "experience",
                    "question_type": q.type,  # 'mcqa', 'multi_select', 'multi_select_with_time', or 'integer'
                    "question": q.question,
                    "choices": q.choices,
                })
        
        # Determine frontend question names based on assignment (if available)
        if assignment is not None:
            if mode == "pre-test":
                frontend_names = assignment.frontend_pre_test or []
            else:
                frontend_names = assignment.frontend_post_test or []
        else:
            # Fallback: deterministic fixed selection
            frontend_names, _, _, _ = _build_identical_skill_check_assignment_names(db)

        # Load frontend questions by name
        frontend_questions_raw = db.query(MCQAData).filter(
            MCQAData.type == "frontend",
            MCQAData.name.in_(frontend_names)
        ).all() if frontend_names else []
        
        # Sort questions to match the order in frontend_names (assignment order)
        frontend_questions_map = {q.name: q for q in frontend_questions_raw}
        frontend_questions = [frontend_questions_map[name] for name in frontend_names if name in frontend_questions_map]
        
        # Check if we should inject sanity_frontend question in this phase
        # (Backward compatibility: if assignment was created before sanity questions were added to columns)
        should_inject_sanity_frontend = False
        if assignment is not None and assignment.sanity_frontend_phase and assignment.sanity_frontend_phase == mode:
            # Only inject if not already in the list (new assignments have it in the column)
            has_sanity = any(q.name == "sanity_frontend" for q in frontend_questions)
            if not has_sanity:
                should_inject_sanity_frontend = True
        
        # Inject sanity_frontend at a random position if needed
        if should_inject_sanity_frontend:
            sanity_frontend_q = db.query(MCQAData).filter(
                MCQAData.type == "frontend",
                MCQAData.name == "sanity_frontend"
            ).first()
            if sanity_frontend_q:
                # Insert at a random position within the frontend questions
                insert_position = random.randint(0, len(frontend_questions))
                frontend_questions.insert(insert_position, sanity_frontend_q)
        
        for q in frontend_questions:
            # Convert choices array to choiceA, choiceB, choiceC, choiceD format
            choices_dict = {}
            if len(q.choices) >= 1:
                choices_dict["choiceA"] = q.choices[0]
            if len(q.choices) >= 2:
                choices_dict["choiceB"] = q.choices[1]
            if len(q.choices) >= 3:
                choices_dict["choiceC"] = q.choices[2]
            if len(q.choices) >= 4:
                choices_dict["choiceD"] = q.choices[3]
            
            # Use name as ID if available, fallback to numeric id for backward compatibility
            question_id = f"frontend_{q.name}" if q.name else f"frontend_{q.id}"
            
            questions.append({
                "id": question_id,
                "type": "frontend",
                "question_type": "mcqa",
                "question": q.question,
                "answer": q.answer,
                "choices": q.choices,  # Keep original for reference
                **choices_dict,
            })
        
        # Determine UX question names based on assignment (if available)
        if assignment is not None:
            if mode == "pre-test":
                ux_names = assignment.ux_pre_test or []
            else:
                ux_names = assignment.ux_post_test or []
        else:
            # Fallback: deterministic fixed selection
            _, ux_names, _, _ = _build_identical_skill_check_assignment_names(db)

        # Load UX questions by name
        ux_questions_raw = db.query(MCQAData).filter(
            MCQAData.type == "ux",
            MCQAData.name.in_(ux_names)
        ).all() if ux_names else []
        
        # Sort questions to match the order in ux_names (assignment order)
        ux_questions_map = {q.name: q for q in ux_questions_raw}
        ux_questions = [ux_questions_map[name] for name in ux_names if name in ux_questions_map]
        
        # Check if we should inject sanity_ux question in this phase
        # (Backward compatibility: if assignment was created before sanity questions were added to columns)
        should_inject_sanity_ux = False
        if assignment is not None and assignment.sanity_ux_phase and assignment.sanity_ux_phase == mode:
            # Only inject if not already in the list (new assignments have it in the column)
            has_sanity = any(q.name == "sanity_ux" for q in ux_questions)
            if not has_sanity:
                should_inject_sanity_ux = True
        
        # Inject sanity_ux at a random position if needed
        if should_inject_sanity_ux:
            sanity_ux_q = db.query(MCQAData).filter(
                MCQAData.type == "ux",
                MCQAData.name == "sanity_ux"
            ).first()
            if sanity_ux_q:
                # Insert at a random position within the UX questions
                insert_position = random.randint(0, len(ux_questions))
                ux_questions.insert(insert_position, sanity_ux_q)
        
        for q in ux_questions:
            # Convert choices array to choiceA, choiceB, choiceC, choiceD format
            choices_dict = {}
            if len(q.choices) >= 1:
                choices_dict["choiceA"] = q.choices[0]
            if len(q.choices) >= 2:
                choices_dict["choiceB"] = q.choices[1]
            if len(q.choices) >= 3:
                choices_dict["choiceC"] = q.choices[2]
            if len(q.choices) >= 4:
                choices_dict["choiceD"] = q.choices[3]
            
            # Use name as ID if available, fallback to numeric id for backward compatibility
            question_id = f"ux_{q.name}" if q.name else f"ux_{q.id}"
            
            questions.append({
                "id": question_id,
                "type": "ux",
                "question_type": "mcqa",
                "question": q.question,
                "answer": q.answer,
                "choices": q.choices,  # Keep original for reference
                **choices_dict,
            })
        
        # Determine coding task assignments (CodeData.task_name) based on assignment
        selected_tasks: list[tuple[str, str]] = []
        if assignment is not None:
            if mode == "pre-test":
                code_normal_names = assignment.code_pre_test or []
                code_debug_names = assignment.debug_pre_test or []
            else:
                code_normal_names = assignment.code_post_test or []
                code_debug_names = assignment.debug_post_test or []
            selected_tasks.extend((name, "normal") for name in code_normal_names)
            selected_tasks.extend((name, "debug") for name in code_debug_names)
        else:
            # Fallback: deterministic fixed selection (no persistent assignment)
            _, _, code_normal_names, code_debug_names = _build_identical_skill_check_assignment_names(db)
            selected_tasks.extend((name, "normal") for name in code_normal_names)
            selected_tasks.extend((name, "debug") for name in code_debug_names)

        # Load the selected coding questions
        selected_task_names = [task_name for task_name, _ in selected_tasks]
        code_questions = db.query(CodeData).filter(
            CodeData.task_name.in_(selected_task_names)
        ).all()
        
        # Create a mapping of task_name to code_data
        code_data_map = {q.task_name: q for q in code_questions}
        
        # Add questions in the order: normal tasks first, then debug tasks
        for task_name, code_type in selected_tasks:
            if task_name in code_data_map:
                q = code_data_map[task_name]
                # For normal tasks: show blank code (user implements from scratch)
                # For debug tasks: show model code (user debugs/fixes existing code)
                questions.append({
                    "id": f"code_{code_type}_{q.task_name}",
                    "type": "coding",
                    "question_type": "coding",
                    "task_id": q.task_name,  # Keep for backward compatibility
                    "python_code": q.blank_code_py if code_type == "normal" else q.model_code_py,
                    "js_code": q.blank_code_js if code_type == "normal" else q.model_code_js,
                    "test_cases": q.test_cases_py,  # Python test cases
                    "test_cases_py": q.test_cases_py,
                    "test_cases_js": q.test_cases_js,
                    "docstring_py": q.docstring_py or "",
                    "docstring_js": q.docstring_js or "",
                    "code_type": code_type,
                })
        
        return {
            "questions": questions,
            "total": len(questions),
            "mode": mode
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to load skill check questions: {str(e)}"}
        )


@app.post("/api/skill-check/run-tests", tags=["Tasks"])
async def run_skill_check_tests(request: dict):
    """
    Execute test cases for skill check coding questions.
    
    Request body:
    {
        "code": "...",           # User's code
        "test_cases": "...",     # Test cases (assert statements)
        "language": "python" | "javascript"
    }
    
    Returns:
    {
        "success": boolean,
        "all_passed": boolean,
        "error_message": string | null,  # First assertion failure error if any
        "stdout": string,
        "stderr": string
    }
    """
    try:
        code = request.get("code", "")
        test_cases = request.get("test_cases", "")
        language = request.get("language", "python")
        
        if not code:
            return JSONResponse(
                status_code=400,
                content={"error": "Code is required"}
            )
        
        if not test_cases:
            return JSONResponse(
                status_code=400,
                content={"error": "Test cases are required"}
            )
        
        if language not in ["python", "javascript"]:
            return JSONResponse(
                status_code=400,
                content={"error": "Language must be 'python' or 'javascript'"}
            )
        
        # Combine user code with test cases
        # Wrap test execution to stop on first failure and handle console.log separately
        if language == "python":
            # For Python: Just combine code and test cases (Python assertions already stop on first failure)
            combined_code = f"{code}\n{test_cases}"

            # print(combined_code[:1500])
        else:  # javascript
            # For JavaScript: Wrap test execution and handle console.log separately
            # Save original console methods
            js_wrapper = """
// Save original console methods
const _originalConsoleLog = console.log;
const _originalConsoleAssert = console.assert;

// Capture user console.log output separately
let userOutput = [];
console.log = function(...args) {
    userOutput.push(args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' '));
};

// Wrap console.assert to stop on first failure
console.assert = function(condition, ...args) {
    if (!condition) {
        const message = args.length > 0 ? args[0] : 'Assertion failed';
        // Restore original console methods
        console.log = _originalConsoleLog;
        console.assert = _originalConsoleAssert;
        // Throw error to stop execution
        throw new Error(message);
    }
};

try {
"""
            js_wrapper_end = """
} catch (e) {
    // Restore console methods
    console.log = _originalConsoleLog;
    console.assert = _originalConsoleAssert;
    // Print user console.log output even on failure
    if (userOutput.length > 0) {
        console.log(userOutput.join('\\n'));
    }
    // Print error to stderr and exit
    // Format error message to match backend parsing expectations
    const errorMsg = e.message || 'Assertion failed';
    console.error('AssertionError: ' + errorMsg);
    process.exit(1);
}

// Restore console methods after successful execution
console.log = _originalConsoleLog;
console.assert = _originalConsoleAssert;
// Output user console.log statements to stdout (if any)
if (userOutput.length > 0) {
    console.log(userOutput.join('\\n'));
}
"""
            # Indent the test cases
            indented_test_cases = "\n".join("    " + line if line.strip() else line for line in test_cases.split("\n"))
            combined_code = f"{code}\n{js_wrapper}{indented_test_cases}\n{js_wrapper_end}"
        
        # Execute the code
        if language == "python":
            result = await onecompiler_service.execute_python(combined_code)
        else:  # javascript
            result = await onecompiler_service.execute_javascript(combined_code)
        
        # Check if execution service returned an error (e.g., API key missing, API failure)
        if not result.get("success", False) and result.get("error"):
            return {
                "success": False,
                "all_passed": False,
                "error_message": f"Execution error: {result.get('error')}",
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", result.get("error", "")),
                "exit_code": result.get("exit_code", 1)
            }
        
        # Parse results to determine if all tests passed
        # Tests pass ONLY if code compiles, runs without errors, and all assertions pass
        stderr = result.get("stderr", "")
        stdout = result.get("stdout", "")
        exit_code = result.get("exit_code", 1)
        
        # Combine outputs for error detection
        combined_output = (stderr + "\n" + stdout).lower()
        
        # Check for syntax/compilation errors (these should always fail tests)
        # If code doesn't compile, it cannot pass tests
        has_syntax_error = False
        
        if language == "python":
            # Python syntax/compilation errors
            syntax_error_indicators = [
                'syntaxerror',
                'syntax error',
                'indentationerror',
                'indentation error',
            ]
            has_syntax_error = any(indicator in combined_output for indicator in syntax_error_indicators)
        else:  # javascript
            # JavaScript syntax/compilation errors
            syntax_error_indicators = [
                'syntaxerror',
                'syntax error',
                'unexpected token',
                'unexpected identifier',
                'unexpected end of input',
            ]
            has_syntax_error = any(indicator in combined_output for indicator in syntax_error_indicators)
        
        # Check for assertion errors in output (even if exit_code is 0)
        # JavaScript assertions might throw but still exit with code 0
        has_assertion_error = (
            'assertionerror' in combined_output or 
            'assertion failed' in combined_output or
            'assertionerror' in stderr or
            'assertionerror' in stdout or
            'assertion failed' in stderr.lower() or
            'assertion failed' in stdout.lower()
        )
        
        # Check if there are any runtime errors in stderr (excluding warnings)
        # If stderr has content that's not a warning, it's likely an error
        has_runtime_error = False
        if stderr and stderr.strip():
            stderr_lower = stderr.lower().strip()
            # Check if it's not just a warning
            if not stderr_lower.startswith('warning'):
                # Check for common error patterns
                error_patterns = ['error', 'exception', 'failed', 'traceback']
                has_runtime_error = any(pattern in stderr_lower for pattern in error_patterns)
        
        # Tests pass ONLY if ALL of these conditions are met:
        # 1. Execution service succeeded (success=True)
        # 2. Exit code is 0 (code compiled and ran without errors)
        # 3. No syntax/compilation errors detected
        # 4. No assertion errors detected
        # 5. No runtime errors in stderr
        all_passed = (
            result.get("success", False) and  # Execution service succeeded
            exit_code == 0 and                # Code compiled and ran without errors
            not has_syntax_error and          # No syntax/compilation errors
            not has_assertion_error and      # No assertion failures
            not has_runtime_error            # No runtime errors
        )
        error_message = None
        
        # If execution failed or has errors, extract the error message
        if not all_passed:
            
            # Prioritize stderr for errors, but also check stdout
            error_output = stderr if stderr else stdout
            # Also check stdout if stderr doesn't have the error
            if error_output and not any(keyword in error_output.lower() for keyword in ['assertionerror', 'assertion failed', 'syntaxerror', 'syntax error', 'error', 'exception']):
                if stdout and any(keyword in stdout.lower() for keyword in ['assertionerror', 'assertion failed', 'syntaxerror', 'syntax error', 'error', 'exception']):
                    error_output = stdout
            
            if error_output:
                error_message = None
                error_output_trimmed = error_output.strip()
                
                if language == "python":
                    # Keep the full error output including traceback
                    # For syntax/compilation errors, this will show the full error
                    if has_syntax_error:
                        # For syntax/compilation errors, keep the full error message with traceback
                        error_message = error_output_trimmed or "Syntax/Compilation error"
                    elif 'AssertionError' in error_output_trimmed:
                        # For assertion errors, keep the full error including traceback
                        error_message = error_output_trimmed
                    else:
                        # For other errors (runtime errors, etc.), keep full error output including traceback
                        error_message = error_output_trimmed or "Execution error"
                    
                else:  # javascript
                    # For JavaScript, keep the full error output (no manual parsing)
                    # This includes syntax errors, assertion errors, and runtime errors
                    error_message = error_output_trimmed or "Error"
        
        return {
            "success": True,
            "all_passed": all_passed,
            "error_message": error_message,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "exit_code": result.get("exit_code", 1)
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to execute test cases: {str(e)}"}
        )


@app.post("/api/skill-check/run-code", tags=["Tasks"])
async def run_skill_check_code(request: dict):
    """
    Execute user code with custom inputs for skill check coding questions.
    
    Request body:
    {
        "code": "...",           # User's code
        "function_name": "...",   # Name of function to call (optional, will try to detect)
        "inputs": {...},         # Dictionary of input parameter names to values
        "language": "python" | "javascript"
    }
    
    Returns:
    {
        "success": boolean,
        "stdout": string,
        "stderr": string,
        "result": any            # Return value of the function (if applicable)
    }
    """
    try:
        code = request.get("code", "")
        function_name = request.get("function_name", "")
        inputs = request.get("inputs", {})
        language = request.get("language", "python")
        
        if not code:
            return JSONResponse(
                status_code=400,
                content={"error": "Code is required"}
            )
        
        if language not in ["python", "javascript"]:
            return JSONResponse(
                status_code=400,
                content={"error": "Language must be 'python' or 'javascript'"}
            )
        
        # Build execution code that calls the function with inputs
        if language == "python":
            # For Python: try to detect function name if not provided
            if not function_name:
                # Simple regex to find function definitions
                import re
                func_match = re.search(r'def\s+(\w+)\s*\(', code)
                if func_match:
                    function_name = func_match.group(1)
            
            if function_name:
                # Build call with **kwargs
                inputs_str = ", ".join([f"{k}={repr(v)}" for k, v in inputs.items()])
                execution_code = f"{code}\n\n# Call the function with provided inputs\nresult = {function_name}({inputs_str})\nprint(result)"
            else:
                # If no function found, just execute the code and print any result
                execution_code = code
        else:  # javascript
            # For JavaScript: try to detect function name if not provided
            if not function_name:
                # Simple regex to find function definitions (function name() or const name = function() or const name = () =>)
                import re
                func_match = re.search(r'(?:function\s+(\w+)|const\s+(\w+)\s*=\s*(?:function|\([^)]*\)\s*=>))', code)
                if func_match:
                    function_name = func_match.group(1) or func_match.group(2)
            
            if function_name:
                # Build call with inputs - properly format JavaScript object
                inputs_parts = []
                for k, v in inputs.items():
                    # Format value appropriately for JavaScript
                    if isinstance(v, str):
                        inputs_parts.append(f"{k}: {json.dumps(v)}")
                    elif isinstance(v, (int, float, bool)):
                        inputs_parts.append(f"{k}: {json.dumps(v)}")
                    elif isinstance(v, (list, dict)):
                        inputs_parts.append(f"{k}: {json.dumps(v)}")
                    else:
                        inputs_parts.append(f"{k}: {json.dumps(str(v))}")
                inputs_str = ", ".join(inputs_parts)
                execution_code = f"{code}\n\n// Call the function with provided inputs\nconst result = {function_name}({{{inputs_str}}});\nconsole.log(result);"
            else:
                # If no function found, just execute the code
                execution_code = code
        
        # Execute the code
        if language == "python":
            result = await onecompiler_service.execute_python(execution_code)
        else:  # javascript
            result = await onecompiler_service.execute_javascript(execution_code)
        
        # Check if execution service returned an error (e.g., API key missing, API failure)
        if not result.get("success", False) and result.get("error"):
            return {
                "success": False,
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", result.get("error", "")),
                "result": None,
                "exit_code": result.get("exit_code", 1),
                "error": result.get("error")
            }
        
        # Extract result from stdout if available
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        
        # Try to parse the result from stdout (last line is usually the result)
        parsed_result = None
        if stdout:
            lines = stdout.strip().split('\n')
            if lines:
                last_line = lines[-1]
                # Try to parse as JSON or Python literal
                try:
                    parsed_result = json.loads(last_line)
                except:
                    # Try to evaluate as Python literal (for Python)
                    if language == "python":
                        try:
                            parsed_result = eval(last_line)
                        except:
                            parsed_result = last_line
                    else:
                        parsed_result = last_line
        
        return {
            "success": result.get("success", False),
            "stdout": stdout,
            "stderr": stderr,
            "result": parsed_result,
            "exit_code": result.get("exit_code", 1)
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to execute code: {str(e)}"}
        )


@app.post("/api/skill-check/log-mcqa-response", tags=["Tasks"])
async def log_mcqa_response(request: dict, db: Session = Depends(get_db)):
    """
    Log a user's MCQA response when they hit next question.
    
    Request body:
    {
        "user_id": int,
        "question_id": str,  # e.g., "experience_1", "nasa_1", or MCQA id
        "question_type": str,  # 'experience', 'nasa_tli', 'ux', 'frontend'
        "phase": str | null,  # 'pre-test', 'post-test', or 'retake_{uuid}'
        "answer_text": List[str],  # List of answer texts
        "answer_letter": List[str],  # List of answer letters (e.g., ['A', 'B'])
    }
    """
    try:
        user_id = request.get("user_id")
        question_id = request.get("question_id")
        question_type = request.get("question_type")
        phase = request.get("phase")
        answer_text = request.get("answer_text", [])
        answer_letter = request.get("answer_letter", [])
        # Frontend may provide gold answers directly (especially for retake mode)
        gold_answer_letter_provided = request.get("gold_answer_letter")
        gold_answer_text_provided = request.get("gold_answer_text")
        
        if not user_id or not question_id or not question_type:
            return JSONResponse(
                status_code=400,
                content={"error": "user_id, question_id, and question_type are required"}
            )
        
        # Determine if answer is correct and compute gold answers
        correct = True  # Default to True for experience/nasa_tli
        gold_answer_letter: list[str] = []
        gold_answer_text: list[str] = []
        
        # Use provided gold answers if available (from frontend), otherwise look up from database
        if gold_answer_letter_provided is not None and gold_answer_text_provided is not None:
            gold_answer_letter = gold_answer_letter_provided if isinstance(gold_answer_letter_provided, list) else []
            gold_answer_text = gold_answer_text_provided if isinstance(gold_answer_text_provided, list) else []
            # Compare user's answer letters with correct answer
            if question_type in ['ux', 'frontend']:
                user_answers = [a.strip().upper() for a in answer_letter if a and a.strip()]
                # Sort both for comparison (order doesn't matter for multi-select)
                if not gold_answer_letter or not user_answers:
                    # If either is empty, they must both be empty to be correct
                    correct = len(gold_answer_letter) == 0 and len(user_answers) == 0
                else:
                    correct = sorted(gold_answer_letter) == sorted(user_answers)
        elif question_type in ['ux', 'frontend']:
            # For UX and frontend questions, check against MCQA database
            # Extract name or numeric ID from question_id (e.g., "frontend_choices_1" -> "choices_1", "ux_5" -> 5)
            try:
                mcqa_question = None
                if '_' in question_id:
                    # Try to extract name first (everything after the first underscore)
                    prefix, identifier = question_id.split('_', 1)
                    # Try to find by name first
                    mcqa_question = db.query(MCQAData).filter(MCQAData.name == identifier).first()
                    # If not found by name, try numeric ID (backward compatibility)
                    if not mcqa_question:
                        try:
                            mcqa_id = int(identifier)
                            mcqa_question = db.query(MCQAData).filter(MCQAData.id == mcqa_id).first()
                        except ValueError:
                            pass
                else:
                    # Try as numeric ID (backward compatibility)
                    mcqa_id = int(question_id)
                    mcqa_question = db.query(MCQAData).filter(MCQAData.id == mcqa_id).first()
                if mcqa_question and mcqa_question.answer:
                    # Correct answer is a single letter like "B" or multiple like "B,C"
                    gold_answer_letter = [a.strip().upper() for a in mcqa_question.answer.split(',')]
                    # Map gold letters to gold answer texts using MCQA choices
                    if mcqa_question.choices:
                        for letter in gold_answer_letter:
                            idx = ord(letter) - ord('A')
                            if 0 <= idx < len(mcqa_question.choices):
                                gold_answer_text.append(mcqa_question.choices[idx])
                    # Compare user's answer letters with correct answer
                    user_answers = [a.strip().upper() for a in answer_letter if a and a.strip()]
                    # Sort both for comparison (order doesn't matter for multi-select)
                    # Ensure both lists are non-empty and match exactly
                    if not gold_answer_letter or not user_answers:
                        # If either is empty, they must both be empty to be correct
                        correct = len(gold_answer_letter) == 0 and len(user_answers) == 0
                    else:
                        correct = sorted(gold_answer_letter) == sorted(user_answers)
                else:
                    # If no answer in DB, default to True and leave gold_* empty
                    correct = True
            except (ValueError, AttributeError):
                correct = True  # If parsing fails, default to True
        # For experience and nasa_tli, correct is always True (no right/wrong answers)
        
        # Create response
        response_create = UserMCQASkillResponseCreate(
            user_id=user_id,
            question_id=question_id,
            question_type=question_type,
             phase=phase,
            answer_text=answer_text,
            answer_letter=answer_letter,
            gold_answer_text=gold_answer_text,
            gold_answer_letter=gold_answer_letter,
            correct=correct,
        )
        
        response = UserMCQASkillResponseCRUD.create(db, response_create)
        
        return {
            "success": True,
            "response_id": response.id,
            "correct": correct
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to log MCQA response: {str(e)}"}
        )


@app.post("/api/skill-check/log-code-response", tags=["Tasks"])
async def log_code_response(request: dict, db: Session = Depends(get_db)):
    """
    Log a user's code response when they hit the test case button.
    
    Request body:
    {
        "user_id": int,
        "question_id": str,  # ID of the code question
        "question_type": str,  # 'normal' or 'debug'
        "phase": str | null,  # 'pre-test', 'post-test', or 'retake_{uuid}'
        "py_code": str,  # User's Python code (optional)
        "js_code": str,  # User's JavaScript code (optional)
        "submitted_language": str,  # 'python' or 'javascript'
        "state": str,  # 'started', 'failed', or 'passed'
    }
    """
    try:
        user_id = request.get("user_id")
        question_id = request.get("question_id")
        question_type = request.get("question_type", "normal")
        phase = request.get("phase")
        py_code = request.get("py_code")
        js_code = request.get("js_code")
        submitted_language = request.get("submitted_language")
        state = request.get("state")
        
        if not user_id or not question_id or not submitted_language or not state:
            return JSONResponse(
                status_code=400,
                content={"error": "user_id, question_id, submitted_language, and state are required"}
            )
        
        if state not in ['started', 'failed', 'passed', 'view_solution']:
            return JSONResponse(
                status_code=400,
                content={"error": "state must be 'started', 'failed', 'passed', or 'view_solution'"}
            )
        
        if submitted_language not in ['python', 'javascript']:
            return JSONResponse(
                status_code=400,
                content={"error": "submitted_language must be 'python' or 'javascript'"}
            )
        
        # Create response
        response_create = UserCodeSkillResponseCreate(
            user_id=user_id,
            question_id=question_id,
            question_type=question_type,
            phase=phase,
            py_code=py_code,
            js_code=js_code,
            submitted_language=submitted_language,
            state=state,
        )
        
        response = UserCodeSkillResponseCRUD.create(db, response_create)
        
        return {
            "success": True,
            "response_id": response.id
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to log code response: {str(e)}"}
        )


@app.post("/api/skill-check/report-question", tags=["Tasks"])
async def report_skill_check_question(request: dict, db: Session = Depends(get_db)):
    """
    Report a skill check question with rationale.
    Also logs the response as incorrect with empty answers.
    
    Request body:
    {
        "user_id": int,
        "question_id": str,  # ID of the reported question
        "question_type": str,  # 'experience', 'nasa_tli', 'ux', 'frontend', 'coding'
        "phase": str | null,  # 'pre-test', 'post-test', or 'retake_{uuid}'
        "report_type": str,  # 'issue_stops_solving', 'frustrated_unable_to_solve', 'insufficient_programming_experience', or 'other'
        "rationale": str,  # Required rationale explaining the report
    }
    """
    try:
        user_id = request.get("user_id")
        question_id = request.get("question_id")
        question_type = request.get("question_type")
        phase = request.get("phase")
        report_type = request.get("report_type")
        rationale = request.get("rationale")
        
        if not user_id or not question_id or not question_type or not report_type or not rationale:
            return JSONResponse(
                status_code=400,
                content={"error": "user_id, question_id, question_type, report_type, and rationale are required"}
            )
        
        if report_type not in ['issue_stops_solving', 'frustrated_unable_to_solve', 'insufficient_programming_experience', 'other']:
            return JSONResponse(
                status_code=400,
                content={"error": "report_type must be 'issue_stops_solving', 'frustrated_unable_to_solve', 'insufficient_programming_experience', or 'other'"}
            )
        
        trimmed_rationale = rationale.strip()
        if not trimmed_rationale:
            return JSONResponse(
                status_code=400,
                content={"error": "rationale cannot be empty"}
            )
        
        # Check word count (minimum 10 words)
        word_count = len(trimmed_rationale.split())
        if word_count < 10:
            return JSONResponse(
                status_code=400,
                content={"error": f"rationale must be at least 10 words (currently {word_count} words)"}
            )
        
        # Create the report
        report_create = ReportSkillCheckQuestionCreate(
            user_id=user_id,
            question_id=question_id,
            question_type=question_type,
            phase=phase,
            report_type=report_type,
            rationale=trimmed_rationale,
        )
        
        report = ReportSkillCheckQuestionCRUD.create(db, report_create)
        
        # Also log the response as incorrect with empty answers
        if question_type == 'coding':
            # Log code response with empty code and reported state
            code_type = request.get("code_type", "normal")  # Assume normal if not provided
            code_response_create = UserCodeSkillResponseCreate(
                user_id=user_id,
                question_id=question_id,
                question_type=code_type,
                phase=phase,
                py_code='',
                js_code='',
                submitted_language='python',  # Default, doesn't matter since code is empty
                state='reported',
            )
            UserCodeSkillResponseCRUD.create(db, code_response_create)
        else:
            # Log MCQA response with empty answers and incorrect
            mcqa_response_create = UserMCQASkillResponseCreate(
                user_id=user_id,
                question_id=question_id,
                question_type=question_type,
                phase=phase,
                answer_text=[],
                answer_letter=[],
                gold_answer_text=None,
                gold_answer_letter=None,
                correct=False,
            )
            UserMCQASkillResponseCRUD.create(db, mcqa_response_create)
        
        return {
            "success": True,
            "report_id": report.id
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to report question: {str(e)}"}
        )


def _get_completion_status_for_phase(
    user_id: int,
    phase: str,
    assignment: Optional[SkillCheckAssignment],
    db: Session
) -> dict:
    """
    Helper function to compute completion status for a given phase.
    Returns the same structure as get_skill_check_completion_status.
    """
    if not assignment:
        # No assignment means user hasn't started
        print(f"⚠️  No assignment found for user_id={user_id}")
        return {
            "completed": False,
            "has_responses": False,
            "total_expected": 0,
            "total_answered": 0,
            "current_question_index": 0
        }
    
    # Determine expected question IDs based on phase.
    config_key = "pre_test" if phase == "pre-test" else "post_test"
    question_ids_config = SKILL_CHECK_QUESTION_IDS[config_key]
    
    # Build sets for completion checking (needed to verify specific question IDs are answered)
    expected_mcqa_question_ids = set()
    expected_code_question_ids = set()
    
    # Calculate total_expected directly from assignment arrays (simpler than counting sets)
    total_expected_mcqa = 0
    total_expected_code = 0
    
    if phase == "pre-test":
        # Experience questions: query database to get actual count (not all IDs in config may exist)
        experience_questions = db.query(ExperienceData).filter(
            ExperienceData.id.in_(question_ids_config["experience"])
        ).all()
        experience_count = len(experience_questions)
        total_expected_mcqa += experience_count
        for q in experience_questions:
            expected_mcqa_question_ids.add(f"exp_{q.id}")
        
        # Frontend questions from assignment (now stored as names)
        # Only count questions that actually exist in the database (matching questions endpoint logic)
        if assignment.frontend_pre_test:
            frontend_names = [name for name in assignment.frontend_pre_test if name]
            # Load questions that actually exist
            frontend_questions_raw = db.query(MCQAData).filter(
                MCQAData.type == "frontend",
                MCQAData.name.in_(frontend_names)
            ).all() if frontend_names else []
            frontend_questions_map = {q.name: q for q in frontend_questions_raw}
            frontend_questions = [frontend_questions_map[name] for name in frontend_names if name in frontend_questions_map]
            frontend_count = len(frontend_questions)
            total_expected_mcqa += frontend_count
            for q in frontend_questions:
                question_id = f"frontend_{q.name}" if q.name else f"frontend_{q.id}"
                expected_mcqa_question_ids.add(question_id)
        
        # Add sanity_frontend if assigned to pre-test and it exists in database
        if assignment.sanity_frontend_phase and assignment.sanity_frontend_phase == "pre-test":
            sanity_frontend_q = db.query(MCQAData).filter(
                MCQAData.type == "frontend",
                MCQAData.name == "sanity_frontend"
            ).first()
            if sanity_frontend_q:
                total_expected_mcqa += 1
                expected_mcqa_question_ids.add("frontend_sanity_frontend")
        
        # UX questions from assignment (now stored as names)
        # Only count questions that actually exist in the database (matching questions endpoint logic)
        if assignment.ux_pre_test:
            ux_names = [name for name in assignment.ux_pre_test if name]
            # Load questions that actually exist
            ux_questions_raw = db.query(MCQAData).filter(
                MCQAData.type == "ux",
                MCQAData.name.in_(ux_names)
            ).all() if ux_names else []
            ux_questions_map = {q.name: q for q in ux_questions_raw}
            ux_questions = [ux_questions_map[name] for name in ux_names if name in ux_questions_map]
            ux_count = len(ux_questions)
            total_expected_mcqa += ux_count
            for q in ux_questions:
                question_id = f"ux_{q.name}" if q.name else f"ux_{q.id}"
                expected_mcqa_question_ids.add(question_id)
        
        # Add sanity_ux if assigned to pre-test and it exists in database
        if assignment.sanity_ux_phase and assignment.sanity_ux_phase == "pre-test":
            sanity_ux_q = db.query(MCQAData).filter(
                MCQAData.type == "ux",
                MCQAData.name == "sanity_ux"
            ).first()
            if sanity_ux_q:
                total_expected_mcqa += 1
                expected_mcqa_question_ids.add("ux_sanity_ux")
        
        # Code normal questions from assignment
        # Only count tasks that actually exist in the database (matching questions endpoint logic)
        if assignment.code_pre_test:
            code_normal_names = [name for name in assignment.code_pre_test if name]
            # Load tasks that actually exist
            code_questions = db.query(CodeData).filter(
                CodeData.task_name.in_(code_normal_names)
            ).all()
            code_data_map = {q.task_name: q for q in code_questions}
            code_normal_count = len([name for name in code_normal_names if name in code_data_map])
            total_expected_code += code_normal_count
            for task_name in code_normal_names:
                if task_name in code_data_map:
                    expected_code_question_ids.add(f"code_normal_{task_name}")
        
        # Code debug questions from assignment
        # Only count tasks that actually exist in the database (matching questions endpoint logic)
        if assignment.debug_pre_test:
            code_debug_names = [name for name in assignment.debug_pre_test if name]
            # Load tasks that actually exist (reuse query if code_normal_names overlap)
            if assignment.code_pre_test:
                all_code_names = list(set(code_debug_names + [name for name in assignment.code_pre_test if name]))
            else:
                all_code_names = code_debug_names
            code_questions = db.query(CodeData).filter(
                CodeData.task_name.in_(all_code_names)
            ).all()
            code_data_map = {q.task_name: q for q in code_questions}
            code_debug_count = len([name for name in code_debug_names if name in code_data_map])
            total_expected_code += code_debug_count
            for task_name in code_debug_names:
                if task_name in code_data_map:
                    expected_code_question_ids.add(f"code_debug_{task_name}")
    else:  # post-test (no experience section)
        # Frontend questions from assignment (now stored as names)
        # Only count questions that actually exist in the database (matching questions endpoint logic)
        if assignment.frontend_post_test:
            frontend_names = [name for name in assignment.frontend_post_test if name]
            # Load questions that actually exist
            frontend_questions_raw = db.query(MCQAData).filter(
                MCQAData.type == "frontend",
                MCQAData.name.in_(frontend_names)
            ).all() if frontend_names else []
            frontend_questions_map = {q.name: q for q in frontend_questions_raw}
            frontend_questions = [frontend_questions_map[name] for name in frontend_names if name in frontend_questions_map]
            frontend_count = len(frontend_questions)
            total_expected_mcqa += frontend_count
            for q in frontend_questions:
                question_id = f"frontend_{q.name}" if q.name else f"frontend_{q.id}"
                expected_mcqa_question_ids.add(question_id)
        
        # Add sanity_frontend if assigned to post-test and it exists in database
        if assignment.sanity_frontend_phase and assignment.sanity_frontend_phase == "post-test":
            sanity_frontend_q = db.query(MCQAData).filter(
                MCQAData.type == "frontend",
                MCQAData.name == "sanity_frontend"
            ).first()
            if sanity_frontend_q:
                total_expected_mcqa += 1
                expected_mcqa_question_ids.add("frontend_sanity_frontend")
        
        # UX questions from assignment (now stored as names)
        # Only count questions that actually exist in the database (matching questions endpoint logic)
        if assignment.ux_post_test:
            ux_names = [name for name in assignment.ux_post_test if name]
            # Load questions that actually exist
            ux_questions_raw = db.query(MCQAData).filter(
                MCQAData.type == "ux",
                MCQAData.name.in_(ux_names)
            ).all() if ux_names else []
            ux_questions_map = {q.name: q for q in ux_questions_raw}
            ux_questions = [ux_questions_map[name] for name in ux_names if name in ux_questions_map]
            ux_count = len(ux_questions)
            total_expected_mcqa += ux_count
            for q in ux_questions:
                question_id = f"ux_{q.name}" if q.name else f"ux_{q.id}"
                expected_mcqa_question_ids.add(question_id)
        
        # Add sanity_ux if assigned to post-test and it exists in database
        if assignment.sanity_ux_phase and assignment.sanity_ux_phase == "post-test":
            sanity_ux_q = db.query(MCQAData).filter(
                MCQAData.type == "ux",
                MCQAData.name == "sanity_ux"
            ).first()
            if sanity_ux_q:
                total_expected_mcqa += 1
                expected_mcqa_question_ids.add("ux_sanity_ux")
        
        # Code normal questions from assignment
        # Only count tasks that actually exist in the database (matching questions endpoint logic)
        if assignment.code_post_test:
            code_normal_names = [name for name in assignment.code_post_test if name]
            # Load tasks that actually exist
            code_questions = db.query(CodeData).filter(
                CodeData.task_name.in_(code_normal_names)
            ).all()
            code_data_map = {q.task_name: q for q in code_questions}
            code_normal_count = len([name for name in code_normal_names if name in code_data_map])
            total_expected_code += code_normal_count
            for task_name in code_normal_names:
                if task_name in code_data_map:
                    expected_code_question_ids.add(f"code_normal_{task_name}")
        
        # Code debug questions from assignment
        # Only count tasks that actually exist in the database (matching questions endpoint logic)
        if assignment.debug_post_test:
            code_debug_names = [name for name in assignment.debug_post_test if name]
            # Load tasks that actually exist (reuse query if code_normal_names overlap)
            if assignment.code_post_test:
                all_code_names = list(set(code_debug_names + [name for name in assignment.code_post_test if name]))
            else:
                all_code_names = code_debug_names
            code_questions = db.query(CodeData).filter(
                CodeData.task_name.in_(all_code_names)
            ).all()
            code_data_map = {q.task_name: q for q in code_questions}
            code_debug_count = len([name for name in code_debug_names if name in code_data_map])
            total_expected_code += code_debug_count
            for task_name in code_debug_names:
                if task_name in code_data_map:
                    expected_code_question_ids.add(f"code_debug_{task_name}")
    
    # Get all MCQA responses for this user and phase
    mcqa_responses = db.query(UserMCQASkillResponse).filter(
        UserMCQASkillResponse.user_id == user_id,
        UserMCQASkillResponse.phase == phase
    ).all()
    
    # Get all code responses for this user and phase
    # Only count as answered if state is 'passed' or 'reported' (not 'started' or 'failed')
    code_responses = db.query(UserCodeSkillResponse).filter(
        UserCodeSkillResponse.user_id == user_id,
        UserCodeSkillResponse.phase == phase,
        UserCodeSkillResponse.state.in_(['passed', 'reported'])
    ).all()
    
    # Track answered questions (use question_id)
    answered_mcqa_ids = {resp.question_id for resp in mcqa_responses}
    answered_code_ids = {resp.question_id for resp in code_responses}
    
    # Check if all expected questions are answered
    all_mcqa_answered = expected_mcqa_question_ids.issubset(answered_mcqa_ids)
    all_code_answered = expected_code_question_ids.issubset(answered_code_ids)
    
    # Use the directly calculated totals (simpler than counting sets)
    total_expected = total_expected_mcqa + total_expected_code
    total_answered = len(answered_mcqa_ids) + len(answered_code_ids)
    has_responses = len(mcqa_responses) > 0 or len(code_responses) > 0
    completed = all_mcqa_answered and all_code_answered
    
    # Build ordered list using the same helper function as get_skill_check_questions
    # Then find first unanswered question in that list
    current_question_index = 0
    
    if not completed:
        # Use the helper function to build ordered list (guaranteed to match questions endpoint)
        ordered_question_ids = _build_ordered_question_ids(phase, assignment, db)
        
        # Find first unanswered question in the ordered list
        all_answered_ids = answered_mcqa_ids | answered_code_ids
        for idx, q_id in enumerate(ordered_question_ids):
            if q_id not in all_answered_ids:
                current_question_index = idx
                break
        else:
            # All questions answered (shouldn't happen if completed is False, but handle it)
            current_question_index = len(ordered_question_ids)
    
    return {
        "completed": completed,
        "has_responses": has_responses,
        "total_expected": total_expected,
        "total_answered": total_answered,
        "current_question_index": current_question_index
    }


@app.get("/api/skill-check/completion-status", tags=["Tasks"])
async def get_skill_check_completion_status(
    user_id: int = Query(..., description="User ID to check completion status for"),
    phase: str = Query(..., description="Phase to check: 'pre-test' or 'post-test'"),
    db: Session = Depends(get_db),
):
    """
    Check if a user has completed all questions for a given phase of the skill check.
    
    Returns:
    {
        "completed": boolean,
        "has_responses": boolean,  # True if user has any responses for this phase
        "total_expected": int,
        "total_answered": int
    }
    """
    try:
        if phase not in ["pre-test", "post-test"]:
            return JSONResponse(
                status_code=400,
                content={"error": "Phase must be 'pre-test' or 'post-test'"}
            )
        
        # Get user's assignment to know which questions they should have answered
        assignment = (
            db.query(SkillCheckAssignment)
            .filter(SkillCheckAssignment.user_id == user_id)
            .first()
        )
        
        return _get_completion_status_for_phase(user_id, phase, assignment, db)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to check completion status: {str(e)}"}
        )


@app.get("/api/skill-check/completion-status-both", tags=["Tasks"])
async def get_skill_check_completion_status_both(
    user_id: int = Query(..., description="User ID to check completion status for"),
    db: Session = Depends(get_db),
):
    """
    Check completion status for both pre-test and post-test phases in a single call.
    This is more efficient than making two separate calls.
    
    Returns:
    {
        "pre_test": {
            "completed": boolean,
            "has_responses": boolean,
            "total_expected": int,
            "total_answered": int,
            "current_question_index": int
        },
        "post_test": {
            "completed": boolean,
            "has_responses": boolean,
            "total_expected": int,
            "total_answered": int,
            "current_question_index": int
        }
    }
    """
    try:
        # Get user's assignment once (shared between both phases)
        assignment = (
            db.query(SkillCheckAssignment)
            .filter(SkillCheckAssignment.user_id == user_id)
            .first()
        )
        
        # Get completion status for both phases
        pre_test_status = _get_completion_status_for_phase(user_id, "pre-test", assignment, db)
        post_test_status = _get_completion_status_for_phase(user_id, "post-test", assignment, db)
        
        return {
            "pre_test": pre_test_status,
            "post_test": post_test_status
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to check completion status: {str(e)}"}
        )


@app.post("/api/skill-check/log-navigation-event", tags=["Tasks"])
async def log_navigation_event(request: dict, db: Session = Depends(get_db)):
    """
    Log a navigation event (tab switching, window focus changes) during skill checks.
    
    Request body:
    {
        "user_id": int,
        "question_id": str | null,  # ID of the question (optional)
        "test_type": str,  # 'pre-test', 'post-test', or 'retake_{uuid}'
        "time_away_ms": int | null,  # Time away in milliseconds (optional, only when user returns)
    }
    """
    try:
        user_id = request.get("user_id")
        question_id = request.get("question_id")
        test_type = request.get("test_type")
        time_away_ms = request.get("time_away_ms")
        
        if not user_id or not test_type:
            return JSONResponse(
                status_code=400,
                content={"error": "user_id and test_type are required"}
            )
        
        # Accept 'pre-test', 'post-test', or 'retake_{uuid}' format
        if test_type not in ['pre-test', 'post-test'] and not test_type.startswith('retake_'):
            return JSONResponse(
                status_code=400,
                content={"error": "test_type must be 'pre-test', 'post-test', or 'retake_{uuid}'"}
            )
        
        # Create navigation event
        event_create = NavigationEventCreate(
            user_id=user_id,
            question_id=question_id,
            test_type=test_type,
            time_away_ms=time_away_ms,
        )
        
        event = NavigationEventCRUD.create(db, event_create)
        
        return {
            "success": True,
            "event_id": event.id
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to log navigation event: {str(e)}"}
        )


@app.post("/api/task-events", tags=["Tasks"])
async def log_task_event(payload: TaskEventCreate, db: Session = Depends(get_db)):
    """Log website requirement task lifecycle events."""
    try:
        allowed_event_names = {
            "loaded_in",
            "timer_started",
            "left_page",
            "started_edits",
            "started_ai_query",
            "questions_generation_started",
            "questions_generation_completed",
            "continued_to_questions",
            "timer_paused",
            "timer_resumed",
            "submitted",
        }

        if payload.event_name not in allowed_event_names:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "event_name must be one of loaded_in, timer_started, left_page, started_edits, started_ai_query, questions_generation_started, questions_generation_completed, continued_to_questions, timer_paused, timer_resumed, submitted"
                },
            )

        user = db.query(User).filter(User.id == payload.user_id).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})

        project = db.query(Project).filter(Project.id == payload.project_id).first()
        if not project:
            return JSONResponse(status_code=404, content={"error": "Project not found"})

        event = TaskEventCRUD.create(db, payload)
        return {"success": True, "event_id": event.id}

    except Exception as e:
        print(f"Error logging task event: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to log task event"})


@app.get("/api/task-events/timer-state", tags=["Tasks"])
async def get_task_timer_state(
    user_id: int = Query(..., description="User ID"),
    project_id: int = Query(..., description="Project ID"),
    duration_seconds: int = Query(30 * 60, ge=1, description="Timer duration in seconds"),
    db: Session = Depends(get_db),
):
    """Return remaining timer seconds based on first timer_started and pause/resume events."""
    try:
        relevant_events = (
            db.query(TaskEvent)
            .filter(
                TaskEvent.user_id == user_id,
                TaskEvent.project_id == project_id,
                TaskEvent.event_name.in_(["timer_started", "timer_paused", "timer_resumed"]),
            )
            .order_by(TaskEvent.created_at.asc())
            .all()
        )

        first_timer_started_event = next((event for event in relevant_events if event.event_name == "timer_started"), None)

        if not first_timer_started_event:
            return {
                "has_started": False,
                "remaining_seconds": duration_seconds,
                "duration_seconds": duration_seconds,
                "started_at": None,
                "is_paused": False,
                "paused_seconds": 0,
            }

        started_at = first_timer_started_event.created_at
        now = datetime.now(started_at.tzinfo) if started_at.tzinfo else datetime.utcnow()
        total_elapsed_seconds = max(0, (now - started_at).total_seconds())

        paused_total_seconds = 0.0
        pause_started_at = None

        for event in relevant_events:
            if event.created_at < started_at:
                continue

            if event.event_name == "timer_paused":
                if pause_started_at is None:
                    pause_started_at = event.created_at
            elif event.event_name == "timer_resumed":
                if pause_started_at is not None:
                    paused_total_seconds += max(0.0, (event.created_at - pause_started_at).total_seconds())
                    pause_started_at = None

        is_paused = pause_started_at is not None
        if is_paused and pause_started_at is not None:
            paused_total_seconds += max(0.0, (now - pause_started_at).total_seconds())

        elapsed_active_seconds = max(0, int(total_elapsed_seconds - paused_total_seconds))
        remaining_seconds = max(0, duration_seconds - elapsed_active_seconds)

        return {
            "has_started": True,
            "remaining_seconds": remaining_seconds,
            "duration_seconds": duration_seconds,
            "started_at": started_at.isoformat(),
            "is_paused": is_paused,
            "paused_seconds": int(paused_total_seconds),
        }
    except Exception as e:
        print(f"Error fetching task timer state: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch task timer state"})


@app.get("/api/task-files-db", tags=["Tasks"])
async def get_task_files_from_db(taskId: str, userId: Optional[int] = None, db: Session = Depends(get_db)):
    try:
        normalized_task_id = _slugify(taskId)
        # Find by slug of name
        _sync_project_dates_from_dummy(db)
        # Always resolve project from task_id (unified approach)
        project = _resolve_project_from_task_id(db, taskId)
        fallback_task = None
        if not project:
            # Fallback to tasks.json when DB row is missing (prevents empty editor)
            fallback_task = _load_task_definition_from_json(normalized_task_id)
            if not fallback_task:
                return JSONResponse(status_code=404, content={"error": "Task not found"})

        # Helper to resolve a repo-relative path (e.g., data/code_files/...) to file content
        def resolve_content(value: str) -> str:
            try:
                if isinstance(value, str) and value.startswith("data/"):
                    backend_dir = os.path.dirname(__file__)
                    repo_root = os.path.abspath(os.path.join(backend_dir, ".."))
                    file_path = os.path.join(repo_root, value)
                    if os.path.exists(file_path):
                        with open(file_path, "r", encoding="utf-8") as f:
                            return f.read()
                    return f"// File not found: {value}"
                return value or ""
            except Exception as e:
                return f"// Error reading file: {str(e)}"

        # Helper to determine language key from file name
        def get_language_key_from_filename(filename: str) -> Optional[str]:
            lower = filename.lower()
            if lower.endswith('.html'):
                return 'html'
            elif lower.endswith('.css'):
                return 'css'
            elif lower.endswith(('.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx')):
                return 'js'
            return None

        files: List[Dict[str, Any]] = []
        user_code = None
        initial_code_payload: Optional[Dict[str, Any]] = None
        project_files: List[Dict[str, Any]] = []

        if project:
            project_files = _normalize_project_files(project.files)
        # If DB files are missing/empty, try dummy task definition as a fallback
        if (not project_files or len(project_files) == 0) and fallback_task is None:
            fallback_task = _load_task_definition_from_json(normalized_task_id)
        if not project_files and fallback_task:
            project_files = _normalize_project_files(fallback_task.get("files"))
        
        # If userId is provided, try to load saved code first
        if userId and project:
            # Verify we're loading code for the correct project
            user_code = CodeCRUD.get_latest_by_user_and_project(db, user_id=userId, project_id=project.id)

        # Prefer latest saved editor code for this task, unless a kiosk account just reset.
        if user_code and user_code.code and not _is_kiosk_reset_code(user_code):
            initial_code_payload = user_code.code
        # Special-case follow-up task: seed from latest zic-zac-zoe submission.
        elif userId and project and normalized_task_id == "zic-zac-zoe-follow-up":
            previous_project = _resolve_project_from_task_id(db, "zic-zac-zoe")
            if previous_project:
                previous_submission = (
                    db.query(Submission)
                    .filter(
                        Submission.user_id == userId,
                        Submission.project_id == previous_project.id,
                    )
                    .order_by(Submission.created_at.desc())
                    .first()
                )
                if previous_submission and previous_submission.code:
                    initial_code_payload = previous_submission.code

        def _resolve_saved_content(saved_code: Dict[str, Any], file_name: str) -> Optional[str]:
            if file_name in saved_code:
                value = saved_code.get(file_name)
                return "" if value is None else str(value)
            lang_key = get_language_key_from_filename(file_name)
            if lang_key and lang_key in saved_code:
                value = saved_code.get(lang_key)
                return "" if value is None else str(value)
            return None

        # If user has saved code (or follow-up seed code), use it; otherwise use project starter files
        if initial_code_payload:
            saved_code = initial_code_payload
            for fileConfig in project_files:
                try:
                    name = fileConfig.get("name")
                    language = fileConfig.get("language", "plaintext")
                    if not name:
                        continue

                    restored_content = _resolve_saved_content(saved_code, name)
                    if restored_content is not None:
                        content = restored_content
                    else:
                        # Fall back to project starter file
                        content = resolve_content(fileConfig.get("content", ""))
                    
                    files.append({
                        "id": name,
                        "name": name,
                        "type": "file",
                        "content": content,
                        "language": language,
                    })
                except Exception as e:
                    pass
        else:
            # No saved code, use project starter files
            for fileConfig in project_files:
                try:
                    name = fileConfig.get("name")
                    language = fileConfig.get("language", "plaintext")
                    content = fileConfig.get("content", "")
                    files.append({
                        "id": name,
                        "name": name,
                        "type": "file",
                        "content": resolve_content(content),
                        "language": language,
                    })
                except Exception as e:
                    pass
        # Derive metadata from DB when available, otherwise fallback task definition
        project_name = project.name if project else (fallback_task.get("name") if fallback_task else None)
        code_start_date = (
            project.code_start_date.isoformat() if project and project.code_start_date
            else fallback_task.get("code_start_date") if fallback_task else None
        )
        voting_start_date = (
            project.voting_start_date.isoformat() if project and project.voting_start_date
            else fallback_task.get("voting_start_date") if fallback_task else None
        )
        voting_end_date = (
            project.voting_end_date.isoformat() if project and project.voting_end_date
            else fallback_task.get("voting_end_date") if fallback_task else None
        )

        return {
            "files": files,
            "projectId": project.id if project else None,
            "projectName": project_name,
            "codeStartDate": code_start_date,
            "votingStartDate": voting_start_date,
            "votingEndDate": voting_end_date,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/code-logs", tags=["Code"])
async def log_code_snapshot(payload: CodeLogRequest, db: Session = Depends(get_db)):
    try:
        user = db.query(User).filter(User.id == payload.user_id).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})

        # Always resolve project from task_id (unified approach - ignore project_id)
        project = None
        if payload.task_id:
            project = _resolve_project_from_task_id(db, payload.task_id)
        
        # Only fallback to project_id if task_id is not provided (for backwards compatibility)
        if project is None and payload.project_id is not None:
            project = db.query(Project).filter(Project.id == payload.project_id).first()

        if project is None:
            return JSONResponse(status_code=404, content={"error": "Project not found"})

        code_payload = {key: (value or "") for key, value in (payload.code or {}).items()}
        if not code_payload:
            return JSONResponse(status_code=400, content={"error": "Code payload is empty"})

        metadata = dict(payload.metadata or {})
        if payload.event:
            metadata.setdefault("event", payload.event)
        metadata.setdefault("recorded_at", datetime.utcnow().isoformat())
        metadata.setdefault("code_keys", list(code_payload.keys()))
        metadata.setdefault("mode", payload.mode or "regular")
        if payload.task_id:
            metadata.setdefault("task_id", payload.task_id)
        metadata.setdefault("user_id", payload.user_id)

        code_create = CodeCreate(
            user_id=payload.user_id,
            project_id=project.id,
            code=code_payload,
            mode=payload.mode or "regular",
            metadata=metadata,
        )

        code_record = CodeCRUD.create(db, code_create)
        return {"success": True, "codeId": code_record.id}

    except Exception as e:
        print(f"Error logging code snapshot: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to log code snapshot"})


class ModerationCheckRequest(BaseModel):
    title: str
    description: Optional[str] = None
    image: Optional[str] = None  # Base64 data URI or URL

    class Config:
        populate_by_name = True


@app.post("/api/submissions/check-moderation", tags=["Submissions"])
async def check_moderation(payload: ModerationCheckRequest):
    """
    Check if project title, description, and image are appropriate using OpenAI moderation API.
    Only used for public tasks (non-required tasks or past study date).
    """
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return JSONResponse(
                status_code=500,
                content={"error": "OpenAI API key not configured", "is_appropriate": False}
            )

        client = openai.OpenAI(api_key=api_key)
        
        # Prepare inputs for moderation API
        moderation_inputs = []
        
        # Add title as text input
        if payload.title:
            moderation_inputs.append({
                "type": "text",
                "text": payload.title
            })
        
        # Add description as text input
        if payload.description:
            moderation_inputs.append({
                "type": "text",
                "text": payload.description
            })
        
        # Add image - handle both base64 data URIs and URLs
        if payload.image:
            image_url = payload.image
            # If it's a base64 data URI, use it directly (API supports data URIs)
            # Format: data:image/png;base64,<base64_data>
            if not image_url.startswith("http://") and not image_url.startswith("https://"):
                # Assume it's already a data URI or base64
                if not image_url.startswith("data:"):
                    # If it's just base64, wrap it in data URI
                    image_url = f"data:image/png;base64,{image_url}"
            
            moderation_inputs.append({
                "type": "image_url",
                "image_url": {
                    "url": image_url
                }
            })
        
        if not moderation_inputs:
            return JSONResponse(
                status_code=400,
                content={"error": "No content provided for moderation", "is_appropriate": False}
            )
        
        # Call OpenAI moderation API
        try:
            response = client.moderations.create(
                model="omni-moderation-latest",
                input=moderation_inputs,
            )
            
            # Check results - if any input is flagged, the content is inappropriate
            is_appropriate = True
            
            # Process results - the API returns results for each input
            if hasattr(response, 'results') and response.results:
                for result in response.results:
                    # Check if this result is flagged
                    if hasattr(result, 'flagged') and result.flagged:
                        is_appropriate = False
                        break  # No need to check further if any content is flagged
            
            return {
                "is_appropriate": is_appropriate,
                "error": None
            }
            
        except Exception as api_error:
            print(f"Error calling OpenAI moderation API: {api_error}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={
                    "error": f"Moderation API error: {str(api_error)}",
                    "is_appropriate": False
                }
            )
            
    except Exception as e:
        print(f"Error in moderation check: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Failed to check moderation: {str(e)}",
                "is_appropriate": False
            }
        )


@app.post("/api/submissions", tags=["Submissions"])
async def create_submission(payload: SubmissionRequest, db: Session = Depends(get_db)):
    try:
        user = db.query(User).filter(User.id == payload.user_id).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})

        # Always resolve project from task_id (unified approach - ignore project_id)
        project = None
        if payload.task_id:
            project = _resolve_project_from_task_id(db, payload.task_id)
        
        # Only fallback to project_id if task_id is not provided (for backwards compatibility)
        if project is None and payload.project_id is not None:
            project = db.query(Project).filter(Project.id == payload.project_id).first()

        if project is None:
            return JSONResponse(status_code=404, content={"error": "Project not found"})

        code_payload = {}
        for key, value in (payload.code or {}).items():
            try:
                code_payload[str(key)] = "" if value is None else str(value)
            except Exception:
                code_payload[str(key)] = ""

        if not code_payload:
            return JSONResponse(status_code=400, content={"error": "Submission code payload cannot be empty"})

        cleaned_title = (payload.title or "").strip()
        if not cleaned_title:
            cleaned_title = "Untitled"

        linked_evaluation: Optional[SubmissionEvaluation] = None
        if payload.evaluation_id:
            linked_evaluation = SubmissionEvaluationCRUD.get_by_id(db, payload.evaluation_id)
            if not linked_evaluation:
                return JSONResponse(status_code=404, content={"error": "Evaluation not found"})
            if linked_evaluation.user_id != user.id or linked_evaluation.project_id != project.id:
                return JSONResponse(status_code=400, content={"error": "Evaluation does not match user/project"})

        is_forced_timeout_submission = bool(payload.forced_timeout)
        is_disqualified = bool(
            is_forced_timeout_submission and linked_evaluation is not None and not bool(linked_evaluation.is_valid)
        )
        disqualification_reason = "timeout_invalid_evaluation" if is_disqualified else None

        submission_create = SubmissionCreate(
            user_id=user.id,
            project_id=project.id,
            code=code_payload,
            title=cleaned_title,
            description=(payload.description or "").strip() or None,
            image=payload.image,
            is_forced_timeout_submission=is_forced_timeout_submission,
            is_disqualified=is_disqualified,
            disqualification_reason=disqualification_reason,
        )

        submission_record = SubmissionCRUD.create(db, submission_create)
        
        # Link evaluation to submission if evaluation_id is provided
        if payload.evaluation_id:
            SubmissionEvaluationCRUD.link_to_submission(db, payload.evaluation_id, submission_record.id)
        
        # Update comprehension questions with user answers and scores
        if payload.comprehension_answers:
            print(f"Updating comprehension answers for {len(payload.comprehension_answers)} questions")
            for question_name, user_answer in payload.comprehension_answers.items():
                # Find the most recent comprehension question for this user, project, and question_name
                question = db.query(ComprehensionQuestion).filter(
                    ComprehensionQuestion.user_id == payload.user_id,
                    ComprehensionQuestion.project_id == project.id,
                    ComprehensionQuestion.question_name == question_name
                ).order_by(ComprehensionQuestion.created_at.desc()).first()

                # These two required-task submit-modal questions are frontend-defined,
                # so create database rows on-demand the first time we receive them.
                if question is None and question_name in {
                    "required_task_implemented_requirements",
                    "required_task_open_feedback",
                }:
                    if question_name == "required_task_implemented_requirements":
                        requirement_choices = project.requirements if isinstance(project.requirements, list) else []
                        question = ComprehensionQuestion(
                            user_id=payload.user_id,
                            project_id=project.id,
                            question_name=question_name,
                            question="Which requirements were you able to successfully implement?",
                            question_type="multi_select",
                            choices=requirement_choices,
                            answer=None,
                            user_answer=None,
                            score=None,
                        )
                    else:
                        question = ComprehensionQuestion(
                            user_id=payload.user_id,
                            project_id=project.id,
                            question_name=question_name,
                            question="Any other comments on your interaction with the AI assistant while completing this task?",
                            question_type="free_response",
                            choices=None,
                            answer=None,
                            user_answer=None,
                            score=None,
                        )
                    db.add(question)
                    db.flush()
                    print(f"Created missing comprehension question: {question_name}")
                
                if question:
                    print(f"Found question: {question_name}, type: {question.question_type}, answer: {question.answer}, user_answer: {user_answer}")
                    # Parse user_answer - it should be a binary array for multi_select, string for others
                    parsed_user_answer = user_answer
                    if question.question_type == 'multi_select':
                        # user_answer should already be a binary array from frontend
                        if isinstance(user_answer, str):
                            import json
                            try:
                                parsed_user_answer = json.loads(user_answer)
                            except:
                                parsed_user_answer = user_answer
                        elif not isinstance(user_answer, list):
                            parsed_user_answer = []
                    elif question.question_type == 'matrix':
                        import json
                        if isinstance(user_answer, list):
                            parsed_user_answer = json.dumps(user_answer)
                        elif isinstance(user_answer, str) and user_answer.strip():
                            parsed_user_answer = user_answer.strip()
                        else:
                            parsed_user_answer = None
                    else:
                        # For non-multi_select, keep as string
                        parsed_user_answer = str(user_answer) if user_answer else None
                    
                    # Update user_answer (store as JSON for multi_select, string for others)
                    question.user_answer = parsed_user_answer
                    
                    # Score for identify_own (left/right code block): 1.0 if correct, 0.0 if wrong
                    if question_name and question_name.startswith("identify_own"):
                        try:
                            correct_answer = question.answer
                            if isinstance(correct_answer, str):
                                correct_answer = int(correct_answer.strip())
                            elif not isinstance(correct_answer, (int, float)):
                                correct_answer = None
                            user_answer_str = str(parsed_user_answer).strip() if parsed_user_answer is not None else ""
                            user_index = None
                            if user_answer_str == "The Left Code is Mine" or user_answer_str == "1":
                                user_index = 1
                            elif user_answer_str == "The Right Code is Mine" or user_answer_str == "2":
                                user_index = 2
                            else:
                                match = re.match(r"^(\d+)", user_answer_str)
                                if match:
                                    user_index = int(match.group(1))
                            if correct_answer is not None and user_index is not None:
                                question.score = 1.0 if user_index == correct_answer else 0.0
                                print(f"  Question {question_name}: identify_own score = {question.score} (user={user_index}, correct={correct_answer})")
                            else:
                                question.score = None
                        except Exception as e:
                            print(f"  Error calculating identify_own score for {question_name}: {e}")
                            question.score = None

                    # Score for required_task_implemented_requirements: proportion of requirements selected (0–1)
                    elif question_name == "required_task_implemented_requirements":
                        try:
                            user_selected = parsed_user_answer if isinstance(parsed_user_answer, list) else []
                            if len(user_selected) > 0:
                                total = sum(1 for x in user_selected if x == 1 or x == "1")
                                question.score = float(total) / len(user_selected)
                                print(f"  Question {question_name}: score = {question.score} ({total}/{len(user_selected)} selected)")
                            else:
                                question.score = 0.0
                                print(f"  Question {question_name}: score = 0.0 (no choices)")
                        except Exception as e:
                            print(f"  Error calculating required_task_implemented_requirements score: {e}")
                            question.score = None

                    # Calculate score for self_report questions (extract number 1-5 from answer)
                    elif question_name and question_name.startswith('self_report'):
                        try:
                            # user_answer is a string like "1 - Strongly disagree" or "5 - Strongly agree"
                            user_answer_str = str(parsed_user_answer) if parsed_user_answer else ""
                            # Extract the number at the start (1-5)
                            import re
                            match = re.match(r'^(\d+)', user_answer_str.strip())
                            if match:
                                score_value = int(match.group(1))
                                # Ensure it's between 1 and 5
                                if 1 <= score_value <= 5:
                                    question.score = float(score_value)
                                    print(f"  Question {question_name}: self_report score = {question.score}")
                                else:
                                    print(f"  Question {question_name}: invalid score value {score_value}, expected 1-5")
                                    question.score = None
                            else:
                                print(f"  Question {question_name}: could not extract score from answer '{user_answer_str}'")
                                question.score = None
                        except Exception as e:
                            print(f"  Error calculating self_report score for question {question_name}: {e}")
                            import traceback
                            traceback.print_exc()
                            question.score = None
                    
                    # Calculate score for multi_select questions that have an answer field
                    elif question.question_type == 'multi_select':
                        print(f"Processing multi_select question: {question_name}")
                        print(f"  question.answer: {question.answer}, type: {type(question.answer)}")
                        if question.answer is not None:
                            try:
                                # Parse the correct answer array (should be a list like [1, 0, 1, 0])
                                correct_answers = question.answer
                                if isinstance(correct_answers, str):
                                    # Handle PostgreSQL array format {0,1,1,0} or JSON format [0,1,1,0]
                                    if correct_answers.strip().startswith('{') and correct_answers.strip().endswith('}'):
                                        # PostgreSQL array format: {0,1,1,0} -> [0,1,1,0]
                                        array_str = correct_answers.strip()[1:-1]  # Remove curly braces
                                        correct_answers = [int(x.strip()) for x in array_str.split(',') if x.strip()]
                                    else:
                                        # Try JSON format
                                        import json
                                        correct_answers = json.loads(correct_answers)
                                
                                if not isinstance(correct_answers, list):
                                    print(f"  Warning: answer is not a list, got: {type(correct_answers)}")
                                    correct_answers = []
                                
                                # user_answer should already be a binary array [1, 0, 1, 0]
                                user_selected = parsed_user_answer if isinstance(parsed_user_answer, list) else []
                                print(f"  correct_answers: {correct_answers}, user_selected: {user_selected}")
                                
                                # Calculate score: percentage of correct matches
                                # Compare element by element: correct_answers[i] should match user_selected[i]
                                if len(correct_answers) == len(user_selected) and len(correct_answers) > 0:
                                    matches = sum(1 for i in range(len(correct_answers)) if correct_answers[i] == user_selected[i])
                                    question.score = matches / len(correct_answers)
                                    print(f"  Question {question_name}: score = {question.score} ({matches}/{len(correct_answers)} matches)")
                                else:
                                    question.score = 0.0
                                    print(f"  Question {question_name}: length mismatch - correct: {len(correct_answers)}, user: {len(user_selected)}")
                            except Exception as e:
                                print(f"  Error calculating score for question {question_name}: {e}")
                                import traceback
                                traceback.print_exc()
                                question.score = None
                        else:
                            print(f"  Question {question_name} has no answer field, skipping score calculation")
                    
                    # Mechanism and change (snippet_mechanism, snippet_change_impact): gold may be a 1-based index
                    # (legacy) or the correct choice text (current); user answer matches either scheme.
                    is_mechanism_or_change = (
                        question_name
                        and ("snippet_mechanism" in question_name or "snippet_change_impact" in question_name)
                    )
                    if is_mechanism_or_change and question.answer is not None:
                        try:
                            user_answer_str = str(parsed_user_answer).strip() if parsed_user_answer else ""
                            gold_raw = question.answer
                            gold_str = str(gold_raw).strip() if gold_raw is not None else ""

                            correct_idx: Optional[int] = None
                            if type(gold_raw) is int or type(gold_raw) is float:
                                correct_idx = int(gold_raw)
                            elif gold_str.isdigit():
                                correct_idx = int(gold_str)

                            if correct_idx is not None:
                                match = re.match(r"^(\d+)", user_answer_str)
                                user_answer_idx = int(match.group(1)) if match else None
                                if user_answer_idx is not None:
                                    question.user_answer = user_answer_idx
                                    question.score = 1.0 if user_answer_idx == correct_idx else 0.0
                                else:
                                    question.score = None
                            elif user_answer_str:
                                question.score = 1.0 if user_answer_str == gold_str else 0.0
                            else:
                                question.score = None
                        except Exception:
                            question.score = None
                    
                    # Calculate score for MCQA questions that have an answer field (e.g., sanity_check).
                    # identify_own is scored above; skip here to avoid overwriting.
                    # mechanism/change snippet questions are handled above.
                    elif (
                        question.question_type in ('mcqa', 'code_compare')
                        and question.answer is not None
                        and not (question_name and question_name.startswith("identify_own"))
                        and not is_mechanism_or_change
                    ):
                        print(f"Processing MCQA question: {question_name}")
                        print(f"  question.answer: {question.answer}, type: {type(question.answer)}, user_answer: {parsed_user_answer}")
                        try:
                            # Parse the correct answer (should be an integer index, 1-based)
                            correct_answer = question.answer
                            if isinstance(correct_answer, str):
                                correct_answer = int(correct_answer.strip())
                            elif not isinstance(correct_answer, (int, float)):
                                print(f"  Warning: answer is not a number, got: {type(correct_answer)}")
                                correct_answer = None
                            
                            if correct_answer is not None:
                                # Parse user_answer - it might be a string like "1 - Strongly disagree" or just "1"
                                user_answer_str = str(parsed_user_answer) if parsed_user_answer else ""
                                # Extract the number at the start (1-5 or whatever the index is)
                                import re
                                match = re.match(r'^(\d+)', user_answer_str.strip())
                                if match:
                                    user_answer_idx = int(match.group(1))
                                    # Compare the user's answer index with the correct answer index
                                    if user_answer_idx == correct_answer:
                                        question.score = 1.0
                                        print(f"  Question {question_name}: score = 1.0 (correct answer)")
                                    else:
                                        question.score = 0.0
                                        print(f"  Question {question_name}: score = 0.0 (user selected {user_answer_idx}, correct was {correct_answer})")
                                else:
                                    print(f"  Question {question_name}: could not extract index from answer '{user_answer_str}'")
                                    question.score = None
                            else:
                                print(f"  Question {question_name}: invalid answer format")
                                question.score = None
                        except Exception as e:
                            print(f"  Error calculating score for MCQA question {question_name}: {e}")
                            import traceback
                            traceback.print_exc()
                            question.score = None
                    
                    db.flush()
                    print(f"  Updated question {question_name}: user_answer={question.user_answer}, score={question.score}")
            
            db.commit()

        if _is_multi_submission_user(user):
            try:
                _reset_kiosk_in_progress_workspace(db, user.id, project.id)
            except Exception as reset_error:
                print(f"Error resetting kiosk workspace after submission: {reset_error}")
        
        return {
            "success": True,
            "submissionId": submission_record.id,
            "isDisqualified": is_disqualified,
            "disqualificationReason": disqualification_reason,
        }
    except Exception as e:
        print(f"Error creating submission: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to create submission"})


@app.get("/api/submissions", tags=["Submissions"])
async def list_submissions(
    project_id: Optional[int] = Query(default=None, alias="projectId"),
    task_id: Optional[str] = Query(default=None, alias="taskId"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    voter_id: Optional[int] = Query(default=None, alias="voterId"),
    filter_unseen: Optional[str] = Query(default=None, alias="filterUnseen"),
    filter_saved: Optional[str] = Query(default=None, alias="filterSaved"),
    filter_not_reported: Optional[str] = Query(default=None, alias="filterNotReported"),
    include_disqualified: Optional[str] = Query(default=None, alias="includeDisqualified"),
    db: Session = Depends(get_db),
):
    try:
        query = db.query(Submission)

        # Always resolve project from task_id if provided (unified approach)
        project = None
        if task_id:
            project = _resolve_project_from_task_id(db, task_id)
        elif project_id is not None:
            project = db.query(Project).filter(Project.id == project_id).first()
        
        if project:
            query = query.filter(Submission.project_id == project.id)
        elif project_id is not None:
            query = query.filter(Submission.project_id == project_id)

        include_disqualified_bool = include_disqualified is not None and str(include_disqualified).lower() == "true"
        if not include_disqualified_bool:
            query = query.filter(Submission.is_disqualified == False)

        # Convert string query params to booleans - handle None and empty strings
        filter_unseen_bool = filter_unseen is not None and str(filter_unseen).lower() == "true"
        filter_saved_bool = filter_saved is not None and str(filter_saved).lower() == "true"
        filter_not_reported_bool = filter_not_reported is not None and str(filter_not_reported).lower() == "true"
        
        # Apply filters based on voter feedback if voter_id is provided
        if voter_id is not None and (filter_unseen_bool or filter_saved_bool or filter_not_reported_bool):
            from sqlalchemy import func
            from sqlalchemy.orm import aliased
            
            # Get all feedback for this voter, ordered by most recent first
            # Then we'll join to get the most recent feedback per submission
            feedback_alias = aliased(SubmissionFeedback)
            
            # Subquery: Get the most recent feedback timestamp for each submission
            most_recent_time_subq = (
                db.query(
                    SubmissionFeedback.submission_id,
                    func.max(SubmissionFeedback.created_at).label("max_created_at")
                )
                .filter(SubmissionFeedback.voter_id == voter_id)
                .group_by(SubmissionFeedback.submission_id)
                .subquery()
            )
            
            # Subquery: Get the actual most recent feedback record for each submission
            most_recent_feedback_subq = (
                db.query(
                    feedback_alias.submission_id,
                    feedback_alias.is_saved,
                    feedback_alias.is_reported
                )
                .join(
                    most_recent_time_subq,
                    (feedback_alias.submission_id == most_recent_time_subq.c.submission_id) &
                    (feedback_alias.created_at == most_recent_time_subq.c.max_created_at) &
                    (feedback_alias.voter_id == voter_id)
                )
                .subquery()
            )
            
            # Build OR conditions for filters (submission must match at least one checked filter)
            filter_conditions = []
            
            # LEFT OUTER JOIN to get feedback data (NULL if no feedback exists)
            query = query.outerjoin(
                most_recent_feedback_subq,
                Submission.id == most_recent_feedback_subq.c.submission_id
            )
            
            if filter_unseen_bool:
                # Submissions where user has no feedback at all (unseen)
                # The LEFT JOIN will have NULL submission_id for unseen submissions
                condition = most_recent_feedback_subq.c.submission_id.is_(None)
                filter_conditions.append(condition)
            
            if filter_saved_bool:
                # Submissions where user has saved (is_saved = True in most recent feedback)
                condition = most_recent_feedback_subq.c.is_saved == True
                filter_conditions.append(condition)
            
            if filter_not_reported_bool:
                # Submissions where user hasn't reported
                # This means: no feedback exists (unseen) OR feedback exists with is_reported = False
                # Since we're using LEFT JOIN, if submission_id IS NULL, then is_reported is also NULL
                # So we want: (submission_id IS NULL) OR (is_reported = False)
                condition = (
                    (most_recent_feedback_subq.c.submission_id.is_(None)) |
                    (most_recent_feedback_subq.c.is_reported == False)
                )
                filter_conditions.append(condition)
            
            # Apply AND logic: submission must match ALL filter conditions
            if filter_conditions:
                query = query.filter(and_(*filter_conditions))

        # Get all submissions for the project, ordered by most recent first
        all_submissions = (
            query.order_by(Submission.created_at.desc())
            .all()
        )
        
        # Group by user_id and keep only the most recent submission for each user,
        # except multi-submission kiosk accounts, which keep every submission.
        multi_submit_user_ids = _multi_submission_user_ids(db)
        most_recent_by_user: Dict[int, Submission] = {}
        multi_submit_submissions: List[Submission] = []
        for submission in all_submissions:
            user_id = submission.user_id
            if user_id in multi_submit_user_ids:
                multi_submit_submissions.append(submission)
                continue
            if user_id not in most_recent_by_user:
                most_recent_by_user[user_id] = submission
            else:
                # Keep the most recent one (since we're already ordered by created_at desc)
                existing = most_recent_by_user[user_id]
                if submission.created_at and existing.created_at:
                    if submission.created_at > existing.created_at:
                        most_recent_by_user[user_id] = submission
        
        # Convert to list and apply pagination
        submissions = list(most_recent_by_user.values()) + multi_submit_submissions
        # Sort by created_at descending (most recent first)
        # Use a very old date as fallback for None values
        min_date = datetime(1970, 1, 1)
        submissions.sort(key=lambda s: s.created_at if s.created_at else min_date, reverse=True)
        # Apply pagination
        submissions = submissions[skip:skip + limit]

        submission_ids = [submission.id for submission in submissions]
        feedback_summaries: Dict[int, Dict[str, Any]] = {}
        if submission_ids:
            # Order by created_at desc to make it easier to find most recent per voter
            feedback_entries = (
                db.query(SubmissionFeedback)
                .filter(SubmissionFeedback.submission_id.in_(submission_ids))
                .order_by(SubmissionFeedback.created_at.desc())
                .all()
            )
            feedback_by_submission: Dict[int, List[SubmissionFeedback]] = defaultdict(list)
            for entry in feedback_entries:
                feedback_by_submission[entry.submission_id].append(entry)

            for submission_id, entries in feedback_by_submission.items():
                feedback_summaries[submission_id] = build_rating_summary(entries)

        response: List[Dict[str, Any]] = []
        for submission in submissions:
            rating_summary = feedback_summaries.get(
                submission.id, {"average": None, "count": 0, "perMetric": {}}
            )
            response.append(
                {
                    "id": submission.id,
                    "title": submission.title,
                    "description": submission.description,
                    "image": submission.image,
                    "projectId": submission.project_id,
                    "userId": submission.user_id,
                    "createdAt": submission.created_at.isoformat() if submission.created_at else None,
                    "updatedAt": submission.updated_at.isoformat() if submission.updated_at else None,
                    "isDisqualified": bool(submission.is_disqualified),
                    "disqualificationReason": submission.disqualification_reason,
                    "ratingSummary": rating_summary,
                }
            )

        return {"items": response, "count": len(response), "hasMore": len(response) == limit}
    except Exception as e:
        print(f"Error listing submissions: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to list submissions"})


@app.get("/api/submissions/gallery-count", tags=["Submissions"])
async def submission_gallery_count(
    task_id: str = Query(..., alias="taskId", description="Task slug / project name key (same as submissions list)"),
    db: Session = Depends(get_db),
):
    """Distinct users with a non-disqualified submission for this task — matches gallery card count."""
    try:
        project = _resolve_project_from_task_id(db, task_id)
        if not project:
            return {"count": 0}
        multi_submit_user_ids = _multi_submission_user_ids(db)
        n = (
            db.query(func.count(distinct(Submission.user_id)))
            .filter(Submission.project_id == project.id)
            .filter(Submission.is_disqualified == False)
        )
        if multi_submit_user_ids:
            n = n.filter(~Submission.user_id.in_(multi_submit_user_ids))
        distinct_user_count = int(n.scalar() or 0)
        extra_count = 0
        if multi_submit_user_ids:
            extra_count = int(
                db.query(func.count(Submission.id))
                .filter(Submission.project_id == project.id)
                .filter(Submission.is_disqualified == False)
                .filter(Submission.user_id.in_(multi_submit_user_ids))
                .scalar()
                or 0
            )
        return {"count": distinct_user_count + extra_count}
    except Exception as e:
        print(f"Error counting gallery submissions: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to count submissions"})


@app.get("/api/submissions/gallery-counts", tags=["Submissions"])
async def submission_gallery_counts(
    task_ids: List[str] = Query(
        ..., alias="taskIds", description="Task slugs / project name keys. Repeat this query param for each task."
    ),
    db: Session = Depends(get_db),
):
    """Distinct users with a non-disqualified submission for each task id."""
    try:
        normalized_task_ids = [task_id for task_id in task_ids if isinstance(task_id, str) and task_id.strip()]
        if not normalized_task_ids:
            return {"byTaskId": {}}

        project_ids_by_task_id: Dict[str, int] = {}
        for task_id in normalized_task_ids:
            project = _resolve_project_from_task_id(db, task_id)
            if project:
                project_ids_by_task_id[task_id] = project.id

        if not project_ids_by_task_id:
            return {"byTaskId": {task_id: 0 for task_id in normalized_task_ids}}

        project_ids = list(project_ids_by_task_id.values())
        multi_submit_user_ids = _multi_submission_user_ids(db)

        distinct_query = (
            db.query(Submission.project_id, func.count(distinct(Submission.user_id)))
            .filter(Submission.project_id.in_(project_ids))
            .filter(Submission.is_disqualified == False)
        )
        if multi_submit_user_ids:
            distinct_query = distinct_query.filter(~Submission.user_id.in_(multi_submit_user_ids))
        rows = distinct_query.group_by(Submission.project_id).all()
        counts_by_project_id = {int(project_id): int(count or 0) for project_id, count in rows}

        if multi_submit_user_ids:
            extra_rows = (
                db.query(Submission.project_id, func.count(Submission.id))
                .filter(Submission.project_id.in_(project_ids))
                .filter(Submission.is_disqualified == False)
                .filter(Submission.user_id.in_(multi_submit_user_ids))
                .group_by(Submission.project_id)
                .all()
            )
            for project_id, count in extra_rows:
                counts_by_project_id[int(project_id)] = counts_by_project_id.get(int(project_id), 0) + int(count or 0)

        response = {}
        for task_id in normalized_task_ids:
            project_id = project_ids_by_task_id.get(task_id)
            response[task_id] = counts_by_project_id.get(project_id, 0) if project_id is not None else 0
        return {"byTaskId": response}
    except Exception as e:
        print(f"Error counting gallery submissions in batch: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to count submissions"})


@app.get("/api/submissions/{submission_id}", tags=["Submissions"])
async def get_submission_detail(submission_id: int, db: Session = Depends(get_db)):
    try:
        submission = db.query(Submission).filter(Submission.id == submission_id).first()
        if not submission:
            return JSONResponse(status_code=404, content={"error": "Submission not found"})

        task_description = ""
        task_name = ""
        if submission.project_id:
            project = db.query(Project).filter(Project.id == submission.project_id).first()
            if project:
                dummy_meta = _load_dummy_task_metadata()
                task_meta = dummy_meta.get(_slugify(project.name), {})
                task_description = _resolve_task_description(
                    project.name,
                    fallback_description=project.description or "",
                    task_meta=task_meta,
                )
                task_name = project.title or project.name or ""
                if project.label and project.label.lower() == "replication" and task_name:
                    prefix = f"Create your own version of {task_name}: "
                    description_stripped = task_description.strip()
                    if re.match(r"^\s*<p", description_stripped, re.IGNORECASE):
                        task_description = re.sub(
                            r"^(\s*<p[^>]*>)",
                            rf"\1{prefix}",
                            description_stripped,
                            flags=re.IGNORECASE,
                        )
                    else:
                        task_description = f"<p><strong>{prefix}</strong></p>{description_stripped}"

        return {
            "id": submission.id,
            "title": submission.title,
            "description": submission.description,
            "image": submission.image,
            "projectId": submission.project_id,
            "userId": submission.user_id,
            "createdAt": submission.created_at.isoformat() if submission.created_at else None,
            "updatedAt": submission.updated_at.isoformat() if submission.updated_at else None,
            "isDisqualified": bool(submission.is_disqualified),
            "disqualificationReason": submission.disqualification_reason,
            "code": submission.code or {},
            "taskDescription": task_description,
            "taskName": task_name,
            "ratingSummary": build_rating_summary(
                db.query(SubmissionFeedback)
                .filter(SubmissionFeedback.submission_id == submission_id)
                .order_by(SubmissionFeedback.created_at.desc())
                .all()
            ),
        }
    except Exception as e:
        print(f"Error fetching submission detail: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch submission"})


@app.post("/api/submissions/{submission_id}/feedback", tags=["Submissions"])
async def submit_submission_feedback(submission_id: int, payload: SubmissionFeedbackRequest, db: Session = Depends(get_db)):
    try:
        submission = db.query(Submission).filter(Submission.id == submission_id).first()
        if not submission:
            return JSONResponse(status_code=404, content={"error": "Submission not found"})

        normalized_scores: Dict[str, int] = {}
        for key, value in (payload.scores or {}).items():
            try:
                numeric_value = int(value)
            except (TypeError, ValueError):
                numeric_value = 0
            numeric_value = max(1, min(5, numeric_value))
            normalized_scores[str(key)] = numeric_value

        normalized_comment = (payload.comment or "").strip() or None
        normalized_report_type = (payload.report_type or "").strip() or None if payload.report_type else None
        normalized_report_rationale = (payload.report_rationale or "").strip() or None if payload.report_rationale else None

        # Always create a new record instead of updating
        feedback_create = SubmissionFeedbackCreate(
            submission_id=submission.id,
            project_id=submission.project_id,
            voter_id=payload.voter_id,
            scores=normalized_scores,
            comment=normalized_comment,
            is_saved=payload.is_saved if payload.is_saved is not None else False,
            is_reported=payload.is_reported if payload.is_reported is not None else False,
            report_type=normalized_report_type,
            report_rationale=normalized_report_rationale,
        )
        feedback_record = SubmissionFeedbackCRUD.create(db, feedback_create)
        return SubmissionFeedbackModel.from_orm(feedback_record)
    except Exception as e:
        print(f"Error submitting submission feedback: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to submit feedback"})


@app.get("/api/submissions/{submission_id}/feedback", tags=["Submissions"])
async def get_submission_feedback(
    submission_id: int,
    voter_id: Optional[int] = Query(default=None, alias="voterId"),
    db: Session = Depends(get_db),
):
    try:
        submission = db.query(Submission).filter(Submission.id == submission_id).first()
        if not submission:
            return JSONResponse(status_code=404, content={"error": "Submission not found"})

        if voter_id is not None:
            feedback = SubmissionFeedbackCRUD.get_by_submission_and_voter(db, submission_id, voter_id)
            if not feedback:
                return JSONResponse(status_code=404, content={"error": "Feedback not found"})
            return SubmissionFeedbackModel.from_orm(feedback)

        feedback_entries = (
            db.query(SubmissionFeedback)
            .filter(SubmissionFeedback.submission_id == submission_id)
            .order_by(SubmissionFeedback.created_at.desc())
            .all()
        )
        return [SubmissionFeedbackModel.from_orm(entry) for entry in feedback_entries]
    except Exception as e:
        print(f"Error fetching submission feedback: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch feedback"})


@app.post("/api/comprehension-questions/generate", tags=["Comprehension Questions"])
async def generate_comprehension_questions(
    payload: GenerateComprehensionQuestionsRequest,
    db: Session = Depends(get_db)
):
    """
    Generate comprehension questions for a submission and store them in the database.
    This endpoint will auto-generate questions based on the submission content.
    """
    try:
        # Verify user exists
        user = db.query(User).filter(User.id == payload.user_id).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})

        # Verify project exists
        project = db.query(Project).filter(Project.id == payload.project_id).first()
        if not project:
            return JSONResponse(status_code=404, content={"error": "Project not found"})

        # TODO: Implement question generation logic here
        # This is a placeholder - you will fill in the actual generation logic
        # Check if this is a required task (post-test required tasks)
        REQUIRED_TASK_NAMES = {
            'website_tutorial_intro',
            'zic_zac_zoe',
            'website_tutorial_follow_up',
            'zic_zac_zoe_follow_up',
        }
        is_required_task = project.name and project.name.lower() in REQUIRED_TASK_NAMES
        target_selection_context = _build_target_selection_context(project)
        task_description = _resolve_task_description(
            project.name,
            fallback_description=getattr(project, "description", None) or "",
        )
        task_requirements = (
            (target_selection_context or {}).get("requirements")
            if isinstance((target_selection_context or {}).get("requirements"), list)
            else None
        )
        experiment_group = _normalize_experiment_group(payload.experiment_group)
        if not experiment_group:
            user_settings = user.settings if isinstance(user.settings, dict) else {}
            experiment_group = _normalize_experiment_group(
                user_settings.get(SIGNUP_EXPERIMENT_GROUP_SETTINGS_KEY)
            )

        result = await _generate_comprehension_questions(
            submission_title=payload.submission_title,
            submission_description=payload.submission_description,
            submission_code=payload.submission_code,
            is_required_task=is_required_task,
            project_name=project.name.lower() if project.name else None,
            project_label=(project.label or "").strip().lower() or None,
            ai_assistant_mode=payload.ai_assistant_mode,
            experiment_group=experiment_group,
            target_selection_context=target_selection_context,
            task_description=task_description or None,
            task_requirements=task_requirements,
        )
        generated_questions = result["questions"]
        generation_warnings = result.get("warnings", [])
        for idx, q in enumerate(generated_questions, start=1):
            q_name = q.get("question_name", "")
            q_type = q.get("question_type", "")
            q_text_preview = str(q.get("question", "")).replace("\n", " ")[:90]

        # Store questions in database
        created_questions = []
        for question_data in generated_questions:
            # Prefer gold_answer (correct choice text) when present so DB has it for scoring/analytics
            stored_answer = question_data.get("gold_answer")
            if stored_answer is None:
                stored_answer = question_data.get("answer")
            question_record = ComprehensionQuestion(
                user_id=payload.user_id,
                project_id=payload.project_id,
                question_name=question_data["question_name"],
                question=question_data["question"],
                question_type=question_data["question_type"],
                choices=question_data.get("choices"),
                answer=stored_answer,
                user_answer=None,
                score=None
            )
            db.add(question_record)
            db.flush()
            created_questions.append({
                "id": question_record.id,
                "question_name": question_record.question_name,
                "question": question_record.question,
                "question_type": question_record.question_type,
                "choices": question_record.choices,
                "answer": question_record.answer
            })

        db.commit()

        return {
            "success": True,
            "questions": created_questions,
            "count": len(created_questions),
            "warnings": generation_warnings,
        }

    except Exception as e:
        db.rollback()
        print(f"Error generating comprehension questions: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to generate comprehension questions: {str(e)}"}
        )


@app.post("/api/comprehension-questions/save-tutorial", tags=["Comprehension Questions"])
async def save_tutorial_questions(
    payload: SaveTutorialQuestionsRequest,
    db: Session = Depends(get_db)
):
    """
    Save tutorial comprehension questions and answers for the playground task.
    This endpoint creates or gets a Playground project and saves the questions/answers.
    """
    try:
        # Verify user exists
        user = db.query(User).filter(User.id == payload.user_id).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})

        # Get or create Playground project
        playground_project = db.query(Project).filter(
            func.lower(Project.name) == "playground"
        ).first()
        
        if not playground_project:
            playground_project = ProjectCRUD.create(
                db,
                ProjectCreate(
                    name="Playground",
                    description="Tutorial playground task",
                    files=None,
                    code_start_date=None,
                    voting_start_date=None,
                    voting_end_date=None,
                )
            )

        # Save questions and answers
        saved_questions = []
        print(f"Saving tutorial questions for user {payload.user_id}, project {playground_project.id}")
        print(f"Questions: {len(payload.questions)}, Answers: {list(payload.answers.keys())}")
        
        for question_data in payload.questions:
            question_name = question_data.get("question_name") or question_data.get("id", "")
            question_text = question_data.get("question", "")
            question_type = question_data.get("question_type", "free_response")
            choices = question_data.get("choices")
            answer = question_data.get("answer")
            
            print(f"Processing question: {question_name}, type: {question_type}")
            
            # Check if question already exists for this user and project
            existing_question = db.query(ComprehensionQuestion).filter(
                ComprehensionQuestion.user_id == payload.user_id,
                ComprehensionQuestion.project_id == playground_project.id,
                ComprehensionQuestion.question_name == question_name
            ).order_by(ComprehensionQuestion.created_at.desc()).first()
            
            if existing_question:
                # Update existing question
                question_record = existing_question
                print(f"Found existing question: {question_name}")
            else:
                # Create new question
                question_record = ComprehensionQuestion(
                    user_id=payload.user_id,
                    project_id=playground_project.id,
                    question_name=question_name,
                    question=question_text,
                    question_type=question_type,
                    choices=choices,
                    answer=answer,
                    user_answer=None,
                    score=None
                )
                db.add(question_record)
                db.flush()
                print(f"Created new question: {question_name}")
            
            # Update with user answer if provided
            if question_name in payload.answers:
                print(f"Updating answer for {question_name}: {payload.answers[question_name]}")
                user_answer = payload.answers[question_name]
                
                # Parse user_answer - it should be a binary array for multi_select, string for others
                parsed_user_answer = user_answer
                if question_type == 'multi_select':
                    # user_answer should already be a binary array from frontend
                    if isinstance(user_answer, str):
                        import json
                        try:
                            parsed_user_answer = json.loads(user_answer)
                        except:
                            parsed_user_answer = user_answer
                    elif not isinstance(user_answer, list):
                        parsed_user_answer = []
                    # Store as JSON string for multi_select
                    import json
                    question_record.user_answer = json.dumps(parsed_user_answer) if parsed_user_answer else None
                else:
                    # For non-multi_select, keep as string
                    parsed_user_answer = str(user_answer) if user_answer else None
                    question_record.user_answer = parsed_user_answer
                
                print(f"Set user_answer for {question_name}: {question_record.user_answer} (type: {type(question_record.user_answer)})")
                
                # Calculate score for self_report questions (extract number 1-5 from answer)
                if question_name and question_name.startswith('self_report'):
                    try:
                        user_answer_str = str(parsed_user_answer) if parsed_user_answer else ""
                        import re
                        match = re.match(r'^(\d+)', user_answer_str.strip())
                        if match:
                            score_value = int(match.group(1))
                            if 1 <= score_value <= 5:
                                question_record.score = float(score_value)
                        else:
                            question_record.score = None
                    except Exception:
                        question_record.score = None
                
                # Calculate score for multi_select questions that have an answer field
                elif question_type == 'multi_select' and answer:
                    try:
                        # Parse correct answer (should be a binary array)
                        if isinstance(answer, str):
                            try:
                                correct_answer = json.loads(answer)
                            except:
                                correct_answer = [int(x) for x in answer.split(',') if x.strip()]
                        else:
                            correct_answer = answer
                        
                        if isinstance(correct_answer, list) and isinstance(parsed_user_answer, list):
                            if len(correct_answer) == len(parsed_user_answer):
                                matches = sum(1 for i in range(len(correct_answer)) if correct_answer[i] == parsed_user_answer[i])
                                question_record.score = float(matches) / len(correct_answer) if len(correct_answer) > 0 else 0.0
                            else:
                                question_record.score = None
                        else:
                            question_record.score = None
                    except Exception:
                        question_record.score = None
                
                # Calculate score for mcqa questions
                elif question_type in ('mcqa', 'code_compare') and answer:
                    try:
                        correct_answer = int(answer) if isinstance(answer, (int, str)) and str(answer).isdigit() else None
                        user_answer_int = None
                        if isinstance(parsed_user_answer, str):
                            # Extract number from string like "1 - Strongly disagree"
                            import re
                            match = re.match(r'^(\d+)', parsed_user_answer.strip())
                            if match:
                                user_answer_int = int(match.group(1))
                        elif isinstance(parsed_user_answer, (int, float)):
                            user_answer_int = int(parsed_user_answer)
                        
                        if correct_answer is not None and user_answer_int is not None:
                            question_record.score = 1.0 if correct_answer == user_answer_int else 0.0
                        else:
                            question_record.score = None
                    except Exception:
                        question_record.score = None
            
            saved_questions.append({
                "id": question_record.id,
                "question_name": question_record.question_name,
                "question": question_record.question,
                "question_type": question_record.question_type,
                "user_answer": question_record.user_answer,
                "score": question_record.score
            })

        db.commit()

        return {
            "success": True,
            "questions": saved_questions,
            "count": len(saved_questions)
        }

    except Exception as e:
        db.rollback()
        print(f"Error saving tutorial comprehension questions: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to save tutorial comprehension questions: {str(e)}"}
        )



def generate_distractor_functions(functions_map: Dict[str, str]) -> list[str]:
    """
    Generate plausible function names that don't exist in the code.
    functions_map: name -> implementation (included in prompt, truncated if long).
    """
    if not functions_map:
        return []

    random_model = "openai/gpt-5.2-2025-12-11"
    backup_model = "openai/gpt-5.2-2025-12-11"

    # Format each function with name + implementation (truncate long bodies)
    function_blocks = []
    for name, code in functions_map.items():
        code_snippet = code
        function_blocks.append(f"<function name=\"{name}\">\n{code_snippet}\n</function>")
    functions_text = "\n\n".join(function_blocks)
    existing_names = list(functions_map.keys())

    prompt = """
<task>
You are an expert at generating function names that do not exist in a user's code but plausibly could.

Given the existing function names and their implementations below, generate exactly four function names that mimic the style and domain of the existing code but do not actually exist. Use the implementations to understand what the code does (e.g. game turns, setup, UI) so your fake names are plausible. For example, if the code has "playTurn" and "setupLogic", you could generate "endTurn", "setupGame", "resetBoard". We will show these to users and ask them to identify which names exist and which do not, testing their comprehension of their own code.
</task>

Here are the existing functions (name and implementation):
<functions>
{functions_text}
</functions>

<function requirements>
- Mimic the style and domain of the existing function names; use the implementations to inform plausible fake names.
- None of the generated function names should be the same as the existing function names: {existing_names_list}. This is extremely important.
- You may generate: 1) names for features that do not exist in the code; 2) wrapper/helper names that do not exist; 3) names that suggest further decomposition of the real logic.
</function requirements>

<format>
Generate your output as a JSON with the key "fake_function_names" and the value being an array of exactly four function names as strings:
{{
    "fake_function_names": ["function_name_1", "function_name_2", "function_name_3", "function_name_4"]
}}

Do not generate anything else.
</format>
""".format(
        functions_text=functions_text,
        existing_names_list=existing_names,
    ).strip()

    for num_tries in range(5):
        try:
            response = litellm.completion(
                model=random_model if num_tries == 0 else backup_model,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            output = response.choices[0].message.content.replace('`', '').replace('json', '').strip()
            if "{" in output and "}" in output:
                output = output[output.index("{"):output.rindex("}")+1].strip()
            output = json.loads(output)
            if type(output.get("fake_function_names", [])) == list and len(output.get("fake_function_names", [])) == 4:
                return output["fake_function_names"]
        except Exception as e:
            print('failed on distractor func:', str(e))
    return []


def _extract_css_property_names(css_code: str) -> List[str]:
    """
    Extract unique CSS property names from declarations in appearance order.
    """
    if not css_code or not css_code.strip():
        return []

    # Matches declarations like "background-color: pink;" while avoiding selectors and at-rules.
    matches = re.findall(r"(^|[;{\s])([a-zA-Z-]{2,})\s*:", css_code)
    properties: List[str] = []
    for _, prop in matches:
        normalized = prop.strip().lower()
        if not normalized:
            continue
        if normalized.startswith("--"):  # Ignore custom property declarations.
            continue
        if normalized in {"http", "https"}:
            continue
        properties.append(normalized)

    return list(dict.fromkeys(properties))


def _extract_class_and_id_selectors(css_code: str) -> List[str]:
    """
    Extract class and ID selectors from the CSS stylesheet only. Returns list of strings
    in selector form: ".className" for classes, "#idName" for IDs, preserving the user's exact casing (deduped by exact string).
    """
    if not css_code or not css_code.strip():
        return []
    seen: set = set()
    result: List[str] = []

    def add(selector: str) -> None:
        s = selector.strip()
        if s and s not in seen:
            seen.add(s)
            result.append(s)

    for m in re.finditer(r"\.([a-zA-Z_-][a-zA-Z0-9_-]*)", css_code):
        add("." + m.group(1))
    for m in re.finditer(r"#([a-zA-Z_-][a-zA-Z0-9_-]*)", css_code):
        add("#" + m.group(1))

    return result


MAX_DISTRACTOR_CSS_BLOCK_LINES = 20


def generate_distractor_class_id_selectors(
    selector_to_block: Dict[str, str],
    count: int = 5,
) -> List[str]:
    """
    Generate fake-but-plausible class/ID selectors that do not appear in the stylesheet.
    selector_to_block: map of selector string (e.g. .header, #main) -> rule block (implementation).
    When implementations are provided, the model uses them to match project style and domain.
    """
    real_selectors = list(selector_to_block.keys())
    if not real_selectors or count <= 0:
        return []

    random_model = "openai/gpt-5.2-2025-12-11"
    backup_model = "openai/gpt-5.2-2025-12-11"

    # Format each selector with its rule block (truncate long blocks)
    selector_blocks = []
    for sel, block in selector_to_block.items():
        block = (block or "").strip()
        if block:
            lines = block.splitlines()
            if len(lines) > MAX_DISTRACTOR_CSS_BLOCK_LINES:
                lines = lines[:MAX_DISTRACTOR_CSS_BLOCK_LINES]
                block = "\n".join(lines) + "\n  /* ... */"
        selector_blocks.append(
            f'<selector name="{sel}">\n{block or "(no rule block extracted)"}\n</selector>'
        )
    selectors_text = "\n\n".join(selector_blocks)
    existing_names_list = real_selectors

    prompt = """
<task>
You are an expert at generating fake-but-plausible CSS class and ID selectors for distractor questions.

Given the existing selectors and their rule blocks below, generate exactly {count} selectors that look like they could belong to the same project but DO NOT appear in the existing list. Use the rule blocks to understand the project's style and domain (e.g. layout, components, colors) so your fake selectors are plausible. Use the same style: .name for classes, #name for IDs. Preserve the user's casing (e.g. .Header and #Main vs .header and #main).
</task>

Here are the existing selectors and their rule blocks:
<selectors>
{selectors_text}
</selectors>

<requirements>
- Output exactly {count} fake selectors.
- None of the generated selectors may match any existing ones: {existing_names_list}. (Including different prefix: if .header exists, do not generate .header or #header.)
- Mix of class (.) and id (#) selectors similar to the existing list.
- Preserve the user's casing style so fake selectors look consistent.
- Return only the selector strings (e.g. .footer, #sidebar). No comments or explanations.
</requirements>

<format>
Return JSON:
{{
  "fake_selectors": [".footer", "#sidebar", ".card"]
}}
</format>
""".format(
        selectors_text=selectors_text,
        existing_names_list=existing_names_list,
        count=count,
    ).strip()

    existing_set = {s.strip().lower() for s in real_selectors}
    for num_tries in range(5):
        try:
            response = litellm.completion(
                model=random_model if num_tries == 0 else backup_model,
                messages=[{"role": "user", "content": prompt}],
            )
            output = response.choices[0].message.content.replace("`", "").replace("json", "").strip()
            if "{" in output and "}" in output:
                output = output[output.index("{"): output.rindex("}") + 1].strip()
            output_obj = json.loads(output)
            candidates = output_obj.get("fake_selectors", [])
            if not isinstance(candidates, list):
                continue

            cleaned: List[str] = []
            for item in candidates:
                if not isinstance(item, str):
                    continue
                sel = item.strip()
                if not sel or (not sel.startswith(".") and not sel.startswith("#")):
                    continue
                if sel.lower() in existing_set:
                    continue
                if len(sel) < 2:
                    continue
                cleaned.append(sel)
                existing_set.add(sel.lower())

            cleaned = list(dict.fromkeys(cleaned))
            if len(cleaned) >= count:
                return cleaned[:count]
        except Exception as e:
            print("failed on css distractor selectors:", str(e))

    return []


def generate_css_selector_descriptions(
    real_selectors: List[str],
    fake_selectors: List[str],
) -> List[Dict[str, str]]:
    """
    Generate short descriptions for real and fake CSS class/ID selectors (like JS function descriptions).
    Returns list of {"selector": ".header", "description": "Targets the page header."}.
    """
    if not real_selectors and not fake_selectors:
        return []

    random_model = "openai/gpt-5.2-2025-12-11"
    backup_model = "openai/gpt-5.2-2025-12-11"

    real_list_str = "\n".join(
        [f"<existing selector {i+1}>\n{sel}\n</existing selector {i+1}>" for i, sel in enumerate(real_selectors)]
    )
    fake_list_str = "\n".join(
        [f"<fake selector {i+1}>\n{sel}\n</fake selector {i+1}>" for i, sel in enumerate(fake_selectors)]
    )

    prompt = """
<task>
You are an expert at writing concise descriptions for CSS class and ID selectors.

Given a list of existing selectors (from a stylesheet) and fake selectors (plausible but not in the stylesheet), write one short sentence describing what each selector likely targets or styles. Descriptions for real and fake selectors should have the same tone and style so a user cannot tell which are real from the description alone.
</task>

Existing selectors (in the stylesheet):
<existing selectors>
{real_list_str}
</existing selectors>

Fake selectors (not in the stylesheet):
<fake selectors>
{fake_list_str}
</fake selectors>

<requirements>
- Return one description for every selector listed above.
- Each description must be one sentence and less than 10 words.
- Start every description with "This selector..." (e.g. "This selector targets the main navigation bar.").
- Copy selector names exactly (e.g. .header, #main). Keep style consistent across real and fake.
</requirements>

<format>
Return JSON:
{{
  "selector_objects": [
    {{ "selector": ".header", "description": "This selector targets the page header." }},
    {{ "selector": "#main", "description": "This selector styles the main content area." }}
  ]
}}
</format>
""".format(real_list_str=real_list_str, fake_list_str=fake_list_str).strip()

    expected = set(real_selectors + fake_selectors)
    for num_tries in range(5):
        try:
            response = litellm.completion(
                model=random_model if num_tries == 0 else backup_model,
                messages=[{"role": "user", "content": prompt}],
            )
            output = response.choices[0].message.content.replace("`", "").replace("json", "").strip()
            if "{" in output and "}" in output:
                output = output[output.index("{"): output.rindex("}") + 1].strip()
            output_obj = json.loads(output)
            objects = output_obj.get("selector_objects", [])
            if not isinstance(objects, list) or len(objects) != len(expected):
                continue
            found = set()
            cleaned: List[Dict[str, str]] = []
            for item in objects:
                if not isinstance(item, dict):
                    continue
                sel = str(item.get("selector", "")).strip()
                desc = str(item.get("description", "")).strip()
                if not sel or not desc:
                    continue
                found.add(sel)
                cleaned.append({"selector": sel, "description": desc})
            if found == expected and len(cleaned) == len(expected):
                return cleaned
        except Exception as e:
            print("failed on css selector descriptions:", str(e))

    # Fallback
    fallback = []
    for sel in real_selectors + fake_selectors:
        kind = "class" if sel.startswith(".") else "id"
        name = sel[1:] if len(sel) > 1 else sel
        fallback.append({
            "selector": sel,
            "description": f"This selector applies styles to the {name.replace('-', ' ')} {kind}.",
        })
    return fallback


def generate_distractor_css_properties(property_names: List[str], count: int = 5) -> List[str]:
    """
    Prompt 1: Generate plausible CSS property names that do not exist in the stylesheet.
    """
    if not property_names or count <= 0:
        return []

    random_model = "openai/gpt-5.2-2025-12-11"
    backup_model = "openai/gpt-5.2-2025-12-11"

    prompt = """
<task>
You are an expert at generating fake-but-plausible CSS property names for distractor questions.

Given existing CSS property names used in a stylesheet, generate exactly {count} CSS property names that are plausible in the same project style but DO NOT appear in the existing list.
</task>

Here are the existing CSS property names:
<css_property_names>
{property_names}
</css_property_names>

<requirements>
- Output exactly {count} fake CSS property names.
- None of the generated names may match any existing property names.
- Keep names realistic and style-consistent (e.g., kebab-case CSS properties).
- Do not generate comments or explanations.
</requirements>

<format>
Return JSON:
{{
  "fake_css_property_names": ["property_1", "property_2", "property_3"]
}}
</format>
""".format(property_names=property_names, count=count).strip()

    existing_set = {x.strip().lower() for x in property_names}
    for num_tries in range(5):
        try:
            response = litellm.completion(
                model=random_model if num_tries == 0 else backup_model,
                messages=[{"role": "user", "content": prompt}],
            )
            output = response.choices[0].message.content.replace("`", "").replace("json", "").strip()
            if "{" in output and "}" in output:
                output = output[output.index("{"):output.rindex("}") + 1].strip()
            output_obj = json.loads(output)
            candidates = output_obj.get("fake_css_property_names", [])
            if not isinstance(candidates, list):
                continue

            cleaned: List[str] = []
            for item in candidates:
                if not isinstance(item, str):
                    continue
                prop = item.strip().lower()
                if not prop:
                    continue
                if prop in existing_set:
                    continue
                if not re.match(r"^[a-z][a-z0-9-]*$", prop):
                    continue
                cleaned.append(prop)

            cleaned = list(dict.fromkeys(cleaned))
            if len(cleaned) >= count:
                return cleaned[:count]
        except Exception as e:
            print("failed on css distractor properties:", str(e))

    return []


def generate_css_property_descriptions(
    real_property_names: List[str],
    fake_property_names: List[str],
) -> List[Dict[str, str]]:
    """
    Prompt 2: Generate short descriptions for real and fake CSS property names.
    """
    if not real_property_names and not fake_property_names:
        return []

    random_model = "openai/gpt-5.2-2025-12-11"
    backup_model = "openai/gpt-5.2-2025-12-11"

    real_list_str = "\n".join(
        [
            f"<existing property {idx+1}>\n<property name>\n{name}\n</property name>\n</existing property {idx+1}>"
            for idx, name in enumerate(real_property_names)
        ]
    )
    fake_list_str = "\n".join(
        [
            f"<fake property {idx+1}>\n<property name>\n{name}\n</property name>\n</fake property {idx+1}>"
            for idx, name in enumerate(fake_property_names)
        ]
    )

    prompt = """
<task>
You are an expert at writing concise CSS property descriptions.

Given existing and fake CSS property names, write one short sentence describing what each property modifies.
Descriptions for real and fake properties should have the same tone and style.
</task>

Existing properties:
<existing properties>
{real_list_str}
</existing properties>

Fake properties:
<fake properties>
{fake_list_str}
</fake properties>

<requirements>
- Return one description for every property listed above.
- Each description must be one sentence and less than 12 words.
- Start every description with "This style...".
- Copy property names exactly.
- Keep style consistent across real and fake entries.
</requirements>

<format>
Return JSON:
{{
  "property_objects": [
    {{
      "property_name": "background-color",
      "description": "This style changes the element background color."
    }}
  ]
}}
</format>
""".format(real_list_str=real_list_str, fake_list_str=fake_list_str).strip()

    expected_names = set(real_property_names + fake_property_names)
    for num_tries in range(5):
        try:
            response = litellm.completion(
                model=random_model if num_tries == 0 else backup_model,
                messages=[{"role": "user", "content": prompt}],
            )
            output = response.choices[0].message.content.replace("`", "").replace("json", "").strip()
            if "{" in output and "}" in output:
                output = output[output.index("{"):output.rindex("}") + 1].strip()
            output_obj = json.loads(output)
            property_objects = output_obj.get("property_objects", [])
            if not isinstance(property_objects, list):
                continue
            if len(property_objects) != len(expected_names):
                continue

            found_names = set()
            cleaned_objects: List[Dict[str, str]] = []
            for item in property_objects:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("property_name", "")).strip().lower()
                description = str(item.get("description", "")).strip()
                if not name or not description:
                    continue
                found_names.add(name)
                cleaned_objects.append({"property_name": name, "description": description})

            if found_names == expected_names and len(cleaned_objects) == len(expected_names):
                return cleaned_objects
        except Exception as e:
            print("failed on css property descriptions:", str(e))

    # Fallback deterministic descriptions
    fallback = []
    for name in real_property_names + fake_property_names:
        fallback.append(
            {
                "property_name": name,
                "description": f"This style changes the element's {name.replace('-', ' ')}.",
            }
        )
    return fallback


def generate_css_style_questions(css_code: str) -> List[Dict[str, Any]]:
    """
    Build a CSS distractor question: which class and ID selectors exist in your CSS stylesheet?
    Extracts .class and #id from CSS only, generates fake ones and descriptions (like JS), multi_select.
    """
    css_code = css_code or ""
    real_selectors_all = _extract_class_and_id_selectors(css_code)
    if len(real_selectors_all) < 2:
        return []

    target_count = min(5, len(real_selectors_all))
    sampled_real = random.sample(real_selectors_all, k=target_count)
    # Build selector -> rule block so the distractor model sees context (like JS names + implementations)
    selector_to_block: Dict[str, str] = {}
    for sel in sampled_real:
        block = qgh._extract_css_block_by_selector(css_code, sel)
        selector_to_block[sel] = block or ""
    fake_selectors = generate_distractor_class_id_selectors(selector_to_block, count=target_count)
    if len(fake_selectors) != target_count:
        return []

    description_objects = generate_css_selector_descriptions(sampled_real, fake_selectors)
    if not description_objects:
        return []

    fake_set = {s.lower() for s in fake_selectors}
    random.shuffle(description_objects)
    description_objects = description_objects[: min(4, len(description_objects))]

    choices = [f"`{obj['selector']}`: {obj['description']}" for obj in description_objects]
    answers = [0 if obj["selector"].strip().lower() in fake_set else 1 for obj in description_objects]

    return [
        {
            "question_name": "css_style_distractors",
            "question": "Which of these selectors exist in your CSS stylesheet? Each option shows a class or ID selector and a description of what it modifies. It is possible that all of these or none of these exist.",
            "question_type": "multi_select",
            "choices": choices,
            "answer": answers,
        }
    ]


def generate_ui_features(
    html_code: str,
    css_code: str,
    js_code: str,
    task_description: Optional[str] = None,
    task_requirements: Optional[List[str]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Generate real and fake UI features for distractor questions.
    Optional task_description and task_requirements give context about what the user was asked to build.
    """
    # random_model = random.choice(["openai/gpt-5.1-2025-11-13", "anthropic/claude-sonnet-4-5-20250929", "gemini/gemini-3-pro-preview"])
    # backup_model = "gemini/gemini-3-pro-preview"

    random_model = "openai/gpt-5.2-2025-12-11"
    backup_model = "openai/gpt-5.2-2025-12-11"

    task_context_section = ""
    if task_description or (task_requirements and len(task_requirements) > 0):
        parts = []
        if task_description and task_description.strip():
            parts.append(
                "<task_description>\nThis is what the user was asked to build:\n{desc}\n</task_description>".format(
                    desc=task_description.strip()
                )
            )
        if task_requirements and len(task_requirements) > 0:
            req_list = "\n".join(f"- {r}" for r in task_requirements if r and str(r).strip())
            if req_list:
                parts.append(
                    "<task_requirements>\nRequired features or requirements from the assignment:\n{reqs}\n</task_requirements>".format(
                        reqs=req_list
                    )
                )
        if parts:
            task_context_section = "\n\n" + "\n\n".join(parts) + "\n\n"

    prompt = """
<task>
You are an expert at generating a set of features that exist in a user's website, and a set of features that do not exist in a user's website.

Given the user's HTML, CSS, and JavaScript code, generate a set of four features that exist in the website, and a set of four features that do not exist in the website but plausibly could exist in the website. We will eventually show these features to users and ask them to identify which features exist and which do not exist, testing their comprehension of their own website.
</task>
{task_context}Here is the HTML code:
<html>
{html}
</html>

Here is the CSS code:
<css>
{css}
</css>

Here is the JavaScript code:
<javascript>
{js}
</javascript>

<feature requirements>
- Each feature should be a concise sentence/phrase, no more than 15 words.
- When generating features that do exist in the website, make sure that they actually exist.
- When generating features that do not exist in the website, make sure that they do not exist. However, they should be things that plausibly could exist in this website.
- The fake features should not be over-the-top for a simple website. For example, instead of mentioning something like "a special animation", if the website is relatively plain, you could just say "highlighted", "distinct", etc.
- You never hallucinate.
- Generate exactly four real features and four fake features.
- Remember, the fake and real features should be EXACTLY the same in style, including sentence structure, words, level of detail, granularity, number of elements listed, etc.
- Do not add any stylistic differences between real and fake features. For example, if a real feature says "The user can win the game via X, Y, or Z", a bad fake feature would be "The user can win the game via A". They should be consistent, like making the real feature just "The user can win the game via X"
- Modularize real features into atomic units. Do not loop multiple features into a single generated options
- None of the features should use words like "also" or "in addition". This is a giveaway that it is fake.
- All of the features will be merged into a single list, so do not generate fake features that reveal that other features are real. For example, if one real feature says "Players place symbols via X and O", a bad fake feature is "Players can change the symbols from symbols X and O", since it's obvious then that the game does have X and O.
</feature requirements>

<format>
Generate your output as a JSON with two keys: 1) "real_features" - an array of four features that exist in the website; and 2) "fake_features" - an array of four features that do not exist in the website:
{{
    "real_features": ["feature_1", "feature_2", "feature_3", "feature_4"],
    "fake_features": ["feature_5", "feature_6", "feature_7", "feature_8"]
}}

Do not generate anything else.
</format>
""".format(
        task_context=task_context_section,
        html=html_code,
        css=css_code,
        js=js_code,
    ).strip()

    for num_tries in range(5):

        try:
            response = litellm.completion(
                model=random_model if num_tries == 0 else backup_model,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            output = response.choices[0].message.content.replace('`', '').replace('json', '').strip()
            if "{" in output and "}" in output:
                output = output[output.index("{"):output.rindex("}")+1].strip()
            output = json.loads(output)
            if type(output.get("real_features", [])) == list and type(output.get("fake_features", [])) == list and len(output.get("real_features", [])) == 4 and len(output.get("fake_features", [])) == 4:
                return output["real_features"], output["fake_features"]
        except Exception as e:
            print('failed on UI:', str(e))
    return [], []


def generate_ui_questions(
    submission_code: Dict[str, str],
    task_description: Optional[str] = None,
    task_requirements: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    MAX_FEATURES_TO_SHOW = 4
    questions = []

    # 1) Get the user's current code - combine all JavaScript files
    js_code = ""
    html_code = ""
    css_code = ""
    
    for filename, code_content in submission_code.items():
        if filename.endswith('.js') or filename.endswith('.javascript'):
            js_code += code_content + "\n\n"
        elif filename.endswith('.html'):
            html_code += code_content + "\n\n"
        elif filename.endswith('.css'):
            css_code += code_content + "\n\n"

    real_features, fake_features = generate_ui_features(
        html_code,
        css_code,
        js_code,
        task_description=task_description,
        task_requirements=task_requirements,
    )
    if len(real_features) == 0 or len(fake_features) == 0:
        return []
    all_features = real_features + fake_features
    random.shuffle(all_features)
    all_features = all_features[:MAX_FEATURES_TO_SHOW]
    
    questions.append({
        "question_name": "ui_features_distractors",
        "question": f"Which of the following features exist in your website? It is possible that all of these or none of these exist.",
        "question_type": "multi_select",
        "choices": [x for x in all_features],
        "answer": [1 if x in real_features else 0 for x in all_features]
    })
    return questions


def generate_css_questions(submission_code: Dict[str, str]) -> List[Dict[str, Any]]:
    """Generate CSS-only comprehension questions from submitted code."""
    css_code = ""
    for filename, code_content in submission_code.items():
        if filename.endswith('.css'):
            css_code += code_content + "\n\n"
    return generate_css_style_questions(css_code)

def generate_js_descriptions(real_names: list[str], fake_names: list[str], real_implementations: list[str]) -> list[str]:
    """
    Generate descriptions for real and fake models
    """
    
    existing_functions_str = "\n".join([f"<existing function {idx+1}>\n<function name>\n{name}\n</function name>\n<function implementation>\n{real_implementations[idx]}\n</function implementation>\n</existing function {idx+1}>" for idx, name in enumerate(real_names)])

    fake_functions_str = "\n".join([f"<fake function {len(real_names)+idx+1}>\n<function name>\n{name}\n</function name>\n</fake function {idx+1}>" for idx, name in enumerate(fake_names)])

    random_model = "openai/gpt-5.2-2025-12-11"
    backup_model = "openai/gpt-5.2-2025-12-11"

    prompt = """
<task>
You are an expert at generating descriptions for user functions, regardless of whether they are real and have accompanying implementation, or whether they are fake and have no implementations.

Given a list of function names, you will generate a description that describes the code at a high-level. You will see two function types: 1) existing function names; and 2) fake function names. Existing function names will come with a name and a implementation code block, so the generated description will faithfully describe how the code works. Fake functions will only come with the function name, and you need to come up with a description that describes how the function could work if it actually existed, using the exact same style as the existing function names. The goal is to eventually show these to users and have them pick which actually exist in their code, so do not add any obvious differences between the real and fake descriptions.
</task>

Each existing function will be in the form:
<existing function [function_idx]>
<function name>
...
</function name>
<function implementation>
...
</function implementation>
</existing function [function_idx]>

Each fake function will be in the form:
<fake function [function_idx]>
<function name>
...
</function name>
</fake function [function_idx]>

Here are the existing functions with their name and implementation:
<existing functions>
{existing_functions_str}
</existing functions>

Here are the fake functions which only have a name:
<fake functions>
{fake_functions_str}
</fake functions>

<requirements>
- Mimic the style and content of the existing function names
- All of the descriptions should be a single sentence and less than 10 words
- For the existing function names, the descriptions should be a high-level overview of how the function works (e.g., "This function checks whether a user has won the game")
- For the fake function names, the descriptions should be feasible descriptions of what the function could do if it actually existed based on its name 
- The descriptions for fake and existing functions should be written in identical styles. The length, punctuation, specificity, and word choice should be similar.
- Do not introduce any surface-level stylistic cues or artifacts where it is shallowly possible to tell whether the function is existing or fake; it shouldn't be obvious for someone who hasn't written the code. 
- All descriptions should start with the phrase "This function..."
- Copy the function names EXACTLY. Do not generate any new function names. They should exactly match the names in the existing and fake functions blocks. This is extremely important.
</requirements>

<format>
Generate your output as a JSON object with exactly one key: "function_objects". The value must be an array of objects. The array must have exactly {total_count} items (one per function: {real_count} existing + {fake_count} fake). Each object must have exactly two string keys: "function_name" and "description". Use the exact function names from the input; do not change spelling, casing, or punctuation.

Example structure (replace with your {total_count} items):
{{
    "function_objects": [
        {{ "function_name": "<exact name from input>", "description": "This function ..." }},
        {{ "function_name": "<exact name from input>", "description": "This function ..." }}
    ]
}}

Do not generate anything else. No other keys. Raw JSON only.
</format>
""".format(
        existing_functions_str=existing_functions_str,
        fake_functions_str=fake_functions_str,
        total_count=len(real_names) + len(fake_names),
        real_count=len(real_names),
        fake_count=len(fake_names),
    ).strip()

    for num_tries in range(5):

        try:
            response = litellm.completion(
                model=random_model if num_tries == 0 else backup_model,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            output = response.choices[0].message.content.replace('`', '').replace('json', '').strip()
            if "{" in output and "}" in output:
                output = output[output.index("{"):output.rindex("}")+1].strip()
            output = json.loads(output)
            if output.get("function_objects", []) and len(output.get("function_objects", [])) == len(real_names) + len(fake_names):

                all_function_names = set()
                for item in output["function_objects"]:
                    if item.get("function_name", "") and item.get("description", ""):
                        all_function_names.add(item.get("function_name", ""))

                if all_function_names == set(real_names + fake_names):
                    return output["function_objects"]
        except Exception as e:
            pass  # retry on next attempt

    return []


def generate_js_questions(submission_code: Dict[str, str], include_explanation: bool = True) -> List[Dict[str, Any]]:
    """ 
    Generate comprehension questions based on the user's submitted code.
    
    Args:
        submission_code: Dictionary mapping filename to code content
        include_explanation: If True, include the explanation question. If False, only include MCQA questions.
        
    Returns:
        List of question dictionaries
    """
    MAX_FUNCTION_NAMES_TO_SHOW = 4
    questions = []
    
    # 1) Get the user's current code - combine all JavaScript files
    js_code = ""
    html_code = ""
    css_code = ""
    
    for filename, code_content in submission_code.items():
        if filename.endswith('.js') or filename.endswith('.javascript'):
            js_code += code_content + "\n\n"
        elif filename.endswith('.html'):
            html_code += code_content + "\n\n"
        elif filename.endswith('.css'):
            css_code += code_content + "\n\n"

    # Parse JavaScript functions
    functions_map = qgh._parse_javascript_functions(js_code)

    if len(functions_map) == 0:
        print(
            "[generate_js_questions] Skipping all JS questions (including 'Which of the following JavaScript functions exist in your code?'): "
            "no JavaScript functions were parsed from the submission (missing or empty .js/.javascript files, or parser found no functions).",
            flush=True,
        )
        return []

    real_function_names = list(functions_map.keys())
    fake_function_names = generate_distractor_functions(functions_map)

    # Only sample code blocks for explanation if we're including explanation questions
    sampled_function_name = None
    sampled_function_code = None
    sampled_html_component_name = None
    sampled_html_component_code = None
    sampled_css_block_name = None
    sampled_css_block_code = None
    
    if include_explanation:
        eligible_functions = {
            name: code
            for name, code in functions_map.items()
            if name not in qgh.JS_FUNCTIONS_EXCLUDED_FROM_CODE_BLOCK_SAMPLING
            and qgh._count_code_lines(code) <= qgh.MAX_CODE_COMPARE_BLOCK_LINES
        }
        if eligible_functions:
            sampled_function_name = random.choice(list(eligible_functions.keys()))
            sampled_function_code = eligible_functions[sampled_function_name]
            sampled_line_count = qgh._count_code_lines(sampled_function_code)
            qgh._code_compare_debug_log(
                f"[code-compare] sampled js function='{sampled_function_name}' lines={sampled_line_count} max_lines={qgh.MAX_CODE_COMPARE_BLOCK_LINES}"
            )
        else:
            fallback_functions = {
                name: code
                for name, code in functions_map.items()
                if name not in qgh.JS_FUNCTIONS_EXCLUDED_FROM_CODE_BLOCK_SAMPLING
            }
            if fallback_functions:
                sampled_function_name = random.choice(list(fallback_functions.keys()))
                sampled_function_code = fallback_functions[sampled_function_name]
                sampled_line_count = qgh._count_code_lines(sampled_function_code)
                qgh._code_compare_debug_log(
                    f"[code-compare] sampled fallback js function='{sampled_function_name}' lines={sampled_line_count} (no <= {qgh.MAX_CODE_COMPARE_BLOCK_LINES} candidates)"
                )

        sampled_html_component = qgh._sample_html_component_for_explanation(html_code)
        if sampled_html_component:
            sampled_html_component_name, sampled_html_component_code = sampled_html_component
            qgh._code_compare_debug_log(f"[code-compare] sampled html component='{sampled_html_component_name}'")

        sampled_css_block = qgh._sample_css_block_for_explanation(css_code)
        if sampled_css_block:
            sampled_css_block_name, sampled_css_block_code = sampled_css_block
            qgh._code_compare_debug_log(f"[code-compare] sampled css block='{sampled_css_block_name}'")
        # For CSS code-compare we use the full stylesheet (not a single block); distractor is entire sheet.

    # For the MCQA question, exclude the sampled function (if any) from the choices
    real_function_names_for_mcqa = [name for name in real_function_names if name != sampled_function_name] if sampled_function_name else real_function_names
    
    if len(fake_function_names) > len(real_function_names_for_mcqa):
        random.shuffle(fake_function_names)
        fake_function_names = fake_function_names[:len(real_function_names_for_mcqa)]
    if len(real_function_names_for_mcqa) > len(fake_function_names):
        random.shuffle(real_function_names_for_mcqa)
        real_function_names_for_mcqa = real_function_names_for_mcqa[:len(fake_function_names)]
    all_function_names_to_show = real_function_names_for_mcqa + fake_function_names
    if len(all_function_names_to_show) > MAX_FUNCTION_NAMES_TO_SHOW:
        random.shuffle(all_function_names_to_show)
        all_function_names_to_show = all_function_names_to_show[:MAX_FUNCTION_NAMES_TO_SHOW]

    real_functions_final = [x for x in all_function_names_to_show if x in real_function_names_for_mcqa]
    fake_functions_final = [x for x in all_function_names_to_show if x in fake_function_names]

    description_object = generate_js_descriptions(real_functions_final, fake_functions_final, [functions_map[x] for x in real_functions_final])
    random.shuffle(description_object)

    function_name_choices = [f"`{x['function_name']}()`: {x['description']}" for x in description_object]
    if not function_name_choices:
        print(
            "[generate_js_questions] 'Which of the following JavaScript functions exist in your code?' question not generated: "
            "generate_js_descriptions returned no descriptions (LLM call failed or response validation failed after retries).",
            flush=True,
        )
    if function_name_choices:
        questions.append({
            "question_name": "function_names_distractors",
            "question": f"Which of the following JavaScript functions exist in your code? Each option shows a function name and a description of its implementation. It is possible that all of these or none of these exist.",
            "question_type": "multi_select",
            "choices": function_name_choices,
            "answer": [0 if x['function_name'] in fake_function_names else 1 for x in description_object]
        })

    # Add code-comparison questions in explicit display order:
    # 1) HTML, 2) CSS, 3) JavaScript.
    # Build these in parallel, then append in deterministic order.
    compare_specs = [
        {
            "question_name": "identify_own_html_component",
            "code_kind_label": f"HTML component {sampled_html_component_name}" if sampled_html_component_name else "HTML component",
            "code_language": "html",
            "original_code": sampled_html_component_code,
            "has_sampled_name": bool(sampled_html_component_name),
            "has_sampled_code": bool(sampled_html_component_code),
            "log_label": "html",
            "attempt_message": f"[code-compare] attempting html question component='{sampled_html_component_name}'",
        },
        {
            "question_name": "identify_own_css_block",
            "code_kind_label": "CSS stylesheet",
            "code_language": "css",
            "original_code": css_code,
            "full_context_code": None,
            "has_sampled_name": bool(css_code.strip()),
            "has_sampled_code": bool(css_code.strip()),
            "log_label": "css",
            "attempt_message": "[code-compare] attempting css question (full stylesheet)",
        },
        {
            "question_name": "identify_own_js_function",
            "code_kind_label": f"JavaScript function {sampled_function_name}()" if sampled_function_name else "JavaScript function",
            "code_language": "javascript",
            "original_code": sampled_function_code,
            "has_sampled_name": bool(sampled_function_name),
            "has_sampled_code": bool(sampled_function_code),
            "log_label": "js",
            "attempt_message": f"[code-compare] attempting js question function='{sampled_function_name}'",
        },
    ]

    pending_specs = []
    if include_explanation:
        for spec in compare_specs:
            if spec["has_sampled_name"] and spec["has_sampled_code"]:
                qgh._code_compare_debug_log(spec["attempt_message"])
                pending_specs.append(spec)
            else:
                qgh._code_compare_debug_log(
                    f"[code-compare] skipping {spec['log_label']} compare question "
                    f"(include_explanation={include_explanation}, "
                    f"has_sampled_name={spec['has_sampled_name']}, "
                    f"has_sampled_code={spec['has_sampled_code']})"
                )
    else:
        for spec in compare_specs:
            qgh._code_compare_debug_log(
                f"[code-compare] skipping {spec['log_label']} compare question "
                f"(include_explanation={include_explanation}, "
                f"has_sampled_name={spec['has_sampled_name']}, "
                f"has_sampled_code={spec['has_sampled_code']})"
            )

    compare_results_by_name: Dict[str, Optional[Dict[str, Any]]] = {}
    if pending_specs:
        with ThreadPoolExecutor(max_workers=len(pending_specs)) as executor:
            futures = [
                executor.submit(
                    qgh._build_code_compare_question,
                    question_name=spec["question_name"],
                    code_kind_label=spec["code_kind_label"],
                    code_language=spec["code_language"],
                    original_code=spec["original_code"],
                    full_context_code=spec.get("full_context_code"),
                )
                for spec in pending_specs
            ]
            for spec, future in zip(pending_specs, futures):
                compare_results_by_name[spec["question_name"]] = future.result()

    for spec in compare_specs:
        result = compare_results_by_name.get(spec["question_name"])
        if result:
            questions.append(result)
            qgh._code_compare_debug_log(f"[code-compare] appended {spec['question_name']}")
        elif spec["question_name"] in compare_results_by_name:
            qgh._code_compare_debug_log(
                f"[code-compare] {spec['log_label']} compare question build returned None"
            )

    return questions


def _generate_distractor_functions(existing_functions: List[str]) -> List[str]:
    """
    Generate plausible function names that don't exist in the code.
    """
    common_function_names = [
        "initialize", "setup", "configure", "validate", "process",
        "handleClick", "handleSubmit", "handleChange", "handleInput",
        "updateUI", "render", "display", "show", "hide",
        "calculate", "compute", "transform", "format", "parse",
        "fetchData", "saveData", "loadData", "deleteData",
        "checkStatus", "verify", "authenticate", "authorize"
    ]
    
    # Filter out functions that already exist
    distractors = [f for f in common_function_names if f not in existing_functions]
    
    # Also generate variations of existing function names
    for func in existing_functions[:3]:  # Limit to first 3 to avoid too many
        # Add prefixes/suffixes
        distractors.extend([
            f"init{func.capitalize()}",
            f"{func}Handler",
            f"handle{func.capitalize()}",
            f"{func}Async",
            f"validate{func.capitalize()}"
        ])
    
    # Return a reasonable number of distractors
    return distractors[:max(5, len(existing_functions) * 2)]


def _extract_existing_features(js_code: str, html_code: str, css_code: str) -> List[str]:
    """
    Extract features that exist in the code based on patterns.
    """
    features = []
    all_code = (js_code + " " + html_code + " " + css_code).lower()
    
    # Check for common features/patterns
    feature_patterns = {
        "Event Listeners": ["addeventlistener", "onclick", "onchange", "onsubmit", "oninput"],
        "DOM Manipulation": ["getelementbyid", "queryselector", "innerhtml", "textcontent", "appendchild"],
        "API Calls": ["fetch", "xmlhttprequest", "axios", "ajax"],
        "Local Storage": ["localstorage", "sessionstorage"],
        "Animations": ["setinterval", "settimeout", "requestanimationframe", "transition", "animation"],
        "Form Handling": ["form", "input", "textarea", "select", "submit"],
        "Data Validation": ["validate", "check", "verify", "test"],
        "Error Handling": ["try", "catch", "error", "throw"],
        "Async Operations": ["async", "await", "promise", "then", "catch"],
        "CSS Styling": ["classlist", "style", "css", "stylesheet"],
        "Responsive Design": ["media query", "@media", "viewport", "responsive"],
        "User Input": ["prompt", "confirm", "alert", "input", "textarea"]
    }
    
    for feature_name, patterns in feature_patterns.items():
        if any(pattern in all_code for pattern in patterns):
            features.append(feature_name)
    
    return features


def _generate_distractor_features(existing_features: List[str]) -> List[str]:
    """
    Generate plausible features that don't exist in the code.
    """
    all_possible_features = [
        "Event Listeners", "DOM Manipulation", "API Calls", "Local Storage",
        "Animations", "Form Handling", "Data Validation", "Error Handling",
        "Async Operations", "CSS Styling", "Responsive Design", "User Input",
        "WebSockets", "Service Workers", "IndexedDB", "WebGL",
        "Canvas Drawing", "Video/Audio Playback", "File Upload", "Drag and Drop",
        "Geolocation", "Camera Access", "Push Notifications", "Payment Processing"
    ]
    
    # Return features that don't exist
    return [f for f in all_possible_features if f not in existing_features]

async def _generate_comprehension_questions(
    submission_title: str,
    submission_description: str,
    submission_code: Dict[str, str],
    is_required_task: bool = True,
    project_name: Optional[str] = None,
    project_label: Optional[str] = None,
    ai_assistant_mode: Optional[str] = None,
    experiment_group: Optional[str] = None,
    target_selection_context: Optional[Dict[str, Any]] = None,
    task_description: Optional[str] = None,
    task_requirements: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Generate comprehension questions based on the submission.
    Combines self-report questions with code-based questions.
    
    Args:
        submission_title: Title of the submission
        submission_description: Description of the submission
        submission_code: Dictionary mapping filename to code content
        is_required_task: If True, include all questions (for post-test required tasks).
                         If False, only include self_report_understanding and MCQA questions,
                         excluding explanation question.
        project_name: Lowercase name of the project/task (e.g., "snake", "platformer")
        project_label: Task label (e.g., "replication", "open-ended"). Used to skip css_style_distractors for game-based tasks.
        task_description: Optional description of what the user was asked to build (for UI feature distractors).
        task_requirements: Optional list of assignment requirements (for UI feature distractors).
    
    Returns a dict with "questions" (list of question dicts) and "warnings" (list of str). Each question has the structure:
    {
        "question_name": str,  # e.g., "purpose_1", "technology_2"
        "question": str,  # The actual question text/stem
        "question_type": str,  # "mcqa", "multi_select", "matrix", or "free_response"
        "choices": Optional[Any],  # list[str] for mcqa/multi_select; dict rows/scale for matrix
        "answer": Optional[str]  # Correct answer for scoring
    }
    """

    normalized_mode = (ai_assistant_mode or "").strip().lower()
    normalize_experiment_mode = _normalize_experiment_group(experiment_group)

    SANITY_QUESTION_PROBABILITY = 0.5
    # Tasks that should always have attention checks
    ALWAYS_ATTENTION_CHECK_TASKS = {'platformer'}
    # Website-requirements tasks that should always include a fixed sanity check.
    # Warm-up tasks are intentionally excluded.
    FIXED_SANITY_CHECK_TASKS = {'zic_zac_zoe', 'zic_zac_zoe_follow_up'}
    # Warm-up tasks should only use fixed agreement prompts.
    # Do not generate code/UI comprehension items for these tasks.
    WARM_UP_TASKS = {'website_tutorial_intro', 'website_tutorial_follow_up'}
    # These tasks should only include the standard self-report block.
    # Skip generated code/UI/snippet questions and sanity checks.
    SELF_REPORT_ONLY_TASKS = {'zic_zac_zoe_follow_up'}

    questions = []
    self_report_options = ["1 - Strongly disagree", "2 - Disagree", "3 - Neither agree nor disagree", "4 - Agree", "5 - Strongly agree"]
    
    # Add self-report questions
    prefix = "How much do you agree with this statement"

    # Warm-up tasks only receive fixed agreement questions.
    # Intentionally skip all generated comprehension question logic.
    if project_name and project_name in WARM_UP_TASKS:
        questions.extend([
            {
                "question_name": "warmup_success",
                "question": "I successfully completed the task",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
            {
                "question_name": "warmup_understand",
                "question": "I understood the requirements.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            }])
        return {"questions": questions, "warnings": []}

    if is_required_task:
        # For required tasks, include all self-report questions

        # questions = [
        #     {
        #         "question_name": "self_report_confidence",
        #         "question": f"I am confident this submission meets the task requirements.",
        #         "question_type": "mcqa",
        #         "choices": self_report_options,
        #         "answer": ""
        #     },
        # ]
        questions = []

        agent_mode_questions = [
            {
                "question_name": "self_report_easy_with_agent",
                "question": f"The AI in Agent Mode (where it directly edited files) was helpful for this task.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
            {
                "question_name": "self_report_demand_with_agent",
                "question": f"Working with the AI in Agent Mode required a lot of mental effort.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
            {
                "question_name": "self_report_review_with_agent",
                "question": f"I read and reviewed the AI-generated code.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
            # {
            #     "question_name": "self_report_detection_with_agent",
            #     "question": f"I could tell when there were errors in the AI-generated code.",
            #     "question_type": "mcqa",
            #     "choices": self_report_options,
            #     "answer": ""
            # },
        ]

        chat_mode_questions = [
            {
                "question_name": "self_report_easy_with_chat",
                "question": f"The AI in Chat Mode (where it generated code/syntax help) was helpful for this task.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
            {
                "question_name": "self_report_demand_with_chat",
                "question": f"Working with the AI in Chat Mode required a lot of mental effort.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },    
            {
                "question_name": "self_report_review_with_chat",
                "question": f"I read and reviewed the AI chatbot's responses.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
            # {
            #     "question_name": "self_report_detection_with_chat",
            #     "question": f"I could tell when there were errors in the AI chatbot's responses.",
            #     "question_type": "mcqa",
            #     "choices": self_report_options,
            #     "answer": ""
            # }     
        ]

        
        questions.extend(agent_mode_questions if normalized_mode == "agent" else chat_mode_questions)

        if project_name in SELF_REPORT_ONLY_TASKS:
            if normalize_experiment_mode == 'agent':
                questions.extend([
                    {
                        "question_name": "free_response_task_agent",
                        "question": f"Was there anything specific about the AI in Agent Mode (directly editing your code) in the first task that made it easier or harder to work with to complete tasks?",
                        "question_type": "free_response",
                        "choices": [],
                        "answer": ""
                    },
                ])
            questions.extend([
                {
                    "question_name": "free_response_task_chat",
                    "question": f"Was there anything specific about the AI in Chat Mode (providing high-level syntax) that made it easier or harder to work with to complete tasks?",
                    "question_type": "free_response",
                    "choices": [],
                    "answer": ""
                },
            ])

        questions.extend([
            {
                "question_name": "self_report_internalization",
                "question": f"The code feels like my own work.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
            {
                "question_name": "self_report_understanding",
                "question": f"I understand how my code works.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
            {
                "question_name": "self_report_modify",
                "question": f"I could easily add new features (e.g., new game rules, UI components, CSS styles) to my code without using AI tools.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
        ])
    
        if project_name in SELF_REPORT_ONLY_TASKS:
            if normalize_experiment_mode == 'agent':
                questions.extend([
                    {
                        "question_name": "free_response_understanding_agent",
                        "question": f"Was there anything specific about the AI in Agent Mode (directly editing your code) in the first task that made it easier or harder to understand your code? Feel free to recall or compare with any AI programming tools you have previously used",
                        "question_type": "free_response",
                        "choices": [],
                        "answer": ""
                    },
                ])
            else:
                questions.extend([
                    {
                        "question_name": "free_response_understanding_chat",
                        "question": f"Was there anything specific about the AI in Chat Mode (providing high-level syntax) in the first task that made it easier or harder to understand your code? Feel free to recall or compare with any AI programming tools you have previously used",
                        "question_type": "free_response",
                        "choices": [],
                        "answer": ""
                    },
                ])
            questions.extend([
                {
                    "question_name": "free_response_extending",
                    "question": f"Was there any specific property of your code (e.g., number of functions, number of lines, comments) in the first task that made it easier or harder to extend your code in the second task?",
                    "question_type": "free_response",
                    "choices": [],
                    "answer": ""
                },
            ])
        questions.append(
            {
                "question_name": "preference_future_ai_assistant_matrix",
                "question": "For future tasks in our interface, how much would you prefer each of the following ways of working with AI? Use the scale from least preferred to most preferred for each row.\n\nNote: Your response will NOT affect how you are assigned in future tasks.",
                "question_type": "matrix",
                "choices": {
                    "rows": [
                        "No AI assistance",
                        "Chat Mode: A model that can only provide syntax help without access to your code",
                        "Agent Mode: A model that can directly access and edit your code",
                        "The option to switch between Chat Mode and Agent Mode",
                    ],
                    "scale": [
                        "1 - Least preferred",
                        "2",
                        "3",
                        "4",
                        "5 - Most preferred",
                    ],
                },
                "answer": "",
            }
        )
        if project_name in SELF_REPORT_ONLY_TASKS:
            questions.extend([
                {
                    "question_name": "free_response_preference_features_like",
                    "question": f"Which features of the AI assistants did you find helpful? Feel free to recall or compare with any AI programming tools you have previously used (Optional)",
                    "question_type": "free_response",
                    "choices": [],
                    "answer": ""
                },
            ])
            questions.extend([
                {
                    "question_name": "free_response_preference_features_want",
                    "question": f"Were there any additional or different features that you wish the AI assistants you worked with had? Feel free to recall or compare with any AI programming tools you have previously used (Optional)",
                    "question_type": "free_response",
                    "choices": [],
                    "answer": ""
                },
            ])
    else:
        # For non-required tasks, only include self_report_understanding
        questions.extend([
            {
                "question_name": "self_report_happy_game",
                "question": f"I am happy with the game I created.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
        ])
        questions.extend([
            {
                "question_name": "self_report_AI_helpful",
                "question": f"The AI assistant was helpful for this task.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
        ])
        questions.extend([
            {
                "question_name": "self_report_understanding_game",
                "question": f"I understand how my code works.",
                "question_type": "mcqa",
                "choices": self_report_options,
                "answer": ""
            },
        ])
        questions.extend([
            {
                "question_name": "free_response_game",
                "question": f"Was there anything else that you liked or disliked during the interaction with the AI? (Optional)",
                "question_type": "free_response",
                "choices": [],
                "answer": ""
            },
        ])

    if project_name and project_name in SELF_REPORT_ONLY_TASKS:
        questions.append(
            {
                "question_name": "self_report_expected_follow_up_modification",
                "question": "Did you anticipate that you would be asked to extend or modify the code you submitted in the first recreation task?",
                "question_type": "mcqa",
                "choices": ["Yes", "No", "Unsure"],
                "answer": "",
            }
        )
        return {"questions": questions, "warnings": []}

    #questions = []
    num_self_report_questions = len(questions)
    
    # Run HTML compare first so we can pass its "real block" (HTML+JS) to snippet understanding questions.
    html_compare_result = await asyncio.to_thread(
        qgh.generate_single_code_compare_question,
        submission_code,
        "html",
        is_required_task,
        project_name,
        target_selection_context,
    )
    html_compare_question, html_real_block = html_compare_result if isinstance(html_compare_result, tuple) else (html_compare_result, None)

    # Run compare + other questions first so we know whether pairwise compare succeeded per language.
    (
        code_questions,
        ui_questions,
        css_questions,
        css_compare_result,
        js_compare_result,
    ) = await asyncio.gather(
        asyncio.to_thread(generate_js_questions, submission_code, include_explanation=False),
        asyncio.to_thread(
            generate_ui_questions,
            submission_code,
            task_description,
            task_requirements,
        ),
        asyncio.to_thread(generate_css_questions, submission_code),
        asyncio.to_thread(
            qgh.generate_single_code_compare_question,
            submission_code,
            "css",
            is_required_task,
            project_name,
            target_selection_context,
        ),
        asyncio.to_thread(
            qgh.generate_single_code_compare_question,
            submission_code,
            "js",
            is_required_task,
            project_name,
            target_selection_context,
        ),
    )
    if isinstance(css_compare_result, tuple):
        css_compare_question, css_snippet_block = css_compare_result[0], css_compare_result[1]
    else:
        css_compare_question, css_snippet_block = css_compare_result, None
    if isinstance(js_compare_result, tuple):
        js_compare_question, js_snippet_block = js_compare_result[0], js_compare_result[1]
    else:
        js_compare_question, js_snippet_block = js_compare_result, None

    # Snippet (purpose/mechanism) questions use the same block as the pairwise compare for that language.
    (
        html_snippet_questions,
        css_snippet_questions,
        js_snippet_questions,
    ) = await asyncio.gather(
        asyncio.to_thread(
            qgh.generate_snippet_understanding_questions,
            submission_code,
            "html",
            is_required_task,
            project_name,
            target_selection_context,
            html_real_block,
        ),
        asyncio.to_thread(
            qgh.generate_snippet_understanding_questions,
            submission_code,
            "css",
            is_required_task,
            project_name,
            target_selection_context,
            css_snippet_block,
            bool(css_compare_question),
        ),
        asyncio.to_thread(
            qgh.generate_snippet_understanding_questions,
            submission_code,
            "js",
            is_required_task,
            project_name,
            target_selection_context,
            js_snippet_block,
            bool(js_compare_question),
        ),
    )
    is_game_based_task = (project_label or "").strip().lower() in ("replication", "open-ended")
    
    warnings: List[str] = []
    questions.extend(ui_questions)
    if not is_game_based_task:
        questions.extend(css_questions)
    questions.extend(code_questions)
    # Deterministic compare question order: html -> css -> js
    if html_compare_question:
        questions.append(html_compare_question)
    if css_compare_question:
        questions.append(css_compare_question)
    if js_compare_question:
        questions.append(js_compare_question)
    # Add snippet understanding questions in deterministic language order.
    questions.extend(html_snippet_questions)
    questions.extend(css_snippet_questions)
    questions.extend(js_snippet_questions)

    # Add sanity check question
    # Always add for snake and platformer, otherwise 50% probability for other required tasks
    should_add_sanity_check = False
    should_add_fixed_sanity_check = False
    if project_name and project_name in ALWAYS_ATTENTION_CHECK_TASKS:
        should_add_sanity_check = True
    if project_name and project_name in FIXED_SANITY_CHECK_TASKS:
        should_add_fixed_sanity_check = True
    
    if should_add_fixed_sanity_check:
        # Keep this sanity check deterministic for website-requirements tasks.
        fixed_choice = "2 - Disagree"
        position_to_insert = min(3, len(questions))
        sanity_question = {
            "question_name": "sanity_check",
            "question": f"Attention Check: Please select \"{fixed_choice}\" as your answer",
            "question_type": "mcqa",
            "choices": self_report_options,
            "answer": self_report_options.index(fixed_choice) + 1
        }
        questions.insert(position_to_insert, sanity_question)
    elif should_add_sanity_check:
        position_to_insert = random.randint(0, num_self_report_questions)
        choice_to_select = random.choice(self_report_options)
        sanity_question = {
            "question_name": "sanity_check",
            "question": f"Attention Check: Please select \"{choice_to_select}\" as your answer",
            "question_type": "mcqa",
            "choices": self_report_options,
            "answer": self_report_options.index(choice_to_select) + 1
        }
        questions.insert(position_to_insert, sanity_question)

    question_names = [str(q.get("question_name", "")) for q in questions]

    return {"questions": questions, "warnings": warnings}


async def _evaluate_submission(
    submission_title: str,
    submission_description: str,
    submission_code: Dict[str, str],
    task_description: Optional[str] = None,
    task_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Evaluate a submission using LLM-as-a-judge on four dimensions.
    
    Args:
        submission_title: Title of the submission
        submission_description: Description of the submission
        submission_code: Dictionary mapping filename to code content
        task_description: Description of the task/project
        task_name: Name of the task/project
    
    Returns a dictionary with the following structure:
    {
        "is_valid": bool,  # Whether submission is valid
        "explanation": str  # Explanation for the decision
    }
    """
    model = "openai/gpt-5.2-2025-12-11"
    
    # Combine code files
    js_code = ""
    html_code = ""
    css_code = ""
    
    for filename, code_content in submission_code.items():
        if filename.endswith('.js') or filename.endswith('.javascript'):
            js_code += code_content + "\n\n"
        elif filename.endswith('.html'):
            html_code += code_content + "\n\n"
        elif filename.endswith('.css'):
            css_code += code_content + "\n\n"

    # Combine HTML, CSS, and JS into a single HTML document
    # If HTML already has structure, inject CSS and JS appropriately
    # Otherwise, wrap everything in a basic HTML structure
    newline = '\n'
    
    if html_code and ('<html' in html_code.lower() or '<!doctype' in html_code.lower()):
        # HTML already has structure - inject CSS in <head> and JS before </body>
        website_code = html_code
        if css_code:
            # Try to inject CSS in <head>, or add it if no head exists
            if '</head>' in html_code.lower():
                website_code = website_code.replace('</head>', f'<style>{newline}{css_code}{newline}</style>{newline}</head>', 1)
            elif '<body>' in html_code.lower():
                website_code = website_code.replace('<body>', f'<head><style>{newline}{css_code}{newline}</style></head>{newline}<body>', 1)
            else:
                website_code = f'<head><style>{newline}{css_code}{newline}</style></head>{newline}{website_code}'
        
        if js_code:
            # Inject JS before </body> or at the end
            if '</body>' in html_code.lower():
                website_code = website_code.replace('</body>', f'<script>{newline}{js_code}{newline}</script>{newline}</body>', 1)
            else:
                website_code = f'{website_code}{newline}<script>{newline}{js_code}{newline}</script>'
    else:
        # No HTML structure - create a complete HTML document
        css_section = f'<style>{newline}{css_code}{newline}</style>' if css_code else ''
        js_section = f'<script>{newline}{js_code}{newline}</script>' if js_code else ''
        title_text = submission_title if submission_title else "Submission"
        html_body = html_code if html_code else ''
        
        website_code = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title_text}</title>
    {css_section}
</head>
<body>
    {html_body}
    {js_section}
</body>
</html>"""
    
    # Build the prompt
    task_info = ""
    if task_description:
        task_info = f"\n\n<task_description>\n{task_description}\n</task_description>"
    if task_name:
        task_info += f"\n\n<task_name>\n{task_name}\n</task_name>"
    
    prompt = f"""<task>
You are an expert at evaluating websites submitted by users.

Given a user's submission, your job is to evaluate whether the submission is a valid, good-faith attempt to complete the task. Afterwards, you will write an explanation summarizing your evaluation.
</task>

Here is information about the task the user submitted to:
<task_description>
{task_info}
</task_description>

Here is the title of the user's submission
<submission_title>
{submission_title}
</submission_title>

Here is the description of the user's submission:
<submission_description>
{submission_description}
</submission_description>

Here is the code for the user's submission:
<submission_code>
{website_code}
</submission_code>

<validity_criteria>
A submission is **INVALID** if:
- It attempts to game or circumvent the task description (e.g., submitting something completely unrelated to the task)
- It contains offensive, inappropriate, or harmful content
- It is clearly a placeholder or empty submission with no real effort
- It violates basic ethical guidelines
A submission is **VALID** if it represents a genuine attempt to complete the task, even if the quality is low.
</validity_criteria>

<explanation_criteria>
- The explanation should be clear and helpful, explaining why the submission is valid or invalid
- The explanation can summarize what the user did well and what the user could improve on.
- Use a friendly, constructive, and honest tone. Do not be too critical or harsh. Similarly, do not be overly sycophantic or overly complimentary.
- The user you should read the explanation and have ideas of how they could improve their submission. The user's goal is to a win a competition where other users will vote on the submissions based on tsak fulfillment, style, enjoyment, and creativity.
- The explanation should be in plain English and without emojis.
- The explanation should be written in second person (e.g. "You", "your", "your submission", "your project", etc.).
- The explanation should be written in markdown format using bullet points that are concise and easy to read. Generate the markdown directly (no need for ```markdown or ```).
- Generate no more than three sentences total.
</explanation_criteria>

<format>
You must output a JSON object with the following structure:
{{
    "is_valid": <boolean>,
    "explanation": "<string explaining your evaluation, especially focusing on why is_valid is true or false>"
}}
Do not generate anything else.
</format>
"""
    
    # JSON schema for structured output
    json_schema = {
        "type": "object",
        "properties": {
            "is_valid": {
                "type": "boolean",
                "description": "Whether the submission is valid"
            },
            "explanation": {
                "type": "string",
                "description": "Explanation for the evaluation"
            }
        },
        "required": ["is_valid", "explanation"],
        "additionalProperties": False
    }
    
    # Retry logic similar to other LLM calls
    for attempt in range(3):
        try:
            response = litellm.completion(
                model=model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "submission_evaluation",
                        "schema": json_schema,
                        "strict": True
                    }
                }
            )
            
            output = response.choices[0].message.content.strip()
            # Parse JSON
            result = json.loads(output)
            
            # Validate structure
            if all(key in result for key in ["is_valid", "explanation"]):
                result["explanation"] = result["explanation"].replace("```markdown", "").replace("```", "")
                return result
            else:
                raise ValueError("Missing required fields in response")
                
        except Exception as e:
            print(f"Error in evaluation attempt {attempt + 1}: {e}")
            if attempt == 2:
                # Fallback: return default invalid response
                return {
                    "is_valid": False,
                    "explanation": f"Evaluation failed after 3 attempts. Error: {str(e)}"
                }
            import time
            time.sleep(0.7)
    
    # Should not reach here, but return default if it does
    return {
        "is_valid": False,
        "explanation": "Evaluation failed: Unable to process submission"
    }


@app.post("/api/submissions/evaluate", tags=["Submissions"])
async def evaluate_submission(
    payload: EvaluateSubmissionRequest,
    db: Session = Depends(get_db)
):
    """
    Evaluate a submission using LLM-as-a-judge on four dimensions.
    This endpoint evaluates submissions for non-required tasks or tasks past the study date.
    """
    try:
        # Verify user exists
        user = db.query(User).filter(User.id == payload.user_id).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})

        # Verify project exists
        project = db.query(Project).filter(Project.id == payload.project_id).first()
        if not project:
            return JSONResponse(status_code=404, content={"error": "Project not found"})

        # Evaluate the submission
        evaluation_result = await _evaluate_submission(
            submission_title=payload.submission_title,
            submission_description=payload.submission_description,
            submission_code=payload.submission_code,
            task_description=_resolve_task_description(
                project.name,
                fallback_description=project.description or "",
            ),
            task_name=project.name
        )

        # Save evaluation to database (lazy: set scores to 0 for backward compatibility)
        evaluation_data_for_db = {
            **evaluation_result,
            "task_fulfillment": 0,
            "style": 0,
            "enjoyment": 0,
            "creativity": 0,
        }
        evaluation_create = SubmissionEvaluationCreate(
            user_id=payload.user_id,
            project_id=payload.project_id,
            submission_id=None,  # Will be linked when submission is created
            evaluation_data=evaluation_data_for_db,
            is_valid=evaluation_result.get("is_valid", False)
        )
        
        evaluation_record = SubmissionEvaluationCRUD.create(db, evaluation_create)

        return {
            "success": True,
            "evaluation": evaluation_result,
            "evaluation_id": evaluation_record.id
        }

    except Exception as e:
        print(f"Error evaluating submission: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to evaluate submission: {str(e)}"}
        )


@app.get("/api/users/{user_id}/submission-feedback", tags=["Submissions"])
async def list_user_submission_feedback(
    user_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})

        feedback_entries = SubmissionFeedbackCRUD.get_by_voter(db, user_id, skip=skip, limit=limit)
        return [SubmissionFeedbackModel.from_orm(entry) for entry in feedback_entries]
    except Exception as e:
        print(f"Error listing user submission feedback: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to list submission feedback"})


@app.get("/api/users/{user_id}/submissions", tags=["Submissions"])
async def list_user_submissions(
    user_id: int,
    project_id: Optional[int] = Query(default=None, alias="projectId"),
    db: Session = Depends(get_db),
):
    """Get user's submissions. Returns only the most recent submission per project."""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})

        # Get all submissions for this user
        query = db.query(Submission).filter(Submission.user_id == user_id)
        
        if project_id is not None:
            query = query.filter(Submission.project_id == project_id)
        
        # Order by created_at descending to get most recent first
        all_submissions = query.order_by(Submission.created_at.desc()).all()
        
        # Group by project_id and keep only the most recent for each project
        most_recent_by_project: Dict[int, Submission] = {}
        for submission in all_submissions:
            if submission.project_id not in most_recent_by_project:
                most_recent_by_project[submission.project_id] = submission
        
        # Convert to response format
        response: List[Dict[str, Any]] = []
        for submission in most_recent_by_project.values():
            # Get rating summary for this submission
            feedback_entries = (
                db.query(SubmissionFeedback)
                .filter(SubmissionFeedback.submission_id == submission.id)
                .order_by(SubmissionFeedback.created_at.desc())
                .all()
            )
            rating_summary = build_rating_summary(feedback_entries)
            
            response.append({
                "id": submission.id,
                "title": submission.title,
                "description": submission.description,
                "image": submission.image,
                "projectId": submission.project_id,
                "userId": submission.user_id,
                "createdAt": submission.created_at.isoformat() if submission.created_at else None,
                "updatedAt": submission.updated_at.isoformat() if submission.updated_at else None,
                "code": submission.code or {},
                "ratingSummary": rating_summary,
            })
        
        # Sort by created_at descending (most recent first)
        response.sort(key=lambda x: x["createdAt"] or "", reverse=True)
        
        return {"items": response, "count": len(response)}
    except Exception as e:
        print(f"Error listing user submissions: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to list user submissions"})


@app.get("/api/users/{user_id}/submissions/check", tags=["Submissions"])
async def check_user_submission(
    user_id: int,
    project_id: Optional[int] = Query(default=None, alias="projectId"),
    task_id: Optional[str] = Query(default=None, alias="taskId"),
    db: Session = Depends(get_db),
):
    """Check if user has an existing submission for a project. Returns the most recent submission if exists."""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})

        # Always resolve project from task_id if provided (unified approach)
        project = None
        if task_id:
            project = _resolve_project_from_task_id(db, task_id)
        elif project_id is not None:
            project = db.query(Project).filter(Project.id == project_id).first()

        if project is None:
            return JSONResponse(status_code=404, content={"error": "Project not found"})

        # Get the most recent submission for this user and project
        existing_submission = (
            db.query(Submission)
            .filter(
                Submission.user_id == user_id,
                Submission.project_id == project.id
            )
            .order_by(Submission.created_at.desc())
            .first()
        )

        if existing_submission:
            return {
                "exists": True,
                "submission": {
                    "id": existing_submission.id,
                    "title": existing_submission.title,
                    "description": existing_submission.description,
                    "createdAt": existing_submission.created_at.isoformat() if existing_submission.created_at else None,
                }
            }
        else:
            return {"exists": False, "submission": None}
    except Exception as e:
        print(f"Error checking user submission: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to check user submission"})


@app.get("/api/users/{user_id}/stats", tags=["Users"])
async def get_user_stats(
    user_id: int,
    db: Session = Depends(get_db),
):
    """Get comprehensive statistics for a user including AI usage, skill check performance, and comprehension scores"""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": "User not found"})

        # AI Statistics
        assistant_logs = db.query(AssistantLog).filter(AssistantLog.user_id == user_id).all()
        num_prompts = len(assistant_logs)
        
        # Calculate lines generated from assistant logs
        total_lines = 0
        for log in assistant_logs:
            if log.generated_code:
                # generated_code is a JSON dict, count lines in all values
                for key, value in log.generated_code.items():
                    if isinstance(value, dict) and "content" in value:
                        content = value["content"]
                        if isinstance(content, str):
                            total_lines += len(content.splitlines())
                    elif isinstance(value, str):
                        total_lines += len(value.splitlines())
        
        # Count LLM ideas used (from CodePreference where user_selection is not null)
        llm_ideas_used = db.query(CodePreference).filter(
            CodePreference.user_id == user_id,
            CodePreference.user_selection.isnot(None)
        ).count()

        # MCQA Accuracy over time (frontend and ux) with frontend topic breakdown
        mcqa_responses = db.query(UserMCQASkillResponse).filter(
            UserMCQASkillResponse.user_id == user_id,
            UserMCQASkillResponse.question_type.in_(['frontend', 'ux'])
        ).order_by(UserMCQASkillResponse.created_at.asc()).all()
        
        # Helper to map MCQA question ids to frontend topics
        def get_mcqa_topic(question_id: str) -> str:
            qid = (question_id or "").lower()
            if "js" in qid or "javascript" in qid:
                return "js"
            if "css" in qid:
                return "css"
            if "html" in qid:
                return "html"
            return "other"

        def add_mcqa_point(target: list, responses: list, phase: str, topic: str):
            if not responses:
                return
            correct = sum(1 for r in responses if r.correct)
            total = len(responses)
            target.append({
                "phase": phase,
                "question_category": topic,
                "accuracy": correct / total if total > 0 else 0,
                "correct": correct,
                "total": total,
                "timestamp": responses[0].created_at.isoformat() if responses[0].created_at else None
            })
        
        # Group by phase and calculate accuracy
        mcqa_frontend_data = []
        mcqa_ux_data = []
        
        for phase in ['pre-test', 'post-test']:
            phase_responses = [r for r in mcqa_responses if r.phase == phase]
            if phase_responses:
                frontend_responses = [r for r in phase_responses if r.question_type == 'frontend']
                ux_responses = [r for r in phase_responses if r.question_type == 'ux']
                
                # Overall frontend accuracy plus topic-level breakdowns
                add_mcqa_point(mcqa_frontend_data, frontend_responses, phase, "all")
                add_mcqa_point(
                    mcqa_frontend_data,
                    [r for r in frontend_responses if get_mcqa_topic(r.question_id) == "html"],
                    phase,
                    "html"
                )
                add_mcqa_point(
                    mcqa_frontend_data,
                    [r for r in frontend_responses if get_mcqa_topic(r.question_id) == "css"],
                    phase,
                    "css"
                )
                add_mcqa_point(
                    mcqa_frontend_data,
                    [r for r in frontend_responses if get_mcqa_topic(r.question_id) == "js"],
                    phase,
                    "js"
                )
                
                if ux_responses:
                    correct = sum(1 for r in ux_responses if r.correct)
                    total = len(ux_responses)
                    mcqa_ux_data.append({
                        "phase": phase,
                        "accuracy": correct / total if total > 0 else 0,
                        "correct": correct,
                        "total": total,
                        "timestamp": ux_responses[0].created_at.isoformat() if ux_responses[0].created_at else None
                    })
        
        # Also include retake phases
        retake_phases = set(r.phase for r in mcqa_responses if r.phase and r.phase.startswith('retake_'))
        for retake_phase in retake_phases:
            phase_responses = [r for r in mcqa_responses if r.phase == retake_phase]
            if phase_responses:
                frontend_responses = [r for r in phase_responses if r.question_type == 'frontend']
                ux_responses = [r for r in phase_responses if r.question_type == 'ux']
                
                # Overall frontend accuracy plus topic-level breakdowns
                add_mcqa_point(mcqa_frontend_data, frontend_responses, retake_phase, "all")
                add_mcqa_point(
                    mcqa_frontend_data,
                    [r for r in frontend_responses if get_mcqa_topic(r.question_id) == "html"],
                    retake_phase,
                    "html"
                )
                add_mcqa_point(
                    mcqa_frontend_data,
                    [r for r in frontend_responses if get_mcqa_topic(r.question_id) == "css"],
                    retake_phase,
                    "css"
                )
                add_mcqa_point(
                    mcqa_frontend_data,
                    [r for r in frontend_responses if get_mcqa_topic(r.question_id) == "js"],
                    retake_phase,
                    "js"
                )
                
                if ux_responses:
                    correct = sum(1 for r in ux_responses if r.correct)
                    total = len(ux_responses)
                    mcqa_ux_data.append({
                        "phase": retake_phase,
                        "accuracy": correct / total if total > 0 else 0,
                        "correct": correct,
                        "total": total,
                        "timestamp": ux_responses[0].created_at.isoformat() if ux_responses[0].created_at else None
                    })

        # Coding accuracy and speed (from scratch and debug)
        code_responses = db.query(UserCodeSkillResponse).filter(
            UserCodeSkillResponse.user_id == user_id
        ).order_by(UserCodeSkillResponse.created_at.asc()).all()
        
        # Log all coding responses for debugging
        print(f"\n=== CODING RESPONSES FOR USER {user_id} ===")
        print(f"Total responses: {len(code_responses)}")
        for i, resp in enumerate(code_responses, 1):
            print(f"{i}. question_id={resp.question_id}, question_type={resp.question_type}, phase={resp.phase}, state={resp.state}, created_at={resp.created_at}")
        print("=== END CODING RESPONSES ===\n")
        
        # Group by question_id and phase to calculate tries and time per question
        # Then aggregate by phase and question_type
        coding_normal_data = []
        coding_debug_data = []
        
        # Helper function to normalize phase
        def normalize_phase(phase: str) -> str:
            if phase == "pre-test":
                return "pre"
            elif phase == "post-test":
                return "post"
            # Keep retake phases as-is (they'll show as "R{uuid}" in the frontend)
            return phase or 'none'
        
        # Group responses by question_id, normalized phase, and question_type
        # Using normalized phase from the start ensures proper deduplication of unique questions
        # Structure: {(question_id, normalized_phase, normalized_question_type): [responses]}
        question_groups: Dict[tuple, List[UserCodeSkillResponse]] = {}
        for response in code_responses:
            # Normalize phase and question_type immediately to ensure proper grouping
            normalized_phase = normalize_phase(response.phase)
            # Simple check: if it's "debug" (case-insensitive), it's debug, otherwise it's "from scratch" (normal)
            question_type_lower = (response.question_type or '').lower().strip()
            normalized_question_type = 'debug' if question_type_lower == 'debug' else 'normal'
            # Use normalized values for grouping to ensure questions are properly deduplicated
            key = (response.question_id, normalized_phase, normalized_question_type)
            if key not in question_groups:
                question_groups[key] = []
            question_groups[key].append(response)
        
        # Calculate metrics per individual question
        # Return individual question-level data instead of aggregated data
        coding_normal_data = []
        coding_debug_data = []
        coding_combined_data = []
        
        for (question_id, normalized_phase, normalized_question_type), responses in question_groups.items():
            # Values are already normalized from the grouping step above
            
            # Sort by created_at to find first started and first passed
            sorted_responses = sorted(
                [r for r in responses if r.created_at],
                key=lambda r: r.created_at
            )
            
            started_responses = [r for r in sorted_responses if r.state == 'started']
            passed_responses = [r for r in sorted_responses if r.state == 'passed']
            reported_responses = [r for r in sorted_responses if r.state == 'reported']
            failed_responses = [r for r in sorted_responses if r.state == 'failed']
            
            # A question is "attempted" if it has a passed, failed, or reported response
            # "started" alone doesn't count as an attempt since no code was submitted
            has_attempt = bool(passed_responses or reported_responses or failed_responses)
            if not has_attempt:
                continue  # Skip questions that were only started but never submitted
            
            # Calculate pass rate for this specific question
            passed = 1 if passed_responses else 0
            score = 1.0 if passed else 0.0
            
            # Calculate time taken (only if question was passed)
            time_taken_seconds = 0.0
            timestamp = None
            
            if passed_responses:
                # Question was passed - use passed timestamp and calculate time
                first_started = started_responses[0] if started_responses else None
                first_passed = passed_responses[0]
                
                if first_started and first_started.created_at and first_passed.created_at:
                    time_taken_seconds = (first_passed.created_at - first_started.created_at).total_seconds()
                    timestamp = first_passed.created_at.isoformat()
            else:
                # Question was not passed - use timestamp from any available response for plotting
                # Try in order: reported, failed, started (any of these will work for plotting)
                first_attempt = None
                if reported_responses and reported_responses[0].created_at:
                    first_attempt = reported_responses[0]
                elif failed_responses and failed_responses[0].created_at:
                    first_attempt = failed_responses[0]
                elif started_responses and started_responses[0].created_at:
                    first_attempt = started_responses[0]
                
                if first_attempt and first_attempt.created_at:
                    timestamp = first_attempt.created_at.isoformat()
            
            # Create data point for this individual question
            data_point = {
                "name": question_id,
                "score": score,
                "test_project_id": normalized_phase,  # 'pre', 'post', or 'retake_{uuid}'
                "time_taken_seconds": time_taken_seconds,
                "timestamp": timestamp or ""
            }
            
            # Add to appropriate list based on question_type
            if normalized_question_type == 'debug':
                coding_debug_data.append(data_point)
            else:
                # All other values (including 'normal') go to 'normal' (from scratch)
                coding_normal_data.append(data_point)
            
            # Also add to combined (all questions regardless of type)
            coding_combined_data.append(data_point)

        # Comprehension scores
        comprehension_questions = db.query(ComprehensionQuestion).filter(
            ComprehensionQuestion.user_id == user_id
        ).all()
        
        # Separate MCQA and multi-select questions
        mcqa_questions = [q for q in comprehension_questions if q.question_type in ('mcqa', 'code_compare') and q.user_answer is not None]
        multi_select_questions = [q for q in comprehension_questions if q.question_type == 'multi_select' and q.score is not None]
        
        # For MCQA, extract choice number (1-5) from user_answer instead of using score
        import re
        mcqa_choice_values = []
        for q in mcqa_questions:
            user_answer_str = str(q.user_answer) if q.user_answer else ""
            match = re.match(r'^(\d+)', user_answer_str.strip())
            if match:
                choice_num = int(match.group(1))
                if 1 <= choice_num <= 5:
                    mcqa_choice_values.append(choice_num)
        
        # Calculate average MCQA as choice number (1-5), then convert to percentage out of 5
        avg_mcqa_choice = sum(mcqa_choice_values) / len(mcqa_choice_values) if mcqa_choice_values else None
        avg_mcqa_score = (avg_mcqa_choice / 5.0) if avg_mcqa_choice is not None else None
        
        avg_multi_select_score = sum(q.score for q in multi_select_questions) / len(multi_select_questions) if multi_select_questions else None
        
        # Group comprehension scores by project
        project_mcqa_scores = defaultdict(list)
        project_multi_select_scores = defaultdict(list)
        project_names = {}
        
        # Fetch project names
        project_ids = set()
        for q in comprehension_questions:
            if q.project_id:
                project_ids.add(q.project_id)
        
        if project_ids:
            projects = db.query(Project).filter(Project.id.in_(project_ids)).all()
            for p in projects:
                project_names[p.id] = p.name or p.title or f"Project {p.id}"
        
        # Group MCQA questions by project
        for q in mcqa_questions:
            if q.project_id and q.user_answer:
                user_answer_str = str(q.user_answer) if q.user_answer else ""
                match = re.match(r'^(\d+)', user_answer_str.strip())
                if match:
                    choice_num = int(match.group(1))
                    if 1 <= choice_num <= 5:
                        project_mcqa_scores[q.project_id].append(choice_num)
        
        # Group multi-select questions by project
        for q in multi_select_questions:
            if q.project_id and q.score is not None:
                project_multi_select_scores[q.project_id].append(q.score)
        
        # Get submission order for projects (chronological order of first submission per project)
        submissions = db.query(Submission).filter(
            Submission.user_id == user_id
        ).order_by(Submission.created_at.asc()).all()
        
        # Create ordered list of project_ids based on first submission time
        project_order = []
        seen_projects = set()
        for submission in submissions:
            if submission.project_id and submission.project_id not in seen_projects:
                project_order.append(submission.project_id)
                seen_projects.add(submission.project_id)
        
        # Build per-project data
        all_project_ids = set(project_mcqa_scores.keys()) | set(project_multi_select_scores.keys())
        
        # Sort projects: first by submission order, then by project_id for any not in submissions
        def get_project_order(project_id):
            if project_id in project_order:
                return project_order.index(project_id)
            # Projects not in submissions go to the end, sorted by ID
            return len(project_order) + project_id
        
        sorted_project_ids = sorted(all_project_ids, key=get_project_order)
        
        per_project_scores = []
        for project_id in sorted_project_ids:
            project_name = project_names.get(project_id, f"Project {project_id}")
            mcqa_values = project_mcqa_scores.get(project_id, [])
            multi_select_values = project_multi_select_scores.get(project_id, [])
            
            avg_mcqa_choice_project = sum(mcqa_values) / len(mcqa_values) if mcqa_values else None
            avg_mcqa_score_project = (avg_mcqa_choice_project / 5.0) if avg_mcqa_choice_project is not None else None
            avg_multi_select_score_project = sum(multi_select_values) / len(multi_select_values) if multi_select_values else None
            
            per_project_scores.append({
                "project_id": project_id,
                "project_name": project_name,
                "avg_mcqa": avg_mcqa_score_project,
                "avg_multi_select": avg_multi_select_score_project,
                "mcqa_count": len(mcqa_values),
                "multi_select_count": len(multi_select_values)
            })

        return {
            "ai_stats": {
                "num_prompts": num_prompts,
                "total_lines_generated": total_lines,
                "llm_ideas_used": llm_ideas_used
            },
            "mcqa_accuracy": {
                "frontend": mcqa_frontend_data,
                "ux": mcqa_ux_data
            },
            "coding_performance": {
                "from_scratch": coding_normal_data,
                "debug": coding_debug_data,
                "combined": coding_combined_data
            },
            "comprehension_scores": {
                "avg_mcqa": avg_mcqa_score,
                "avg_multi_select": avg_multi_select_score,
                "mcqa_count": len(mcqa_choice_values),
                "multi_select_count": len(multi_select_questions),
                "per_project": per_project_scores
            }
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": f"Failed to get user stats: {str(e)}"})


def _project_is_open_ended_game_dev(project: Project) -> bool:
    """Matches StatsPage open-ended game dev tasks (Platformer + Browse open-ended; excludes website recreation)."""
    name = (project.name or "").lower()
    if name == "playground":
        return False
    lab = (project.label or "").lower().replace("_", "-")
    if lab == "website-requirements":
        return False
    website_req_names = frozenset(
        {
            "website_tutorial_intro",
            "zic_zac_zoe",
            "website_tutorial_follow_up",
            "zic_zac_zoe_follow_up",
        }
    )
    if name in website_req_names:
        return False
    if name == "platformer":
        return True
    effective = (project.label or "open-ended").lower().replace("_", "-")
    return effective == "open-ended"


WEBSITE_REQUIREMENT_TASK_NAMES = frozenset(
    {
        "website_tutorial_intro",
        "zic_zac_zoe",
        "website_tutorial_follow_up",
        "zic_zac_zoe_follow_up",
    }
)
GAME_REQUIRED_TASK_NAMES = frozenset({"platformer"})


def _is_website_requirement_project(project: Project) -> bool:
    label = (project.label or "").lower().replace("_", "-")
    if label == "website-requirements":
        return True
    return (project.name or "") in WEBSITE_REQUIREMENT_TASK_NAMES


def _is_additional_open_ended_task(project: Project) -> bool:
    """Open-ended tasks that count toward Stage 3 pay (excludes website recreation and Platformer)."""
    name = (project.name or "").lower()
    if name == "playground":
        return False
    if name in WEBSITE_REQUIREMENT_TASK_NAMES or name in GAME_REQUIRED_TASK_NAMES:
        return False
    if _is_website_requirement_project(project):
        return False
    effective = (project.label or "open-ended").lower().replace("_", "-")
    return effective == "open-ended"


def _get_user_ai_stats(user_id: int, db: Session) -> Dict[str, Any]:
  assistant_logs = db.query(AssistantLog).filter(AssistantLog.user_id == user_id).all()
  num_prompts = len(assistant_logs)
  total_lines = 0
  for log in assistant_logs:
    if log.generated_code:
      for key, value in log.generated_code.items():
        if isinstance(value, dict) and "content" in value:
          content = value["content"]
          if isinstance(content, str):
            total_lines += len(content.splitlines())
        elif isinstance(value, str):
          total_lines += len(value.splitlines())
  llm_ideas_used = db.query(CodePreference).filter(
    CodePreference.user_id == user_id,
    CodePreference.user_selection.isnot(None),
  ).count()
  return {
    "num_prompts": num_prompts,
    "total_lines_generated": total_lines,
    "llm_ideas_used": llm_ideas_used,
  }


def _compute_compensation_user_progress(user_id: int, db: Session) -> Dict[str, Any]:
  """Per-user task progress and submission aggregates for the compensation page."""
  all_submissions = (
    db.query(Submission)
    .filter(Submission.user_id == user_id)
    .order_by(Submission.created_at.desc())
    .all()
  )
  most_recent_by_project: Dict[int, Submission] = {}
  for submission in all_submissions:
    if submission.project_id not in most_recent_by_project:
      most_recent_by_project[submission.project_id] = submission

  submission_ids = [s.id for s in most_recent_by_project.values()]
  feedback_by_submission: Dict[int, List[SubmissionFeedback]] = defaultdict(list)
  if submission_ids:
    all_feedback = (
      db.query(SubmissionFeedback)
      .filter(SubmissionFeedback.submission_id.in_(submission_ids))
      .order_by(SubmissionFeedback.created_at.desc())
      .all()
    )
    for entry in all_feedback:
      feedback_by_submission[entry.submission_id].append(entry)

  total_rating = 0.0
  rating_count = 0
  total_votes = 0
  for submission in most_recent_by_project.values():
    rating_summary = build_rating_summary(feedback_by_submission.get(submission.id, []))
    if rating_summary.get("average"):
      total_rating += rating_summary["average"]
      rating_count += 1
    total_votes += int(rating_summary.get("count") or 0)

  projects = db.query(Project).all()
  project_by_id = {p.id: p for p in projects}

  completed_task_names: set = set()
  submitted_game_task_names: set = set()
  open_ended_game_dev_submission_count = 0

  for project_id in most_recent_by_project:
    project = project_by_id.get(project_id)
    if not project or not project.name:
      continue
    if _slugify(project.name) == "playground":
      continue
    completed_task_names.add(project.name)
    if not _is_website_requirement_project(project):
      submitted_game_task_names.add(project.name.lower())
    if _project_is_open_ended_game_dev(project):
      open_ended_game_dev_submission_count += 1

  additional_task_names = [
    p.name
    for p in projects
    if p.name and _is_additional_open_ended_task(p)
  ]

  completed_required_tasks = sum(
    1 for name in WEBSITE_REQUIREMENT_TASK_NAMES if name in completed_task_names
  )
  completed_game_required_tasks = sum(
    1 for name in GAME_REQUIRED_TASK_NAMES if name in completed_task_names
  )
  completed_additional_website_tasks = sum(
    1 for name in additional_task_names if name in completed_task_names
  )

  return {
    "total_submissions": len(most_recent_by_project),
    "average_rating": total_rating / rating_count if rating_count > 0 else 0.0,
    "num_votes": total_votes,
    "open_ended_game_dev_submission_count": open_ended_game_dev_submission_count,
    "completed_required_tasks": completed_required_tasks,
    "completed_game_required_tasks": completed_game_required_tasks,
    "submitted_game_task_count": len(submitted_game_task_names),
    "platformer_submitted": "platformer" in submitted_game_task_names,
    "completed_additional_website_tasks": completed_additional_website_tasks,
    "total_additional_website_tasks": len(additional_task_names),
  }


def _compute_compensation_study_status(user_id: int, db: Session) -> Dict[str, Any]:
  """Study-wide pool status (slower; loaded after user progress on the compensation page)."""
  stage3_estimate = _compute_stage3_compensation_estimate(user_id, db)
  post_test_pool_status = _compute_post_test_pool_status(user_id, db)
  return {
    "stage3_estimate": stage3_estimate,
    "post_test_pool_status": post_test_pool_status,
    "study_wide_open_ended_submissions": stage3_estimate.get("open_ended_submissions_study_wide"),
    "study_wide_post_test_completions_count": post_test_pool_status.get("eligible_users_count"),
  }


@app.get("/api/users/{user_id}/compensation-summary", tags=["Study"])
async def get_compensation_summary(user_id: int, db: Session = Depends(get_db)):
  """Fast compensation payload: per-user progress, aggregates, and AI usage."""
  user = db.query(User).filter(User.id == user_id).first()
  if not user:
    return JSONResponse(status_code=404, content={"error": "User not found"})
  try:
    progress = _compute_compensation_user_progress(user_id, db)
    ai_stats = _get_user_ai_stats(user_id, db)
    assignment = (
      db.query(SkillCheckAssignment).filter(SkillCheckAssignment.user_id == user_id).first()
    )
    pre_st = _get_completion_status_for_phase(user_id, "pre-test", assignment, db)
    post_st = _get_completion_status_for_phase(user_id, "post-test", assignment, db)
    return {
      **progress,
      "ai_stats": ai_stats,
      "pre_test_completed": bool(pre_st.get("completed")),
      "post_test_completed": bool(post_st.get("completed")),
    }
  except Exception as e:
    print(f"Error computing compensation summary: {e}")
    import traceback
    traceback.print_exc()
    return JSONResponse(
      status_code=500, content={"error": "Failed to compute compensation summary"}
    )


@app.get("/api/users/{user_id}/compensation-study-status", tags=["Study"])
async def get_compensation_study_status(user_id: int, db: Session = Depends(get_db)):
  """Study-wide compensation pool status (stage 3 pay pool, post-test cap)."""
  user = db.query(User).filter(User.id == user_id).first()
  if not user:
    return JSONResponse(status_code=404, content={"error": "User not found"})
  try:
    return _compute_compensation_study_status(user_id, db)
  except Exception as e:
    print(f"Error computing compensation study status: {e}")
    import traceback
    traceback.print_exc()
    return JSONResponse(
      status_code=500, content={"error": "Failed to compute compensation study status"}
    )


@app.get("/api/study/submission-count", tags=["Study"])
async def get_study_submission_count(db: Session = Depends(get_db)):
    """Submission counts for compensation page: all vs open-ended game dev tasks only."""
    try:
        total = db.query(func.count(Submission.id)).scalar()
        total_int = int(total or 0)
        all_projects = db.query(Project).all()
        qualifying_ids = [p.id for p in all_projects if _project_is_open_ended_game_dev(p)]
        if not qualifying_ids:
            open_ended = 0
        else:
            # Match stage3 pay-pool ordering: disqualified rows are excluded from the queue
            open_ended = (
                db.query(func.count(Submission.id))
                .filter(Submission.project_id.in_(qualifying_ids))
                .filter(Submission.is_disqualified == False)
                .scalar()
            )
            open_ended = int(open_ended or 0)
        return {
            "total_submissions": total_int,
            "open_ended_game_submissions": open_ended,
        }
    except Exception as e:
        print(f"Error counting study submissions: {e}")
        return JSONResponse(
            status_code=500, content={"error": "Failed to count submissions"}
        )


@app.get("/api/study/post-test-completions-count", tags=["Study"])
async def get_post_test_completions_count(db: Session = Depends(get_db)):
    """Number of users who fully completed the post-test skill check (same rules as completion-status)."""
    try:
        assignments = db.query(SkillCheckAssignment).all()
        completed = 0
        for assignment in assignments:
            st = _get_completion_status_for_phase(
                assignment.user_id, "post-test", assignment, db
            )
            if st.get("completed"):
                completed += 1
        return {"post_test_completions_count": int(completed)}
    except Exception as e:
        print(f"Error counting post-test completions: {e}")
        return JSONResponse(
            status_code=500, content={"error": "Failed to count post-test completions"}
        )


def _compute_stage3_compensation_estimate(user_id: int, db: Session) -> Dict[str, Any]:
    """
    Stage 3: $15 per 3 eligible tasks, but only tasks whose user's *first* submission on that
    project falls within the first N open-ended game-dev submissions study-wide (by time).
    """
    cap = int(os.getenv("STAGE3_MAX_SUBMISSIONS_FOR_COMPENSATION", "500"))
    tasks_per_block = 3
    dollars_per_block = 15

    all_projects = db.query(Project).all()
    qualifying = [p for p in all_projects if _project_is_open_ended_game_dev(p)]
    qualifying_ids = [p.id for p in qualifying]
    if not qualifying_ids:
        return {
            "tasks_completed_total": 0,
            "tasks_in_pay_pool": 0,
            "tasks_outside_pool": 0,
            "reward_blocks": 0,
            "reward_dollars": 0,
            "open_ended_submissions_study_wide": 0,
            "pay_pool_cap": cap,
            "pool_closed": False,
        }

    subs = (
        db.query(Submission)
        .filter(Submission.project_id.in_(qualifying_ids))
        .filter(Submission.is_disqualified == False)
        .order_by(Submission.created_at.asc(), Submission.id.asc())
        .all()
    )

    open_ended_count = len(subs)
    pool_closed = open_ended_count >= cap

    first_rank: Dict[Tuple[int, int], int] = {}
    for rank, s in enumerate(subs, start=1):
        key = (s.user_id, s.project_id)
        if key not in first_rank:
            first_rank[key] = rank

    platformer_ids = [p.id for p in qualifying if (p.name or "").lower() == "platformer"]
    additional_ids = [p.id for p in qualifying if (p.name or "").lower() != "platformer"]

    user_submitted_pids = {pid for (uid, pid) in first_rank if uid == user_id}

    tasks_total = 0
    tasks_in_pool = 0
    for pid in platformer_ids:
        if pid not in user_submitted_pids:
            continue
        tasks_total += 1
        if first_rank.get((user_id, pid), cap + 1) <= cap:
            tasks_in_pool += 1
    for pid in additional_ids:
        if pid not in user_submitted_pids:
            continue
        tasks_total += 1
        if first_rank.get((user_id, pid), cap + 1) <= cap:
            tasks_in_pool += 1

    tasks_outside_pool = tasks_total - tasks_in_pool
    blocks = tasks_in_pool // tasks_per_block
    dollars = blocks * dollars_per_block

    return {
        "tasks_completed_total": tasks_total,
        "tasks_in_pay_pool": tasks_in_pool,
        "tasks_outside_pool": tasks_outside_pool,
        "reward_blocks": blocks,
        "reward_dollars": dollars,
        "open_ended_submissions_study_wide": open_ended_count,
        "pay_pool_cap": cap,
        "pool_closed": pool_closed,
    }


@app.get("/api/users/{user_id}/stage3-compensation-estimate", tags=["Study"])
async def get_stage3_compensation_estimate(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse(status_code=404, content={"error": "User not found"})
    try:
        return _compute_stage3_compensation_estimate(user_id, db)
    except Exception as e:
        print(f"Error computing stage3 estimate: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500, content={"error": "Failed to compute stage 3 estimate"}
        )


def _is_post_test_counting_project(p: Project) -> bool:
    """Non–website-recreation game tasks (same basis as post-test task-count in the app)."""
    name = (p.name or "").lower()
    if name == "playground":
        return False
    lab = (p.label or "").lower().replace("_", "-")
    if lab == "website-requirements":
        return False
    website_req_names = frozenset(
        {
            "website_tutorial_intro",
            "zic_zac_zoe",
            "website_tutorial_follow_up",
            "zic_zac_zoe_follow_up",
        }
    )
    return name not in website_req_names


def _compute_post_test_pool_status(user_id: int, db: Session) -> Dict[str, Any]:
    """
    Post-test cap is based on completion: first N users to *complete* the post-test (by completion time)
    get the reward and fill the pool. While fewer than N have completed, the post-test remains open for
    any eligible user (post_test_open=True). Once N have completed, no one new can take it.
    """
    cap = int(os.getenv("POST_TEST_PARTICIPANT_CAP", "50"))
    n_required = int(os.getenv("NUM_TASKS_REQUIRED_UNTIL_POSTTEST", "10"))

    assignment = (
        db.query(SkillCheckAssignment).filter(SkillCheckAssignment.user_id == user_id).first()
    )
    post_st = _get_completion_status_for_phase(user_id, "post-test", assignment, db)
    post_completed = bool(post_st.get("completed"))

    # Completion time per user: max(created_at) over all post-test responses (MCQA + code)
    mcqa_max = (
        db.query(UserMCQASkillResponse.user_id, func.max(UserMCQASkillResponse.created_at).label("ts"))
        .filter(UserMCQASkillResponse.phase == "post-test")
        .group_by(UserMCQASkillResponse.user_id)
        .all()
    )
    code_max = (
        db.query(UserCodeSkillResponse.user_id, func.max(UserCodeSkillResponse.created_at).label("ts"))
        .filter(UserCodeSkillResponse.phase == "post-test")
        .group_by(UserCodeSkillResponse.user_id)
        .all()
    )
    completion_ts_by_user: Dict[int, Any] = {}
    for uid, t in mcqa_max:
        if t:
            completion_ts_by_user[uid] = max(completion_ts_by_user.get(uid, t), t)
    for row in code_max:
        uid, t = row[0], row[1]
        if t:
            completion_ts_by_user[uid] = max(completion_ts_by_user.get(uid, t), t)

    # Build list of (user_id, completion_ts) for users who completed post-test; order by completion time
    assignments = db.query(SkillCheckAssignment).all()
    completed_with_time: List[tuple] = []
    for a in assignments:
        st = _get_completion_status_for_phase(a.user_id, "post-test", a, db)
        if not st.get("completed"):
            continue
        ts = completion_ts_by_user.get(a.user_id)
        if ts is None:
            continue
        completed_with_time.append((a.user_id, ts))
    completed_with_time.sort(key=lambda x: (x[1], x[0]))
    completions_count = len(completed_with_time)
    pool_user_ids = {uid for uid, _ in completed_with_time[:cap]}
    post_test_open = completions_count < cap

    # Eligibility to take post-test (task requirement)
    game_projects = [p for p in db.query(Project).all() if _is_post_test_counting_project(p)]
    game_pids = [p.id for p in game_projects]
    platformer_pids = frozenset(
        p.id for p in game_projects if (p.name or "").lower() == "platformer"
    )
    if not game_pids or not platformer_pids:
        return {
            "meets_task_requirement": False,
            "in_post_test_pool": False,
            "post_test_completed": post_completed,
            "participant_cap": cap,
            "pool_filled": not post_test_open,
            "post_test_open": post_test_open,
            "eligible_users_count": 0,
            "your_rank": next((i + 1 for i, (uid, _) in enumerate(completed_with_time) if uid == user_id), None),
        }

    subs = (
        db.query(Submission)
        .filter(Submission.project_id.in_(game_pids))
        .filter(Submission.is_disqualified == False)
        .order_by(Submission.created_at.asc(), Submission.id.asc())
        .all()
    )
    user_projects: Dict[int, set] = defaultdict(set)
    for s in subs:
        user_projects[s.user_id].add(s.project_id)
    meets = (
        user_id in user_projects
        and len(user_projects[user_id]) >= n_required
        and bool(user_projects[user_id] & platformer_pids)
    )

    # in_post_test_pool = user completed and is among first cap by completion time
    in_pool = post_completed and user_id in pool_user_ids

    return {
        "meets_task_requirement": meets,
        "in_post_test_pool": in_pool,
        "post_test_completed": post_completed,
        "participant_cap": cap,
        "pool_filled": not post_test_open,
        "post_test_open": post_test_open,
        "eligible_users_count": completions_count,
        "your_rank": next((i + 1 for i, (uid, _) in enumerate(completed_with_time) if uid == user_id), None),
    }


@app.get("/api/users/{user_id}/post-test-pool-status", tags=["Study"])
async def get_post_test_pool_status(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse(status_code=404, content={"error": "User not found"})
    try:
        return _compute_post_test_pool_status(user_id, db)
    except Exception as e:
        print(f"Error computing post-test pool status: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500, content={"error": "Failed to compute post-test pool status"}
        )


@app.get("/api/leaderboard", tags=["Submissions"])
async def get_leaderboard(db: Session = Depends(get_db)):
    """Get leaderboard with user rankings based on average rating and submission count"""
    try:
        # Optimization: Use batch queries instead of N+1 queries
        # Note: We could further optimize by selecting only specific columns, but:
        # 1. The biggest win is already achieved (3 queries vs N+1)
        # 2. build_rating_summary expects ORM objects, so we'd need refactoring
        # 3. Column selection would help with network/memory but database still reads rows
        # 4. The complexity isn't worth the marginal gain for this use case
        
        # 1. Get all users
        all_users = db.query(User).all()
        user_ids = [user.id for user in all_users]
        if not user_ids:
            return []
        
        # 2. Get all submissions in one query, grouped by user_id
        all_submissions = db.query(Submission).filter(Submission.user_id.in_(user_ids)).all()
        
        # Build lookup structures
        submissions_by_user: Dict[int, List[Submission]] = defaultdict(list)
        submission_ids = []
        for submission in all_submissions:
            submissions_by_user[submission.user_id].append(submission)
            submission_ids.append(submission.id)
        
        # Count unique projects per user using the pre-loaded submissions
        user_submission_counts: Dict[int, int] = {}
        for user_id, user_submissions in submissions_by_user.items():
            unique_projects = {sub.project_id for sub in user_submissions}
            user_submission_counts[user_id] = len(unique_projects)
        
        # 3. Get all feedback entries in one query (ordered by created_at desc for each submission)
        feedback_by_submission: Dict[int, List[SubmissionFeedback]] = defaultdict(list)
        if submission_ids:
            all_feedback = (
                db.query(SubmissionFeedback)
                .filter(SubmissionFeedback.submission_id.in_(submission_ids))
                .order_by(SubmissionFeedback.submission_id, SubmissionFeedback.created_at.desc())
                .all()
            )
            # Group feedback by submission_id (already ordered by created_at desc)
            for feedback in all_feedback:
                feedback_by_submission[feedback.submission_id].append(feedback)
        
        # 4. Get all skill check responses to count distinct phases per user
        skill_check_phases_by_user: Dict[int, set] = defaultdict(set)
        if user_ids:
            # Get MCQA skill check responses
            mcqa_responses = (
                db.query(UserMCQASkillResponse)
                .filter(UserMCQASkillResponse.user_id.in_(user_ids))
                .filter(UserMCQASkillResponse.phase.isnot(None))
                .all()
            )
            for response in mcqa_responses:
                skill_check_phases_by_user[response.user_id].add(response.phase)
            
            # Get code skill check responses
            code_responses = (
                db.query(UserCodeSkillResponse)
                .filter(UserCodeSkillResponse.user_id.in_(user_ids))
                .filter(UserCodeSkillResponse.phase.isnot(None))
                .all()
            )
            for response in code_responses:
                skill_check_phases_by_user[response.user_id].add(response.phase)
        
        # 5. Calculate stats for each user using pre-loaded data
        user_stats = []
        for user in all_users:
            user_submissions = submissions_by_user.get(user.id, [])
            submission_count = user_submission_counts.get(user.id, 0)
            
            # Calculate average rating across all submissions using pre-loaded feedback
            all_ratings = []
            for submission in user_submissions:
                feedback_entries = feedback_by_submission.get(submission.id, [])
                if feedback_entries:
                    # Use the same logic as build_rating_summary to get average
                    rating_summary = build_rating_summary(feedback_entries)
                    if rating_summary["average"] is not None:
                        all_ratings.append(rating_summary["average"])
            
            # Calculate overall average rating
            average_rating = sum(all_ratings) / len(all_ratings) if all_ratings else 0.0
            
            # Count distinct skill check phases (pre-test, post-test, retake sessions)
            skill_check_count = len(skill_check_phases_by_user.get(user.id, set()))
            
            user_stats.append({
                "user_id": user.id,
                "username": user.username,
                "average_rating": round(average_rating, 2),
                "submission_count": submission_count,
                "skill_check_count": skill_check_count,
            })
        
        # Calculate normalized ranks
        # Sort by average rating (descending) and submission count (descending)
        sorted_by_rating = sorted(user_stats, key=lambda x: x["average_rating"], reverse=True)
        sorted_by_submissions = sorted(user_stats, key=lambda x: x["submission_count"], reverse=True)
        
        # Create rank maps
        rating_ranks = {}
        for rank, stat in enumerate(sorted_by_rating, start=1):
            rating_ranks[stat["user_id"]] = rank
        
        submission_ranks = {}
        for rank, stat in enumerate(sorted_by_submissions, start=1):
            submission_ranks[stat["user_id"]] = rank
        
        # Calculate overall rank (average of normalized ranks)
        # Normalize ranks to 0-1 scale, then average
        max_rank = len(user_stats)
        for stat in user_stats:
            rating_rank = rating_ranks[stat["user_id"]]
            submission_rank = submission_ranks[stat["user_id"]]
            
            # Normalize to 0-1 (lower rank = better, so we invert)
            normalized_rating = 1.0 - (rating_rank - 1) / max_rank if max_rank > 0 else 0
            normalized_submission = 1.0 - (submission_rank - 1) / max_rank if max_rank > 0 else 0
            
            # Average the normalized ranks (higher is better)
            overall_score = (normalized_rating + normalized_submission) / 2.0
            stat["overall_score"] = overall_score
        
        # Sort by overall score (descending)
        sorted_leaderboard = sorted(user_stats, key=lambda x: x["overall_score"], reverse=True)
        
        # Add final rank
        for rank, stat in enumerate(sorted_leaderboard, start=1):
            stat["rank"] = rank
        
        return sorted_leaderboard
    except Exception as e:
        print(f"Error fetching leaderboard: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "Failed to fetch leaderboard"})


def _strip_html_to_text(html: Optional[str], max_len: Optional[int] = None) -> str:
    if not html:
        return ""
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    if max_len and len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _project_is_open_ended(p: Project) -> bool:
    """Match browse/stats: open-ended game tasks only (excludes replication / website-requirements)."""
    raw = (p.label or "open-ended").strip().lower().replace("_", "-")
    if raw == "website-requirements":
        raw = "replication"
    if raw != "open-ended":
        return False
    if _slugify(p.name) == "playground":
        return False
    return True


@app.get("/api/leaderboard/projects", tags=["Submissions"])
async def get_project_leaderboard(db: Session = Depends(get_db)):
    """Per-project stats for open-ended tasks only (excludes replication, playground)."""
    try:
        projects_all = db.query(Project).order_by(Project.name.asc()).all()
        projects = [p for p in projects_all if _project_is_open_ended(p)]
        if not projects:
            return []

        # Submissions (exclude disqualified)
        base_sub_q = db.query(Submission).filter(Submission.is_disqualified.is_(False))
        all_submissions = base_sub_q.all()
        if not all_submissions:
            submission_ids: List[int] = []
            by_project_subs: Dict[int, List[Submission]] = defaultdict(list)
        else:
            submission_ids = [s.id for s in all_submissions]
            by_project_subs = defaultdict(list)
            for s in all_submissions:
                by_project_subs[s.project_id].append(s)

        feedback_by_submission: Dict[int, List[SubmissionFeedback]] = defaultdict(list)
        if submission_ids:
            all_feedback = (
                db.query(SubmissionFeedback)
                .filter(SubmissionFeedback.submission_id.in_(submission_ids))
                .order_by(SubmissionFeedback.submission_id, SubmissionFeedback.created_at.desc())
                .all()
            )
            for fb in all_feedback:
                feedback_by_submission[fb.submission_id].append(fb)

        counts = (
            db.query(Submission.project_id, func.count(Submission.id))
            .filter(Submission.is_disqualified.is_(False))
            .group_by(Submission.project_id)
            .all()
        )
        count_by_pid = {pid: c for pid, c in counts}

        feedback_counts = (
            db.query(SubmissionFeedback.project_id, func.count(SubmissionFeedback.id))
            .group_by(SubmissionFeedback.project_id)
            .all()
        )
        votes_by_pid = {pid: n for pid, n in feedback_counts}

        out = []
        for p in projects:
            subs = by_project_subs.get(p.id, [])
            ratings: List[float] = []
            for sub in subs:
                entries = feedback_by_submission.get(sub.id, [])
                summary = build_rating_summary(entries)
                if summary.get("average") is not None:
                    ratings.append(float(summary["average"]))

            avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None
            best_score = round(max(ratings), 2) if ratings else None
            desc_plain = _strip_html_to_text(p.description or "", max_len=400)

            def _fmt_date(d) -> Optional[str]:
                if d is None:
                    return None
                return d.isoformat() if hasattr(d, "isoformat") else str(d)

            out.append(
                {
                    "project_id": p.id,
                    "task_slug": p.name,
                    # Same as tasks-db "id" — use this for /vibe?task=… (raw name can differ, e.g. snake_game vs snake-game)
                    "task_route_id": _slugify(p.name),
                    "title": p.title or p.name,
                    "description_preview": desc_plain,
                    "submission_count": int(count_by_pid.get(p.id, 0)),
                    "total_vote_records": int(votes_by_pid.get(p.id, 0)),
                    "rated_submissions": len(ratings),
                    "average_rating": avg_rating,
                    "best_score": best_score,
                    "voting_start_date": _fmt_date(p.voting_start_date),
                    "voting_end_date": _fmt_date(p.voting_end_date),
                }
            )

        out.sort(key=lambda x: (-x["submission_count"], x["task_slug"].lower()))
        return out
    except Exception as e:
        print(f"Error fetching project leaderboard: {e}")
        import traceback

        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "Failed to fetch project leaderboard"})


@app.post("/api/execute-endpoint", tags=["Code Execution"])
async def execute_endpoint(request_data: dict):
    """Execute Python code and optionally call a specific endpoint function using RapidAPI OneCompiler."""
    try:
        python_code = request_data.get("pythonCode", "")
        endpoint_name = request_data.get("endpoint", "")
        user_args = request_data.get("args", {})

        if not python_code:
            return JSONResponse(status_code=400, content={"error": "No Python code provided"})
        
        # Check if RapidAPI key is configured
        if not rapidapi_key:
            return JSONResponse(status_code=500, content={
                "success": False,
                "error": "RapidAPI key not configured. Set RAPIDAPI_KEY environment variable.",
                "error_type": "configuration_error"
            })
        
        # If no endpoint specified, just return the available endpoints
        if not endpoint_name:
            try:
                endpoints = endpoint_parser.parse_to_dict(python_code)
                return {
                    "success": True,
                    "endpoints": endpoints,
                    "count": len(endpoints),
                    "message": "Code parsed successfully"
                }
            except Exception as e:
                return JSONResponse(status_code=400, content={
                    "success": False,
                    "error": str(e),
                    "error_type": "parsing_error"
                })
        
        # Execute the specific endpoint using RapidAPI OneCompiler
        try:
            # Create a modified version of the code that can execute the endpoint function
            # We need to add the endpoint decorator logic and call the specific function
            
            # First, parse the endpoints to get the function info
            endpoints = endpoint_parser.parse_to_dict(python_code)
            
            # Find the function that matches the endpoint path
            function_name = None
            for ep in endpoints:
                if ep.get('endpoint') == endpoint_name or ep.get('name') == endpoint_name:
                    function_name = ep['name']
                    break
            
            if not function_name:
                return JSONResponse(status_code=400, content={
                    "success": False,
                    "error": f"Endpoint '{endpoint_name}' not found in the code. Available endpoints: {[ep.get('endpoint', ep.get('name')) for ep in endpoints]}",
                    "error_type": "endpoint_not_found"
                })
            
            # Create execution code that includes the endpoint decorator and calls the function
            # Use eval() + type casting approach for better argument handling
            user_args_str = repr(user_args) if user_args is not None else "{}"
            
            # Get function signature for type casting
            import ast
            
            # Parse the function to get its signature and type annotations
            try:
                tree = ast.parse(python_code)
                function_node = None
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef) and node.name == function_name:
                        function_node = node
                        break
                
                type_casting_code = ""
                if function_node and function_node.args.args:
                    type_casting_code = "# Type casting based on function annotations\n"
                    for arg in function_node.args.args:
                        arg_name = arg.arg
                        if arg.annotation:
                            # Extract type annotation
                            if isinstance(arg.annotation, ast.Name):
                                type_name = arg.annotation.id
                                type_casting_code += f"""
    if '{arg_name}' in evaluated_args:
        try:
            evaluated_args['{arg_name}'] = {type_name}(evaluated_args['{arg_name}'])
        except (ValueError, TypeError):
            pass  # Keep original value if casting fails
"""
            except Exception:
                type_casting_code = "# Type casting not available\n"
            
            execution_code = f"""
# Mock Flask objects
class MockRequest:
    def __init__(self, args):
        self.args = args
        self.form = args
    def get_json(self):
        return {user_args_str}

request = MockRequest({user_args_str})

def jsonify(data):
    return data

# Original code (using function annotations)
{python_code}

# Call the specific endpoint function with user arguments using eval()
try:
    # Use eval to properly handle each argument with correct types
    user_args_dict = eval({repr(user_args_str)})
    # Evaluate each argument individually to ensure proper type handling
    evaluated_args = {{}}
    for key, value in user_args_dict.items():
        # Try to evaluate the value directly, preserving its original type
        try:
            # If it's already a proper type, use it directly
            if isinstance(value, (int, float, bool, list, dict)) or value is None:
                evaluated_args[key] = value
            else:
                # For strings, try to evaluate them to get the proper type
                evaluated_args[key] = eval(str(value))
        except:
            # If evaluation fails, use the original value
            evaluated_args[key] = value
    
    {type_casting_code}
    
    result = {function_name}(**evaluated_args)
    print("ENDPOINT_RESULT:", result)
except Exception as e:
    print("ENDPOINT_ERROR:", str(e))
"""
            
            # Execute using RapidAPI OneCompiler
            result = await onecompiler_service.execute_python(execution_code)
            
            # Check for timeout or execution errors first
            if not result.get("success"):
                # Check if it's a timeout error (has stderr with timeout message)
                stderr = result.get("stderr", "")
                if stderr and "Timeout Error" in stderr:
                    return JSONResponse(status_code=500, content={
                        "success": False,
                        "error": stderr,
                        "error_type": "execution_error"
                    })
                # Otherwise return the error
                return JSONResponse(status_code=500, content={
                    "success": False,
                    "error": result.get('error', 'Unknown error'),
                    "error_type": "execution_error"
                })
            
            # Execution was successful
            stdout = result.get("stdout", "")
            
            # Parse the result from stdout
            if "ENDPOINT_RESULT:" in stdout:
                # Extract the result after ENDPOINT_RESULT:
                result_line = [line for line in stdout.split('\n') if 'ENDPOINT_RESULT:' in line]
                if result_line:
                    try:
                        # Try to parse the result as JSON
                        result_str = result_line[0].split('ENDPOINT_RESULT:', 1)[1].strip()
                        # Try JSON parsing first
                        import json
                        endpoint_result = json.loads(result_str)
                    except json.JSONDecodeError:
                        # If not valid JSON, try to parse as Python literal
                        try:
                            import ast
                            endpoint_result = ast.literal_eval(result_str)
                        except (ValueError, SyntaxError):
                            # If all parsing fails, return as string
                            endpoint_result = result_str
                    except Exception as parse_error:
                        # If parsing fails, return the raw string
                        endpoint_result = result_str
                else:
                    endpoint_result = stdout
            elif "ENDPOINT_ERROR:" in stdout:
                # Extract error from stdout
                error_line = [line for line in stdout.split('\n') if 'ENDPOINT_ERROR:' in line]
                if error_line:
                    error_msg = error_line[0].split('ENDPOINT_ERROR:', 1)[1].strip()
                    return JSONResponse(status_code=500, content={
                        "success": False,
                        "error": error_msg,
                        "error_type": "execution_error"
                    })
                else:
                    endpoint_result = stdout
            else:
                # If no specific markers found, return the full stdout
                endpoint_result = stdout
            
            return {
                "success": True,
                "result": endpoint_result,
                "endpoint": endpoint_name,
                "args_used": user_args,
                "execution_method": "rapidapi_onecompiler"
            }
                
        except Exception as e:
            return JSONResponse(status_code=500, content={
                "success": False,
                "error": str(e),
                "error_type": "execution_error"
            })
            
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "success": False,
            "error": str(e),
            "error_type": "unexpected_error"
        })

@app.post("/api/validate-python", tags=["Code Execution"])
async def validate_python(request_data: dict):
    """Validate Python code syntax using RapidAPI OneCompiler."""
    try:
        python_code = request_data.get("pythonCode", "")
        
        if not python_code:
            return JSONResponse(status_code=400, content={"error": "No Python code provided"})
        
        # Check if RapidAPI key is configured
        if not rapidapi_key:
            return JSONResponse(status_code=500, content={
                "success": False,
                "error": "RapidAPI key not configured. Set RAPIDAPI_KEY environment variable.",
                "error_type": "configuration_error"
            })
        
        # Use OneCompiler service for syntax validation
        result = await onecompiler_service.validate_python_syntax(python_code)
        
        if result.get("success"):
            return {
                "success": True,
                "message": result.get("message", "Python code is syntactically valid")
            }
        else:
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": result.get("error", "Syntax validation failed"),
                "line": result.get("line"),
                "offset": result.get("offset")
            })
        
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "success": False,
            "error": str(e)
        })

@app.post("/api/start-python-server")
async def start_python_server(request_data: dict):
    """Start a Python Flask server as a subprocess."""
    try:
        python_code = request_data.get("pythonCode", "")
        port = request_data.get("port", 5000)
        
        if not python_code:
            return JSONResponse(status_code=400, content={"error": "No Python code provided"})
        
        # Stop any existing server on this port
        if port in active_processes:
            await stop_python_server(port)
        
        # Create a temporary file for the Python code
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(python_code)
            temp_file_path = f.name
        
        try:
            # Start the Python subprocess
            process = subprocess.Popen(
                ["python3", temp_file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Store process info
            active_processes[port] = {
                "process": process,
                "temp_file": temp_file_path,
                "start_time": asyncio.get_event_loop().time(),
                "code": python_code
            }
            
            # Give the process a moment to start
            await asyncio.sleep(1)
            
            # Check if process is still running
            if process.poll() is None:
                return {
                    "success": True,
                    "port": port,
                    "processId": process.pid,
                    "message": f"Python server started on port {port}"
                }
            else:
                # Process died, get error output
                stdout, stderr = process.communicate()
                error_msg = stderr or stdout or "Process exited unexpectedly"
                return JSONResponse(
                    status_code=500, 
                    content={"error": f"Failed to start Python server: {error_msg}"}
                )
                
        except Exception as e:
            # Clean up temp file on error
            try:
                os.unlink(temp_file_path)
            except:
                pass
            return JSONResponse(status_code=500, content={"error": str(e)})
            
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/stop-python-server")
async def stop_python_server_endpoint(request_data: dict):
    """Stop a Python Flask server subprocess."""
    try:
        port = request_data.get("port")
        if port is None:
            return JSONResponse(status_code=400, content={"error": "No port specified"})
        
        success = await stop_python_server(port)
        if success:
            return {"success": True, "message": f"Server on port {port} stopped"}
        else:
            return JSONResponse(status_code=404, content={"error": f"No server found on port {port}"})
            
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

async def stop_python_server(port):
    """Helper function to stop a Python server process."""
    if port not in active_processes:
        return False
    
    process_info = active_processes[port]
    process = process_info["process"]
    temp_file = process_info["temp_file"]
    
    try:
        # Terminate the process
        if process.poll() is None:  # Process is still running
            process.terminate()
            
            # Wait for graceful termination
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate gracefully
                process.kill()
                process.wait()
        
        # Clean up temp file
        try:
            os.unlink(temp_file)
        except:
            pass
            
        # Remove from active processes
        del active_processes[port]
        
        return True
        
    except Exception as e:
        print(f"Error stopping server on port {port}: {e}")
        return False

@app.get("/api/list-python-servers")
async def list_python_servers():
    """List all active Python server processes."""
    servers = []
    for port, info in active_processes.items():
        process = info["process"]
        servers.append({
            "port": port,
            "processId": process.pid,
            "status": "running" if process.poll() is None else "stopped",
            "startTime": info["start_time"]
        })
    
    return {"servers": servers}

@app.post("/api/chat", tags=["Chat"])
async def chat_endpoint(request_data: dict):
    """REST API endpoint for non-streaming chat requests."""
    try:
        messages = request_data.get("messages", [])
        model = request_data.get("model", "gpt-4")
        max_tokens = request_data.get("max_tokens", 1000)
        proactive = request_data.get("proactive", False)
        current_code = request_data.get("current_code", "")
        
        # Prepare messages for AI
        if proactive and current_code:
            user_message = messages[-1] if messages else {"role": "user", "content": ""}
            enhanced_message = {
                "role": "user", 
                "content": f"Code:\n{current_code}\n\nMessage:\n{user_message.get('content', '')}"
            }
            messages_to_send = messages[:-1] + [enhanced_message]
        else:
            messages_to_send = messages
        
        # Get response from chat model
        response = await chat_model.stream_response(
            messages=messages_to_send,
            model=model,
            max_tokens=max_tokens,
            on_chunk=None,
            on_complete=None,
            on_error=None,
            current_code=current_code
        )
        
        # For autocomplete strategy, also return the generated code
        generated_code = ""
        if hasattr(chat_model.strategy, 'get_last_generated_code'):
            generated_code = chat_model.strategy.get_last_generated_code()
        
        return {
            "response": response,
            "generated_code": generated_code
        }
        
    except Exception as e:
        return {"error": str(e)}, 500


@app.get("/tasks/{task_name}", tags=["Tasks"])
async def get_task(task_name: str):
    try:
        backend_dir = os.path.dirname(__file__)
        repo_root = os.path.abspath(os.path.join(backend_dir, ".."))
        data_path = os.path.join(repo_root, "data", "tasks.json")
        if not os.path.exists(data_path):
            return JSONResponse(status_code=404, content={"error": "tasks.json not found"})

        with open(data_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        tasks = payload.get("tasks", [])
        task = next((t for t in tasks if t.get("name") == task_name), None)
        if not task:
            return JSONResponse(status_code=404, content={"error": f"Task '{task_name}' not found"})

        # Handle task description - if it's a file path, load the content
        task_description = _resolve_task_description(
            task.get("name"),
            fallback_description=task.get("description", ""),
        )
        if task_description.startswith("data/code_files/"):
            file_path = os.path.join(repo_root, task_description)
            try:
                if os.path.exists(file_path):
                    with open(file_path, "r", encoding="utf-8") as desc_file:
                        task_description = desc_file.read()
                    # Compute base relative path for assets (e.g., images) and convert to data URLs
                    base_rel_dir = os.path.dirname(task.get("description", ""))
                    # base_rel_dir like data/code_files/tictactoe_solution
                    if base_rel_dir:
                        import re
                        import base64
                        
                        # Replace src="..." with data URLs for images
                        def _repl_src(match):
                            url = match.group(1)
                            if url.startswith(('http://','https://','data:','/')):
                                return f'src="{url}"'
                            
                            # Try to load the image and convert to data URL
                            img_path = os.path.join(repo_root, base_rel_dir, url)
                            if os.path.exists(img_path):
                                try:
                                    with open(img_path, 'rb') as img_file:
                                        img_data = img_file.read()
                                        # Determine MIME type from extension
                                        mime_type = 'application/octet-stream'
                                        if url.lower().endswith('.png'):
                                            mime_type = 'image/png'
                                        elif url.lower().endswith(('.jpg', '.jpeg')):
                                            mime_type = 'image/jpeg'
                                        elif url.lower().endswith('.gif'):
                                            mime_type = 'image/gif'
                                        elif url.lower().endswith('.svg'):
                                            mime_type = 'image/svg+xml'
                                        
                                        data_url = f'data:{mime_type};base64,{base64.b64encode(img_data).decode()}'
                                        return f'src="{data_url}"'
                                except Exception as e:
                                    print(f"Error converting image to data URL: {e}")
                            
                            # Fallback to /assets/ URL if conversion fails
                            return f'src="/assets/{base_rel_dir.strip("/")}/{url}"'
                        
                        task_description = re.sub(r'src="([^"]+)"', _repl_src, task_description)
                else:
                    task_description = f"Description file not found: {file_path}"
            except Exception as e:
                task_description = f"Error reading description file: {str(e)}"
        
        # Update the task with the loaded description
        task["description"] = task_description

        # Handle tests - if it's a directory path (or array of paths), load all test files
        tests = task.get("tests", [])
        loaded_tests = []
        
        # Convert single string to array for uniform processing
        test_dirs = []
        if isinstance(tests, str) and tests.startswith("data/test_cases/"):
            test_dirs = [tests]
        elif isinstance(tests, list):
            test_dirs = [t for t in tests if isinstance(t, str) and t.startswith("data/test_cases/")]
        
        # Load tests from all directories
        for test_dir_path in test_dirs:
            test_dir = os.path.join(repo_root, test_dir_path)
            
            # Determine test type prefix from directory name
            test_type_prefix = ""
            if "/backend" in test_dir_path or test_dir_path.endswith("backend"):
                test_type_prefix = "Backend"
            elif "/frontend" in test_dir_path or test_dir_path.endswith("frontend"):
                test_type_prefix = "End-to-End"
            elif "/html" in test_dir_path or test_dir_path.endswith("html"):
                test_type_prefix = "HTML"
            
            try:
                if os.path.exists(test_dir) and os.path.isdir(test_dir):
                    # Load all .json test files in the test directory
                    for filename in sorted(os.listdir(test_dir)):
                        if filename.endswith('.json'):
                            test_file_path = os.path.join(test_dir, filename)
                            with open(test_file_path, 'r', encoding='utf-8') as test_file:
                                test_content = test_file.read()
                                
                            # Load JSON test file with prefix
                            test_cases_from_file = load_json_test_file(test_content, filename, test_type_prefix)
                            loaded_tests.extend(test_cases_from_file)
            except Exception as e:
                print(f"Error loading tests from {test_dir}: {e}")
        
        task["tests"] = loaded_tests

        files = []
        for fdef in task.get("files", []):
            content = fdef.get("content", "")
            
            # Check if content is a file path (starts with data/code_files/)
            if content.startswith("data/code_files/"):
                # Read content from file
                file_path = os.path.join(repo_root, content)
                try:
                    if os.path.exists(file_path):
                        with open(file_path, "r", encoding="utf-8") as content_file:
                            content = content_file.read()
                    else:
                        content = f"// File not found: {file_path}"
                except Exception as e:
                    content = f"// Error reading file: {str(e)}"
            
            files.append({
                "id": fdef.get("name"),
                "name": fdef.get("name"),
                "type": "file",
                "content": content,
                "language": fdef.get("language", "plaintext")
            })

        return {"task": task, "files": files}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/load-test-cases")
async def load_test_cases(request: dict):
    """
    Load test cases from task data.
    
    The task.tests field should contain an array of:
    - Directory paths (strings) - will load all JSON files from that directory
    - Test objects (dicts) - will be used directly
    
    Request body:
    {
        "task": {
            "tests": [
                "data/test_cases/tictactoe/backend",
                "data/test_cases/tictactoe/frontend",
                ...
            ]
        },
        "public_only": true/false  # Optional: filter for public tests only (default: true)
    }
    
    Returns:
    {
        "testCases": [
            {
                "title": "...",
                "tests": [...]
            }
        ]
    }
    """
    try:
        task = request.get("task", {})
        public_only = request.get("public_only", True)
        
        all_tests = []
        base_path = Path(__file__).parent.parent
        
        # Load tests from task.tests field
        if "tests" in task and isinstance(task["tests"], list):
            for test_entry in task["tests"]:
                # Check if it's a directory path (string)
                if isinstance(test_entry, str):
                    # Convert relative path to absolute
                    test_dir = base_path / test_entry
                    
                    if test_dir.exists() and test_dir.is_dir():
                        # Load all JSON files from this directory
                        for json_file in sorted(test_dir.glob("*.json")):
                            try:
                                with open(json_file, 'r') as f:
                                    tests_from_file = json.load(f)
                                    if isinstance(tests_from_file, list):
                                        all_tests.extend(tests_from_file)
                                    else:
                                        all_tests.append(tests_from_file)
                                print(f"✓ Loaded {len(tests_from_file) if isinstance(tests_from_file, list) else 1} tests from {json_file.name}")
                            except Exception as e:
                                print(f"✗ Error loading test file {json_file}: {e}")
                    else:
                        print(f"⚠ Test directory not found: {test_dir}")
                # If it's a dict, it's an inline test definition
                elif isinstance(test_entry, dict):
                    all_tests.append(test_entry)
        
        # Filter tests by public flag if requested
        if public_only:
            all_tests = [test for test in all_tests if test.get("public", False)]
        
        # Organize test cases by title
        test_cases_by_title = {}
        for test in all_tests:
            title = test.get("title", "Uncategorized")
            if title not in test_cases_by_title:
                test_cases_by_title[title] = []
            test_cases_by_title[title].append(test)
        
        # Convert to array format for frontend
        organized_tests = []
        for title, cases in test_cases_by_title.items():
            organized_tests.append({
                "title": title,
                "tests": cases
            })
        
        print(f"✓ Returning {len(organized_tests)} test groups with {len(all_tests)} total tests")
        return {"testCases": organized_tests}
    except Exception as e:
        print(f"✗ Error in load_test_cases: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/llm-judge")
async def llm_judge(request: dict):
    """
    Use OpenAI's vision API to judge a screenshot against test criteria.
    
    Request body:
    {
        "screenshot": "data:image/png;base64,...",  # Base64 encoded screenshot
        "testCase": {
            "name": "...",
            "description": "..."
        },
        "htmlCode": "..."  # Optional HTML code for context
    }
    
    Returns:
    {
        "judgment": "pass" | "fail",
        "explanation": "..."
    }
    """
    print('hello!')
    try:
        screenshot = request.get("screenshot")
        test_case = request.get("testCase", {})
        html_code = request.get("htmlCode", "")
        
        if not screenshot:
            return JSONResponse(status_code=400, content={"error": "No screenshot provided"})
        
        if not test_case.get("description"):
            return JSONResponse(status_code=400, content={"error": "No test description provided"})
        
        # Get OpenAI API key
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return JSONResponse(status_code=500, content={
                "error": "OpenAI API key not configured"
            })
        
        # Prepare the prompt for GPT-4 Vision
        prompt = f"""You are a test judge evaluating a web page screenshot against specific criteria.

Test Name: {test_case['name']}
Test Description: {test_case['description']}

Your task:
1. Carefully examine the screenshot of the rendered web page
2. Determine if the page meets the requirement described above
3. Respond with ONLY a JSON object in this exact format:
{{
    "judgment": "pass" or "fail",
    "explanation": "A clear explanation of your decision"
}}

Be strict but fair in your evaluation. If the requirement is met, even if not perfectly, your judgment should be \"pass\". If critical elements are missing or the requirement is clearly not satisfied, your judgment should be \"fail\"."""

        # Call OpenAI Vision API
        client = openai.OpenAI(api_key=api_key)

        for num_attempts in range(5):
            response = client.chat.completions.create(
                model="gpt-4o-2024-08-06",  # Use gpt-4o which supports vision
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": screenshot,
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=500,
            )
            
            # Parse the response
            response_text = response.choices[0].message.content.strip()
            response_text = response_text.replace("`", "").replace("json", "")
            response_text = response_text[response_text.index("{"):response_text.rindex("}")+1]
                
            result = json.loads(response_text)
            print(result)

            if result.get("judgment", "") not in {"pass", "fail"} or result.get("explanation", "") == "":
                continue
        
            return {
                "judgment": result.get("judgment", "fail").lower(),
                "explanation": result.get("explanation", "No explanation provided")   
            }
        
    except Exception as e:
        print(f"Error in LLM judge: {e}")
        return JSONResponse(status_code=500, content={
            "error": f"Failed to judge screenshot: {str(e)}"
        })

# Authentication endpoints
SIGNUP_EXPERIMENT_GROUP_SETTINGS_KEY = "experiment_group"
SIGNUP_LEGACY_GROUPS_SETTINGS_KEY = "groups"
WEBSITE_REQUIREMENTS_SKIPPED_SETTINGS_KEY = "websiteRequirementsSkipped"
SIGNUP_GROUP_CHAT = "chat"
SIGNUP_GROUP_AGENT = "agent"
VALID_SIGNUP_GROUPS = {SIGNUP_GROUP_CHAT, SIGNUP_GROUP_AGENT}


def _normalize_experiment_group(group: Any) -> Optional[str]:
    """Normalize experiment group and return valid value or None."""
    if not isinstance(group, str):
        return None
    normalized = group.strip().lower()
    if normalized not in VALID_SIGNUP_GROUPS:
        return None
    return normalized


def _is_eligible_for_experiment_group_sampling(user_settings: Any) -> bool:
    """Whether a user should be included in experiment group balancing counts."""
    if not isinstance(user_settings, dict):
        return False
    # Exclude users who explicitly chose to skip website requirements tasks.
    return user_settings.get(WEBSITE_REQUIREMENTS_SKIPPED_SETTINGS_KEY) is not True


def _get_experiment_group_counts(db: Session) -> Dict[str, int]:
    """Count assigned experiment groups across eligible non-skipping users.
    When on_or_after is set below, only users with created_at >= that date are included.
    """
    on_or_after: Optional[datetime] = datetime(2026, 3, 18)
    counts = {
        SIGNUP_GROUP_CHAT: 0,
        SIGNUP_GROUP_AGENT: 0,
        "unassigned": 0,
    }

    query = db.query(User.settings, User.created_at)
    if on_or_after is not None:
        query = query.filter(User.created_at >= on_or_after)
    existing_users = query.all()

    for (user_settings, created_at) in existing_users:
        if not _is_eligible_for_experiment_group_sampling(user_settings):
            continue

        if not isinstance(user_settings, dict):
            counts["unassigned"] += 1
            continue

        group = _normalize_experiment_group(
            user_settings.get(SIGNUP_EXPERIMENT_GROUP_SETTINGS_KEY)
        )
        if group == SIGNUP_GROUP_CHAT:
            counts[SIGNUP_GROUP_CHAT] += 1
        elif group == SIGNUP_GROUP_AGENT:
            counts[SIGNUP_GROUP_AGENT] += 1
        else:
            counts["unassigned"] += 1
    print(counts)
    return counts


def _assign_signup_group(
    username: str,
    email: str,
    settings: Dict[str, Any],
    db: Session,
) -> str:

    """Assign signup to one 50/50 experiment group: 'chat' or 'agent'."""
    explicit_group = _normalize_experiment_group(
        settings.get(SIGNUP_EXPERIMENT_GROUP_SETTINGS_KEY)
    )
    if explicit_group:
        return explicit_group

    # Backward compatibility for any payloads still sending settings.groups.
    legacy_groups = settings.get(SIGNUP_LEGACY_GROUPS_SETTINGS_KEY)
    if isinstance(legacy_groups, list) and legacy_groups:
        legacy_group = _normalize_experiment_group(legacy_groups[0])
        if legacy_group:
            return legacy_group

    _ = (username, email)

    # Counterbalanced assignment: choose the arm with fewer users so far.
    # If tied, use pure 50/50 random assignment.
    counts = _get_experiment_group_counts(db)
    chat_count = counts[SIGNUP_GROUP_CHAT]
    agent_count = counts[SIGNUP_GROUP_AGENT]

    if chat_count < agent_count:
        return SIGNUP_GROUP_CHAT
    if agent_count < chat_count:
        return SIGNUP_GROUP_AGENT
    print('randomly picking!')
    return random.choice([SIGNUP_GROUP_CHAT, SIGNUP_GROUP_AGENT])


def _build_signup_settings(
    username: str,
    email: str,
    incoming_settings: Optional[Dict[str, Any]],
    db: Session,
) -> Dict[str, Any]:
    """Create final settings payload for newly created users."""
    settings = dict(incoming_settings) if isinstance(incoming_settings, dict) else {}
    settings[SIGNUP_EXPERIMENT_GROUP_SETTINGS_KEY] = _assign_signup_group(
        username=username,
        email=email,
        settings=settings,
        db=db,
    )
    # Keep only the canonical experiment key moving forward.
    settings.pop(SIGNUP_LEGACY_GROUPS_SETTINGS_KEY, None)
    return settings


def _ensure_user_experiment_group(db: Session, user: User) -> str:
    """Ensure an existing user has a valid experiment_group, assigning if missing."""
    current_settings = user.settings if isinstance(user.settings, dict) else {}
    existing_group = _normalize_experiment_group(
        current_settings.get(SIGNUP_EXPERIMENT_GROUP_SETTINGS_KEY)
    )
    if existing_group:
        return existing_group

    updated_settings = dict(current_settings)
    updated_settings[SIGNUP_EXPERIMENT_GROUP_SETTINGS_KEY] = _assign_signup_group(
        username=user.username,
        email=user.email,
        settings=updated_settings,
        db=db,
    )
    updated_settings.pop(SIGNUP_LEGACY_GROUPS_SETTINGS_KEY, None)

    user.settings = updated_settings
    db.add(user)
    db.commit()
    db.refresh(user)
    return updated_settings[SIGNUP_EXPERIMENT_GROUP_SETTINGS_KEY]


@app.post("/signup", tags=["Authentication"])
async def signup(user_data: UserCreate, db: Session = Depends(get_db)):
    """Create a new user account."""
    try:
        # Check if user already exists
        existing_user = db.query(User).filter(
            or_(User.username == user_data.username, User.email == user_data.email)
        ).first()
        
        if existing_user:
            if existing_user.username == user_data.username:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already registered"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered"
                )
        
        # Hash the password
        hashed_password = get_password_hash(user_data.password)
        
        # Create new user
        signup_settings = _build_signup_settings(
            username=user_data.username,
            email=user_data.email,
            incoming_settings=user_data.settings,
            db=db,
        )

        db_user = User(
            username=user_data.username,
            email=user_data.email,
            password=hashed_password,
            settings=signup_settings,
        )
        
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        
        # Create access token
        access_token = create_access_token(data={"sub": str(db_user.id)})
        
        return {
            "message": "User created successfully",
            "user": {
                "id": db_user.id,
                "username": db_user.username,
                "email": db_user.email,
                "settings": db_user.settings or {},
                "created_at": db_user.created_at
            },
            "access_token": access_token,
            "token_type": "bearer"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating user: {str(e)}"
        )


@app.post("/login", tags=["Authentication"])
async def login(credentials: dict, db: Session = Depends(get_db)):
    """Authenticate user and return access token."""
    try:
        username_or_email = credentials.get("username_or_email")
        password = credentials.get("password")
        
        if not username_or_email or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username/email and password are required"
            )
        
        # Find user by username or email
        user = db.query(User).filter(
            or_(User.username == username_or_email, User.email == username_or_email)
        ).first()

        # Provide clearer error messages
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Username or email not found"
            )

        if not verify_password(password, user.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect password"
            )

        # Backfill assignment for legacy users missing experiment_group.
        _ensure_user_experiment_group(db, user)
        
        # Create access token
        access_token = create_access_token(data={"sub": str(user.id)})
        
        return {
            "message": "Login successful",
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "settings": user.settings or {},
                "created_at": user.created_at
            },
            "access_token": access_token,
            "token_type": "bearer"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error during login: {str(e)}"
        )


@app.get("/auth/validate", tags=["Authentication"])
async def validate_auth_token(request: Request, db: Session = Depends(get_db)):
    """Validate an authentication token and return the associated user."""
    try:
        auth_header = request.headers.get("Authorization")
        token: Optional[str] = None

        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()

        if not token:
            token = request.query_params.get("token")

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authentication token"
            )

        payload = verify_token(token)
        if not payload or "sub" not in payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )

        user_id = payload.get("sub")
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload"
            )

        user = db.query(User).filter(User.id == user_id_int).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )

        # Backfill assignment for legacy users missing experiment_group.
        _ensure_user_experiment_group(db, user)

        return {
            "valid": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "settings": user.settings or {},
                "created_at": user.created_at
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error validating token: {str(e)}"
        )


@app.post("/send-password-reset", tags=["Authentication"])
async def send_password_reset(request: PasswordResetRequest, db: Session = Depends(get_db)):
    """Send password reset email to user."""
    try:
        username_or_email = request.username_or_email
        
        # Find user by username or email
        user = db.query(User).filter(
            or_(User.username == username_or_email, User.email == username_or_email)
        ).first()
        
        if not user:
            # Return specific error for better UX
            return JSONResponse(
                status_code=404,
                content={
                    "detail": "No account found with that username or email address"
                }
            )
        
        # Generate reset token
        reset_token = generate_reset_token()
        expires_at = datetime.utcnow() + timedelta(minutes=30)
        
        # Invalidate any existing reset tokens for this user
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used == False
        ).update({"used": True})
        
        # Create new reset token
        reset_token_data = PasswordResetTokenCreate(
            user_id=user.id,
            token=reset_token,
            expires_at=expires_at,
            used=False
        )
        
        reset_token_record = PasswordResetToken(**reset_token_data.dict())
        
        db.add(reset_token_record)
        db.commit()
        
        # Send email
        email_sent = send_password_reset_email(user.email, user.username, reset_token)
        
        if email_sent:
            return {
                "message": "Password reset email sent successfully",
                "user_exists": True
            }
        else:
            return {
                "message": "Password reset email could not be sent, but reset token generated",
                "reset_token": reset_token,  # For development/testing
                "user_exists": True
            }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error sending password reset: {str(e)}"
        )


@app.get("/validate-reset-token", tags=["Authentication"])
async def validate_reset_token(token: str, db: Session = Depends(get_db)):
    """Validate reset token and return username if valid."""
    try:
        # Find valid reset token
        reset_token_record = db.query(PasswordResetToken).filter(
            PasswordResetToken.token == token,
            PasswordResetToken.used == False,
            PasswordResetToken.expires_at > datetime.utcnow()
        ).first()
        
        if not reset_token_record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token"
            )
        
        # Get user
        user = db.query(User).filter(User.id == reset_token_record.user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User not found"
            )
        
        return {
            "valid": True,
            "username": user.username,
            "expires_at": reset_token_record.expires_at.isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error validating token: {str(e)}"
        )


@app.put("/api/users/{user_id}/settings", tags=["Users"])
async def update_user_settings(
    user_id: int,
    request: dict,
    db: Session = Depends(get_db)
):
    """Update user settings. Merges with existing settings."""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Get new settings from request
        new_settings = request.get("settings", {})
        if not isinstance(new_settings, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Settings must be a dictionary"
            )
        
        # Merge with existing settings
        current_settings = user.settings or {}
        updated_settings = {**current_settings, **new_settings}
        
        # Update user settings
        user.settings = updated_settings
        db.commit()
        db.refresh(user)
        
        return {
            "message": "Settings updated successfully",
            "settings": user.settings
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating settings: {str(e)}"
        )


@app.get("/api/admin/experiment-groups", tags=["Users"])
async def get_experiment_group_counts(db: Session = Depends(get_db)):
    """Return experiment-group counts for admin/debugging."""
    try:
        counts = _get_experiment_group_counts(db)
        assigned_total = counts[SIGNUP_GROUP_CHAT] + counts[SIGNUP_GROUP_AGENT]
        total_users = assigned_total + counts["unassigned"]
        return {
            "settings_key": SIGNUP_EXPERIMENT_GROUP_SETTINGS_KEY,
            "groups": {
                SIGNUP_GROUP_CHAT: counts[SIGNUP_GROUP_CHAT],
                SIGNUP_GROUP_AGENT: counts[SIGNUP_GROUP_AGENT],
            },
            "assigned_total": assigned_total,
            "unassigned": counts["unassigned"],
            "total_users": total_users,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching experiment-group counts: {str(e)}"
        )


@app.post("/reset-password", tags=["Authentication"])
async def reset_password(request: PasswordResetConfirm, db: Session = Depends(get_db)):
    """Reset user password using reset token."""
    try:
        token = request.token
        new_password = request.new_password
        
        # Find valid reset token
        reset_token_record = db.query(PasswordResetToken).filter(
            PasswordResetToken.token == token,
            PasswordResetToken.used == False,
            PasswordResetToken.expires_at > datetime.utcnow()
        ).first()
        
        if not reset_token_record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token"
            )
        
        # Get user
        user = db.query(User).filter(User.id == reset_token_record.user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User not found"
            )
        
        # Hash new password
        hashed_password = get_password_hash(new_password)
        
        # Update user password
        user.password = hashed_password
        user.updated_at = datetime.utcnow()
        
        # Mark token as used
        reset_token_record.used = True
        
        db.commit()
        
        return {
            "message": "Password reset successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error resetting password: {str(e)}"
        )


@app.post("/api/execute-test-cases")
async def execute_test_cases(request: dict):
    """
    Execute a subset of test cases against the backend code.
    
    Request body:
    {
        "testCases": [...],  # Array of test cases to execute
        "backendCode": "...",  # The backend Python code to test
        "port": 5000  # Optional port number
    }
    
    Returns:
    {
        "results": [
            {
                "testName": "...",
                "status": "pass" | "fail" | "error",
                "message": "...",
                "expected": {...},
                "actual": {...}
            }
        ]
    }
    """
    try:
        test_cases = request.get("testCases", [])
        backend_code = request.get("backendCode", "")
        port = request.get("port", 5000)
        
        results = []
        
        # Parse endpoints from backend code
        try:
            endpoints = endpoint_parser.parse_to_dict(backend_code)
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"error": f"Failed to parse backend code: {str(e)}"}
            )
        
        # Execute each test case
        for test in test_cases:
            test_name = test.get("name", "Unknown Test")
            metadata = test.get("metadata", {})
            test_type = metadata.get("type", "endpoint")
            
            # All tests should be endpoint-based now
            if test_type != "endpoint":
                results.append({
                    "testName": test_name,
                    "status": "skip",
                    "message": f"Test type '{test_type}' not supported"
                })
                continue
            
            endpoint_path = metadata.get("endpoint", "")
            test_input = metadata.get("input", {})
            expected = metadata.get("expected")
            
            # Execute the endpoint using OneCompiler (same as /api/execute-endpoint)
            try:
                # Find the function name from parsed endpoints
                endpoint = next((ep for ep in endpoints if ep.get("endpoint") == endpoint_path), None)
                
                if not endpoint:
                    results.append({
                        "testName": test_name,
                        "status": "error",
                        "message": f"Endpoint {endpoint_path} not found in backend code",
                        "expected": expected,
                        "actual": None
                    })
                    continue
                
                function_name = endpoint['name']
                user_args_str = repr(test_input) if test_input is not None else "{}"
                
                # Build execution code (same pattern as /api/execute-endpoint)
                execution_code = f"""
# Backend code
{backend_code}

# Execute the endpoint with parameters
try:
    user_args_dict = {user_args_str}
    result = {function_name}(**user_args_dict)
    print("ENDPOINT_RESULT:", result)
except Exception as e:
    print("ENDPOINT_ERROR:", str(e))
"""
                
                # Execute using OneCompiler
                exec_result = await onecompiler_service.execute_python(execution_code)
                
                if exec_result.get("success"):
                    stdout = exec_result.get("stdout", "")
                    
                    # Parse the result from stdout
                    if "ENDPOINT_RESULT:" in stdout:
                        result_line = [line for line in stdout.split('\n') if 'ENDPOINT_RESULT:' in line]
                        if result_line:
                            try:
                                result_str = result_line[0].split('ENDPOINT_RESULT:', 1)[1].strip()
                                # Try JSON parsing first
                                try:
                                    actual = json.loads(result_str)
                                except json.JSONDecodeError:
                                    # Try Python literal eval
                                    try:
                                        actual = ast.literal_eval(result_str)
                                    except (ValueError, SyntaxError):
                                        actual = result_str
                            except Exception:
                                actual = result_str
                        else:
                            actual = stdout
                    elif "ENDPOINT_ERROR:" in stdout:
                        error_line = [line for line in stdout.split('\n') if 'ENDPOINT_ERROR:' in line]
                        error_msg = error_line[0].split('ENDPOINT_ERROR:', 1)[1].strip() if error_line else "Unknown error"
                        results.append({
                            "testName": test_name,
                            "status": "error",
                            "message": error_msg,
                            "expected": expected,
                            "actual": None
                        })
                        continue
                    else:
                        actual = stdout
                    
                    # Simple equality comparison
                    passed = (actual == expected)
                    
                    if passed:
                        results.append({
                            "testName": test_name,
                            "status": "pass",
                            "message": "Test passed successfully",
                            "expected": expected,
                            "actual": actual
                        })
                    else:
                        results.append({
                            "testName": test_name,
                            "status": "fail",
                            "message": f"Expected {expected} but got {actual}",
                            "expected": expected,
                            "actual": actual
                        })
                else:
                    results.append({
                        "testName": test_name,
                        "status": "error",
                        "message": exec_result.get("error", "Unknown error"),
                        "expected": expected,
                        "actual": None
                    })
                    
            except Exception as e:
                results.append({
                    "testName": test_name,
                    "status": "error",
                    "message": str(e),
                    "expected": expected,
                    "actual": None
                })
        
        return {"results": results}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    
    try:
        while True:
            # Receive message from frontend
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            # Extract message content
            messages = message_data.get("messages", [])
            model = message_data.get("model", "gpt-4")
            max_tokens = message_data.get("max_tokens", 1000)
            proactive = message_data.get("proactive", False)
            current_code = message_data.get("current_code", "")
            
            # Prepare messages for AI
            if proactive and current_code:
                # Add code context for proactive responses
                user_message = messages[-1] if messages else {"role": "user", "content": ""}
                enhanced_message = {
                    "role": "user", 
                    "content": f"Code:\n{current_code}\n\nMessage:\n{user_message.get('content', '')}"
                }
                messages_to_send = messages[:-1] + [enhanced_message]
            else:
                messages_to_send = messages
            
            # Stream response back to frontend
            async def on_chunk(chunk: str):
                await websocket.send_text(json.dumps({
                    "type": "chunk",
                    "content": chunk
                }))
            
            async def on_complete(full_response: str):
                # For autocomplete strategy, also send the generated code
                generated_code = ""
                if hasattr(chat_model.strategy, 'get_last_generated_code'):
                    generated_code = chat_model.strategy.get_last_generated_code()
                
                await websocket.send_text(json.dumps({
                    "type": "complete",
                    "content": full_response,
                    "generated_code": generated_code
                }))
            
            # For autocomplete strategy, send generated code immediately when ready
            async def on_code_ready(generated_code: str):
                await websocket.send_text(json.dumps({
                    "type": "code_ready",
                    "generated_code": generated_code
                }))
            
            async def on_error(error: str):
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": f"Error: {error}"
                }))
            
            # Stream the response
            await chat_model.stream_response(
                messages=messages_to_send,
                model=model,
                max_tokens=max_tokens,
                on_chunk=on_chunk,
                on_complete=on_complete,
                on_error=on_error,
                on_code_ready=on_code_ready,
                current_code=current_code
            )
            
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")
        await websocket.send_text(json.dumps({
            "type": "error",
            "content": f"Server error: {str(e)}"
        }))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=4828, reload=True)
