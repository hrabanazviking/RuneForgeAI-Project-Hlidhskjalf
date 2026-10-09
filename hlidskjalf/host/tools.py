"""Slice 46 — Tool invocation bridge (Phase G: Host Integration).

``ToolRegistry`` lets host-side callables be invoked from the edge through
the MCP client/server pair from slice 41:

    registry = ToolRegistry()
    registry.register("add", lambda args: args["a"] + args["b"])
    registry.invoke("add", {"a": 2, "b": 3})
    # -> {"ok": True, "tool": "add", "args": {...}, "result": 5}

Internally every registered callable is exposed as a tool on an in-process
:class:`~hlidskjalf.host.mcp.MockMCPServer`, and :meth:`invoke` goes through
the MCP client (``tools/call``) — the same path a real edge↔host session uses.
Results are wrapped in a result envelope; tool or transport failures become
``{"ok": False, ...}`` envelopes rather than raising.

Public API:
    ToolRegistry — register()/unregister()/invoke()/list_tools()/close().
    ToolInvocationError — raised for registry-level misuse (unknown tool etc.)
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from .mcp import MCPClient, MCPError, MockMCPServer, make_pipe_pair

ToolCallable = Callable[[dict[str, Any]], Any]


class ToolInvocationError(Exception):
    """Registry-level invocation failure (unknown tool, bad arguments)."""


class ToolRegistry:
    """Registry of host callables invokable over MCP.

    On construction the registry spawns an in-process MCP client/server pair
    (slice 41). :meth:`register` publishes a callable as an MCP tool on the
    mock server; :meth:`invoke` calls it via the MCP client, so the code path
    exercised here is identical to a real host↔edge MCP session.
    """

    def __init__(self) -> None:
        self._server = MockMCPServer()
        self._client: Optional[MCPClient]
        self._client, _ = make_pipe_pair(self._server)
        self._callables: dict[str, ToolCallable] = {}

    # -- registration --------------------------------------------------
    def register(
        self,
        name: str,
        func: ToolCallable,
        *,
        description: str = "",
        input_schema: Optional[dict[str, Any]] = None,
    ) -> None:
        """Publish ``func`` as the MCP tool ``name``.

        ``func`` receives the arguments dict and returns a JSON-serializable
        value. Handler exceptions are captured by the mock server and surface
        as ``{"ok": False, ...}`` envelopes from :meth:`invoke`.
        """
        if not name:
            raise ToolInvocationError("tool name must not be empty")
        if name in self._callables:
            raise ToolInvocationError(f"tool already registered: {name!r}")
        self._callables[name] = func

        def _handler(arguments: dict[str, Any]) -> Any:
            return func(arguments)

        self._server.register_tool(
            name, _handler, description=description, input_schema=input_schema
        )

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry and the mock server."""
        if name not in self._callables:
            raise ToolInvocationError(f"unknown tool: {name!r}")
        del self._callables[name]
        del self._server._tools[name]

    def tool_names(self) -> list[str]:
        """Return the registered tool names."""
        return list(self._callables)

    def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP tool descriptors via the client (round-trip check)."""
        if self._client is None:
            raise ToolInvocationError("registry is closed")
        return self._client.list_tools()

    # -- invocation -----------------------------------------------------
    def invoke(self, name: str, args: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Invoke ``name`` with ``args`` via the MCP client.

        Always returns a result envelope::

            {"ok": True,  "tool": name, "args": args, "result": value}
            {"ok": False, "tool": name, "args": args,
             "error": ..., "error_type": ...}

        ``error_type`` is one of "unknown_tool", "tool_error", "mcp_error",
        or "serialization_error".
        """
        arguments = dict(args or {})
        envelope: dict[str, Any] = {"tool": name, "args": arguments}
        if name not in self._callables:
            envelope.update(
                ok=False,
                error=f"unknown tool: {name!r}",
                error_type="unknown_tool",
            )
            return envelope
        if self._client is None:
            envelope.update(
                ok=False, error="registry is closed", error_type="mcp_error"
            )
            return envelope
        try:
            result = self._client.call_tool(name, arguments)
        except MCPError as exc:
            envelope.update(
                ok=False, error=str(exc), error_type="mcp_error"
            )
            return envelope
        if result.get("isError"):
            detail = _content_text(result)
            envelope.update(
                ok=False, error=detail, error_type="tool_error"
            )
            return envelope
        text = _content_text(result)
        try:
            value = json.loads(text) if text else None
        except json.JSONDecodeError as exc:
            envelope.update(
                ok=False,
                error=f"tool returned non-JSON content: {exc}",
                error_type="serialization_error",
            )
            return envelope
        envelope.update(ok=True, result=value)
        return envelope

    def close(self) -> None:
        """Shut down the MCP pair. Idempotent."""
        if self._client is not None:
            self._client.close()
            self._client = None
        self._server.stop()

    def __enter__(self) -> "ToolRegistry":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def _content_text(result: dict[str, Any]) -> str:
    """Extract the first text content block from an MCP tools/call result."""
    for block in result.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text", ""))
    return ""
