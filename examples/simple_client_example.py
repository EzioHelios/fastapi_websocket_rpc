import asyncio
from fastapi_websocket_rpc import WebSocketRpcClient
from simple_server_example import ConcatServer

PORT = 9000

async def run_client(uri):
    async with WebSocketRpcClient(uri, ConcatServer()) as client:
        # call concat on the other side
        response = await client.other.get_method("concat_cd")(c="hello", d=" world")
        # print result
        print(response)  # will print "hello world"
        # call concat on the other side
        response = await client.other.get_method("concat_ab")(a="hello", b=" world")
        # print result
        print(response)  # will print "hello world"
        # call concat on the other side
        response = await client.other.get_method("echo")(text="hello world")
        # print result
        print(response)  # will print "hello world"


if __name__ == "__main__":
    # run the client until it completes interaction with server
    asyncio.get_event_loop().run_until_complete(
        run_client(f"ws://localhost:{PORT}/ws")
    )
