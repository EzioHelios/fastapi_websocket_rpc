from multiprocessing import Process
import os
from typing import List, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field
import pytest
import uvicorn
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, WebSocket
from fastapi_websocket_rpc import WebsocketRPCEndpoint, rpc_call
from fastapi_websocket_rpc.rpc_methods import RpcMethodsBase
from fastapi_websocket_rpc.schemas import RpcResponse
from fastapi_websocket_rpc.utils import gen_uid
from fastapi_websocket_rpc.websocket_rpc_client import WebSocketRpcClient


# Configurable
PORT = int(os.environ.get("PORT") or "9000")
# Random ID
CLIENT_ID = gen_uid()
uri = f"ws://localhost:{PORT}/ws/{CLIENT_ID}"


class UserBase(BaseModel):
    name: str


class User(UserBase):
    id: UUID = Field(default_factory=uuid4)
    tags: List[str] = Field(default_factory=list)
    token: str = Field(default_factory=gen_uid)


simple_user_list = [
    User(name="Jack", tags=["man"], token="21d0dbd71f5248338396bfffc212e004"),
    User(name="Ross", tags=["woman"]),
]


def find_user(id: UUID | str) -> User:
    for user in simple_user_list:
        if str(user.id) == str(id):
            return user
    raise HTTPException(status_code=404, detail="User not found")


class IUserCreate(UserBase):
    pass


class IUserUpdate(BaseModel):
    id: UUID
    name: Optional[str] = None
    tags: Optional[List[str]] = None


class IUserRead(UserBase):
    id: UUID
    tags: List[str]


class IHeader(BaseModel):
    access_token: str


def get_access_token_embedded(header: IHeader = Body(embed=True)):
    return header.access_token


def get_access_token(header: IHeader):
    return header.access_token


def get_user_embedded(access_token: str = Depends(get_access_token_embedded)) -> User:
    for user in simple_user_list:
        if user.token == access_token:
            return user
    raise HTTPException(status_code=401, detail=f"Invalid token: {access_token}")


def get_user(access_token: str = Depends(get_access_token)) -> User:
    for user in simple_user_list:
        if user.token == access_token:
            return user
    raise HTTPException(status_code=401, detail=f"Invalid token: {access_token}")


class RpcCRUD(RpcMethodsBase):

    @rpc_call("create", response_model=IUserRead)
    async def create(
        self, obj_in: IUserCreate, current_user: User = Depends(get_user_embedded)
    ) -> User:
        global simple_user_list
        user = User(**obj_in.model_dump())
        simple_user_list.append(user)
        return user

    @rpc_call("update", response_model=IUserRead)
    async def multiply(
        self, obj_in: IUserUpdate, current_user: User = Depends(get_user)
    ) -> User:
        obj = find_user(obj_in.id)
        obj_dict = obj_in.model_dump(exclude_unset=True)
        for k in obj_dict:
            setattr(obj, k, obj_dict[k])
        return obj

    @rpc_call("read", response_model=IUserRead)
    async def read(self, id: UUID) -> User:
        obj = find_user(id)
        return obj


def setup_calc_server():
    app = FastAPI()
    router = APIRouter()
    # expose calculator methods
    endpoint = WebsocketRPCEndpoint(RpcCRUD())
    # init the endpoint

    @router.websocket("/ws/{client_id}")
    async def websocket_rpc_endpoint(websocket: WebSocket, client_id: str):
        await endpoint.main_loop(websocket, client_id)

    app.include_router(router)
    uvicorn.run(app, port=PORT)


@pytest.fixture()
def server():
    # Run the server as a separate process
    proc = Process(target=setup_calc_server, args=(), daemon=True)
    proc.start()
    yield proc
    proc.kill()  # Cleanup after test


@pytest.mark.asyncio
async def test_custom_server_methods(server):
    """
    Test rpc with calling custom methods on server sides
    """
    async with WebSocketRpcClient(
        uri,
        # we don't expose anything to the server
        RpcMethodsBase(),
        default_response_timeout=4,
    ) as client:
        jack_token = simple_user_list[0].token
        response = await client.other.get_method("create")(
            name="puppy", header=IHeader(access_token=jack_token)
        )
        assert isinstance(response, RpcResponse)
        user_read = IUserRead.model_validate(response.result)
        assert user_read.name == "puppy"
        response = await client.other.get_method("update")(
            id=user_read.id, tags=["pet"], access_token=jack_token
        )
        assert isinstance(response, RpcResponse)
        user_read = IUserRead.model_validate(response.result)
        assert user_read.tags == ["pet"]
