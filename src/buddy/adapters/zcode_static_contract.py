"""Free static verification of the allowlist chain in the public ZCode CLI bundle (L6-A3).

The L6-A2 review smuggled two bundles past a checker built from local regex
assembly: a membership test dropped as a bare statement before an unconditional
register, and an ``allowedTools`` object parked in a third argument the named
registration function never reads. This module replaces that assembly with a
bounded, string/comment/bracket-aware tokenizer and complete-template matching
over the supported JavaScript shapes. It never executes the bundle, embeds no
general interpreter and adds no dependency; anything outside the supported
templates fails closed with a specific reason.

The verified mechanisms are structural, never name-bound: the named
``registerBuiltInTools`` must build one Set from its options parameter's
``allowedTools`` and gate a single per-tool for-of registration through either
the complete positive membership condition or a top-level short-circuit OR
chain whose membership rejection is one complete operand and whose register
call is the last. Every other rejection operand must be a pure comparison, a
verified Set membership test or a verified pure name predicate, so discarded
tests, comma or assignment decoys, extra tails, second registrations, rewritten
Sets and side-effecting calls cannot pass. The registered argument is the loop
variable itself or a transform that provably returns every Read/Glob/Grep entry
unchanged. The named ``resolveBuiltInToolAllowlist`` must return the config
``toolAllowlist`` directly, through one same-value local, or through the
recognized alias-map/explore-filter/root-child helper chain whose every helper
preserves the read-only names, and the registration must actually be invoked
with that resolver's call as the unique ``allowedTools`` value of a direct
object literal at the options parameter's position. Minified identifiers are
read from the text and followed to their declarations; versions, hashes and
named-certificate knowledge play no part. The proof covers only the packaged
code; whether a live session enforced anything stays with the protocol facts,
the unified tool evidence and the separately approved native checks.
"""
from __future__ import annotations

import re

__all__ = ["allowlist_chain_problem"]

#: One verified function or call region is tokenized within this many characters.
_MAX_REGION_CHARS = 16384
#: A constant identifier is followed through at most this many indirections.
_MAX_CONST_DEPTH = 4
#: Purity verification of a helper chain is bounded and cycle-safe.
_MAX_PURE_DEPTH = 8
#: One parsed region consumes at most this many tokens.
_MAX_REGION_TOKENS = 20000
#: The read-only tool names every alias, filter and transform must preserve.
_READ_ONLY_TOOL_NAMES = frozenset({"Read", "Glob", "Grep"})

_REGISTER_EXPORT = "registerBuiltInTools"
_RESOLVE_EXPORT = "resolveBuiltInToolAllowlist"

_REGISTER_NOT_NAMED = "the public bundle does not name registerBuiltInTools"
_RESOLVE_NOT_NAMED = "the public bundle does not name resolveBuiltInToolAllowlist"
_REGISTER_AMBIGUOUS = "the public bundle names registerBuiltInTools ambiguously"
_RESOLVE_AMBIGUOUS = "the public bundle names resolveBuiltInToolAllowlist ambiguously"
_REGISTER_NOT_FUNCTION = "registerBuiltInTools is not a function definition in the public bundle"
_RESOLVE_NOT_FUNCTION = "resolveBuiltInToolAllowlist is not a function definition in the public bundle"
_UNRECOGNIZED_FLOW = ("registerBuiltInTools registers through an unrecognized control flow "
                      "instead of the allowedTools Set membership filter over metadata.name")
_NO_SET_FILTER = ("registerBuiltInTools builds no Set membership filter from its options allowedTools parameter")
_NO_FOR_OF = "registerBuiltInTools filters no per-tool for-of loop over the built-in tools"
_NO_REGISTRY_PARAM = "registerBuiltInTools does not register on one of its own registry parameters"
_REGISTER_OUTSIDE = "registerBuiltInTools carries registrations outside the one allowlist-gated flow"
_SET_REWRITTEN = "registerBuiltInTools rewrites its allowlist Set after the declaration that builds it"
_BAD_PREAMBLE = ("registerBuiltInTools carries a preamble declaration that is not a pure read "
                 "or a verified helper call")
_BAD_OPERAND = ("registerBuiltInTools' rejection chain carries an operand that is not a pure comparison, "
                "a verified Set membership test or a verified name predicate")
_BAD_REGISTER_ARG = ("registerBuiltInTools does not register the loop variable itself or a verified "
                     "tool-preserving transform of it")
_TRANSFORM_PROBLEM = "registerBuiltInTools' tool transform does not preserve the read-only tools' identity"
_RES_NO_READ = ("resolveBuiltInToolAllowlist does not read the config toolAllowlist "
                "through a supported complete return chain")
_RES_HELPER = ("resolveBuiltInToolAllowlist's allowlist helpers do not match the supported "
               "alias-map, explore-filter and root-child templates")
_RES_ALIAS = "resolveBuiltInToolAllowlist's alias map sends a read-only tool name onto another tool"
_RES_EXPLORE = "resolveBuiltInToolAllowlist's explore filter does not keep every read-only tool name"
_NO_WIRING = "registerBuiltInTools is not called with allowedTools from resolveBuiltInToolAllowlist"

#: Calls a verified helper may make on a parameter or local, all pure on strings.
_STRING_METHODS = frozenset({"trim", "indexOf", "slice", "toLowerCase", "toUpperCase", "startsWith",
                             "endsWith", "includes", "charAt", "substring", "substr", "concat",
                             "padStart", "padEnd", "split", "replace", "codePointAt"})
#: Writing the Set variable after its declaration invalidates the filter.
_ASSIGNMENT_PUNCT = frozenset({"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=",
                               ">>>=", "&&=", "||=", "??=", "**=", "++", "--"})

_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER_RE = re.compile(r"0[xXbBoO][0-9a-fA-F]+n?|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?n?")
#: One combined scan collects every plausible constant assignment and both
#: exported mechanism names, so the whole bundle text is regex-scanned once.
_ASSIGNMENT_RE = re.compile(r'(?<![\w$".])([A-Za-z_$][A-Za-z0-9_$]*)\s*=(?![=>])')
_EXPORT_RE = re.compile(r'([\w$]+)\(\s*([\w$]+)\s*,\s*"(' + _REGISTER_EXPORT + r"|" + _RESOLVE_EXPORT
                        + r')"\s*\)')
#: Punctuators longest first so ``===`` never reads as ``=`` nor ``?.`` as ``?``.
_PUNCTUATORS = tuple(sorted(
    (">>>=", "===", "!==", "**=", "...", "<<=", ">>=", ">>>", "&&=", "||=", "??=",
     "=>", "?.", "==", "!=", "<=", ">=", "&&", "||", "??", "++", "--",
     "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>",
     "(", ")", "{", "}", "[", "]", ";", ",", ".", ":", "?", "!", "<", ">",
     "+", "-", "*", "/", "%", "&", "|", "^", "~", "="), key=len, reverse=True))


