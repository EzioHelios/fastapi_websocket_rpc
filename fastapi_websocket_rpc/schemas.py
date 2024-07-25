from enum import Enum
from typing import Any, Dict, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel

from .utils import is_pydantic_pre_v2

UUID = str


class error_code(int, Enum):
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    PARSE_ERROR = -32700


class RpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: Optional[UUID] = None
    method: str
    params: Optional[Dict] = {}


ResponseT = TypeVar("ResponseT")


# Check pydantic version to handle deprecated GenericModel
if is_pydantic_pre_v2():
    from pydantic.generics import GenericModel

    class RpcResponse(GenericModel, Generic[ResponseT]):
        jsonrpc: Literal["2.0"] = "2.0"
        id: Optional[UUID] = None
        result: ResponseT

else:

    class RpcResponse(BaseModel, Generic[ResponseT]):
        jsonrpc: Literal["2.0"] = "2.0"
        id: Optional[UUID] = None
        result: ResponseT


class RpcError(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    code: int
    message: str
    data: Optional[Any] = None


class RpcErrorResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[UUID] = None
    error: Optional[RpcError]


class WebSocketFrameType(str, Enum):
    Text = "text"
    Binary = "binary"
