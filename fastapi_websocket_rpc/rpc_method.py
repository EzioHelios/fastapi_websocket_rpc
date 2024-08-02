import asyncio
import dataclasses
import inspect
import re
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Coroutine,
    Dict,
    Optional,
    Sequence,
    Union,
)

from fastapi import params
from fastapi._compat import (
    ModelField,
    _get_model_config,
    _model_dump,
    _normalize_errors,
)
from fastapi.datastructures import Default, DefaultPlaceholder
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.types import IncEx
from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import (
    get_parameterless_sub_dependant,
    get_typed_return_annotation,
)
from fastapi.utils import (
    create_cloned_field,
    create_response_field,
)
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.routing import get_name

from fastapi_websocket_rpc.schemas import RpcRequest, RpcResponse
from fastapi_websocket_rpc.utils import (
    MethodTypeEnum,
    get_body_field,
    get_dependant,
    solve_dependencies,
)

if TYPE_CHECKING:
    from fastapi_websocket_rpc.rpc_methods import RpcMethodsBase


def generate_unique_id(route: "RpcMethod") -> str:
    operation_id = route.name + route.path
    operation_id = re.sub(r"\W", "_", operation_id)
    return operation_id


def _prepare_response_content(
    res: Any,
    *,
    exclude_unset: bool,
    exclude_defaults: bool = False,
    exclude_none: bool = False,
) -> Any:
    if isinstance(res, BaseModel):
        read_with_orm_mode = getattr(_get_model_config(res), "read_with_orm_mode", None)
        if read_with_orm_mode:
            # Let from_orm extract the data from this model instead of converting
            # it now to a dict.
            # Otherwise, there's no way to extract lazy data that requires attribute
            # access instead of dict iteration, e.g. lazy relationships.
            return res
        return _model_dump(
            res,
            by_alias=True,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
        )
    elif isinstance(res, list):
        return [
            _prepare_response_content(
                item,
                exclude_unset=exclude_unset,
                exclude_defaults=exclude_defaults,
                exclude_none=exclude_none,
            )
            for item in res
        ]
    elif isinstance(res, dict):
        return {
            k: _prepare_response_content(
                v,
                exclude_unset=exclude_unset,
                exclude_defaults=exclude_defaults,
                exclude_none=exclude_none,
            )
            for k, v in res.items()
        }
    elif dataclasses.is_dataclass(res):
        return dataclasses.asdict(res)
    return res


async def serialize_response(
    *,
    field: Optional[ModelField] = None,
    response_content: Any,
    include: Optional[IncEx] = None,
    exclude: Optional[IncEx] = None,
    by_alias: bool = True,
    exclude_unset: bool = False,
    exclude_defaults: bool = False,
    exclude_none: bool = False,
    is_coroutine: bool = True,
) -> Any:
    if field:
        errors = []
        if not hasattr(field, "serialize"):
            # pydantic v1
            response_content = _prepare_response_content(
                response_content,
                exclude_unset=exclude_unset,
                exclude_defaults=exclude_defaults,
                exclude_none=exclude_none,
            )
        if is_coroutine:
            value, errors_ = field.validate(response_content, {}, loc=("response",))
        else:
            value, errors_ = await run_in_threadpool(
                field.validate, response_content, {}, loc=("response",)
            )
        if isinstance(errors_, list):
            errors.extend(errors_)
        elif errors_:
            errors.append(errors_)
        if errors:
            raise ResponseValidationError(
                errors=_normalize_errors(errors), body=response_content
            )

        if hasattr(field, "serialize"):
            return field.serialize(
                value,
                include=include,
                exclude=exclude,
                by_alias=by_alias,
                exclude_unset=exclude_unset,
                exclude_defaults=exclude_defaults,
                exclude_none=exclude_none,
            )

        return jsonable_encoder(
            value,
            include=include,
            exclude=exclude,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
        )
    else:
        return jsonable_encoder(response_content)


