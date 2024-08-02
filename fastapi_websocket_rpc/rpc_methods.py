import asyncio
from enum import StrEnum
import os
import sys
from threading import RLock
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)
import copy

from fastapi import params
from fastapi.datastructures import Default
from fastapi.types import DecoratedCallable, IncEx
from pydantic import BaseModel

from fastapi_websocket_rpc.rpc_method import RpcMethod

if TYPE_CHECKING:
    from fastapi_websocket_rpc.rpc_channel import RpcChannel

from fastapi_websocket_rpc.utils import gen_uid, get_method_type_from_sig

PING_RESPONSE = "pong"


# list of internal methods that can be called from remote
# EXPOSED_BUILT_IN_METHODS = ['_ping_', '_get_channel_id_']
# NULL default value - indicating no response was received
class BuiltInMethods(StrEnum):
    ping = "ping"
    get_channel_id = "get_channel_id"


class NoResponseType:
    pass


NoResponse = NoResponseType()


class RpcCall:
    _single_lock = RLock()
    _rpc_dict: Dict[str, Tuple[str, RpcMethod]] = {}

    def __new__(cls, *args, **kwargs):
        with RpcCall._single_lock:
            if not hasattr(RpcCall, "_instance"):
                RpcCall._instance = object.__new__(cls)

        return RpcCall._instance

    def __call__(
        self,
        path: str | None = None,
        *,
        response_model: Any = Default(None),
        dependencies: Optional[Sequence[params.Depends]] = None,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        response_description: str = "Successful Response",
        deprecated: Optional[bool] = None,
        name: Optional[str] = None,
        operation_id: Optional[str] = None,
        response_model_include: Optional[IncEx] = None,
        response_model_exclude: Optional[IncEx] = None,
        response_model_by_alias: bool = True,
        response_model_exclude_unset: bool = False,
        response_model_exclude_defaults: bool = False,
        response_model_exclude_none: bool = False,
    ) -> Callable[[DecoratedCallable], DecoratedCallable]:

        def decorator(function: DecoratedCallable) -> DecoratedCallable:
            nonlocal path
            if path is None:
                path = function.__name__
            if path in self._rpc_dict:
                raise ValueError(f"Method name {path} is already registered")
            rpc_method = RpcMethod(
                path,
                function,
                get_method_type_from_sig(function),
                response_model=response_model,
                dependencies=dependencies,
                summary=summary,
                description=description,
                response_description=response_description,
                deprecated=deprecated,
                name=name,
                operation_id=operation_id,
                response_model_include=response_model_include,
                response_model_exclude=response_model_exclude,
                response_model_by_alias=response_model_by_alias,
                response_model_exclude_unset=response_model_exclude_unset,
                response_model_exclude_defaults=response_model_exclude_defaults,
                response_model_exclude_none=response_model_exclude_none,
            )
            self._rpc_dict[path] = (function.__name__, rpc_method)
            return function

        return decorator


rpc_call = RpcCall()


class RpcMethodsBase:
    """
    The basic interface RPC channels expects method groups to implement.
     - create copy of the method object
     - set channel
     - provide '_ping_' for keep-alive
    """

    _channel: "RpcChannel"

    def __init__(self):
        pass

    def _set_channel_(self, channel: "RpcChannel") -> None:
        """
        Allows the channel to share access to its functions to the methods once nested under it
        """
        self._channel = channel

    @property
    def channel(self) -> "RpcChannel":
        return self._channel

    def _copy_(self):
        """Simple copy ctor - overriding classes may need to override copy as well."""
        return copy.copy(self)

    async def _ping_(self) -> str:
        """
        built in ping for keep-alive
        """
        return PING_RESPONSE

    async def _get_channel_id_(self) -> str:
        """
        built in channel id to better identify your remote
        """
        return self._channel.id

    def get_method(self, method_name: str) -> RpcMethod | None:
        real_name, method = rpc_call._rpc_dict.get(method_name, (None, None))
        if real_name is not None and hasattr(self, real_name):
            return method
        return None


class ProcessDetails(BaseModel):
    pid: int = os.getpid()
    cmd: List[str] = sys.argv
    workingdir: str = os.getcwd()


class RpcUtilityMethods(RpcMethodsBase):
    """
    A simple set of RPC functions useful for management and testing
    """

    def __init__(self):
        """
        endpoint (WebsocketRPCEndpoint): the endpoint these methods are loaded into
        """
        super().__init__()

    @rpc_call("get_process_details")
    async def get_process_details(self) -> ProcessDetails:
        return ProcessDetails()

    @rpc_call("call_me_back")
    async def call_me_back(
        self, method_name: str = "", args: Dict[str, Any] = {}
    ) -> str | None:
        if self.channel is not None:
            # generate a uid we can use to track this request
            call_id = gen_uid()
            # Call async -  without waiting to avoid locking the event_loop
            asyncio.create_task(
                self.channel.async_call(method_name, args=args, call_id=call_id)
            )
            # return the id- which can be used to check the response once it's received
            return call_id

    @rpc_call("get_response")
    async def get_response(self, call_id: str | None = "") -> Any:
        if self.channel is not None:
            res = self.channel.get_saved_response(call_id)
            self.channel.clear_saved_call(call_id)
            return res

    @rpc_call("echo")
    async def echo(self, text: str) -> str:
        return text


MethodsT = TypeVar("MethodsT", bound=RpcMethodsBase)
