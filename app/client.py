"""The OpenAI-compatible client.

The client owns the conversation history and drives the tool-calling loop:

1. Send ``messages`` + the tool schemas to the server.
2. If the assistant response contains ``tool_calls``, execute them locally (via
   :func:`app.tools.dispatch`) and feed the results back.
3. Repeat until the assistant produces plain content (or ``max_chat_turns`` is hit).

Nothing about the books is stored on the server: the server only decides *what* to do;
the client performs the file operations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import model_registry as reg
from .books_store import BookStore
from .config import CHAT_COMPLETIONS_PATH, MODELS_PATH, Config, Transport
from .images import encode_file_to_base64_png
from .tools import ToolContext, ToolError, dispatch, format_tool_result
from .transport import Response, TransportError, make_transport


@dataclass
class ToolExecution:
    id: str
    name: str
    args: dict
    result: dict

    def summary(self) -> str:
        return f"{self.name}({', '.join(f'{k}={v!r}' for k, v in self.args.items())})"


@dataclass
class ChatResult:
    final_content: str
    model: str
    tool_calls: list[ToolExecution] = field(default_factory=list)
    turns: int = 0


OnEvent = Callable[[dict], None]


class Client:
    """A thin, tool-aware wrapper over the OpenAI-compatible HTTP protocol."""

    def __init__(self, config: Config, tool_context: Optional[ToolContext] = None):
        self.config = config
        self.transport = make_transport(config.server_spec, config.connection_timeout)
        self.tool_ctx = tool_context or ToolContext(BookStore(config.book_root))
        self.messages: list[dict] = []
        # Context messages (e.g. a "current book" system hint) are prepended to every
        # request. They never accumulate into the conversation, so a real OpenAI-compatible
        # server still sees a clean history — while the fake server (and any model) learn
        # which book the tool calls should be tied to.
        self.context_messages: list[dict] = []
        self.model = config.model
        self.last_response: Optional[Response] = None

    # -- lifecycle --------------------------------------------------------
    def set_model(self, model: str) -> None:
        self.model = model

    def set_context(self, text: str) -> None:
        """Replace the context messages (prepended to every request)."""
        self.context_messages = [{"role": "system", "content": text}] if text else []

    def set_current_book(self, book_name: Optional[str]) -> None:
        """Tell the model which book its tool calls should be tied to.

        Sent as a lightweight system message so it also works with real OpenAI-compatible
        models (which honour the system prompt) as well as the bundled fake server.
        """
        if book_name:
            self.set_context(
                f"The user is currently working on the book named \"{book_name}\". "
                "Any instruction that writes, edits, deletes, lists or searches pages refers "
                "to this book unless another book is explicitly named."
            )
        else:
            self.set_context("")

    def clear(self) -> None:
        """Start a fresh conversation, keeping the books untouched."""
        self.messages = []

    def ping(self) -> list[dict]:
        """Contact the server and return its list of models (``GET /v1/models``)."""
        resp = self.transport.get_json(MODELS_PATH)
        models = []
        data = resp.data if isinstance(resp.data, dict) else {}
        for entry in data.get("data", data.get("models", [])):
            if isinstance(entry, dict) and "id" in entry:
                models.append(entry["id"])
            else:
                models.append(entry)
        return models

    def ping_or_error(self) -> str:
        """Return a human status string: the model list, or an error message."""
        try:
            models = self.ping()
        except TransportError as exc:
            raise exc
        if models:
            return f"connected — {len(models)} model(s): {', '.join(models)}"
        return "connected (server reports no models)"

    # -- message building -------------------------------------------------
    def _image_content(self, image: Optional[str | Path]) -> Optional[dict]:
        if not image:
            return None
        return {"type": "image_url", "image_url": {"url": encode_file_to_base64_png(Path(image))}}

    def add_message(self, role: str, content: object) -> dict:
        msg = {"role": role, "content": content}
        self.messages.append(msg)
        return msg

    def add_user_message(self, text: str, image: Optional[str | Path] = None) -> dict:
        if image:
            content: object = [
                {"type": "text", "text": text or ""},
                self._image_content(image),
            ]
        else:
            content = text or ""
        return self.add_message("user", content)

    def _payload(self) -> dict:
        return {
            "model": self.model,
            "messages": self.context_messages + self.messages,
            "tools": reg.tool_schemas(),
            "tool_choice": "auto",
            "stream": bool(self.config.stream),
        }

    # -- the loop ---------------------------------------------------------
    def send(self, text: str, image: Optional[str | Path] = None,
             on_event: Optional[OnEvent] = None) -> ChatResult:
        """Send a user message (optionally with an image) and run the tool loop.

        When ``config.stream`` is true the assistant response is received as an SSE
        stream: content deltas are emitted as ``assistant_delta`` events (so the GUI can
        render them in real time) and tool calls are only *executed* after the whole
        message has finished streaming.
        """
        emit = on_event or (lambda _e: None)
        self.add_user_message(text, image)

        executed: list[ToolExecution] = []
        model_name = self.model
        turns = 0

        for turn in range(self.config.max_chat_turns):
            turns = turn + 1
            emit({"kind": "loop_start", "turn": turn})

            if self.config.stream:
                assistant = self._stream_turn(emit)
            else:
                assistant = self._non_stream_turn(emit)

            tool_calls = assistant.get("tool_calls") or []
            self.messages.append(assistant)

            if not tool_calls:
                return ChatResult(
                    final_content=assistant.get("content") or "",
                    model=model_name,
                    tool_calls=executed,
                    turns=turns,
                )

            for tc in tool_calls:
                self._execute_tool(tc, turn, emit, executed)

        return ChatResult(
            final_content="(maximum tool turns reached without a final answer)",
            model=model_name,
            tool_calls=executed,
            turns=turns,
        )

    # -- turn helpers -----------------------------------------------------
    def _assistant_message_from(self, content, tool_calls=None) -> dict:
        msg: dict = {"role": "assistant", "content": content or None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    def _non_stream_turn(self, emit: OnEvent) -> dict:
        """One non-streaming assistant turn (full response at once)."""
        resp = self.transport.post_json(CHAT_COMPLETIONS_PATH, self._payload())
        self.last_response = resp
        data = resp.data if isinstance(resp.data, dict) else {}
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise TransportError(f"unexpected server response: {data!r}")

        tool_calls = message.get("tool_calls") or []
        emit({"kind": "assistant", "content": message.get("content"),
              "tool_calls": tool_calls})
        return self._assistant_message_from(message.get("content"), tool_calls)

    def _stream_turn(self, emit: OnEvent) -> dict:
        """Consume one SSE assistant turn, emitting deltas as they arrive."""
        content_parts: list[str] = []
        calls: dict[int, dict] = {}   # index -> {"id", "name", "args"}

        stream = self.transport.stream_chat(CHAT_COMPLETIONS_PATH, self._payload())
        for event in stream:
            data = event if isinstance(event, dict) else {}
            try:
                choice = data["choices"][0]
            except (KeyError, IndexError, TypeError):
                continue
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
                emit({"kind": "assistant_delta", "content": piece})
            for tcd in delta.get("tool_calls") or []:
                index = int(tcd.get("index", 0))
                cur = calls.setdefault(index, {"id": "", "name": "", "args": ""})
                if tcd.get("id"):
                    cur["id"] = tcd["id"]
                fn = tcd.get("function") or {}
                if fn.get("name"):
                    cur["name"] = fn["name"]
                if isinstance(fn.get("arguments"), str):
                    cur["args"] += fn["arguments"]

        content = "".join(content_parts)
        tool_calls = None
        if calls:
            tool_calls = [
                {"id": cur["id"] or f"call_stream_{idx}",
                 "type": "function",
                 "function": {"name": cur["name"], "arguments": cur["args"]}}
                for idx, cur in sorted(calls.items())
            ]
        return self._assistant_message_from(content, tool_calls)

    def _execute_tool(self, tc: dict, turn: int, emit: OnEvent,
                      executed: list[ToolExecution]) -> None:
        fn = tc.get("function", {})
        fname = fn.get("name", "")
        args_raw = fn.get("arguments", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except json.JSONDecodeError:
            args = {}
        tool_id = tc.get("id", f"call_{turn}")

        emit({"kind": "tool_call", "id": tool_id, "name": fname, "args": args})
        try:
            result = dispatch(self.tool_ctx, fname, args or {})
        except ToolError as exc:
            result = {"ok": False, "message": str(exc)}
        except Exception as exc:  # defensive
            result = {"ok": False, "message": f"{fname} failed: {exc}"}

        result_str = json.dumps(result, ensure_ascii=False)
        executed.append(ToolExecution(tool_id, fname, args, result))
        emit({"kind": "tool_result", "id": tool_id, "name": fname, "result": result})
        self.messages.append(
            {"role": "tool", "tool_call_id": tool_id, "name": fname, "content": result_str}
        )


def summarize_tool_calls(executed: list[ToolExecution]) -> str:
    """A short human-readable summary of the tools used in a turn."""
    if not executed:
        return ""
    parts = [f"called {e.name}" for e in executed]
    return "; ".join(parts)
