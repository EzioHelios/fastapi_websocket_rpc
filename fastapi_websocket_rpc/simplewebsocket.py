import json
from abc import ABC, abstractmethod
from typing import Awaitable, TypeVar

from .utils import pydantic_serialize


class SimpleWebSocket(ABC):
    """
    Abstract base class for all websocket related wrappers.
    """
    def __init__(self, *args, **kwargs):
        ...

    @abstractmethod
    def connect(self, uri: str, **connect_kwargs) -> Awaitable:
        ...

    @abstractmethod
    def send(self, data) -> Awaitable:
        ...

    # If return None, then it means Connection is closed, and we stop receiving and close.
    @abstractmethod
    def recv(self) -> Awaitable:
        ...

    @abstractmethod
    def close(self, code: int = 1000) -> Awaitable:
        ...


class JsonSerializingWebSocket(SimpleWebSocket):
    def __init__(self, websocket: SimpleWebSocket):
        self._websocket = websocket

    async def connect(self, uri: str, **connect_kwargs):
        await self._websocket.connect(uri, **connect_kwargs)

    def _serialize(self, msg):
        return pydantic_serialize(msg)

    def _deserialize(self, buffer):
        return json.loads(buffer)

    async def send(self, data):
        await self._websocket.send(self._serialize(data))

    async def recv(self):
        msg = await self._websocket.recv()
        if msg is None:
            return None
        return self._deserialize(msg)

    async def close(self, code: int = 1000):
        await self._websocket.close(code)


SerializerT = TypeVar("SerializerT", bound=SimpleWebSocket)
ClientT = TypeVar("ClientT", bound=SimpleWebSocket)
