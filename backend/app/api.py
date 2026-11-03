"""Local HTTP API used by the VS Code extension."""

import secrets
import threading
import json
from queue import Empty, Queue
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from groq import Groq
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from app.agent import WorkspaceAgent, commands_to_dicts, proposals_to_dicts
from app.chat import ChatError
from app.commands import CommandExecutionError, run_approved_command
from app.config import Settings, get_settings
from app.workspace import Workspace, WorkspaceError


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_root: str = Field(min_length=1, max_length=4_096)
    prompt: str = Field(min_length=1, max_length=12_000)
    conversation: list[ConversationMessage] = Field(default_factory=list, max_length=20)
    active_file: str | None = Field(default=None, max_length=1_024)
    active_file_content: str | None = Field(default=None, max_length=300_000)
    selected_text: str | None = Field(default=None, max_length=20_000)
    run_id: str = Field(min_length=1, max_length=64)


class CommandRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_root: str = Field(min_length=1, max_length=4_096)
    argv: list[str] = Field(min_length=1, max_length=30)
    timeout_seconds: int = Field(default=60, ge=1, le=120)


def _settings() -> Settings:
    try:
        return get_settings()
    except PydanticValidationError:
        raise HTTPException(
            status_code=503,
            detail="Groq API key is missing. Configure GROQ_API_KEY in backend/.env.",
        ) from None


def require_local_token(authorization: str | None = Header(default=None)) -> Settings:
    settings = _settings()
    expected = (
        settings.agent_auth_token.get_secret_value().strip()
        if settings.agent_auth_token
        else ""
    )
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Local API token is not configured for this server process.",
        )
    provided = authorization.removeprefix("Bearer ") if authorization else ""
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    return settings


app = FastAPI(
    title="Sagar Code Agent",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
)
_RUNS: dict[str, threading.Event] = {}
_CANCELLED_RUNS: set[str] = set()
_RUNS_LOCK = threading.Lock()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/chat")
def chat(request: ChatRequest, settings: Settings = Depends(require_local_token)) -> dict:
    try:
        workspace = Workspace(
            request.workspace_root,
            editor_file=request.active_file,
            editor_content=request.active_file_content,
        )
    except WorkspaceError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    active_file = request.active_file
    selected_text = request.selected_text
    if active_file:
        try:
            excluded = workspace.is_excluded_path(active_file)
        except WorkspaceError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if excluded:
            active_file = None
            selected_text = None
    if selected_text and Workspace.contains_sensitive_text(selected_text):
        selected_text = None
    if request.active_file_content and Workspace.contains_sensitive_text(request.active_file_content):
        active_file = None
        selected_text = None

    api_key = settings.groq_api_key.get_secret_value().strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Groq API key is empty. Configure GROQ_API_KEY in backend/.env.",
        )
    cancel_event = threading.Event()
    with _RUNS_LOCK:
        if request.run_id in _RUNS:
            raise HTTPException(status_code=409, detail="A request with this ID is already running.")
        if request.run_id in _CANCELLED_RUNS:
            cancel_event.set()
            _CANCELLED_RUNS.discard(request.run_id)
        _RUNS[request.run_id] = cancel_event

    client: Groq | None = None
    try:
        client = Groq(api_key=api_key, timeout=30.0, max_retries=0)
        agent = WorkspaceAgent(client=client, model=settings.groq_model)
        result = agent.run(
            workspace=workspace,
            prompt=request.prompt,
            conversation=[message.model_dump() for message in request.conversation],
            active_file=active_file,
            selected_text=selected_text,
            cancel_event=cancel_event,
        )
    except ChatError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        if client is not None:
            client.close()
        with _RUNS_LOCK:
            _RUNS.pop(request.run_id, None)

    return {
        "answer": result.answer,
        "proposals": proposals_to_dicts(result.proposals),
        "commands": commands_to_dicts(result.commands),
        "tool_steps": result.tool_steps,
        "context_sent": result.context_sent,
    }