class _ParseError(Exception):
    """The region left the supported token or template grammar; fail closed."""


def _token_iter(text: str, start: int, end: int):
    """Yield ``(kind, value, start, end)`` tokens, consuming strings and comments.

    Comments never become tokens, string literals never leak their quotes into
    the token stream, identifiers may carry ``$``, and an interpolated template
    literal is refused rather than treated as an opaque string, so no decoy
    hidden in a comment or a string can stand in for code.
    """
    index, limit = start, min(end, len(text))
    while index < limit:
        character = text[index]
        if character in " \t\r\n\f\v":
            index += 1
        elif character == "/" and index + 1 < limit and text[index + 1] == "/":
            stop = text.find("\n", index)
            index = limit if stop < 0 else stop + 1
        elif character == "/" and index + 1 < limit and text[index + 1] == "*":
            stop = text.find("*/", index + 2)
            if stop < 0:
                raise _ParseError("unterminated comment")
            index = stop + 2
        elif character in "'\"":
            stop = index + 1
            while stop < limit:
                if text[stop] == "\\":
                    stop += 2
                elif text[stop] == character:
                    break
                else:
                    stop += 1
            if stop >= limit:
                raise _ParseError("unterminated string")
            yield ("str", text[index + 1:stop], index, stop + 1)
            index = stop + 1
        elif character == "`":
            stop = index + 1
            while stop < limit:
                if text[stop] == "\\":
                    stop += 2
                elif text[stop] == "`":
                    break
                else:
                    stop += 1
            if stop >= limit:
                raise _ParseError("unterminated template")
            if "${" in text[index + 1:stop]:
                raise _ParseError("interpolated template literal")
            yield ("str", text[index + 1:stop], index, stop + 1)
            index = stop + 1
        elif character.isalpha() or character in "_$":
            found = _IDENT_RE.match(text, index)
            yield ("id", found.group(0), index, found.end())
            index = found.end()
        elif character.isdigit() or (character == "." and index + 1 < limit and text[index + 1].isdigit()):
            found = _NUMBER_RE.match(text, index)
            yield ("num", found.group(0), index, found.end())
            index = found.end()
        else:
            for punctuator in _PUNCTUATORS:
                if text.startswith(punctuator, index):
                    yield ("punct", punctuator, index, index + len(punctuator))
                    index += len(punctuator)
                    break
            else:
                raise _ParseError(f"unsupported character {character!r}")


