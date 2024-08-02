import asyncio
from fastapi_websocket_rpc import WebSocketRpcClient
from fastapi_websocket_rpc.rpc_methods import RpcMethodsBase
from fast_crud_server_example import IHeader, IUserRead, simple_user_list

PORT = 9000

async def run_client(uri):
    async with WebSocketRpcClient(uri, RpcMethodsBase()) as client:
        jack_token = simple_user_list[0].token
        response = await client.other.get_method("create")(
            name="puppy", header=IHeader(access_token=jack_token)
        )
        user_read = IUserRead.model_validate(response.result)
        print(user_read)
        response = await client.other.get_method("update")(
            id=user_read.id, tags=["pet"], header=IHeader(access_token=jack_token)
        )
        user_read = IUserRead.model_validate(response.result)
        print(user_read)


if __name__ == "__main__":
    # run the client until it completes interaction with server
    asyncio.get_event_loop().run_until_complete(
        run_client(f"ws://localhost:{PORT}/ws")
    )
