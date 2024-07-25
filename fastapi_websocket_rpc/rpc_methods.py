import asyncio
from enum import Enum
import os
import sys
from threading import RLock
import typing
import copy

from fastapi.types import DecoratedCallable
from pydantic import BaseModel, validate_call

if typing.TYPE_CHECKING:
    from fastapi_websocket_rpc.rpc_channel import RpcChannel

from .utils import gen_uid

PING_RESPONSE = "pong"
# list of internal methods that can be called from remote
# EXPOSED_BUILT_IN_METHODS = ['_ping_', '_get_channel_id_']
# NULL default value - indicating no response was received
class BuiltInMethods(str, Enum):
    ping = "ping"
    get_channel_id = "get_channel_id"


class NoResponseType:
    pass

NoResponse = NoResponseType()

class RpcCall:
    _single_lock = RLock()
    _rpc_dict: typing.Dict[str, typing.Tuple[str, typing.Callable]] = {}

    def __new__(cls, *args, **kwargs):
        with RpcCall._single_lock:
            if not hasattr(RpcCall, "_instance"):
                RpcCall._instance = object.__new__(cls)

        return RpcCall._instance

    def __call__(self, method_name: str | None = None) -> typing.Callable[[DecoratedCallable], DecoratedCallable]:

        def decorator(func: DecoratedCallable) -> DecoratedCallable:
            nonlocal method_name
            if method_name is None:
                method_name = func.__name__
            if method_name in self._rpc_dict:
                raise ValueError(f"Method name {method_name} is already registered")
            vfunc = validate_call(func)
            self._rpc_dict[method_name] = (func.__name__, vfunc)
            return vfunc

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
        """ Simple copy ctor - overriding classes may need to override copy as well."""
        return copy.copy(self)

    @rpc_call(BuiltInMethods.ping)
    async def _ping_(self) -> str:
        """
        built in ping for keep-alive
        """
        return PING_RESPONSE

    @rpc_call(BuiltInMethods.get_channel_id)
    async def _get_channel_id_(self) -> str:
        """
        built in channel id to better identify your remote
        """
        return self._channel.id
    
    def get_method(self, method_name: str) -> typing.Callable | None:
        real_name, method = rpc_call._rpc_dict.get(method_name, (None, None))
        if real_name is not None and hasattr(self, real_name):
            return method
        return None


class ProcessDetails(BaseModel):
    pid: int = os.getpid()
    cmd: typing.List[str] = sys.argv
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
    async def call_me_back(self, method_name: str="", args: typing.Dict[str, typing.Any]={}) -> str | None:
        if self.channel is not None:
            # generate a uid we can use to track this request
            call_id = gen_uid()
            # Call async -  without waiting to avoid locking the event_loop
            asyncio.create_task(self.channel.async_call(
                method_name, args=args, call_id=call_id))
            # return the id- which can be used to check the response once it's received
            return call_id

    @rpc_call("get_response")
    async def get_response(self, call_id: str | None="") -> typing.Any:
        if self.channel is not None:
            res = self.channel.get_saved_response(call_id)
            self.channel.clear_saved_call(call_id)
            return res

    @rpc_call("echo")
    async def echo(self, text: str) -> str:
        return text

MethodsT = typing.TypeVar("MethodsT", bound=RpcMethodsBase)