class _Parser:
    """A recursive-descent parser over the supported expression and statement grammar.

    Anything outside the grammar — comma sequences, assignments, regexes,
    computed object keys, interpolated templates — raises :class:`_ParseError`,
    which every verifier turns into a fail-closed verdict for its region.
    """

    def __init__(self, tokens):
        self._tokens = iter(tokens)
        self._buffer = []
        self.consumed = 0

    def peek(self, ahead: int = 0):
        while len(self._buffer) <= ahead:
            try:
                self._buffer.append(next(self._tokens))
            except StopIteration:
                self._buffer.append(None)
        return self._buffer[ahead]

    def next(self):
        token = self.peek()
        if token is None:
            raise _ParseError("the region ended mid-statement")
        self.consumed += 1
        self._buffer = self._buffer[1:]
        return token

    def at_punct(self, *values) -> bool:
        token = self.peek()
        return token is not None and token[0] == "punct" and token[1] in values

    def at_id(self, *values) -> bool:
        token = self.peek()
        return token is not None and token[0] == "id" and (not values or token[1] in values)

    def eat_punct(self, value) -> bool:
        if self.at_punct(value):
            self.next()
            return True
        return False

    def expect_punct(self, value):
        if not self.eat_punct(value):
            raise _ParseError(f"expected {value!r}")

    def expect_id(self, value=None) -> str:
        token = self.peek()
        if token is None or token[0] != "id" or (value is not None and token[1] != value):
            raise _ParseError("expected an identifier" + (f" {value!r}" if value else ""))
        return self.next()[1]

    # -- expressions -------------------------------------------------------

    def parse_ternary(self):
        test = self.parse_or()
        if self.eat_punct("?"):
            consequent = self.parse_ternary()
            self.expect_punct(":")
            alternative = self.parse_ternary()
            return ("cond", test, consequent, alternative)
        return test

    def parse_or(self):
        operands = [self.parse_and()]
        while self.eat_punct("||"):
            operands.append(self.parse_and())
        return operands[0] if len(operands) == 1 else ("logic", "||", tuple(operands))

    def parse_and(self):
        operands = [self.parse_equality()]
        while self.eat_punct("&&"):
            operands.append(self.parse_equality())
        return operands[0] if len(operands) == 1 else ("logic", "&&", tuple(operands))

    def parse_equality(self):
        left = self.parse_relational()
        while self.at_punct("===", "!==", "==", "!="):
            operator = self.next()[1]
            left = ("bin", operator, left, self.parse_relational())
        return left

    def parse_relational(self):
        left = self.parse_unary()
        while self.at_punct("<", ">", "<=", ">="):
            operator = self.next()[1]
            left = ("bin", operator, left, self.parse_unary())
        return left

    def parse_unary(self):
        if self.at_punct("!", "-", "+", "~"):
            return ("unary", self.next()[1], self.parse_unary())
        if self.at_id("void"):
            self.next()
            return ("unary", "void", self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self):
        node = self.parse_primary()
        while True:
            if self.at_punct(".") or self.at_punct("?."):
                optional = self.next()[1] == "?."
                node = ("member", node, self.expect_id(), optional)
            elif self.at_punct("["):
                self.next()
                node = ("index", node, self.parse_ternary())
                self.expect_punct("]")
            elif self.at_punct("("):
                self.next()
                node = ("call", node, self.parse_arguments())
            else:
                return node

    def parse_arguments(self):
        arguments = []
        while not self.at_punct(")"):
            if self.eat_punct("..."):
                arguments.append(("spread", self.parse_ternary()))
            else:
                arguments.append(self.parse_ternary())
            if not self.eat_punct(","):
                break
        self.expect_punct(")")
        return tuple(arguments)

    def parse_primary(self):
        token = self.peek()
        if token is None:
            raise _ParseError("the region ended mid-expression")
        kind, value = token[0], token[1]
        if kind == "str":
            self.next()
            return ("str", value)
        if kind == "num":
            self.next()
            return ("num", value)
        if kind == "id":
            if value in ("true", "false", "null"):
                self.next()
                return ("lit", value)
            if value == "new":
                self.next()
                callee = self.expect_id()
                arguments = ()
                if self.at_punct("("):
                    self.next()
                    arguments = self.parse_arguments()
                return ("new", ("id", callee), arguments)
            if self._token_is(self.peek(1), "punct", "=>"):
                self.next()
                self.next()
                return ("arrow", (value,), self.parse_ternary())
            self.next()
            return ("id", value)
        if kind == "punct":
            if value == "(":
                if self._parenthesized_arrow():
                    self.next()
                    parameters = [self.expect_id()]
                    self.expect_punct(")")
                    self.expect_punct("=>")
                    return ("arrow", tuple(parameters), self.parse_ternary())
                self.next()
                node = self.parse_ternary()
                self.expect_punct(")")
                return node
            if value == "{":
                self.next()
                return self.parse_object()
            if value == "[":
                self.next()
                return self.parse_array()
        raise _ParseError(f"unsupported expression at {value!r}")

    @staticmethod
    def _token_is(token, kind: str, value: str) -> bool:
        return token is not None and token[0] == kind and token[1] == value

    def _parenthesized_arrow(self) -> bool:
        """Whether the upcoming ``(`` opens a parenthesized arrow parameter list."""
        depth, ahead = 0, 0
        while True:
            token = self.peek(ahead)
            if token is None or token[0] != "punct":
                return False
            if token[1] == "(":
                depth += 1
            elif token[1] == ")":
                depth -= 1
                if depth == 0:
                    return self._token_is(self.peek(ahead + 1), "punct", "=>")
            elif token[1] in (";", "{", "}"):
                return False
            ahead += 1

    def parse_object(self):
        entries = []
        while not self.at_punct("}"):
            if self.eat_punct("..."):
                entries.append((None, ("spread", self.parse_ternary())))
            else:
                key = self.peek()
                if key is None or key[0] not in ("id", "str"):
                    raise _ParseError("unsupported object key")
                self.next()
                self.expect_punct(":")
                entries.append((key[1], self.parse_ternary()))
            if not self.eat_punct(","):
                break
        self.expect_punct("}")
        return ("object", tuple(entries))

    def parse_array(self):
        elements = []
        while not self.at_punct("]"):
            if self.eat_punct("..."):
                elements.append(("spread", self.parse_ternary()))
            else:
                elements.append(self.parse_ternary())
            if not self.eat_punct(","):
                break
        self.expect_punct("]")
        return ("array", tuple(elements))

    # -- statements --------------------------------------------------------

    def parse_statements(self):
        statements = []
        while self.peek() is not None:
            statements.append(self.parse_statement())
            if self.consumed > _MAX_REGION_TOKENS:
                raise _ParseError("the region exceeded the token budget")
        return statements

    def parse_statement(self):
        if self.eat_punct(";"):
            return ("empty",)
        if self.peek()[0] == "id" and self._token_is(self.peek(1), "punct", "="):
            # A simple assignment parses so a rewritten Set is named precisely;
            # every template below still rejects the statement itself.
            name = self.next()[1]
            self.next()
            value = self.parse_ternary()
            self.eat_punct(";")
            return ("assign", name, value)
        if self.at_id("let", "const", "var"):
            kind = self.next()[1]
            declarators = []
            while True:
                name = self.expect_id()
                initializer = self.parse_ternary() if self.eat_punct("=") else None
                declarators.append((name, initializer))
                if not self.eat_punct(","):
                    break
            self.eat_punct(";")
            return ("let", kind, tuple(declarators))
        if self.at_id("return"):
            self.next()
            token = self.peek()
            value = None
            if not (token is None or self._token_is(token, "punct", ";") or self._token_is(token, "punct", "}")):
                value = self.parse_ternary()
            self.eat_punct(";")
            return ("return", value)
        if self.at_id("if"):
            self.next()
            self.expect_punct("(")
            condition = self.parse_ternary()
            self.expect_punct(")")
            consequence = self.parse_statement_block()
            alternative = None
            if self.at_id("else"):
                self.next()
                alternative = self.parse_statement_block()
            return ("if", condition, consequence, alternative)
        if self.at_id("for"):
            self.next()
            self.expect_punct("(")
            if self.at_id("let", "const", "var"):
                self.next()
            variable = self.expect_id()
            self.expect_id("of")
            iterable = self.parse_ternary()
            self.expect_punct(")")
            return ("forof", variable, iterable, self.parse_statement_block())
        expression = self.parse_ternary()
        self.eat_punct(";")
        return ("expr", expression)

    def parse_statement_block(self):
        if self.eat_punct("{"):
            statements = []
            while not self.at_punct("}"):
                statements.append(self.parse_statement())
                if self.consumed > _MAX_REGION_TOKENS:
                    raise _ParseError("the region exceeded the token budget")
            self.expect_punct("}")
            return statements
        return [self.parse_statement()]


# -- shared node constructors and template shapes ------------------------------

def _member(base, name: str, optional: bool = False):
    return ("member", base, name, optional)


def _name_member(identifier: str):
    return _member(_member(("id", identifier), "metadata"), "name")


def _set_declaration(options: str):
    read = _member(("id", options), "allowedTools")
    return ("cond", read, ("new", ("id", "Set"), (read,)), ("unary", "void", ("num", "0")))


def _membership_rejection(set_var: str, loop_var: str):
    return ("logic", "&&", (("id", set_var),
                            ("unary", "!", ("call", _member(("id", set_var), "has"),
                                            (_name_member(loop_var),)))))


def _positive_gate(set_var: str, loop_var: str):
    return ("logic", "||", (("unary", "!", ("id", set_var)),
                            ("call", _member(("id", set_var), "has"), (_name_member(loop_var),))))


def _is_register_call(node, registry: str) -> bool:
    return node[0] == "call" and node[1] == _member(("id", registry), "register") and bool(node[2])


def _flatten_logic(node, operator: str):
    if node[0] == "logic" and node[1] == operator:
        operands = []
        for part in node[2]:
            operands.extend(_flatten_logic(part, operator))
        return operands
    return [node]


def _pure_read(node) -> bool:
    """A side-effect-free read: literals, bindings, member chains and their logic."""
    kind = node[0]
    if kind in ("str", "num", "lit", "id"):
        return True
    if kind == "member":
        return _pure_read(node[1])
    if kind == "index":
        return _pure_read(node[1]) and _pure_read(node[2])
    if kind == "unary":
        return _pure_read(node[2])
    if kind == "bin":
        return _pure_read(node[2]) and _pure_read(node[3])
    if kind in ("logic", "cond"):
        operands = node[2] if kind == "logic" else node[1:]
        return all(_pure_read(operand) for operand in operands)
    if kind == "array":
        return all(_pure_read(element[1] if element[0] == "spread" else element) for element in node[1])
    if kind == "object":
        return all(_pure_read(value[1] if value[0] == "spread" else value) for _, value in node[1])
    return False


