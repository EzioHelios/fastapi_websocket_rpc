import asyncio
from typing import List, Type
from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect

from .connection_manager import ConnectionManager
from .rpc_channel import OnConnectCallback, OnDisconnectCallback, RpcChannel
from .rpc_methods import RpcMethodsBase
from .logger import get_logger
from .schemas import WebSocketFrameType
from .simplewebsocket import SimpleWebSocket, JsonSerializingWebSocket

logger = get_logger("RPC_ENDPOINT")


class WebSocketSimplifier(SimpleWebSocket):
    """
    Simple wrapper over FastAPI WebSocket to ensure unified interface for send/recv
    """

    def __init__(self, websocket: WebSocket, frame_type: WebSocketFrameType = WebSocketFrameType.Text):
        self.websocket = websocket
        self.frame_type = frame_type
        if self.frame_type == WebSocketFrameType.Binary:
            self.send = self.websocket.send_bytes
            self.recv = self.websocket.receive_bytes
        else:
            self.send = self.websocket.send_text
            self.recv = self.websocket.receive_text

    # This method is only useful on websocket_rpc_client. Here on endpoint file, it has nothing to connect to.
    async def connect(self, uri: str, **connect_kwargs):
        pass

    async def send(self, data) -> None:
        ...

    async def recv(self) -> str | bytes:
        ...

    async def close(self, code: int = 1000):
        return await self.websocket.close(code)


class WebsocketRPCEndpoint:
    """
    A websocket RPC sever endpoint, exposing RPC methods
    """

    def __init__(self, methods: RpcMethodsBase | None = None,
                 manager: ConnectionManager | None= None,
                 on_disconnect: List[OnDisconnectCallback] | None= None,
                 on_connect: List[OnConnectCallback] | None= None,
                 frame_type: WebSocketFrameType = WebSocketFrameType.Text,
                 serializing_socket_cls: Type[SimpleWebSocket] = JsonSerializingWebSocket,
                 rpc_channel_get_remote_id: bool = False):
        """[summary]

        Args:
            methods (RpcMethodsBase): RPC methods to expose
            manager ([ConnectionManager], optional): Connection tracking object. Defaults to None (i.e. new ConnectionManager()).
            on_disconnect (List[coroutine], optional): Callbacks per disconnection
            on_connect(List[coroutine], optional): Callbacks per connection (Server spins the callbacks as a new task, not waiting on it.)
        """
        self.manager = manager if manager is not None else ConnectionManager()
        self.methods = methods if methods is not None else RpcMethodsBase()
        # Event handlers
        self._on_disconnect = on_disconnect
        self._on_connect = on_connect
        self._frame_type = frame_type
        self._serializing_socket_cls = serializing_socket_cls
        self._rpc_channel_get_remote_id = rpc_channel_get_remote_id

    async def main_loop(self, websocket: WebSocket, client_id: str | None = None, **kwargs):
        try:
            await self.manager.connect(websocket)
            logger.info(f"Client connected", {
                        'remote_address': websocket.client})
            simple_websocket = self._serializing_socket_cls(
                WebSocketSimplifier(websocket, frame_type=self._frame_type))
            channel = RpcChannel(self.methods, simple_websocket,
                                 sync_channel_id=self._rpc_channel_get_remote_id, **kwargs)
            # register connect / disconnect handler
            channel.register_connect_handler(self._on_connect)
            channel.register_disconnect_handler(self._on_disconnect)
            # trigger connect handlers
            await channel.on_connect()
            try:
                while True:
                    data = await simple_websocket.recv()
                    await channel.on_message(data)
            except WebSocketDisconnect:
                logger.info(
                    f"Client disconnected - {websocket.client.port} :: {channel.id}") # type: ignore
                await self.handle_disconnect(websocket, channel)
            except Exception as e:
                logger.exception(e)
                # cover cases like - RuntimeError('Cannot call "send" once a close message has been sent.')
                logger.info(
                    f"Client connection failed - {websocket.client.port} :: {channel.id}") # type: ignore
                await self.handle_disconnect(websocket, channel)
        except:
            logger.exception(f"Failed to serve - {websocket.client.port}") # type: ignore
            self.manager.disconnect(websocket)

    async def handle_disconnect(self, websocket: WebSocket, channel: RpcChannel):
        self.manager.disconnect(websocket)
        await channel.on_disconnect()

    async def on_connect(self, channel: RpcChannel, websocket: WebSocket) -> None:
        """
        Called upon new client connection
        """
        # Trigger connect callback if available
        if (self._on_connect is not None):
            asyncio.gather(*[cb(channel) for cb in self._on_connect])

    def register_route(self, router: FastAPI | APIRouter, path="/ws"):
        """
        Register this endpoint as a default websocket route on the given router
        Args:
            router: FastAPI router to load route onto
            path (str, optional): the route path. Defaults to "/ws".
        """

        @router.websocket(path)
        async def websocket_endpoint(websocket: WebSocket):
            await self.main_loop(websocket)