@app.post("/chat/stream")
def chat_stream(
    request: ChatRequest,
    settings: Settings = Depends(require_local_token),
) -> StreamingResponse:
    try:
        workspace = Workspace(
            request.workspace_root,
            editor_file=request.active_file,
            editor_content=request.active_file_content,
        )
    except WorkspaceError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    active_file = request.active_file
    selected_text = request.selected_text
    if active_file:
        try:
            excluded = workspace.is_excluded_path(active_file)
        except WorkspaceError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if excluded:
            active_file = None
            selected_text = None
    if selected_text and Workspace.contains_sensitive_text(selected_text):
        selected_text = None
    if request.active_file_content and Workspace.contains_sensitive_text(request.active_file_content):
        active_file = None
        selected_text = None

    api_key = settings.groq_api_key.get_secret_value().strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Groq API key is empty. Configure GROQ_API_KEY in backend/.env.",
        )

    cancel_event = threading.Event()
    with _RUNS_LOCK:
        if request.run_id in _RUNS:
            raise HTTPException(status_code=409, detail="A request with this ID is already running.")
        if request.run_id in _CANCELLED_RUNS:
            cancel_event.set()
            _CANCELLED_RUNS.discard(request.run_id)
        _RUNS[request.run_id] = cancel_event

    events: Queue[tuple[str, dict]] = Queue()

    def send_text(text: str) -> None:
        events.put(("delta", {"text": text}))

    def run_agent() -> None:
        client: Groq | None = None
        try:
            client = Groq(api_key=api_key, timeout=30.0, max_retries=0)
            result = WorkspaceAgent(client=client, model=settings.groq_model).run(
                workspace=workspace,
                prompt=request.prompt,
                conversation=[message.model_dump() for message in request.conversation],
                active_file=active_file,
                selected_text=selected_text,
                cancel_event=cancel_event,
                on_text=send_text,
            )
            events.put(
                (
                    "complete",
                    {
                        "answer": result.answer,
                        "proposals": proposals_to_dicts(result.proposals),
                        "commands": commands_to_dicts(result.commands),
                        "tool_steps": result.tool_steps,
                        "context_sent": result.context_sent,
                    },
                )
            )
        except ChatError as error:
            events.put(("error", {"detail": str(error)}))
        except Exception:
            events.put(("error", {"detail": "The local agent failed while processing this request."}))
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            with _RUNS_LOCK:
                _RUNS.pop(request.run_id, None)
            events.put(("done", {}))

    worker = threading.Thread(target=run_agent, name="sagar-agent-run", daemon=True)
    worker.start()

    def event_stream():
        try:
            while True:
                try:
                    event, payload = events.get(timeout=10)
                except Empty:
                    yield ": keep-alive\n\n"
                    continue
                if event == "done":
                    break
                data = json.dumps(payload, ensure_ascii=False)
                yield f"event: {event}\ndata: {data}\n\n"
        finally:
            if worker.is_alive():
                cancel_event.set()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    settings: Settings = Depends(require_local_token),
) -> dict[str, bool]:
    del settings
    with _RUNS_LOCK:
        cancel_event = _RUNS.get(run_id)
        if cancel_event is not None:
            cancel_event.set()
        else:
            _CANCELLED_RUNS.add(run_id)
            if len(_CANCELLED_RUNS) > 100:
                _CANCELLED_RUNS.pop()
    return {"stopping": cancel_event is not None}


@app.post("/commands/run")
def run_command(
    request: CommandRunRequest,
    settings: Settings = Depends(require_local_token),
) -> dict:
    del settings  # The dependency authenticates this user-approved request.
    try:
        workspace = Workspace(request.workspace_root)
    except WorkspaceError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    try:
        return run_approved_command(
            request.argv,
            workspace_root=workspace.root,
            timeout_seconds=request.timeout_seconds,
        )
    except CommandExecutionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