def get_async_endpoint(
    dependant: Dependant,
    values: Dict[str, Any],
    is_coroutine: bool,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    # Only called by get_request_handler. Has been split into its own function to
    # facilitate profiling endpoints, since inner functions are harder to profile.
    assert dependant.call is not None, "dependant.call must be a function"
    call = dependant.call

    if is_coroutine:
        return call
    else:
        return lambda *args, **kwargs: run_in_threadpool(call, *args, **kwargs)


def get_request_handler(
    dependant: Dependant,
    method_type: MethodTypeEnum,
    body_field: Optional[ModelField] = None,
    response_field: Optional[ModelField] = None,
    response_model_include: Optional[IncEx] = None,
    response_model_exclude: Optional[IncEx] = None,
    response_model_by_alias: bool = True,
    response_model_exclude_unset: bool = False,
    response_model_exclude_defaults: bool = False,
    response_model_exclude_none: bool = False,
    dependency_overrides_provider: Optional[Any] = None,
) -> Callable[["RpcMethodsBase", RpcRequest], Coroutine[Any, Any, RpcResponse]]:
    assert dependant.call is not None, "dependant.call must be a function"
    is_coroutine = asyncio.iscoroutinefunction(dependant.call)

    async def app(methods: "RpcMethodsBase", request: RpcRequest) -> RpcResponse:
        body = request.params
        solved_result = await solve_dependencies(
            request=request,
            dependant=dependant,
            body=body,
            dependency_overrides_provider=dependency_overrides_provider,
        )
        values, errors, _, _ = solved_result
        if errors:
            raise RequestValidationError(_normalize_errors(errors), body=body)
        else:
            async_call = get_async_endpoint(
                dependant=dependant, values=values, is_coroutine=is_coroutine
            )
            match method_type:
                case MethodTypeEnum.METHOD:
                    raw_response = await async_call(methods, **values)
                case MethodTypeEnum.CLASSMETHOD:
                    raw_response = await async_call(methods.__class__, **values)
                case _:
                    raw_response = await async_call(**values)

            result = await serialize_response(
                field=response_field,
                response_content=raw_response,
                include=response_model_include,
                exclude=response_model_exclude,
                by_alias=response_model_by_alias,
                exclude_unset=response_model_exclude_unset,
                exclude_defaults=response_model_exclude_defaults,
                exclude_none=response_model_exclude_none,
                is_coroutine=is_coroutine,
            )
            response = RpcResponse(id=request.id, result=result)
            return response

    return app


class RpcMethod:
    def __init__(
        self,
        path: str,
        endpoint: Callable[..., Any],
        method_type: MethodTypeEnum,
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
        generate_unique_id_function: Union[
            Callable[["RpcMethod"], str], DefaultPlaceholder
        ] = Default(generate_unique_id),
    ) -> None:
        self.path = path
        self.endpoint = endpoint
        self.method_type = method_type
        if isinstance(response_model, DefaultPlaceholder):
            return_annotation = get_typed_return_annotation(endpoint)
            response_model = return_annotation
        self.response_model = response_model
        self.summary = summary
        self.response_description = response_description
        self.deprecated = deprecated
        self.operation_id = operation_id
        self.response_model_include = response_model_include
        self.response_model_exclude = response_model_exclude
        self.response_model_by_alias = response_model_by_alias
        self.response_model_exclude_unset = response_model_exclude_unset
        self.response_model_exclude_defaults = response_model_exclude_defaults
        self.response_model_exclude_none = response_model_exclude_none
        self.name = get_name(endpoint) if name is None else name
        if isinstance(generate_unique_id_function, DefaultPlaceholder):
            current_generate_unique_id: Callable[["RpcMethod"], str] = (
                generate_unique_id_function.value
            )
        else:
            current_generate_unique_id = generate_unique_id_function
        self.unique_id = self.operation_id or current_generate_unique_id(self)

        if self.response_model:
            response_name = "Response_" + self.unique_id
            self.response_field = create_response_field(
                name=response_name,
                type_=self.response_model,
                mode="serialization",
            )
            # Create a clone of the field, so that a Pydantic submodel is not returned
            # as is just because it's an instance of a subclass of a more limited class
            # e.g. UserInDB (containing hashed_password) could be a subclass of User
            # that doesn't have the hashed_password. But because it's a subclass, it
            # would pass the validation and be returned as is.
            # By being a new field, no inheritance will be passed as is. A new model
            # will always be created.
            # TODO: remove when deprecating Pydantic v1
            self.secure_cloned_response_field: Optional[ModelField] = (
                create_cloned_field(self.response_field)
            )
        else:
            self.response_field = None  # type: ignore
            self.secure_cloned_response_field = None
        self.dependencies = list(dependencies or [])
        self.description = description or inspect.cleandoc(self.endpoint.__doc__ or "")
        # if a "form feed" character (page break) is found in the description text,
        # truncate description text to the content preceding the first "form feed"
        self.description = self.description.split("\f")[0].strip()

        assert callable(endpoint), "An endpoint must be a callable"
        self.dependant = get_dependant(
            path=self.path, call=self.endpoint, method_type=self.method_type
        )
        for depends in self.dependencies[::-1]:
            self.dependant.dependencies.insert(
                0,
                get_parameterless_sub_dependant(depends=depends, path=self.path),
            )
        self.body_field = get_body_field(dependant=self.dependant, name=self.unique_id)
        self.app = self.get_route_handler()

    def get_route_handler(
        self,
    ) -> Callable[["RpcMethodsBase", RpcRequest], Coroutine[Any, Any, RpcResponse]]:
        return get_request_handler(
            dependant=self.dependant,
            method_type=self.method_type,
            response_field=self.secure_cloned_response_field,
            response_model_include=self.response_model_include,
            response_model_exclude=self.response_model_exclude,
            response_model_by_alias=self.response_model_by_alias,
            response_model_exclude_unset=self.response_model_exclude_unset,
            response_model_exclude_defaults=self.response_model_exclude_defaults,
            response_model_exclude_none=self.response_model_exclude_none,
        )
