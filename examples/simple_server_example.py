from pydantic import BaseModel
import uvicorn
from fastapi import Body, Depends, FastAPI
from fastapi_websocket_rpc import WebsocketRPCEndpoint, rpc_call
from fastapi_websocket_rpc.rpc_methods import RpcUtilityMethods

def get_args(c: str = Body(), d: str = Body()) -> "IParameter":
    return IParameter(a=c, b=d)

class IParameter(BaseModel):
    a: str
    b: str

# Methods to expose to the clients
class ConcatServer(RpcUtilityMethods):
    @rpc_call()
    async def concat_cd(self, param: IParameter = Depends(get_args)):
        return param.a + param.b
    
    @rpc_call()
    async def concat_ab(self, param: IParameter):
        return param.a + param.b
    
# Init the FAST-API app
app =  FastAPI()
# Create an endpoint and load it with the methods to expose
endpoint = WebsocketRPCEndpoint(ConcatServer())
# add the endpoint to the app
endpoint.register_route(app)

if __name__ == "__main__":
    # Start the server itself
    uvicorn.run(app, host="0.0.0.0", port=9000)
