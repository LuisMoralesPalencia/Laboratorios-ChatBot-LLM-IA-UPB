"""Cliente y ciclo de vida de procesos MCP del examen final."""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult
from mcp.types import Tool as MCPTool

from mcp_identity import ProcessIdentityContext, context_from_user, issue_bootstrap, issue_session_token
from sessions import Session, UserRepository
from tools import Tool, ToolExecutionError

SERVER_SCRIPT = Path(__file__).resolve().parent / "mcp_server.py"
USERS_PATH = Path(__file__).resolve().parent / "data" / "usuarios.json"
SESSION_META_KEY = "com.upb.exam/session-token"
_ENV_KEYS = ("PATH", "SystemRoot", "TEMP", "TMP", "PYTHONPATH")


class ProcessState(Enum):
    AUSENTE = "AUSENTE"
    INICIANDO = "INICIANDO"
    ACTIVO = "ACTIVO"
    CERRANDO = "CERRANDO"


@dataclass
class MCPProcessHandle:
    state: ProcessState
    client: "MCPClientProxy"
    worker: asyncio.Task[None]
    context: ProcessIdentityContext | None = None
    session_token: str | None = None


SessionInvalidator = Callable[[str], Awaitable[None] | None]


class MCPClientProxy:
    def __init__(self, requests: asyncio.Queue[tuple[str, tuple[Any, ...], dict[str, Any], asyncio.Future[Any]]], server_info: Any):
        self._requests = requests
        self.server_info = server_info

    async def _invoke(self, method: str, *args: Any, **kwargs: Any) -> Any:
        future = asyncio.get_running_loop().create_future()
        await self._requests.put((method, args, kwargs, future))
        return await future

    async def list_tools(self) -> Any:
        return await self._invoke("list_tools")

    async def call_tool(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("call_tool", *args, **kwargs)

    async def read_resource(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("read_resource", *args, **kwargs)


async def _client_worker(
    parameters: StdioServerParameters,
    ready: asyncio.Future[Any],
) -> None:
    requests: asyncio.Queue[tuple[str, tuple[Any, ...], dict[str, Any], asyncio.Future[Any]]] = asyncio.Queue()
    client: Client | None = None
    try:
        client = Client(parameters, raise_exceptions=True)
        await client.__aenter__()
        ready.set_result((requests, client.server_info))
        while True:
            method, args, kwargs, future = await requests.get()
            if method == "__close__":
                await client.__aexit__(None, None, None)
                client = None
                future.set_result(None)
                return
            try:
                future.set_result(await getattr(client, method)(*args, **kwargs))
            except Exception as exc:
                future.set_exception(exc)
    except Exception as exc:
        if not ready.done():
            ready.set_exception(exc)
    finally:
        if client is not None:
            await client.__aexit__(None, None, None)


def _minimal_environment(extra: dict[str, str]) -> dict[str, str]:
    environment = {key: value for key in _ENV_KEYS if (value := os.environ.get(key))}
    environment.update(extra)
    return environment


def _public_key_pem(public_key: Ed25519PublicKey) -> str:
    return public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def public_server_parameters() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
        env=_minimal_environment({"COM_UPB_MCP_MODE": "public"}),
    )


class MCPClientManager:
    """Administra un proceso público y como máximo uno autenticado por sesión."""

    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        public_key: Ed25519PublicKey,
        *,
        users_path: Path = USERS_PATH,
        session_invalidator: SessionInvalidator | None = None,
    ) -> None:
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("private_key debe ser Ed25519.")
        if not isinstance(public_key, Ed25519PublicKey):
            raise TypeError("public_key debe ser Ed25519.")
        self.private_key = private_key
        self.public_key = public_key
        self.users_path = users_path
        self.session_invalidator = session_invalidator
        self._users = UserRepository.from_json(users_path)
        self._handles: dict[str, MCPProcessHandle] = {}
        self._public_handle: MCPProcessHandle | None = None
        self._lock = asyncio.Lock()

    async def get_or_create_public(self) -> MCPProcessHandle:
        async with self._lock:
            if self._public_handle and self._public_handle.state is ProcessState.ACTIVO:
                return self._public_handle
            if self._public_handle:
                self._public_handle.state = ProcessState.CERRANDO
                await self._close_handle(self._public_handle)
            handle = await self._start_handle(public_server_parameters())
            self._public_handle = handle
            return handle

    async def get_or_create_authenticated(
        self,
        session: Session,
        *,
        session_id: str,
    ) -> MCPProcessHandle:
        async with self._lock:
            current = self._handles.get(session_id)
            if current is not None:
                if current.state is ProcessState.ACTIVO:
                    return current
                if current.state is ProcessState.INICIANDO:
                    raise RuntimeError("El proceso MCP de la sesión ya está iniciándose.")
                if current.state is ProcessState.CERRANDO:
                    raise RuntimeError("El proceso MCP de la sesión está cerrándose.")
                raise RuntimeError("El proceso MCP de la sesión no es reutilizable.")

            jti = secrets.token_urlsafe(24)
            context = context_from_user(
                session_id=session_id,
                telegram_user_id=session.telegram_user_id,
                jti=jti,
                users=self._users,
            )
            bootstrap = issue_bootstrap(context, self.private_key)
            session_token = issue_session_token(context, self.private_key)
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[str(SERVER_SCRIPT)],
                env=_minimal_environment(
                    {
                        "COM_UPB_MCP_MODE": "authenticated",
                        "COM_UPB_MCP_BOOTSTRAP_TOKEN": bootstrap,
                        "COM_UPB_MCP_BOOTSTRAP_PUBLIC_KEY": _public_key_pem(self.public_key),
                        "COM_UPB_MCP_USERS_PATH": str(self.users_path),
                    }
                ),
            )
            handle = await self._start_handle(
                parameters,
                context=context,
                session_token=session_token,
            )
            self._handles[session_id] = handle
            return handle

    async def call_tool(
        self,
        session_id: str | None,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> CallToolResult:
        handle = await self._handle_for(session_id)
        if handle.state is not ProcessState.ACTIVO:
            raise RuntimeError("El proceso MCP no está activo.")
        try:
            meta = {SESSION_META_KEY: handle.session_token} if handle.session_token else None
            return await handle.client.call_tool(tool_name, arguments or {}, meta=meta)
        except Exception:
            if session_id is not None:
                await self._fail_authenticated(session_id, handle)
            raise

    async def load_tools(self, session_id: str | None = None) -> list[Tool]:
        handle = await self._handle_for(session_id)
        listed = await handle.client.list_tools()
        return [to_tool(handle.client, mcp_tool, handle.session_token) for mcp_tool in listed.tools]

    async def close_session(self, session_id: str) -> None:
        async with self._lock:
            handle = self._handles.get(session_id)
            if handle is None:
                return
            handle.state = ProcessState.CERRANDO
            await self._close_handle(handle)
            self._handles.pop(session_id, None)

    async def close_all(self) -> None:
        async with self._lock:
            handles = list(self._handles.items())
            for session_id, handle in reversed(handles):
                handle.state = ProcessState.CERRANDO
                await self._close_handle(handle)
                self._handles.pop(session_id, None)
            if self._public_handle:
                self._public_handle.state = ProcessState.CERRANDO
                await self._close_handle(self._public_handle)
                self._public_handle = None

    def state(self, session_id: str | None = None) -> ProcessState:
        handle = self._public_handle if session_id is None else self._handles.get(session_id)
        return handle.state if handle else ProcessState.AUSENTE

    async def _handle_for(self, session_id: str | None) -> MCPProcessHandle:
        if session_id is None:
            return await self.get_or_create_public()
        handle = self._handles.get(session_id)
        if handle is None:
            raise RuntimeError("No existe un proceso MCP para la sesión.")
        return handle

    async def _start_handle(
        self,
        parameters: StdioServerParameters,
        *,
        context: ProcessIdentityContext | None = None,
        session_token: str | None = None,
    ) -> MCPProcessHandle:
        ready = asyncio.get_running_loop().create_future()
        worker = asyncio.create_task(_client_worker(parameters, ready))
        try:
            requests, server_info = await ready
            return MCPProcessHandle(
                ProcessState.ACTIVO,
                MCPClientProxy(requests, server_info),
                worker,
                context,
                session_token,
            )
        except Exception:
            worker.cancel()
            await worker
            raise

    async def _close_handle(self, handle: MCPProcessHandle) -> None:
        try:
            future = asyncio.get_running_loop().create_future()
            await handle.client._requests.put(("__close__", (), {}, future))
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=10)
            except asyncio.TimeoutError:
                handle.worker.cancel()
                try:
                    await handle.worker
                except asyncio.CancelledError:
                    pass
            await handle.worker
        finally:
            handle.state = ProcessState.AUSENTE

    async def _fail_authenticated(self, session_id: str, handle: MCPProcessHandle) -> None:
        async with self._lock:
            if self._handles.get(session_id) is not handle:
                return
            handle.state = ProcessState.CERRANDO
            await self._close_handle(handle)
            self._handles.pop(session_id, None)
        if self.session_invalidator:
            result = self.session_invalidator(session_id)
            if asyncio.iscoroutine(result):
                await result


