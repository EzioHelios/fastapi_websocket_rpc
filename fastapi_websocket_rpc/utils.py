from copy import deepcopy
import datetime
import enum
import inspect
import os
import re
from typing import (
    Callable,
    Dict,
    Type,
    TypeVar,
    Annotated,
    Any,
    Callable,
    List,
    Optional,
    Tuple,
    Union,
    cast,
    get_args,
    get_origin,
)
import uuid
from datetime import timedelta
from random import SystemRandom, randrange

from fastapi import params
from fastapi._compat import (
    PYDANTIC_V2,
    ModelField,
    Required,
    Undefined,
    copy_field_info,
    create_body_model,
    get_annotation_from_field_info,
    get_missing_field_error,
    lenient_issubclass,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import (
    add_non_field_param_to_dependency,
    add_param_to_fields,
    get_flat_dependant,
    get_param_sub_dependant,
    get_typed_signature,
    is_body_param,
    is_coroutine_callable,
)
from fastapi.security import SecurityScopes
from fastapi.utils import (
    create_response_field,
    get_path_param_names,
)
import pydantic
from pydantic.fields import FieldInfo

from fastapi_websocket_rpc.schemas import RpcRequest, RpcResponse


class RandomUtils(object):
    @staticmethod
    def gen_cookie_id() -> str:
        return os.urandom(16).hex()

    @staticmethod
    def gen_uid() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def gen_token(size=256) -> str:
        if size % 2 != 0:
            raise ValueError("Size in bits must be an even number.")
        return (
            uuid.UUID(int=SystemRandom().getrandbits(size // 2)).hex
            + uuid.UUID(int=SystemRandom().getrandbits(size // 2)).hex
        )

    @staticmethod
    def random_datetime(
        start: datetime.datetime | None = None, end: datetime.datetime | None = None
    ) -> datetime.datetime:
        """
        This function will return a random datetime between two datetime
        objects.
        If no range is provided - a last 24-hours range will be used
        :param datetime,None start: start date for range, now if None
        :param datetime,None end: end date for range, next 24-hours if None
        """
        # build range
        now = datetime.datetime.now()
        start = start or now
        end = end or now + timedelta(hours=24)
        delta = end - start
        int_delta = (delta.days * 24 * 60 * 60) + delta.seconds
        # Random time
        random_second = randrange(int_delta)
        # Return random date
        return start + timedelta(seconds=random_second)


gen_uid = RandomUtils.gen_uid
gen_token = RandomUtils.gen_token


class StringUtils(object):
    @staticmethod
    def convert_camelcase_to_underscore(name: str, lower: bool = True) -> str:
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        res = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1)
        if lower:
            return res.lower()
        else:
            return res.upper()


def pydantic_serialize(model: pydantic.BaseModel, **kwargs) -> str:
    if PYDANTIC_V2:
        return model.model_dump_json(**kwargs)
    else:
        return model.json(**kwargs)


BaseModelT = TypeVar("BaseModelT", bound=pydantic.BaseModel)


def pydantic_parse(model: Type[BaseModelT], data, **kwargs) -> BaseModelT:
    if PYDANTIC_V2:
        return model.model_validate(data, **kwargs)
    else:
        return model.parse_obj(data, **kwargs)


class MethodTypeEnum(enum.StrEnum):
    METHOD = "METHOD"
    CLASSMETHOD = "CLASSMETHOD"
    STATICMETHOD = "STATICMETHOD"

    def __str__(self):
        return self.name


def get_method_type_from_sig(function: Callable) -> MethodTypeEnum:
    sig = inspect.signature(function)
    first = next(iter(sig.parameters.values()), None)
    if first:
        if first.name == "self":
            return MethodTypeEnum.METHOD
        if first.name == "cls":
            return MethodTypeEnum.CLASSMETHOD
    return MethodTypeEnum.STATICMETHOD

def get_body_field(*, dependant: Dependant, name: str) -> Optional[ModelField]:
    dependant = get_flat_dependant(dependant)
    if not dependant.body_params:
        return None
    body_params: List[ModelField] = []
    for param in dependant.body_params:
        embed = getattr(param.field_info, "embed", None)
        if embed:
            body_params.append(param)
            continue
        annotation = param.field_info.annotation
        if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
            for name, field_info in annotation.model_fields.items():
                body_params.append(ModelField(field_info=field_info, name=name))
        else:
            setattr(param.field_info, "embed", True)
    model_name = "Body_" + name
    BodyModel = create_body_model(
        fields=body_params, model_name=model_name
    )
    required = any(True for f in body_params if f.required)
    BodyFieldInfo_kwargs: Dict[str, Any] = {
        "annotation": BodyModel,
        "alias": "body",
    }
    if not required:
        BodyFieldInfo_kwargs["default"] = None
    final_field = create_response_field(
        name="body",
        type_=BodyModel,
        required=required,
        alias="body",
        field_info=params.Body(**BodyFieldInfo_kwargs),
    )
    return final_field


async def request_body_to_args(
    required_params: List[ModelField],
    received_body: Union[Dict[str, Any], None],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    values = {}
    errors: List[Dict[str, Any]] = []
    if required_params:
        for field in required_params:
            embed = getattr(field.field_info, "embed", None)
            field_alias_omitted = not embed
            if field_alias_omitted:
                received_body = {field.alias: received_body}
            loc = ("body", field.alias)

            value: Optional[Any] = None
            if received_body is not None:
                try:
                    value = received_body.get(field.alias)
                except AttributeError:
                    errors.append(get_missing_field_error(loc))
                    continue
            if value is None:
                if field.required:
                    errors.append(get_missing_field_error(loc))
                else:
                    values[field.name] = deepcopy(field.default)
                continue

            v_, errors_ = field.validate(value, values, loc=loc)

            if isinstance(errors_, list):
                errors.extend(errors_)
            elif errors_:
                errors.append(errors_)
            else:
                values[field.name] = v_
    return values, errors


def analyze_param(
    *,
    param_name: str,
    annotation: Any,
    value: Any,
    is_path_param: bool,
) -> Tuple[Any, Optional[params.Depends], Optional[ModelField]]:
    field_info = None
    depends = None
    type_annotation: Any = Any
    if (
        annotation is not inspect.Signature.empty
        and get_origin(annotation) is Annotated
    ):
        annotated_args = get_args(annotation)
        type_annotation = annotated_args[0]
        fastapi_annotations = [
            arg
            for arg in annotated_args[1:]
            if isinstance(arg, (FieldInfo, params.Depends))
        ]
        assert (
            len(fastapi_annotations) <= 1
        ), f"Cannot specify multiple `Annotated` FastAPI arguments for {param_name!r}"
        fastapi_annotation = next(iter(fastapi_annotations), None)
        if isinstance(fastapi_annotation, FieldInfo):
            # Copy `field_info` because we mutate `field_info.default` below.
            field_info = copy_field_info(
                field_info=fastapi_annotation, annotation=annotation
            )
            assert field_info.default is Undefined or field_info.default is Required, (
                f"`{field_info.__class__.__name__}` default value cannot be set in"
                f" `Annotated` for {param_name!r}. Set the default value with `=` instead."
            )
            if value is not inspect.Signature.empty:
                assert not is_path_param, "Path parameters cannot have default values"
                field_info.default = value
            else:
                field_info.default = Required
        elif isinstance(fastapi_annotation, params.Depends):
            depends = fastapi_annotation
    elif annotation is not inspect.Signature.empty:
        type_annotation = annotation

    if isinstance(value, params.Depends):
        assert depends is None, (
            "Cannot specify `Depends` in `Annotated` and default value"
            f" together for {param_name!r}"
        )
        assert field_info is None, (
            "Cannot specify a FastAPI annotation in `Annotated` and `Depends` as a"
            f" default value together for {param_name!r}"
        )
        depends = value
    elif isinstance(value, FieldInfo):
        assert field_info is None, (
            "Cannot specify FastAPI annotations in `Annotated` and default value"
            f" together for {param_name!r}"
        )
        field_info = value
        if PYDANTIC_V2:
            field_info.annotation = type_annotation

    if depends is not None and depends.dependency is None:
        depends.dependency = type_annotation

    if lenient_issubclass(
        type_annotation,
        (
            RpcRequest,
            RpcResponse,
        ),
    ):
        assert depends is None, f"Cannot specify `Depends` for type {type_annotation!r}"
        assert (
            field_info is None
        ), f"Cannot specify FastAPI annotation for type {type_annotation!r}"
    elif field_info is None and depends is None:
        default_value = value if value is not inspect.Signature.empty else Required
        if is_path_param:
            # We might check here that `default_value is Required`, but the fact is that the same
            # parameter might sometimes be a path parameter and sometimes not. See
            # `tests/test_infer_param_optionality.py` for an example.
            field_info = params.Path(annotation=type_annotation)
        else:
            field_info = params.Body(annotation=type_annotation, default=default_value)

    field = None
    if field_info is not None:
        if is_path_param:
            assert isinstance(field_info, params.Path), (
                f"Cannot use `{field_info.__class__.__name__}` for path param"
                f" {param_name!r}"
            )
        elif (
            isinstance(field_info, params.Param)
            and getattr(field_info, "in_", None) is None
        ):
            field_info.in_ = params.ParamTypes.query
        use_annotation = get_annotation_from_field_info(
            type_annotation,
            field_info,
            param_name,
        )
        if not field_info.alias and getattr(field_info, "convert_underscores", None):
            alias = param_name.replace("_", "-")
        else:
            alias = field_info.alias or param_name
        field_info.alias = alias
        field = create_response_field(
            name=param_name,
            type_=use_annotation,
            default=field_info.default,
            alias=alias,
            required=field_info.default in (Required, Undefined),
            field_info=field_info,
        )

    return type_annotation, depends, field


def get_dependant(
    *,
    path: str,
    call: Callable[..., Any],
    name: Optional[str] = None,
    security_scopes: Optional[List[str]] = None,
    use_cache: bool = True,
    method_type: Optional[MethodTypeEnum] = None,
) -> Dependant:
    path_param_names = get_path_param_names(path)
    endpoint_signature = get_typed_signature(call)
    signature_params = endpoint_signature.parameters
    dependant = Dependant(
        call=call,
        name=name,
        path=path,
        security_scopes=security_scopes,
        use_cache=use_cache,
    )
    if method_type == MethodTypeEnum.METHOD:
        assert "self" in signature_params, "Method must have self parameter"
        signature_params = {
            k: v for k, v in signature_params.items() if v.name != "self"
        }
    elif method_type == MethodTypeEnum.CLASSMETHOD:
        assert "cls" in signature_params, "Classmethod must have cls parameter"
        signature_params = {
            k: v for k, v in signature_params.items() if v.name != "cls"
        }
    for param_name, param in signature_params.items():
        is_path_param = param_name in path_param_names
        type_annotation, depends, param_field = analyze_param(
            param_name=param_name,
            annotation=param.annotation,
            value=param.default,
            is_path_param=is_path_param,
        )
        if depends is not None:
            sub_dependant = get_param_sub_dependant(
                param_name=param_name,
                depends=depends,
                path=path,
                security_scopes=security_scopes,
            )
            dependant.dependencies.append(sub_dependant)
            continue
        if add_non_field_param_to_dependency(
            param_name=param_name,
            type_annotation=type_annotation,
            dependant=dependant,
        ):
            assert (
                param_field is None
            ), f"Cannot specify multiple FastAPI annotations for {param_name!r}"
            continue
        assert param_field is not None
        if is_body_param(param_field=param_field, is_path_param=is_path_param):
            dependant.body_params.append(param_field)
        else:
            add_param_to_fields(field=param_field, dependant=dependant)
    return dependant


async def solve_dependencies(
    *,
    request: RpcRequest,
    dependant: Dependant,
    body: Optional[Dict[str, Any]] = None,
    response: Optional[RpcResponse] = None,
    dependency_overrides_provider: Optional[Any] = None,
    dependency_cache: Optional[Dict[Tuple[Callable[..., Any], Tuple[str]], Any]] = None,
) -> Tuple[
    Dict[str, Any],
    List[Any],
    RpcResponse,
    Dict[Tuple[Callable[..., Any], Tuple[str]], Any],
]:
    values: Dict[str, Any] = {}
    errors: List[Any] = []
    if response is None:
        response = RpcResponse(result=None)
    dependency_cache = dependency_cache or {}
    sub_dependant: Dependant
    for sub_dependant in dependant.dependencies:
        sub_dependant.call = cast(Callable[..., Any], sub_dependant.call)
        sub_dependant.cache_key = cast(
            Tuple[Callable[..., Any], Tuple[str]], sub_dependant.cache_key
        )
        call = sub_dependant.call
        use_sub_dependant = sub_dependant
        if (
            dependency_overrides_provider
            and dependency_overrides_provider.dependency_overrides
        ):
            original_call = sub_dependant.call
            call = getattr(
                dependency_overrides_provider, "dependency_overrides", {}
            ).get(original_call, original_call)
            use_path: str = sub_dependant.path  # type: ignore
            use_sub_dependant = get_dependant(
                path=use_path,
                call=call,
                name=sub_dependant.name,
                security_scopes=sub_dependant.security_scopes,
            )

        solved_result = await solve_dependencies(
            request=request,
            dependant=use_sub_dependant,
            body=body,
            response=response,
            dependency_overrides_provider=dependency_overrides_provider,
            dependency_cache=dependency_cache,
        )
        (
            sub_values,
            sub_errors,
            _,  # the subdependency returns the same response we have
            sub_dependency_cache,
        ) = solved_result
        dependency_cache.update(sub_dependency_cache)
        if sub_errors:
            errors.extend(sub_errors)
            continue
        if sub_dependant.use_cache and sub_dependant.cache_key in dependency_cache:
            solved = dependency_cache[sub_dependant.cache_key]
        elif is_coroutine_callable(call):
            solved = await call(**sub_values)
        else:
            solved = await run_in_threadpool(call, **sub_values)
        if sub_dependant.name is not None:
            values[sub_dependant.name] = solved
        if sub_dependant.cache_key not in dependency_cache:
            dependency_cache[sub_dependant.cache_key] = solved
    if dependant.body_params:
        (
            body_values,
            body_errors,
        ) = await request_body_to_args(  # body_params checked above
            required_params=dependant.body_params, received_body=body
        )
        values.update(body_values)
        errors.extend(body_errors)
    if dependant.request_param_name and isinstance(request, RpcRequest):
        values[dependant.request_param_name] = request
    if dependant.response_param_name:
        values[dependant.response_param_name] = response
    if dependant.security_scopes_param_name:
        values[dependant.security_scopes_param_name] = SecurityScopes(
            scopes=dependant.security_scopes
        )
    return values, errors, response, dependency_cache