def _exported_names(text: str) -> dict[str, tuple[str, ...]]:
    """The distinct local names a bundle binds to each exported mechanism name."""
    bound = {_REGISTER_EXPORT: [], _RESOLVE_EXPORT: []}
    for match in _EXPORT_RE.finditer(text):
        export, local = match.group(3), match.group(2)
        if local not in bound[export]:
            bound[export].append(local)
    return {export: tuple(names) for export, names in bound.items()}


# -- the bundle with its lazily verified functions, constants and helpers ------

class _Bundle:
    """The bundle text plus memoized parsing, constant resolution and purity."""

    def __init__(self, text: str):
        self.text = text
        self._sites = {}
        self._constants = {}
        self._pure = {}
        self._pure_visiting = set()
        self._assign_index = None

    def sites(self, name: str):
        """Every located ``(parameters, statements, body tokens)`` of one function.

        A definition is located when its header and braces balance at token
        level; ``statements`` is ``None`` when the body then leaves the
        supported grammar, which the caller treats as an unrecognized flow
        rather than a missing function. A name with no definition or several
        located definitions is ambiguous and fails closed at the caller.
        """
        if name not in self._sites:
            found = []
            for match in re.finditer(r"function\s+" + re.escape(name) + r"\s*\(", self.text):
                try:
                    found.append(self._parse_site(match.start(), name))
                except _ParseError:
                    continue
            self._sites[name] = found
        return self._sites[name]

    def _parse_site(self, start: int, name: str):
        limit = min(len(self.text), start + _MAX_REGION_CHARS)
        parser = _Parser(_token_iter(self.text, start, limit))
        parser.expect_id("function")
        parser.expect_id(name)
        parser.expect_punct("(")
        parameters = []
        while not parser.at_punct(")"):
            parameter = parser.expect_id()
            if parser.eat_punct("="):
                parser.parse_ternary()
            parameters.append(parameter)
            if not parser.eat_punct(","):
                break
        parser.expect_punct(")")
        parser.expect_punct("{")
        body = []
        depth = 1
        while True:
            token = parser.next()
            if token[0] == "punct":
                if token[1] == "{":
                    depth += 1
                elif token[1] == "}":
                    depth -= 1
                    if depth == 0:
                        break
            body.append(token)
            if len(body) > _MAX_REGION_TOKENS:
                raise _ParseError("the function body exceeded the token budget")
        try:
            statements = _Parser(iter(body)).parse_statements()
        except _ParseError:
            statements = None
        return tuple(parameters), statements, body

    # -- module constants ---------------------------------------------------

    def _assignments(self):
        """Every plausible constant assignment position, indexed by name once."""
        if self._assign_index is None:
            index = {}
            for match in _ASSIGNMENT_RE.finditer(self.text):
                index.setdefault(match.group(1), []).append(match.end())
            self._assign_index = index
        return self._assign_index

    def constant(self, name: str, depth: int = 0):
        """The single agreed value of a module constant: a string, list or Set.

        Every resolvable ``name =`` assignment in the text must carry the same
        value — strings and comments that merely contain a candidate make the
        name unresolvable unless they agree — and the elements of lists and
        Sets are followed through identifier indirections.
        """
        if name in self._constants:
            return self._constants[name]
        if depth > _MAX_CONST_DEPTH:
            return None
        values = set()
        for position in self._assignments().get(name, ()):
            value = self._constant_value(position, depth)
            if value is not None:
                values.add(value)
        resolved = next(iter(values)) if len(values) == 1 else None
        self._constants[name] = resolved
        return resolved

    def _constant_value(self, position: int, depth: int):
        limit = min(len(self.text), position + 4096)
        try:
            node = _Parser(_token_iter(self.text, position, limit)).parse_ternary()
        except _ParseError:
            return None
        return self._constant_of_node(node, depth)

    def _constant_of_node(self, node, depth: int):
        if node[0] in ("str", "num"):
            return (node[0], node[1])
        if node[0] == "array":
            return self._constant_list(node, depth)
        if node[0] == "new" and node[1] == ("id", "Set") and len(node[2]) == 1:
            argument = node[2][0]
            inner = (self._constant_list(argument, depth) if argument[0] == "array"
                     else self.constant(argument[1], depth + 1) if argument[0] == "id" else None)
            if inner is None or inner[0] != "list":
                return None
            return ("set", frozenset(inner[1]))
        if node[0] == "id":
            return self.constant(node[1], depth + 1)
        return None

    def _constant_list(self, node, depth: int):
        elements = []
        for element in node[1]:
            if element[0] == "str":
                elements.append(element[1])
            elif element[0] == "id":
                inner = self.constant(element[1], depth + 1)
                if inner is None or inner[0] != "str":
                    return None
                elements.append(inner[1])
            else:
                return None
        return ("list", tuple(elements))

    def constant_string(self, node):
        """A node's constant string: a literal or a resolvable identifier."""
        if node[0] == "str":
            return node[1]
        if node[0] == "id":
            value = self.constant(node[1])
            if value is not None and value[0] == "str":
                return value[1]
        return None

    # -- helper purity --------------------------------------------------------

    def pure_function(self, name: str, depth: int = 0) -> bool:
        """Whether a helper's whole body is built from the pure grammar only.

        Allowed statements are declarations, returns and pure guards; allowed
        calls are pure string methods on parameters, locals or resolved
        constants and calls to further verified helpers, so a side effect
        anywhere in the chain disqualifies it.
        """
        if name in self._pure:
            return self._pure[name]
        if depth > _MAX_PURE_DEPTH or name in self._pure_visiting:
            return False
        self._pure_visiting.add(name)
        try:
            sites = self.sites(name)
            if len(sites) != 1:
                verdict = False
            else:
                parameters, statements, _ = sites[0]
                verdict = statements is not None and self._pure_statements(
                    statements, set(parameters), set(), depth)
        finally:
            self._pure_visiting.discard(name)
        self._pure[name] = verdict
        return verdict

    def _pure_statements(self, statements, parameters: set, locals_: set, depth: int) -> bool:
        for statement in statements:
            kind = statement[0]
            if kind == "empty":
                continue
            if kind == "let":
                for name, initializer in statement[2]:
                    if initializer is not None and not self._pure_expression(
                            initializer, parameters, locals_, depth):
                        return False
                    locals_.add(name)
            elif kind == "return":
                if statement[1] is not None and not self._pure_expression(
                        statement[1], parameters, locals_, depth):
                    return False
            elif kind == "if":
                if (statement[3] is not None
                        or not self._pure_expression(statement[1], parameters, locals_, depth)
                        or not self._pure_statements(statement[2], parameters, set(locals_), depth)):
                    return False
            else:
                return False
        return True

    def _pure_expression(self, node, parameters: set, locals_: set, depth: int) -> bool:
        kind = node[0]
        if kind in ("str", "num", "lit", "id"):
            return True
        if kind == "member":
            return self._pure_expression(node[1], parameters, locals_, depth)
        if kind == "index":
            return (self._pure_expression(node[1], parameters, locals_, depth)
                    and self._pure_expression(node[2], parameters, locals_, depth))
        if kind == "unary":
            return self._pure_expression(node[2], parameters, locals_, depth)
        if kind == "bin":
            return (self._pure_expression(node[2], parameters, locals_, depth)
                    and self._pure_expression(node[3], parameters, locals_, depth))
        if kind in ("logic", "cond"):
            operands = node[2] if kind == "logic" else node[1:]
            return all(self._pure_expression(operand, parameters, locals_, depth)
                       for operand in operands)
        if kind == "array":
            return all(self._pure_expression(element[1] if element[0] == "spread" else element,
                                             parameters, locals_, depth) for element in node[1])
        if kind == "object":
            return all(self._pure_expression(value[1] if value[0] == "spread" else value,
                                             parameters, locals_, depth) for _, value in node[1])
        if kind == "arrow":
            return self._pure_expression(node[2], parameters | set(node[1]), locals_, depth)
        if kind == "new":
            return node[1] == ("id", "Set") and all(
                self._pure_expression(argument, parameters, locals_, depth) for argument in node[2])
        if kind == "call":
            return self._pure_call(node, parameters, locals_, depth)
        return False

    def _pure_call(self, node, parameters: set, locals_: set, depth: int) -> bool:
        callee, arguments = node[1], node[2]
        if not all(self._pure_expression(argument, parameters, locals_, depth) for argument in arguments):
            return False
        if callee[0] == "id":
            return self.pure_function(callee[1], depth + 1)
        if callee[0] == "member" and callee[2] in _STRING_METHODS:
            base = callee[1]
            while base[0] == "member":
                base = base[1]
            return base[0] == "id" and (base[1] in parameters or base[1] in locals_
                                        or self.constant(base[1]) is not None)
        return False

    # -- recognized helper templates ------------------------------------------

    def set_builder(self, name: str) -> bool:
        """The pure disallowed-Set builder template over one parameter.

        The optional empty guard, the fresh Set, the single for-of that only
        adds normalized entries through a verified pure normalizer and the
        bounded return together admit exactly the installed builder's shape.
        """
        sites = self.sites(name)
        if len(sites) != 1:
            return False
        parameters, statements, _ = sites[0]
        if not parameters or statements is None:
            return False
        parameter = ("id", parameters[0])
        index = 0
        guard = ("logic", "||", (("unary", "!", parameter),
                                 ("bin", "===", _member(parameter, "length"), ("num", "0"))))
        if index < len(statements) and statements[index][0] == "if":
            if statements[index] != ("if", guard, [("return", None)], None):
                return False
            index += 1
        if index >= len(statements) or statements[index][0] != "let":
            return False
        declaration = statements[index]
        if len(declaration[2]) != 1:
            return False
        set_var, initializer = declaration[2][0]
        if initializer != ("new", ("id", "Set"), ()):
            return False
        index += 1
        if index >= len(statements) or statements[index][0] != "forof":
            return False
        _, loop_var, iterable, loop_body = statements[index]
        if iterable != parameter:
            return False
        loop_id, set_id = ("id", loop_var), ("id", set_var)
        add_direct = ("call", _member(set_id, "add"), (loop_id,))
        built = len(loop_body) == 1 and loop_body[0] == ("expr", add_direct)
        if not built and len(loop_body) == 2:
            first, second = loop_body
            if first[0] == "let" and len(first[2]) == 1:
                norm_var, norm_init = first[2][0]
                if (norm_init[0] == "call" and norm_init[1][0] == "id"
                        and norm_init[2] == (loop_id,) and second == (
                            "expr", ("logic", "&&", (("id", norm_var),
                                                     ("call", _member(set_id, "add"),
                                                      (("id", norm_var),)))))):
                    built = self.pure_function(norm_init[1][1])
        if not built:
            return False
        index += 1
        if index != len(statements) - 1:
            return False
        return statements[-1] == (
            "return", ("cond", ("bin", ">", _member(set_id, "size"), ("num", "0")),
                       set_id, ("unary", "void", ("num", "0"))))


