"""The pydantic base and shared field constraints of the harness-internal formats.

ADR-025 decision 6 describes the run request, run result and live frames with
pydantic instead of per-class handwritten ``__post_init__``/``to_payload``/
``from_payload`` triples. Everything the formats share lives here exactly
once: the frozen no-extra model base with camelCase wire aliases, the board
error boundary around pydantic's validation errors, and the bounded
string/identifier/count/path/tuple/:class:`FrozenJson` field types. Ordinary
pydantic semantics apply end to end: a field with a default may be omitted on
the wire and on the constructor, extra members are refused, and both the
snake_case and camelCase spellings of a name are accepted (``populate_by_name``
is what the constructors already use). Deep JSON stays copy-isolated and
immutable through :class:`FrozenJson`. The public board schemas in
``protocol/schemas.py`` are deliberately untouched by this module.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Optional, Self, Tuple

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)
from pydantic.alias_generators import to_camel
from pydantic_core import core_schema

from ..errors import BoardError
from ..json_codec import canonical_json, decode_strict_json

#: The largest count an internal format carries; matches the JSON safe-integer
#: range the board's own bounded JSON rules use.
MAX_COUNT = 2**53 - 1
#: Existing return codes: negative values are POSIX signal terminations
#: (``-15`` for SIGTERM, ``-9`` for SIGKILL, as ``subprocess`` reports them),
#: nonnegative values include unsigned 32-bit Windows process exit codes.
MIN_EXIT_CODE = -(2**31)
MAX_EXIT_CODE = 2**32 - 1
MAX_PATH_BYTES = 4096

_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def fail(message: str, **details: Any) -> BoardError:
    """The one invalid-argument failure of every internal format check."""
    return BoardError("INVALID_ARGUMENT", message, **details)


def check_text(value: Any, label: str, *, maximum: int) -> str:
    """One nonempty NUL-free string of at most ``maximum`` UTF-8 bytes."""
    if not isinstance(value, str) or not value or "\0" in value or len(value.encode()) > maximum:
        raise fail(f"{label} must be a nonempty string of at most {maximum} UTF-8 bytes", field=label)
    return value


def _wire_label(model: type) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", model.__name__).lower()


class _BoardValueError(BoardError, ValueError):
    """A validation failure that is both the board's structured error and the
    value error pydantic treats as a failed union branch.

    pydantic's smart union probes every branch by calling the model's
    ``__init__``; a plain ``BoardError`` (a ``RuntimeError``) would abort the
    whole union instead of failing one branch, so the error this module raises
    from a failed construction is also a ``ValueError``.
    """


def _validation_failure(model: type, error: ValidationError) -> BoardError:
    issues = error.errors(include_url=False, include_context=False, include_input=False)[:4]
    summary = "; ".join(f"{'.'.join(str(part) for part in issue.get('loc', ())) or 'value'}: "
                        f"{issue.get('type')}" for issue in issues)
    return _BoardValueError("INVALID_ARGUMENT", f"the {_wire_label(model)} value is not valid",
                            reason=summary[:400])


@dataclass(frozen=True, init=False, repr=False)
class FrozenJson:
    """One bounded JSON value kept as canonical text.

    The stored text cannot be mutated through any handle, and every read parses
    a fresh copy, so a caller can never quietly change a frozen value's payload
    through a shared dict. Construction canonicalizes the value once.
    """

    text: str

    def __init__(self, value: Any):
        try:
            text = canonical_json(value)
        except (TypeError, ValueError) as error:
            raise fail("value is not bounded JSON", reason=str(error)[:200]) from None
        object.__setattr__(self, "text", text)

    @classmethod
    def from_value(cls, value: Any, label: str, *, maximum: int) -> "FrozenJson":
        """Canonicalize and bound one decoded JSON field."""
        try:
            text = canonical_json(value)
        except (TypeError, ValueError) as error:
            raise fail(f"{label} is not bounded JSON", field=label, reason=str(error)[:200]) from None
        if len(text.encode()) > maximum:
            raise fail(f"{label} exceeds its {maximum}-byte bound", field=label)
        try:
            decode_strict_json(text)
        except ValueError as error:
            raise fail(f"{label} is not strict JSON", field=label, reason=str(error)[:200]) from None
        instance = cls.__new__(cls)
        object.__setattr__(instance, "text", text)
        return instance

    @property
    def value(self) -> Any:
        return json.loads(self.text)

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"FrozenJson({self.text[:120]})"

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> core_schema.CoreSchema:
        def validate(value: Any) -> "FrozenJson":
            if isinstance(value, FrozenJson):
                return value
            return cls(value)

        return core_schema.no_info_plain_validator_function(
            validate, serialization=core_schema.plain_serializer_function_ser_schema(lambda v: v.value))


def _within_bound(maximum: int):
    def check(frozen: FrozenJson) -> FrozenJson:
        if len(frozen.text.encode()) > maximum:
            raise fail(f"the value exceeds its {maximum}-byte bound", limit=maximum)
        return frozen
    return check


def FrozenJsonAt(maximum: int) -> Any:
    """One required bounded-JSON field of at most ``maximum`` canonical bytes."""
    return Annotated[FrozenJson, AfterValidator(_within_bound(maximum))]


def OptionalFrozenJsonAt(maximum: int) -> Any:
    """One optional bounded-JSON field of at most ``maximum`` canonical bytes."""
    return Annotated[Optional[FrozenJson], AfterValidator(
        lambda frozen: None if frozen is None else _within_bound(maximum)(frozen))]


def Text(maximum: int) -> Any:
    """One required nonempty NUL-free string of at most ``maximum`` UTF-8 bytes."""
    return Annotated[str, AfterValidator(lambda value: check_text(value, "value", maximum=maximum))]


def OptionalText(maximum: int) -> Any:
    """The optional form of :func:`Text`."""
    return Annotated[Optional[str], AfterValidator(
        lambda value: None if value is None else check_text(value, "value", maximum=maximum))]


def RawText(maximum: int) -> Any:
    """Any string of at most ``maximum`` UTF-8 bytes; empty and NUL-bearing are
    existing values (for example a final raw answer text)."""
    def check(value: str) -> str:
        if len(value.encode()) > maximum:
            raise fail(f"the value must be a string of at most {maximum} UTF-8 bytes", limit=maximum)
        return value
    return Annotated[str, AfterValidator(check)]


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise fail("the value must be an identifier of 1..128 characters")
    return value


def _hex64(value: str) -> str:
    if not _SHA256.fullmatch(value):
        raise fail("the value must be a lowercase sha-256 hex digest")
    return value


Identifier = Annotated[str, AfterValidator(_identifier)]
Hex64 = Annotated[str, AfterValidator(_hex64)]
Count = Annotated[int, Field(ge=0, le=MAX_COUNT)]
NonNegativeInt = Annotated[int, Field(ge=0)]
ExitCode = Annotated[int, Field(ge=MIN_EXIT_CODE, le=MAX_EXIT_CODE)]


def _absolute_path(value: str) -> str:
    check_text(value, "value", maximum=MAX_PATH_BYTES)
    # Existing absolute paths of both platform syntaxes are legitimate values:
    # POSIX "/..." and Windows drive "C:\..." / "C:/..." and UNC "\\..." forms.
    posix_absolute = value.startswith("/")
    windows_absolute = (len(value) >= 3 and value[1] == ":" and value[2] in "\\/" and value[0].isalpha()) \
        or value.startswith("\\\\")
    if not posix_absolute and not windows_absolute:
        raise fail("the value must be an absolute path")
    return value


AbsolutePath = Annotated[str, AfterValidator(_absolute_path)]


def JsonTuple(item: Any, *, max_items: int) -> Any:
    """One tuple field of at most ``max_items`` items whose wire form is a JSON
    array of them.

    The field itself is validated in pydantic's lax mode so a JSON array (a
    Python list after decoding) converts into the immutable tuple the Python
    value holds; the element type keeps the model's strict behaviour, which the
    library enforces inside a lax tuple too: a probe on the project's pydantic
    shows a bool never passes an ``int`` element and a ``str`` never passes one
    either, so accepting arrays does not loosen the element types.
    """
    return Annotated[Tuple[item, ...], Field(strict=False, max_length=max_items)]


#: The exact internal frame-version field. The strict mode keeps the scalar
#: strict — ``True`` and ``1.0`` never pass it (a strict ``int`` refuses a bool
#: and a float, where ``Literal[1]`` alone would match them by equality) — and
#: the ``ge``/``le`` pair refuses every other integer. A frame carries ``1`` by
#: default, so the wire may also omit it.
FormatVersion = Annotated[int, Field(strict=True, ge=1, le=1)]


class InternalModel(BaseModel):
    """The common base of the harness-internal formats (ADR-025 decision 6).

    The base is immutable and closed: frozen instances, no extra members,
    strict scalar types (a bool never passes an int field, a number never
    passes a string field), and snake_case construction beside the camelCase
    wire names. Ordinary default semantics apply on the wire exactly as on the
    constructor: a field with a default may be omitted, a required field may
    not, and an unknown member is refused at every nesting level. Validation
    failures surface as :class:`BoardError`, never as pydantic's own error
    type.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as error:
            raise _validation_failure(type(self), error) from None

    @classmethod
    def from_payload(cls, value: Any) -> Self:
        """Validate and unfreeze one model from its internal JSON form."""
        if not isinstance(value, dict):
            raise fail(f"the {_wire_label(cls)} payload must be an object")
        try:
            return cls.model_validate(value)
        except ValidationError as error:
            raise _validation_failure(cls, error) from None

    def to_payload(self) -> dict:
        """The exact wire projection of one model, aliases and all."""
        return self.model_dump(mode="json", by_alias=True)


__all__ = [
    "AbsolutePath", "Count", "ExitCode", "FormatVersion", "FrozenJson", "FrozenJsonAt", "Hex64",
    "Identifier", "InternalModel", "JsonTuple", "MAX_COUNT", "MAX_EXIT_CODE", "MAX_PATH_BYTES",
    "MIN_EXIT_CODE", "NonNegativeInt", "OptionalFrozenJsonAt", "OptionalText", "RawText", "Text",
    "check_text", "fail",
]
