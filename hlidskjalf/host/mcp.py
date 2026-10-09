"""Slice 41 — MCP JSON-RPC client (Phase G: Host Integration).

A minimal, stdlib-only client implementing the JSON-RPC 2.0 framing used by
the Model Context Protocol (MCP) over stdio: newline-delimited JSON messages
written to the server's stdin and read from its stdout.

Public API:
    MCPClient            — JSON-RPC client; ``list_tools()`` / ``call_tool()``.
    MockMCPServer        — in-process mock MCP server for tests; registers
                           fake tools and speaks the same wire format.
    MCPError             — raised when the remote side reports an error.
    make_pipe_pair()     — connect an MCPClient to a MockMCPServer in-process.

Wire protocol (JSON-RPC 2.0, one message per line):
    -> {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    <- {"jsonrpc": "2.0", "id": 1, "result": {"tools": [...]}}
    -> {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "ping", "arguments": {}}}
    <- {"jsonrpc": "2.0", "id": 2, "result": {"content": [...], "isError": false}}
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Any, BinaryIO, Callable, Optional


class MCPError(Exception):
    """The MCP server reported a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"MCP error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class JSONRPCClient:
    """Synchronous JSON-RPC 2.0 client over newline-delimited binary streams.

    ``reader`` supplies server→client bytes, ``writer`` accepts client→server
    bytes. Both are used as binary streams and closed by :meth:`close`.
    """

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self._reader = reader
        self._writer = writer
        self._lock = threading.Lock()
        self._next_id = 0

    def _send_request(self, method: str, params: Any = None) -> Any:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            message = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                message["params"] = params
            payload = (json.dumps(message) + "\n").encode("utf-8")
            self._writer.write(payload)
            self._writer.flush()
            line = self._reader.readline()
        if not line:
            raise MCPError(-32000, "server closed the connection")
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MCPError(-32700, f"malformed JSON-RPC response: {exc}") from exc
        if response.get("id") != request_id:
            raise MCPError(-32603, "response id mismatch")
        if "error" in response:
            error = response["error"] or {}
            raise MCPError(
                int(error.get("code", -32000)),
                str(error.get("message", "unknown error")),
                error.get("data"),
            )
        return response.get("result")

    def close(self) -> None:
        """Close the underlying streams."""
        for stream in (self._reader, self._writer):
            try:
                stream.close()
            except OSError:
                pass


class MCPClient(JSONRPCClient):
    """MCP client exposing ``list_tools()`` and ``call_tool(name, args)``."""

    @classmethod
    def spawn(
        cls,
        command: list[str],
        *,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> "MCPClient":
        """Spawn a real MCP server subprocess and connect over its stdio."""
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=cwd,
        )
        assert proc.stdin is not None and proc.stdout is not None
        client = cls(proc.stdout, proc.stdin)
        client._proc = proc  # type: ignore[attr-defined]
        return client

    def list_tools(self) -> list[dict[str, Any]]:
        """Return the server's tool descriptors."""
        result = self._send_request("tools/list", {}) or {}
        return list(result.get("tools", []))

    def call_tool(
        self, name: str, arguments: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Call a tool; returns the MCP result object (content/isError)."""
        result = self._send_request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        if not isinstance(result, dict):
            raise MCPError(-32603, "unexpected tools/call result shape")
        return result

    def close(self) -> None:
        super().close()
        proc = getattr(self, "_proc", None)
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass


ToolHandler = Callable[[dict[str, Any]], Any]


class MockMCPServer:
    """In-process mock MCP server for tests.

    Fake tools are registered with :meth:`register_tool`; the server speaks
    JSON-RPC 2.0 over the supplied streams and runs its dispatch loop on a
    background thread.
    """

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def register_tool(
        self,
        name: str,
        handler: ToolHandler,
        *,
        description: str = "",
        input_schema: Optional[dict[str, Any]] = None,
    ) -> None:
        """Register a fake tool. ``handler`` receives the arguments dict."""
        self._tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema or {"type": "object"},
            "handler": handler,
        }

    def _handle(self, message: dict[str, Any]) -> Optional[dict[str, Any]]:
        msg_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        try:
            if message.get("jsonrpc") != "2.0":
                raise ValueError("not a JSON-RPC 2.0 message")
            if method == "tools/list":
                tools = [
                    {
                        "name": name,
                        "description": spec["description"],
                        "inputSchema": spec["inputSchema"],
                    }
                    for name, spec in self._tools.items()
                ]
                return self._ok(msg_id, {"tools": tools})
            if method == "tools/call":
                name = params.get("name")
                spec = self._tools.get(name)
                if spec is None:
                    raise KeyError(f"unknown tool: {name!r}")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise TypeError("arguments must be an object")
                try:
                    value = spec["handler"](arguments)
                except Exception as exc:  # tool-level failure -> isError
                    return self._ok(
                        msg_id,
                        {"content": [{"type": "text",
                                      "text": f"{type(exc).__name__}: {exc}"}],
                         "isError": True},
                    )
                return self._ok(
                    msg_id,
                    {"content": [{"type": "text", "text": json.dumps(value)}],
                     "isError": False},
                )
            raise LookupError(f"unknown method: {method!r}")
        except Exception as exc:  # noqa: BLE001 — must serialize all failures
            return self._fail(msg_id, -32603, f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _ok(msg_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _fail(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    def serve(self, reader: BinaryIO, writer: BinaryIO) -> None:
        """Run the JSON-RPC dispatch loop on the current thread."""
        while not self._stop.is_set():
            line = reader.readline()
            if not line:
                break
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                response = self._fail(None, -32700, "parse error")
            else:
                response = self._handle(message)
            if response is not None:
                writer.write((json.dumps(response) + "\n").encode("utf-8"))
                writer.flush()

    def serve_background(self, reader: BinaryIO, writer: BinaryIO) -> None:
        """Run :meth:`serve` on a daemon thread."""
        self._thread = threading.Thread(
            target=self.serve, args=(reader, writer), daemon=True,
            name="mock-mcp-server",
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the dispatch loop to exit and join the thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def run_stdio(self) -> None:
        """Serve MCP over the process stdio (for subprocess-based servers)."""
        import sys

        self.serve(sys.stdin.buffer, sys.stdout.buffer)


def make_pipe_pair(
    server: Optional[MockMCPServer] = None,
) -> tuple[MCPClient, MockMCPServer]:
    """Wire an MCPClient to a MockMCPServer via in-process OS pipes.

    The client writes requests into one pipe (client→server) and reads
    responses from another (server→client); the server loop runs on a
    background thread. Returns ``(client, server)``.
    """
    server = server or MockMCPServer()
    client_to_server_r, client_to_server_w = os.pipe()
    server_to_client_r, server_to_client_w = os.pipe()
    server.serve_background(
        os.fdopen(client_to_server_r, "rb"),
        os.fdopen(server_to_client_w, "wb"),
    )
    client = MCPClient(
        os.fdopen(server_to_client_r, "rb"),
        os.fdopen(client_to_server_w, "wb"),
    )
    return client, server


if __name__ == "__main__":  # pragma: no cover — manual demo server
    _demo = MockMCPServer()
    _demo.register_tool("ping", lambda args: {"pong": True}, description="Ping.")
    _demo.register_tool(
        "echo",
        lambda args: {"echo": args.get("text", "")},
        description="Echo text back.",
    )
    _demo.run_stdio()