def _name_predicate_ok(bundle: _Bundle, name: str) -> bool:
    """The pure name predicate template: ``x ? SET.has(x) : !0``.

    The Set must be a resolved constant of strings; a helper that merely carries
    a plausible name, or one whose predicate can act, never qualifies.
    """
    sites = bundle.sites(name)
    if len(sites) != 1:
        return False
    parameters, statements, _ = sites[0]
    if len(parameters) != 1 or statements is None or len(statements) != 1 or statements[0][0] != "return":
        return False
    value = statements[0][1]
    if value is None or value[0] != "cond":
        return False
    parameter = ("id", parameters[0])
    alternative = value[3]
    if value[1] != parameter or alternative not in (
            ("unary", "!", ("num", "0")), ("unary", "!", ("num", "1")), ("lit", "false")):
        return False
    consequent = value[2]
    if not (consequent[0] == "call" and consequent[1][0] == "member"
            and consequent[1][2] == "has" and not consequent[1][3]
            and consequent[2] == (parameter,)):
        return False
    base = consequent[1][1]
    if base[0] != "id":
        return False
    resolved = bundle.constant(base[1])
    return resolved is not None and resolved[0] == "set"


# -- the registration function -------------------------------------------------

def _registration_problem(bundle: _Bundle, name: str):
    """Verify the registration function; return its wiring context or a reason."""
    sites = bundle.sites(name)
    if len(sites) != 1:
        return None, _REGISTER_NOT_FUNCTION
    parameters, statements, body = sites[0]
    if not _body_has_for_of(body):
        return None, _NO_FOR_OF
    register_sites, receivers = 0, set()
    for index in range(len(body) - 3):
        if (body[index][0] == "id" and body[index + 1][0] == "punct" and body[index + 1][1] == "."
                and body[index + 2][0] == "id" and body[index + 2][1] == "register"
                and body[index + 3][0] == "punct" and body[index + 3][1] == "("):
            register_sites += 1
            receivers.add(body[index][1])
    if not receivers or any(receiver not in parameters for receiver in receivers):
        return None, _NO_REGISTRY_PARAM
    if register_sites != 1:
        return None, _REGISTER_OUTSIDE
    if statements is None:
        return None, _UNRECOGNIZED_FLOW
    declarations, loops = [], []
    for statement in statements:
        if statement[0] == "let":
            declarations.extend(statement[2])
        elif statement[0] == "forof":
            loops.append(statement)
        elif statement[0] not in ("empty", "assign"):
            return None, _UNRECOGNIZED_FLOW
    set_var = options_param = None
    for declared, initializer in declarations:
        for parameter in parameters:
            if initializer == _set_declaration(parameter):
                if set_var is not None:
                    return None, _UNRECOGNIZED_FLOW
                set_var, options_param = declared, parameter
    if set_var is None:
        return None, _NO_SET_FILTER
    writes = 0
    for index in range(len(body) - 1):
        if (body[index][0] == "id" and body[index][1] == set_var
                and body[index + 1][0] == "punct" and body[index + 1][1] in _ASSIGNMENT_PUNCT):
            writes += 1
    if writes > 1:
        return None, _SET_REWRITTEN
    if any(statement[0] == "assign" for statement in statements):
        return None, _UNRECOGNIZED_FLOW
    local_kinds = {}
    for declared, initializer in declarations:
        if declared == set_var:
            continue
        usable, kind = _preamble_declaration(bundle, initializer)
        if not usable:
            return None, _BAD_PREAMBLE
        local_kinds[declared] = kind
    if len(loops) != 1:
        return None, _UNRECOGNIZED_FLOW
    _, loop_var, iterable, loop_body = loops[0]
    if not _pure_read(iterable):
        return None, _UNRECOGNIZED_FLOW
    registry = next(iter(receivers))
    environment = {"set_var": set_var, "set_locals": local_kinds, "loop_var": loop_var,
                   "parameters": parameters, "registry": registry, "options": options_param}
    problem = _loop_flow_problem(bundle, loop_body, environment)
    if problem is not None:
        return None, problem
    return (len(parameters), parameters.index(options_param)), None