def result_to_text(result: CallToolResult) -> str:
    text = "\n".join(block.text for block in result.content if block.type == "text")
    if result.is_error:
        raise ToolExecutionError(text)
    return text


def to_tool(client: MCPClientProxy | Client, mcp_tool: MCPTool, session_token: str | None = None) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        meta = {SESSION_META_KEY: session_token} if session_token else None
        result = await client.call_tool(mcp_tool.name, arguments, meta=meta)
        return result_to_text(result)

    annotations = mcp_tool.annotations
    return Tool(
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        parameters=mcp_tool.input_schema,
        run=run,
        read_only=bool(annotations and annotations.read_only_hint),
        origin=client.server_info.name if client.server_info else "mcp",
    )


async def load_mcp_tools(client: Client, session_token: str | None = None) -> list[Tool]:
    listed = await client.list_tools()
    return [to_tool(client, mcp_tool, session_token) for mcp_tool in listed.tools]


async def read_resource_text(client: Client, uri: str) -> str:
    result = await client.read_resource(uri)
    return "\n".join(content.text for content in result.contents if hasattr(content, "text"))


async def _demo() -> None:
    manager = MCPClientManager(
        Ed25519PrivateKey.generate(),
        Ed25519PrivateKey.generate().public_key(),
    )
    try:
        public = await manager.get_or_create_public()
        print(await read_resource_text(public.client, "academico://calendario"))
    finally:
        await manager.close_all()


if __name__ == "__main__":
    asyncio.run(_demo())
