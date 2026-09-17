"""Safe YAML-to-JSON boundary for the Node runner and profile installer."""
import json
import math
import re
import sys

import yaml

PREFIX = "tag:yaml.org,2002:"
INTEGER = re.compile(r"^[-+]?(?:0b[01]+|0o[0-7]+|0x[0-9a-fA-F]+|[0-9]+)$")
FLOAT = re.compile(r"^(?:[-+]?[0-9]+(?:\.[0-9]*)?(?:[eE][-+]?[0-9]+)?|\.[0-9]+(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")
BOOLEAN = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
NULL = re.compile(r"^(?:~|null|Null|NULL|)$")


def scalar(loader, node):
    value = loader.construct_scalar(node)
    kind = node.tag.removeprefix(PREFIX)
    pattern = {"int": INTEGER, "float": FLOAT, "bool": BOOLEAN, "null": NULL}[kind]
    if not pattern.fullmatch(value):
        raise ValueError("Invalid tagged scalar")
    if kind == "null":
        return None
    if kind == "bool":
        return value.lower() == "true"
    if kind == "int":
        unsigned = value.lstrip("+-")
        return int(value, 0 if unsigned.startswith(("0b", "0o", "0x")) else 10)
    normalized = value.lower()
    if normalized.lstrip("+-") in (".inf", ".nan"):
        return None  # JSON.stringify uses null for non-finite numbers.
    return float(value)


class JsonLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = {}
    yaml_constructors = {
        key: value for key, value in yaml.SafeLoader.yaml_constructors.items()
        if key in {None, PREFIX + "str", PREFIX + "seq", PREFIX + "map"}
    }

    def resolve(self, kind, value, implicit):
        if kind is yaml.ScalarNode and implicit[0]:
            if INTEGER.fullmatch(value) or FLOAT.fullmatch(value):
                try:
                    if value.lower().lstrip("+-") not in (".inf", ".nan") and not math.isfinite(float(value)):
                        return PREFIX + "str"
                except ValueError:
                    pass  # Base-prefixed integers are handled by the constructor.
        return super().resolve(kind, value, implicit)

    def construct_mapping(self, node, deep=False):
        # JSON_SCHEMA treats an untagged << as a literal key, not a merge.
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key is None:
                key = "null"
            elif isinstance(key, bool):
                key = "true" if key else "false"
            elif not isinstance(key, str):
                if isinstance(key, (int, float)):
                    key = str(key)
                else:
                    raise ValueError("Complex mapping keys are unsupported")
            if key in result:
                raise ValueError("Duplicate mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


for name, pattern, starts in (
    ("null", NULL, ["~", "n", "N", ""]),
    ("bool", BOOLEAN, list("tTfF")),
    ("int", INTEGER, list("-+0123456789")),
    ("float", FLOAT, list("-+0123456789.")),
):
    JsonLoader.add_implicit_resolver(PREFIX + name, pattern, starts)
    JsonLoader.add_constructor(PREFIX + name, scalar)


class PatchLoader(JsonLoader):
    pass


PatchLoader.add_constructor(PREFIX + "js", lambda loader, node: loader.construct_scalar(node))


def parse(text, mode="json"):
    if not isinstance(text, str) or mode not in ("json", "patch"):
        raise ValueError("Invalid YAML bridge request")
    return yaml.load(text, Loader=PatchLoader if mode == "patch" else JsonLoader)


def main():
    try:
        request = json.load(sys.stdin)
        json.dump(parse(request["text"], request.get("mode", "json")), sys.stdout, allow_nan=False)
        sys.stdout.write("\n")
    except Exception:
        # Parser exceptions can contain source lines, including credentials.
        print("Invalid YAML input or unsupported value", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