def _preamble_declaration(bundle: _Bundle, initializer):
    """A preamble declaration must be a pure read or a verified helper call."""
    if _pure_read(initializer):
        return True, "pure"
    if (initializer[0] == "call" and initializer[1][0] == "id"
            and all(_pure_read(argument) for argument in initializer[2])):
        helper = initializer[1][1]
        if bundle.set_builder(helper):
            return True, "set"
        if bundle.pure_function(helper):
            return True, "pure"
    return False, None


def _body_has_for_of(body) -> bool:
    """Whether a for-of header appears in the function's own body tokens."""
    for index in range(len(body) - 1):
        if not (body[index][0] == "id" and body[index][1] == "for"
                and body[index + 1][0] == "punct" and body[index + 1][1] == "("):
            continue
        depth = 0
        for candidate in body[index + 1:]:
            if candidate[0] != "punct":
                if depth == 1 and candidate[0] == "id" and candidate[1] == "of":
                    return True
                continue
            if candidate[1] == "(":
                depth += 1
            elif candidate[1] == ")":
                depth -= 1
                if depth == 0:
                    break
    return False


def _loop_flow_problem(bundle: _Bundle, loop_body, environment) -> str | None:
    """The one loop statement must be one of the two complete gated flows."""
    if len(loop_body) != 1:
        return _UNRECOGNIZED_FLOW
    statement = loop_body[0]
    set_var, loop_var = environment["set_var"], environment["loop_var"]
    registry = environment["registry"]
    if statement[0] == "if" and statement[3] is None:
        condition, consequence = statement[1], statement[2]
        if (condition == _positive_gate(set_var, loop_var) and len(consequence) == 1
                and consequence[0][0] == "expr" and _is_register_call(consequence[0][1], registry)):
            return _register_argument_problem(bundle, consequence[0][1][2], environment)
        return _UNRECOGNIZED_FLOW
    if statement[0] == "expr":
        expression = statement[1]
        if expression[0] == "logic" and expression[1] == "||":
            operands = _flatten_logic(expression, "||")
            if operands and _is_register_call(operands[-1], registry):
                membership = _membership_rejection(set_var, loop_var)
                if sum(1 for operand in operands[:-1] if operand == membership) != 1:
                    return _UNRECOGNIZED_FLOW
                for operand in operands[:-1]:
                    if operand != membership and not _rejection_operand_ok(bundle, operand, environment):
                        return _BAD_OPERAND
                return _register_argument_problem(bundle, operands[-1][2], environment)
    return _UNRECOGNIZED_FLOW


def _register_argument_problem(bundle: _Bundle, arguments, environment) -> str | None:
    """The register call's first argument: the loop variable or its transform."""
    if not arguments:
        return _BAD_REGISTER_ARG
    first = arguments[0]
    loop_var, options = environment["loop_var"], environment["options"]
    if first == ("id", loop_var):
        pass
    elif (first[0] == "call" and first[1][0] == "id"
          and tuple(first[2]) == (("id", loop_var), ("id", options))):
        if _transform_problem(bundle, first[1][1]) is not None:
            return _TRANSFORM_PROBLEM
    else:
        return _BAD_REGISTER_ARG
    if not all(_pure_read(argument) for argument in arguments[1:]):
        return _BAD_REGISTER_ARG
    return None


def _rejection_operand_ok(bundle: _Bundle, node, environment) -> bool:
    """One rejection operand: pure comparison logic, a verified Set.has or predicate."""
    kind = node[0]
    if kind in ("str", "num", "lit"):
        return True
    if kind == "unary":
        return node[1] in ("!", "void") and _rejection_operand_ok(bundle, node[2], environment)
    if kind == "logic":
        return all(_rejection_operand_ok(bundle, operand, environment) for operand in node[2])
    if kind == "bin":
        return _pure_read(node[2]) and _pure_read(node[3])
    if kind == "call":
        callee, arguments = node[1], node[2]
        if (callee[0] == "member" and callee[2] == "has" and len(arguments) == 1
                and _pure_read(arguments[0])):
            base = callee[1]
            if base == ("id", environment["set_var"]):
                return True
            if base[0] == "id" and environment["set_locals"].get(base[1]) == "set":
                return True
            if base[0] == "id":
                resolved = bundle.constant(base[1])
                return resolved is not None and resolved[0] == "set"
            return False
        if callee[0] == "id" and len(arguments) == 1 and _pure_read(arguments[0]):
            return _name_predicate_ok(bundle, callee[1])
        return False
    return False


