import os
from typing import List, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field
import uvicorn
from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi_websocket_rpc import WebsocketRPCEndpoint, rpc_call
from fastapi_websocket_rpc.rpc_methods import RpcMethodsBase
from fastapi_websocket_rpc.utils import gen_uid


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
    User(name="Ross", tags=["woman"])
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


def get_access_token(header: IHeader = Body(embed=True)):
    return header.access_token


def get_user(access_token: str = Depends(get_access_token)) -> User:
    for user in simple_user_list:
        if user.token == access_token:
            return user
    raise HTTPException(status_code=401, detail=f"Invalid token: {access_token}")


class RpcCRUD(RpcMethodsBase):

    @rpc_call("create", response_model=IUserRead)
    async def create(self, obj_in: IUserCreate, user: User = Depends(get_user)) -> User:
        global simple_user_list
        user = User(**obj_in.model_dump())
        simple_user_list.append(user)
        return user

    @rpc_call("update", response_model=IUserRead)
    async def multiply(self, obj_in: IUserUpdate) -> User:
        obj = find_user(obj_in.id)
        obj_dict = obj_in.model_dump(
            exclude_unset=True
        )
        for k in obj_dict:
            setattr(obj, k, obj_dict[k])
        return obj
        
    @rpc_call("read", response_model=IUserRead)
    async def read(self, id: UUID) -> User:
        obj = find_user(id)
        return obj


# Init the FAST-API app
app =  FastAPI()
# Create an endpoint and load it with the methods to expose
endpoint = WebsocketRPCEndpoint(RpcCRUD())
# add the endpoint to the app
endpoint.register_route(app)

if __name__ == "__main__":
    # Start the server itself
    uvicorn.run(app, host="0.0.0.0", port=9000)