def _transform_problem(bundle: _Bundle, name: str) -> str | None:
    """The transform returns every read-only tool unchanged, or fails closed.

    Every branch condition must carry a positive ``metadata.name`` equality
    whose resolvable name is none of Read/Glob/Grep, must contain no other
    reference to ``metadata.name`` and no call, and the final fall-through must
    be the bare tool parameter — a ``return e`` elsewhere proves nothing.
    """
    sites = bundle.sites(name)
    if len(sites) != 1:
        return _TRANSFORM_PROBLEM
    parameters, statements, _ = sites[0]
    if (len(parameters) != 2 or statements is None or len(statements) != 1
            or statements[0][0] != "return"):
        return _TRANSFORM_PROBLEM
    node = statements[0][1]
    if node is None:
        return _TRANSFORM_PROBLEM
    while node[0] == "cond":
        problem = _transform_condition_problem(bundle, node[1], parameters[0])
        if problem is not None:
            return problem
        node = node[3]
    return None if node == ("id", parameters[0]) else _TRANSFORM_PROBLEM


def _transform_condition_problem(bundle: _Bundle, condition, tool_parameter: str) -> str | None:
    name_chain = _name_member(tool_parameter)
    keys = []

    def chain_within(node) -> bool:
        """Whether the name chain appears anywhere inside ``node``."""
        if node == name_chain:
            return True
        kind = node[0]
        if kind == "member":
            return chain_within(node[1])
        if kind == "index":
            return chain_within(node[1]) or chain_within(node[2])
        if kind == "unary":
            return chain_within(node[2])
        if kind == "bin":
            return chain_within(node[2]) or chain_within(node[3])
        if kind in ("logic", "cond"):
            operands = node[2] if kind == "logic" else node[1:]
            return any(chain_within(operand) for operand in operands)
        if kind == "array":
            return any(chain_within(element[1] if element[0] == "spread" else element)
                       for element in node[1])
        if kind == "object":
            return any(chain_within(value[1] if value[0] == "spread" else value)
                       for _, value in node[1])
        return False

    def visit(node) -> bool:
        kind = node[0]
        if kind == "bin":
            if node[1] not in ("===", "!==", "==", "!="):
                return False
            for side, other in ((node[2], node[3]), (node[3], node[2])):
                if side == name_chain:
                    # The name chain may appear only as a side of a positive
                    # equality, never nested where a lookup could match any
                    # tool, and never under an inverted comparison.
                    if node[1] != "===":
                        return False
                    keys.append(other)
                elif chain_within(side):
                    return False
            return _pure_read(node[2]) and _pure_read(node[3])
        if kind in ("logic", "cond", "unary"):
            operands = node[2] if kind == "logic" else node[1:]
            return all(visit(operand) for operand in operands)
        return _pure_read(node) and not chain_within(node)

    if not visit(condition) or not keys:
        return _TRANSFORM_PROBLEM
    for key in keys:
        value = bundle.constant_string(key)
        if value is None or value in _READ_ONLY_TOOL_NAMES:
            return _TRANSFORM_PROBLEM
    return None


# -- the resolver function -------------------------------------------------------

def _resolver_problem(bundle: _Bundle, name: str) -> str | None:
    """The resolver's complete return chain over the config toolAllowlist.

    Only three complete chains qualify: returning the read member directly,
    returning one same-value local, or the recognized alias-map, explore-filter
    and root-child helper chain with every helper verified to preserve the
    read-only names.
    """
    sites = bundle.sites(name)
    if len(sites) != 1:
        return _RESOLVE_NOT_FUNCTION
    parameters, statements, _ = sites[0]
    if not parameters or statements is None:
        return _RES_NO_READ
    config_id = parameters[0]
    read = _member(("id", config_id), "toolAllowlist")
    if statements == [("return", read)]:
        return None
    if (len(statements) == 2 and statements[0][0] == "let" and len(statements[0][2]) == 1
            and statements[0][2][0][1] == read
            and statements[1] == ("return", ("id", statements[0][2][0][0]))):
        return None
    if len(statements) != 2 or statements[0][0] != "let" or len(statements[0][2]) != 1:
        return _RES_NO_READ
    local, initializer = statements[0][2][0]
    if (initializer[0] != "call" or initializer[1][0] != "id" or initializer[2] != (read,)):
        return _RES_NO_READ
    alias_map = initializer[1][1]
    if statements[1][0] != "return" or statements[1][1] is None:
        return _RES_NO_READ
    chain = statements[1][1]
    if chain[0] != "cond":
        return _RES_NO_READ
    if chain[1] != ("bin", "!==", _member(("id", config_id), "toolset"), ("str", "explore")):
        return _RES_NO_READ
    if not _config_list_call(chain[2], config_id, local):
        return _RES_NO_READ
    child = chain[2][1][1]
    tail = chain[3]
    if tail[0] != "cond" or tail[1] != ("id", local):
        return _RES_NO_READ
    filter_branch, default_branch = tail[2], tail[3]
    if not (filter_branch[0] == "call" and filter_branch[1][0] == "id"
            and len(filter_branch[2]) == 2 and filter_branch[2][0] == ("id", config_id)):
        return _RES_NO_READ
    filter_child = filter_branch[1][1]
    filter_call = filter_branch[2][1]
    if not (filter_call[0] == "call" and filter_call[1] == _member(("id", local), "filter")
            and len(filter_call[2]) == 1 and filter_call[2][0][0] == "arrow"):
        return _RES_NO_READ
    arrow = filter_call[2][0]
    has_call = arrow[2]
    if not (len(arrow[1]) == 1 and has_call[0] == "call" and has_call[1][0] == "member"
            and has_call[1][2] == "has" and not has_call[1][3]
            and has_call[2] == (("id", arrow[1][0]),)):
        return _RES_NO_READ
    set_base = has_call[1][1]
    explore_set = bundle.constant(set_base[1]) if set_base[0] == "id" else None
    if explore_set is None or explore_set[0] != "set" or not _READ_ONLY_TOOL_NAMES <= explore_set[1]:
        return _RES_EXPLORE
    if not (default_branch[0] == "call" and default_branch[1][0] == "id"
            and len(default_branch[2]) == 2 and default_branch[2][0] == ("id", config_id)
            and default_branch[2][1][0] == "id"):
        return _RES_NO_READ
    default_list = bundle.constant(default_branch[2][1][1])
    if default_list is None or default_list[0] != "list":
        return _RES_NO_READ
    problem = _alias_map_problem(bundle, alias_map)
    if problem is not None:
        return problem
    if _child_problem(bundle, child) is not None or _child_problem(bundle, filter_child) is not None:
        return _RES_HELPER
    return None


def _config_list_call(node, config_id: str, local: str) -> bool:
    return (node[0] == "call" and node[1][0] == "id"
            and tuple(node[2]) == (("id", config_id), ("id", local)))


def _alias_map_problem(bundle: _Bundle, name: str) -> str | None:
    """The alias-map helper ``x?.map(v => alias(v))`` over a pure alias chain."""
    sites = bundle.sites(name)
    if len(sites) != 1:
        return _RES_HELPER
    parameters, statements, _ = sites[0]
    if (len(parameters) != 1 or statements is None or len(statements) != 1
            or statements[0][0] != "return"):
        return _RES_HELPER
    value = statements[0][1]
    if value is None or value[0] != "call" or len(value[2]) != 1:
        return _RES_HELPER
    callee = value[1]
    if not (callee[0] == "member" and callee[2] == "map" and callee[1] == ("id", parameters[0])):
        return _RES_HELPER
    arrow = value[2][0]
    if arrow[0] != "arrow" or len(arrow[1]) != 1:
        return _RES_HELPER
    body = arrow[2]
    if body == ("id", arrow[1][0]):
        return None
    if body[0] == "call" and body[1][0] == "id" and body[2] == (("id", arrow[1][0]),):
        return _alias_chain_problem(bundle, body[1][1])
    return _alias_chain_on_node(bundle, body, arrow[1][0])


def _alias_chain_problem(bundle: _Bundle, name: str) -> str | None:
    sites = bundle.sites(name)
    if len(sites) != 1:
        return _RES_HELPER
    parameters, statements, _ = sites[0]
    if (len(parameters) != 1 or statements is None or len(statements) != 1
            or statements[0][0] != "return"):
        return _RES_HELPER
    return _alias_chain_on_node(bundle, statements[0][1], parameters[0])


def _alias_chain_on_node(bundle: _Bundle, node, parameter: str) -> str | None:
    """A chain of ``x === key ? value :`` conditions ending in the bare ``x``.

    No key may name a read-only tool, and every key and value must resolve to a
    string constant, so the three read-only names always pass unchanged.
    """
    identity = ("id", parameter)
    while node[0] == "cond":
        test = node[1]
        if not (test[0] == "bin" and test[1] == "===" and test[2] == identity):
            return _RES_HELPER
        key = bundle.constant_string(test[3])
        if key is None:
            return _RES_HELPER
        if key in _READ_ONLY_TOOL_NAMES:
            return _RES_ALIAS
        if bundle.constant_string(node[2]) is None:
            return _RES_HELPER
        node = node[3]
    return None if node == identity else _RES_HELPER


def _child_problem(bundle: _Bundle, name: str) -> str | None:
    """The root-child helper: only the child branch appends one constant name."""
    sites = bundle.sites(name)
    if len(sites) != 1:
        return _RES_HELPER
    parameters, statements, _ = sites[0]
    if (len(parameters) != 2 or statements is None or len(statements) != 1
            or statements[0][0] != "return"):
        return _RES_HELPER
    config, list_param = parameters
    value = statements[0][1]
    if value is None or value[0] != "logic" or value[1] != "&&":
        return _RES_HELPER
    operands = _flatten_logic(value, "&&")
    if len(operands) != 2 or operands[0] != ("id", list_param):
        return _RES_HELPER
    branch = operands[1]
    if branch[0] != "cond":
        return _RES_HELPER
    if branch[1] != ("bin", "===", _member(("id", config), "taskType"), ("str", "subagent_child")):
        return _RES_HELPER
    child_branch, root_branch = branch[2], branch[3]
    if root_branch != ("id", list_param):
        return _RES_HELPER
    if child_branch[0] != "cond":
        return _RES_HELPER
    included, identity_branch, appended = child_branch[1], child_branch[2], child_branch[3]
    if not (included[0] == "call" and included[1] == _member(("id", list_param), "includes")
            and len(included[2]) == 1):
        return _RES_HELPER
    key = included[2][0]
    if identity_branch != ("id", list_param):
        return _RES_HELPER
    if appended != ("array", (("spread", ("id", list_param)), key)):
        return _RES_HELPER
    return None if bundle.constant_string(key) is not None else _RES_HELPER


# -- the call-site wiring ---------------------------------------------------------

def _wiring_problem(text: str, register: str, resolver: str, context) -> str | None:
    """One registration call wires the verified resolver at the options position.

    A qualifying call carries exactly the declared parameters' worth of
    arguments, its options-position argument is a direct object literal without
    spread or duplicate ``allowedTools`` keys, and that one value is the
    resolver called on a single ``.config`` member read. Anything else — a
    third argument, a nested or spread decoy, a discarded resolver return —
    leaves the call unqualified.
    """
    parameter_count, options_index = context
    for match in re.finditer(r"(?<![\w$])" + re.escape(register) + r"\s*\(", text):
        prefix = text[:match.start()].rstrip()
        if prefix.endswith("function") or prefix.endswith("."):
            continue
        limit = min(len(text), match.start() + _MAX_REGION_CHARS)
        try:
            parser = _Parser(_token_iter(text, match.start(), limit))
            parser.expect_id(register)
            parser.expect_punct("(")
            arguments = []
            while not parser.at_punct(")"):
                arguments.append(parser.parse_ternary())
                if not parser.eat_punct(","):
                    break
            parser.expect_punct(")")
        except _ParseError:
            continue
        if len(arguments) != parameter_count or options_index >= len(arguments):
            continue
        options = arguments[options_index]
        if options[0] != "object":
            continue
        entries = options[1]
        if any(key is None for key, _ in entries):
            continue
        allowed = [value for key, value in entries if key == "allowedTools"]
        if len(allowed) != 1:
            continue
        value = allowed[0]
        if not (value[0] == "call" and value[1] == ("id", resolver) and len(value[2]) == 1):
            continue
        argument = value[2][0]
        if not (argument[0] == "member" and argument[1][0] == "id"
                and argument[2] == "config" and not argument[3]):
            continue
        return None
    return _NO_WIRING


def allowlist_chain_problem(text: str) -> str | None:
    """The first missing allowlist mechanism in the public CLI bundle text.

    This is the L6-A3 static core behind
    :func:`~buddy.adapters.zcode_read_only.native_contract_problem`: it locates
    the exported registration and resolver functions, verifies the complete
    Set-membership registration flow, the tool-preserving transform, the
    resolver's complete return chain and the call-site wiring, fail-closed with
    a specific reason for every unrecognized shape. The caller supplies the
    bounded public bundle text; nothing here executes the bundle or reads any
    credential.
    """
    bundle = _Bundle(text)
    exported = _exported_names(text)
    registers = exported[_REGISTER_EXPORT]
    resolvers = exported[_RESOLVE_EXPORT]
    if not registers:
        return _REGISTER_NOT_NAMED
    if not resolvers:
        return _RESOLVE_NOT_NAMED
    if len(registers) > 1:
        return _REGISTER_AMBIGUOUS
    if len(resolvers) > 1:
        return _RESOLVE_AMBIGUOUS
    context, problem = _registration_problem(bundle, registers[0])
    if problem is not None:
        return problem
    problem = _resolver_problem(bundle, resolvers[0])
    if problem is not None:
        return problem
    return _wiring_problem(text, registers[0], resolvers[0], context)
