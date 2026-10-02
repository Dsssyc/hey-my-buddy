"""Free static verification of the allowlist chain in the public ZCode CLI bundle (L6-A4).

The L6-A3 full-template review confirmed seven reproducible escapes, all shapes
a genuine token/template match cannot see on its own: a ``var`` allowlist
declared after the loop it gates (hoisting), a local rebinding of the options
parameter or of the ``Set`` constructor, a transform condition that is always
true next to a tool name, decoys living in strings, comments, templates or
regex literals, a formal parameter impersonating a module constant, a
concatenated or rewritten constant, and an escaped string spelling a read-only
tool name as another value. This module keeps the bounded tokenizer and the
complete supported templates, and adds the two lexical layers those escapes
crossed: every discovery regex now runs over one shared outer code context —
comments, strings, templates with their interpolations and regex literals
never provide definitions, calls, assignments or registrations — and every
proof reference resolves against the bound environment of its scope: formal
parameters, locals, loop variables, arrow parameters and the scopes enclosing
both the verified functions and the registration call site, including
``var``-hoisted and later declarations and every write. Constants consume
their whole right-hand side and any unrecognized or disagreeing write leaves
them unsolvable; proof regions carrying escaped string literals are rejected
rather than decoded. The module never executes the bundle, embeds no general
interpreter and adds no dependency; anything outside the supported templates
or outside the provable lexical context fails closed with a specific reason.

The verified mechanisms stay structural, never name-bound: the named
``registerBuiltInTools`` builds one Set from its options parameter's
``allowedTools`` and gates a single per-tool for-of registration through
either the complete positive membership condition or a top-level short-circuit
OR chain whose membership rejection is one complete operand and whose register
call is the last; the transform provably returns every Read/Glob/Grep entry
unchanged; the named ``resolveBuiltInToolAllowlist`` returns the config
``toolAllowlist`` through the recognized helper chain; and the registration is
actually invoked with that resolver's call as the unique ``allowedTools``
value at the options parameter's position from a scope that does not shadow
either function. Versions, hashes and named-certificate knowledge play no
part. The proof covers only the packaged code; whether a live session enforced
anything stays with the protocol facts, the unified tool evidence and the
separately approved native checks.
"""
from __future__ import annotations

import bisect
import re

__all__ = ["allowlist_chain_problem", "lexical_regions"]

#: One verified function or call region is tokenized within this many characters.
_MAX_REGION_CHARS = 16384
#: A constant identifier is followed through at most this many indirections.
_MAX_CONST_DEPTH = 4
#: Purity verification of a helper chain is bounded and cycle-safe.
_MAX_PURE_DEPTH = 8
#: One parsed region consumes at most this many tokens.
_MAX_REGION_TOKENS = 20000
#: A backward group match for slash classification stays within this window.
_MAX_GROUP_WINDOW = 65536
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
_BAD_ORDER = ("registerBuiltInTools does not carry all of its declarations before the "
              "single for-of loop")
_BAD_BINDING = ("registerBuiltInTools rebinds its parameters, the Set constructor, "
                "its locals or its loop variable")
_BAD_DEFAULT = "a verified function declares an unsupported parameter default"
_REASSIGNED = ("registerBuiltInTools or resolveBuiltInToolAllowlist is reassigned "
               "in the public bundle")
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
#: Writing or mutating a module constant in any of these ways leaves it unsolvable.
_COMPOUND_PUNCT = frozenset({"+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=",
                             ">>>=", "&&=", "||=", "??=", "**="})
_MUTATION_METHODS = frozenset({"add", "delete", "clear", "push", "pop", "shift", "unshift",
                               "splice", "sort", "reverse", "fill", "copyWithin"})
#: Assignment right-hand sides must end on one of these, with a reason to be there.
_RHS_TERMININATORS = frozenset({";", ",", ")", "]", "}"})

_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER_RE = re.compile(r"0[xXbBoO][0-9a-fA-F]+n?|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?n?")
#: The export mapping call: a code-position match with the exported mechanism
#: name as a plain string argument — escapes simply never match it.
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
    literal is refused rather than treated as an opaque string. A string or
    template carrying a backslash escape is refused too: a proof region never
    decodes escapes, so a different spelling of a read-only tool name cannot
    enter a template as another value.
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
                    raise _ParseError("escaped string literal in a proof region")
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
                    raise _ParseError("escaped template literal in a proof region")
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


# -- the shared outer lexical context --------------------------------------------

#: Keywords after which a slash starts a regex literal, not a division.
_REGEX_AFTER_WORDS = frozenset({"return", "typeof", "instanceof", "in", "of", "new", "delete",
                                "void", "throw", "case", "do", "else", "yield", "await"})
#: Keywords whose group is a control-statement header: ``if(x) /re/`` is a regex.
_CONTROL_WORDS = frozenset({"if", "while", "for", "switch", "catch", "with"})
_TOP_SCAN = re.compile(r"[/\"'`]")
_EVENT_CHARS = re.compile(r"[{}()\[\],;=]")
_TEXT_SCAN = re.compile(r"\\.|`|\$\{")
_CODE_SCAN = re.compile(r"[{}()'\"`/]")


class _LexicalRegions:
    """The bundle's code context: which byte positions are real code.

    One sequential scan covers line and block comments, plain strings, template
    literals — one span each, interpolations included, nested templates and
    their escapes handled — and regex literals, whose escapes and character
    classes never end the literal early. A slash after an identifier, a
    number, a closing bracket or a finished literal divides; a slash after
    ``)`` divides unless the group is a control-statement header; every other
    position — including after ``}``, where minified bundles essentially never
    divide — masks as a regex, the fail-closed direction. Strings, comments,
    templates and regexes therefore never provide definitions, calls,
    assignments or registrations, while an export's real string argument stays
    matchable because only the match position itself must sit in code.
    """

    def __init__(self, text: str):
        self.text = text
        self.spans = self._scan(text)
        self._starts = tuple(start for start, _ in self.spans)
        code = bytearray(len(text))
        for start, end in self.spans:
            code[start:end] = b"\x01" * (end - start)
        self._code = code

    def is_code(self, position: int) -> bool:
        return position < len(self._code) and not self._code[position]

    def events(self, i: int, stop: int | None = None):
        """Yield ``(position, char)`` for structural code characters at or after ``i``.

        Brackets, separators and the equals sign drive every scope walker;
        masked characters are jumped over whole, so a walker never touches a
        string, comment, template or regex literal character by character.
        """
        text = self.text
        code = self._code
        starts = self._starts
        spans = self.spans
        n = len(text)
        while i < n:
            m = _EVENT_CHARS.search(text, i)
            if m is None:
                return
            p = m.start()
            if stop is not None and p >= stop:
                return
            if code[p]:
                k = bisect.bisect_right(starts, p) - 1
                i = spans[k][1] if k >= 0 else n
                continue
            yield p, text[p]
            i = m.end()

    @staticmethod
    def _scan(text: str):
        spans = []
        add = spans.append
        n = len(text)
        find = text.find
        stack = []  # per open template: [start, mode]; mode 0 text, >=1 interpolation depth
        i = 0
        while i < n:
            if not stack:
                m = _TOP_SCAN.search(text, i)
                if m is None:
                    break
                i = m.start()
                c = text[i]
                if c == "`":
                    stack.append([i, 0])
                    i += 1
                    continue
                if c in "'\"":
                    j = _plain_string_end(text, i, c)
                    add((i, j))
                    i = j
                    continue
                if i + 1 < n and text[i + 1] == "/":
                    k = find("\n", i)
                    j = n if k < 0 else k
                elif i + 1 < n and text[i + 1] == "*":
                    k = find("*/", i + 2)
                    j = n if k < 0 else k + 2
                elif _slash_is_regex(text, i, spans):
                    j = _regex_end(text, i)
                else:
                    i += 1
                    continue
                add((i, j))
                i = j
                continue
            if stack[-1][1] == 0:  # template text: escapes, close, or interpolation
                m = _TEXT_SCAN.search(text, i)
                if m is None:
                    add((stack.pop()[0], n))
                    i = n
                    break
                i = m.start()
                c = text[i]
                if c == "\\":
                    i += 2
                    continue
                if c == "`":
                    start = stack.pop()[0]
                    if not stack:
                        add((start, i + 1))
                    i += 1
                    continue
                stack[-1][1] = 1
                i += 2
                continue
            m = _CODE_SCAN.search(text, i)  # interpolation code
            if m is None:
                add((stack.pop()[0], n))
                i = n
                break
            i = m.start()
            c = text[i]
            if c == "{":
                stack[-1][1] += 1
                i += 1
                continue
            if c == "}":
                stack[-1][1] -= 1
                i += 1
                continue
            if c in "()":
                i += 1
                continue
            if c == "`":
                stack.append([i, 0])
                i += 1
                continue
            if c in "'\"":
                i = _plain_string_end(text, i, c)
                continue
            if i + 1 < n and text[i + 1] == "/":
                k = find("\n", i)
                i = n if k < 0 else k
                continue
            if i + 1 < n and text[i + 1] == "*":
                k = find("*/", i + 2)
                i = n if k < 0 else k + 2
                continue
            if _slash_is_regex(text, i, spans):
                i = _regex_end(text, i)
                continue
            i += 1
        return spans

    def code_matches(self, pattern):
        """Every match of ``pattern`` whose start position is real code."""
        for match in pattern.finditer(self.text):
            if self.is_code(match.start()):
                yield match


def _plain_string_end(text: str, i: int, quote: str) -> int:
    n = len(text)
    j = i + 1
    while j < n:
        k = text.find(quote, j)
        if k < 0:
            return n
        back, slashes = k - 1, 0
        while back >= 0 and text[back] == "\\":
            slashes += 1
            back -= 1
        if slashes % 2 == 0:
            return k + 1
        j = k + 1
    return n


def _regex_end(text: str, i: int) -> int:
    n = len(text)
    j = i + 1
    in_class = False
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "/" or c == "\n":
            break
        j += 1
    return min(j + 1, n)


def _prev_code_pos(text: str, i: int, spans) -> int | None:
    j = i - 1
    if j < 0:
        return None
    if spans and j < spans[-1][1]:
        return spans[-1][1] - 1
    while j >= 0 and text[j].isspace():
        j -= 1
    return None if j < 0 else j


def _slash_is_regex(text: str, i: int, spans) -> bool:
    """Whether the slash at ``i`` starts a regex literal rather than dividing.

    In valid JavaScript a regex can never follow a finished expression, so only
    the ``)`` of a control-statement header and a keyword before the slash are
    real regex positions among the expression-ending ones; everywhere else the
    fail-closed mask wins.
    """
    j = _prev_code_pos(text, i, spans)
    if j is None:
        return True
    prev = text[j]
    if prev.isalnum() or prev in "_$":
        w = j
        while w >= 0 and (text[w].isalnum() or text[w] in "_$"):
            w -= 1
        return text[w + 1:j + 1] in _REGEX_AFTER_WORDS
    if prev == "]":
        return False
    if prev == ")":
        group_start = _group_open_back(text, j)
        if group_start is None:
            return True  # unresolvable context: mask, the fail-closed direction
        k = _prev_code_pos(text, group_start, spans)
        if k is None:
            return False
        if text[k].isalnum() or text[k] in "_$":
            w = k
            while w >= 0 and (text[w].isalnum() or text[w] in "_$"):
                w -= 1
            return text[w + 1:k + 1] in _CONTROL_WORDS
        return False
    if prev in "\"'`" or prev == "/":
        return False  # a literal just ended: a following slash divides
    return True  # after an operator, an opener or a brace, a regex may start


def _group_open_back(text: str, close: int) -> int | None:
    depth = 1
    j = close - 1
    limit = max(0, close - _MAX_GROUP_WINDOW)
    while j >= limit:
        c = text[j]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                return j
        j -= 1
    return None


def lexical_regions(text: str) -> _LexicalRegions:
    """The shared outer code context for every static discovery regex."""
    return _LexicalRegions(text)


# -- the scope map: bound environments and global writes -------------------------

_SCOPE_SCAN = re.compile(r"[{}]|=>|(?<![\w$])(?:function|var|let|const|catch|class|for)(?![\w$])")
_FUNCTION_KINDS = frozenset({"function", "method", "arrow", "catch"})


#: Words classified as keywords when they precede a group or a brace.
_RESERVED_WORDS = frozenset(
    "function var let const catch class for extends get set static async accessor "
    "if while switch with do else try finally break continue return typeof new in of "
    "await yield case delete void throw instanceof this super true false null".split())
_SCOPE_SCAN = re.compile(r"[{}]|=>|(?<![\w$])(?:function|var|let|const|catch|class|for)(?![\w$])")
_FUNCTION_KINDS = frozenset({"function", "method", "arrow", "catch"})


class _ScopeMap:
    """Function, block, catch, loop and method scopes with their bound names.

    One pass over the code context tracks every brace with its kind, the
    parameters of function, method, arrow and catch headers, ``var`` hoisting
    to the enclosing function, ``let``/``const`` in their block, for-of/for-in
    headers, and destructuring patterns (whose binding positions feed the
    constant write analysis). A scope whose parameter list cannot be read — a
    computed-key method, ``]`` before the brace — is marked fuzzy and shadows
    every name inside it, so an unreadable context fails closed.
    """

    def __init__(self, text: str, regions: _LexicalRegions):
        self.text = text
        self.regions = regions
        self.nodes = []            # [start, end, kind, names, parent index]
        self.top_names = set()
        self.destructured = {}     # name -> positions bound by destructuring
        self.for_writes = {}       # name -> positions assigned by a for target
        self._for_headers = []     # (start, end) groups whose keywords self-handle
        self._for_header_starts = []  # the same intervals' starts, for bisect
        self._order = None         # node indices sorted by start, built lazily
        self._parse()

    # -- construction ------------------------------------------------------

    def _parse(self):
        text, regions = self.text, self.regions
        nodes = self.nodes
        stack = []     # node indices of open braces
        pending = None  # (kind, group_end, names) claimed by the next brace
        for m in _SCOPE_SCAN.finditer(text):
            if not regions.is_code(m.start()):
                continue
            token = m.group(0)
            if token == "{":
                kind, names, start = "block", set(), m.start()
                if pending is not None and m.start() >= pending[1]:
                    kind, names, start = pending[0], set(pending[2]), min(pending[3], m.start())
                    pending = None
                elif pending is None:
                    kind, names, start = self._classify_brace(m.start())
                # A brace inside a pending header group (destructured default
                # values) is a plain pattern brace and keeps the pending claim.
                # Function-kind spans open at the parameter group so a default
                # initializer's writes resolve against the parameters' scope.
                nodes.append([start, None, kind, names, stack[-1] if stack else -1])
                stack.append(len(nodes) - 1)
            elif token == "}":
                if stack:
                    nodes[stack.pop()][1] = m.end()
            elif token == "=>":
                self._arrow(m.start(), m.end(), stack)
            else:
                keyword = token
                if keyword == "function":
                    pending = self._function_header(m.start(), m.end(), stack)
                elif keyword == "for":
                    pending = self._for_header(m.end(), stack)
                elif keyword == "catch":
                    pending = self._catch_header(m.end())
                elif keyword == "class":
                    name = self._name_after(m.end())
                    if name is not None and self._statement_position(m.start()):
                        self._bind_in_function(stack, name)
                elif keyword in ("var", "let", "const"):
                    k = bisect.bisect_right(self._for_header_starts, m.start()) - 1
                    inside_for = (k >= 0 and m.start() < self._for_headers[k][1])
                    if not inside_for:
                        self._declarations(m.end(), keyword, stack)

    def _classify_brace(self, pos):
        """The kind, parameter names and header start of an unclaimed brace."""
        before = self._token_before(pos)
        if before is None:
            return "block", set(), pos
        kind, value = before
        if kind == "arrow":
            gt = self._skip_ws_back(pos)
            group_close = self._skip_ws_back(gt) if gt is not None else None
            if group_close is not None and self.text[group_close] == ")":
                group_start = self._group_open_back(group_close)
                if group_start is not None:
                    return ("arrow", set(self._group_names(group_start, group_close)),
                            group_start)
            return "arrow", set(), pos
        if kind == "op" and value == ")":
            group_start = self._group_open_back(pos - 1)
            if group_start is None:
                return "fuzzy", set(), pos
            head = self._token_before(group_start)
            if head is not None and head[0] == "ident":
                return ("method", set(self._group_names(group_start, pos - 1)), group_start)
            return "block", set(), group_start
        if kind == "op" and value == "]":
            return "fuzzy", set(), pos  # a computed-key method: parameters unreadable
        return "block", set(), pos

    def _arrow(self, arrow_start: int, arrow_end: int, stack):
        """Arrow parameters bind at the body, braced or a single expression."""
        before = self._token_before(arrow_start)
        params = []
        if before is not None:
            if before[0] == "op" and before[1] == ")":
                group_start = self._group_open_back(arrow_start - 1)
                if group_start is not None:
                    params = self._group_names(group_start, arrow_start - 1)
            elif before[0] == "ident":
                params = [before[1]]
        if self._next_code_char(arrow_end) == "{":
            return  # the braced body is classified as an arrow scope at its brace
        # ``x => expression``: the binding region runs to the end of the
        # enclosing statement or argument list; over-extending only shadows
        # more, which fails closed.
        depth, stop = 0, len(self.text)
        for pos, c in self.regions.events(arrow_end):
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    stop = pos
                    break
                depth -= 1
            elif c in ";," and depth == 0:
                stop = pos
                break
        self.nodes.append([arrow_start, stop, "arrow", set(params),
                           stack[-1] if stack else -1])

    def _function_header(self, keyword_start, keyword_end, stack):
        """Parse ``[*][name](params)`` after a ``function`` keyword."""
        i = self._skip_ws(keyword_end)
        if i < len(self.text) and self.text[i] == "*":
            i = self._skip_ws(i + 1)
        name = self._name_at(i)
        if name is not None:
            i = self._skip_ws(i + len(name))
        if i >= len(self.text) or self.text[i] != "(":
            return None
        close = self._group_close_forward(i)
        if close is None:
            return None
        if name is not None and self._statement_position(keyword_start):
            self._bind_in_function(stack, name)
        return ("function", close + 1, self._group_names(i, close), i)

    def _for_header(self, pos, stack):
        i = self._skip_ws(pos)
        if self._name_at(i) == "await":
            i = self._skip_ws(i + 5)
        if i >= len(self.text) or self.text[i] != "(":
            return None
        close = self._group_close_forward(i)
        if close is None:
            return None
        self._for_headers.append((i, close))
        self._for_header_starts.append(i)
        names, targets = self._for_header_names(i + 1, close)
        for target in targets:
            self.for_writes.setdefault(target, []).append(i)
        if not names:
            return None
        if self._next_code_char(close + 1) != "{":
            # A non-block loop body: the bindings live in the header alone.
            self.nodes.append([i, close + 1, "for", set(names),
                               stack[-1] if stack else -1])
            return None
        return ("for", close + 1, names, i)

    def _for_header_names(self, start, close):
        text = self.text
        i = self._skip_ws(start)
        declared, targets = [], []
        m = _IDENT_RE.match(text, i)
        if m is not None and m.group(0) in ("let", "const", "var"):
            i = self._skip_ws(m.end())
            if i < close:
                c = text[i]
                if c in "[{":
                    end = self._pattern_close_forward(i)
                    pattern = self._pattern_names(i + 1, end)
                    declared.extend(pattern)
                    targets.extend(pattern)
                elif c.isalpha() or c in "_$":
                    ident = _IDENT_RE.match(text, i)
                    if ident is not None:
                        declared.append(ident.group(0))
                        targets.append(ident.group(0))
            return declared, targets
        # No declaration keyword: a bare or destructured assignment target.
        if i < close:
            c = text[i]
            if c in "[{":
                end = self._pattern_close_forward(i)
                targets.extend(self._pattern_names(i + 1, end))
            elif c.isalpha() or c in "_$":
                ident = _IDENT_RE.match(text, i)
                if ident is not None:
                    targets.append(ident.group(0))
        return declared, targets

    def _catch_header(self, pos):
        i = self._skip_ws(pos)
        if i < len(self.text) and self.text[i] == "(":
            close = self._group_close_forward(i)
            if close is None:
                return None
            return ("catch", close + 1, self._group_names(i, close), i)
        return ("catch", 0 if i >= len(self.text) or self.text[i] != "{" else i, [], pos)

    def _declarations(self, pos, keyword, stack):
        """Collect one declarator list's binding names, destructuring included."""
        text = self.text
        i = self._skip_ws(pos)
        expect_name = True
        while i < len(text):
            c = text[i]
            if expect_name:
                if c in "[{":
                    end = self._pattern_close_forward(i)
                    for name in self._pattern_names(i + 1, end):
                        self._bind(keyword, stack, name)
                        self.destructured.setdefault(name, []).append(i)
                    i = self._skip_ws(end + 1)
                    expect_name = False
                    continue
                ident = _IDENT_RE.match(text, i)
                if ident is None:
                    return
                self._bind(keyword, stack, ident.group(0))
                i = self._skip_ws(ident.end())
                expect_name = False
                continue
            if c == "=":
                i = self._skip_initializer(i + 1)
                continue
            if c == ",":
                i = self._skip_ws(i + 1)
                expect_name = True
                continue
            return  # ';' or any other token ends the declarator list

    def _pattern_names(self, start, end):
        """The binding identifiers of a destructuring pattern, keys excluded.

        Identifiers are read between structural events: one before ``:`` in an
        object pattern is a key, one before ``,`` ``]`` ``}`` or ``=`` is a
        binding, and everything inside a default expression is skipped whole.
        """
        text = self.text
        names = []
        depth = 0
        skipping = None
        for pos, c in self.regions.events(start, stop=end):
            if skipping is not None:
                if c in "[{":
                    skipping += 1
                elif c in "]}":
                    if skipping <= depth:
                        skipping = None  # the closer of this pattern level
                    else:
                        skipping -= 1
                elif c == "," and skipping == depth:
                    skipping = None  # the separator: handled below
                continue
            if c in "[{":
                depth += 1
                continue
            if c in "]}":
                depth -= 1
                continue
            if c in ",=" or (c == ":" and depth > 0):
                ident = self._ident_before(pos, start)
                if ident is not None and c != ":":
                    names.append(ident)
                if c == "=":
                    skipping = depth
                continue
            # a group or separator inside a default: nothing to bind
        return names

    def _ident_before(self, pos: int, floor: int):
        """The identifier ending right before ``pos``, if any."""
        j = self._skip_ws_back(pos)
        if j is None or j < floor:
            return None
        w = j
        while w >= floor and (self.text[w].isalnum() or self.text[w] in "_$"):
            w -= 1
        if w == j:
            return None
        candidate = self.text[w + 1:j + 1]
        return candidate if _IDENT_RE.fullmatch(candidate) else None

    def _bind(self, keyword, stack, name):
        """Bind a declared name; ``var`` hoists to the enclosing function."""
        if keyword == "var":
            target = self._enclosing_function(stack)
        else:
            target = stack[-1] if stack else -1
        if target < 0:
            self.top_names.add(name)
        else:
            self.nodes[target][3].add(name)

    def _bind_in_function(self, stack, name):
        target = self._enclosing_function(stack)
        if target < 0:
            self.top_names.add(name)
        else:
            self.nodes[target][3].add(name)

    def _enclosing_function(self, stack):
        """The innermost open function-kind node index, or -1 for the top level."""
        target = stack[-1] if stack else -1
        while target >= 0 and self.nodes[target][2] not in _FUNCTION_KINDS:
            target = self.nodes[target][4]
        return target

    # -- queries -------------------------------------------------------------

    def chain(self, position: int):
        """The scope node indices containing ``position``, innermost first."""
        if self._order is None:
            self._order = sorted(range(len(self.nodes)), key=lambda k: self.nodes[k][0])
            self._starts_cache = tuple(self.nodes[k][0] for k in self._order)
        result = []
        k = bisect.bisect_right(self._starts_cache, position) - 1
        while k >= 0:
            index = self._order[k]
            start, end = self.nodes[index][0], self.nodes[index][1]
            if end is not None and start <= position < end:
                while index >= 0:
                    result.append(index)
                    index = self.nodes[index][4]
                return result
            k -= 1
        return result

    def bindings_at(self, position: int):
        """Names rebound by the scopes around ``position``; None when unreadable."""
        names = set()
        for index in self.chain(position):
            if self.nodes[index][2] == "fuzzy":
                return None
            names |= self.nodes[index][3]
        return names

    def is_shadowed(self, name: str, position: int) -> bool:
        """Whether any scope between ``position`` and the top level rebinds name."""
        bindings = self.bindings_at(position)
        return bindings is None or name in bindings

    # -- lexical helpers over the code context -------------------------------

    def _skip_ws(self, i: int) -> int:
        text, code = self.text, self.regions._code
        starts, spans = self.regions._starts, self.regions.spans
        n = len(text)
        while i < n:
            if code[i]:
                k = bisect.bisect_right(starts, i) - 1
                i = spans[k][1] if k >= 0 else n
                continue
            if not text[i].isspace():
                return i
            i += 1
        return n

    def _name_at(self, i: int):
        m = _IDENT_RE.match(self.text, i)
        return m.group(0) if m is not None else None

    def _name_after(self, pos: int):
        return self._name_at(self._skip_ws(pos))

    def _next_code_char(self, pos: int):
        i = self._skip_ws(pos)
        return self.text[i] if i < len(self.text) else None

    def _token_before(self, pos: int):
        """The token before ``pos``: (kind, value), keywords classified."""
        i = self._skip_ws_back(pos)
        if i is None:
            return None
        c = self.text[i]
        if c.isalpha() or c in "_$":
            m = _IDENT_RE.match(self.text, i)
            while m is not None and m.start() > 0 and (
                    self.text[m.start() - 1].isalnum() or self.text[m.start() - 1] in "_$"):
                m = _IDENT_RE.match(self.text, m.start() - 1)
            word = m.group(0) if m else c
            if word in _RESERVED_WORDS:
                return ("kw", word)
            return ("ident", word)
        if c == ">" and i > 0 and self.text[i - 1] == "=":
            return ("arrow", "=>")
        return ("op", c)

    def _skip_ws_back(self, pos: int):
        text, code = self.text, self.regions._code
        starts, spans = self.regions._starts, self.regions.spans
        i = pos - 1
        while i >= 0:
            if code[i]:
                k = bisect.bisect_right(starts, i) - 1
                if k < 0:
                    return None
                i = spans[k][0] - 1
                continue
            if not text[i].isspace():
                return i
            i -= 1
        return None

    def _group_open_back(self, close: int):
        depth = 1
        i = close - 1
        limit = max(0, close - _MAX_GROUP_WINDOW)
        while i >= limit:
            c = self.text[i]
            if c == ")":
                depth += 1
            elif c == "(":
                depth -= 1
                if depth == 0:
                    return i
            i -= 1
        return None

    def _group_close_forward(self, open_pos: int):
        depth = 0
        for pos, c in self.regions.events(open_pos):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return pos
        return None

    def _pattern_close_forward(self, open_pos: int):
        """The matching bracket of a destructuring pattern opener."""
        opener = self.text[open_pos]
        closer = "]" if opener == "[" else "}"
        depth = 0
        for pos, c in self.regions.events(open_pos):
            if c in "[{(":
                depth += 1
            elif c in "]})":
                depth -= 1
                if depth == 0 and c == closer:
                    return pos
        return len(self.text) - 1

    def _skip_initializer(self, i: int) -> int:
        """Skip past a declarator's initializer, stopping at its separator."""
        depth = 0
        for pos, c in self.regions.events(i):
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    return pos
                depth -= 1
            elif c in ";," and depth == 0:
                return pos
        return len(self.text)

    def _statement_position(self, pos: int) -> bool:
        """Whether the token at ``pos`` starts a statement, not an expression."""
        i = self._skip_ws_back(pos)
        if i is None:
            return True
        c = self.text[i]
        if c in ";}{":
            return True
        m = _IDENT_RE.match(self.text, i)
        if m is not None:
            return m.group(0) in ("do", "else")
        return False

    def _group_names(self, open_pos: int, close_pos: int):
        """Binding names of a parameter group: plain, defaulted, rest, patterns."""
        names = []
        i = self._skip_ws(open_pos + 1)
        while i < close_pos:
            c = self.text[i]
            if c == ",":
                i = self._skip_ws(i + 1)
                continue
            if c == ".":
                i = self._skip_ws(i + 3 if self.text[i:i + 3] == "..." else i + 1)
                continue
            if c in "[{":
                pattern_end = self._pattern_close_forward(i)
                names.extend(self._pattern_names(i + 1, pattern_end))
                i = self._skip_ws(pattern_end + 1)
                if i < close_pos and self.text[i] == "=":
                    i = self._skip_initializer(i + 1)
                continue
            ident = _IDENT_RE.match(self.text, i)
            if ident is None:
                i += 1
                continue
            names.append(ident.group(0))
            i = self._skip_ws(ident.end())
            if i < close_pos and self.text[i] == "=":
                i = self._skip_initializer(i + 1)
            continue
        return names


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


def _exported_names(bundle):
    """The distinct local names a bundle binds to each exported mechanism name.

    The mapping call is discovered in the shared code context only, so a
    decoy export living in a string or a comment provides nothing, while the
    real export's string argument stays matchable.
    """
    bound = {_REGISTER_EXPORT: [], _RESOLVE_EXPORT: []}
    for match in bundle.regions.code_matches(_EXPORT_RE):
        export, local = match.group(3), match.group(2)
        prefix = bundle.text[:match.start()].rstrip()
        if prefix.endswith("function"):
            continue
        if local not in bound[export]:
            bound[export].append(local)
    return {export: tuple(names) for export, names in bound.items()}


# -- the bundle with its lazily verified functions, constants and helpers ------

class _Bundle:
    """The bundle text plus memoized parsing, constants, purity and scopes."""

    def __init__(self, text: str):
        self.text = text
        self.regions = _LexicalRegions(text)
        self._sites = {}
        self._constants = {}
        self._pure = {}
        self._pure_visiting = set()
        self._scopes = None
        self._writes = {}
        self._write_index = None
        self.proof_names = set()

    def scopes(self):
        if self._scopes is None:
            self._scopes = _ScopeMap(self.text, self.regions)
        return self._scopes

    def chain_bindings(self, position: int):
        """Names the scopes around a position rebind; None when unreadable."""
        return self.scopes().bindings_at(position)

    # -- function sites -------------------------------------------------------

    def sites(self, name: str):
        """Every ``(parameters, defaults, statements, body, body_start)`` of one name.

        A definition is located when its header and braces balance at token
        level, its position is real code — never a string, comment, template
        or regex literal — and none of its default initializers carries an
        escaped literal; ``statements`` is ``None`` when the body then leaves
        the supported grammar, which the caller treats as an unrecognized flow
        rather than a missing function. A name with no definition or several
        located definitions is ambiguous and fails closed at the caller.
        """
        self.proof_names.add(name)
        if name not in self._sites:
            found = []
            pattern = re.compile(r"(?<![\w$])function\s+" + re.escape(name) + r"\s*\(")
            for match in self.regions.code_matches(pattern):
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
        parameters, defaults = [], []
        while not parser.at_punct(")"):
            parameter = parser.expect_id()
            # The default is kept and verified, never silently dropped: only
            # the register options parameter may default to an empty object.
            default = parser.parse_ternary() if parser.eat_punct("=") else None
            parameters.append(parameter)
            defaults.append(default)
            if not parser.eat_punct(","):
                break
        parser.expect_punct(")")
        brace = parser.peek()
        if brace is None or brace[0] != "punct" or brace[1] != "{":
            raise _ParseError("the function body does not open")
        body_start = brace[2]
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
        return tuple(parameters), tuple(defaults), statements, body, body_start

    # -- module constants -----------------------------------------------------

    #: One scan shape for every plausible write of any name, so a bundle's
    #: whole write surface is collected once instead of once per name.
    _GAP = r"(?:\s|/\*[\s\S]*?\*/|//[^\n]*(?:\n|$))*"
    _WRITE_CANDIDATE = re.compile(
        r"(?<![\w$.])([A-Za-z_$][A-Za-z0-9_$]*)(?=" + _GAP +
        r"(?:=(?![=>])|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<=|>>=|>>>=|\*\*=|&&=|\|\|=|\?\?="
        r"|\+\+|--|\[|\?\." + _GAP + r"\[|(?:\.|\?\.)" + _GAP + r"(?:add|delete|clear|push|pop|shift|unshift|splice|sort"
        r"|reverse|fill|copyWithin)" + _GAP + r"(?:\?\." + _GAP + r")?\())")
    _WRITE_PREFIX = re.compile(r"(?<![\w$.])(?:\+\+|--)(?=" + _GAP + r"([A-Za-z_$][A-Za-z0-9_$]*))")

    def write_index(self):
        """Every write-shaped position of every name, keyed by name, once."""
        if self._write_index is None:
            index = {}
            for match in self.regions.code_matches(self._WRITE_CANDIDATE):
                index.setdefault(match.group(1), []).append(match.start(1))
            self._prefix_writes = set()
            for match in self.regions.code_matches(self._WRITE_PREFIX):
                index.setdefault(match.group(1), []).append(match.start(1))
                self._prefix_writes.add((match.group(1), match.start(1)))
            # Assignment patterns are writes even without a declaration keyword.
            # ScopeMap parses binding targets; its lexical regions exclude keys,
            # comments and literal contents from that target walk.
            stack = []
            self._pattern_writes = set()
            for position, char in self.regions.events(0):
                if char in "[{":
                    stack.append((position, char))
                elif char in "]}" and stack:
                    start, opener = stack.pop()
                    if (opener, char) not in (("[", "]"), ("{", "}")):
                        continue
                    following = self._skip_write_ws(position + 1)
                    if (self.text[following:following + 1] == "="
                            and self.text[following + 1:following + 2] not in ("=", ">")):
                        # Collect targets conservatively. Object property keys
                        # are excluded; unreadable defaults cannot establish
                        # that a proof dependency stayed untouched.
                        try:
                            tokens = list(_token_iter(self.text, start + 1, position))
                        except _ParseError:
                            # A malformed/escaped default must not erase an
                            # otherwise visible target before it. Conservatively
                            # retain code identifiers; literals/comments stay out.
                            tokens = [("id", match.group(0), match.start(), match.end())
                                      for match in _IDENT_RE.finditer(self.text, start + 1, position)
                                      if self.regions.is_code(match.start())]
                        for offset, token in enumerate(tokens):
                            if token[0] != "id" or (offset + 1 < len(tokens) and tokens[offset + 1][1] == ":"):
                                continue
                            name = token[1]
                            index.setdefault(name, []).append(start)
                            self._pattern_writes.add((name, start))
            self._write_index = index
        return self._write_index

    def name_writes(self, name: str):
        """Every unshadowed write or mutation of a module-level name.

        A write counts only when no scope between its position and the top
        level rebinds the name, so an unrelated local with the same minified
        name never unsolves the module constant. Kinds: ``assign`` for a plain
        ``=``, and anything else — compound assignment, increments, index
        writes, mutating calls, destructuring bindings and bare for-loop
        targets — which always leaves a constant unsolvable.
        """
        if name in self._writes:
            return self._writes[name]
        candidates = self.write_index().get(name, ())
        events = [(position, "destructure" if (name, position) in self._pattern_writes
                   else "update" if (name, position) in self._prefix_writes
                   else self._classify_write(name, position))
                  for position in candidates]
        scopes = self.scopes()
        for position in scopes.for_writes.get(name, ()):
            events.append((position, "update"))
        for position in scopes.destructured.get(name, ()):
            events.append((position, "destructure"))
        kept, seen = [], set()
        for position, kind in sorted(events):
            if kind == "read" or position in seen or scopes.is_shadowed(name, position):
                continue
            seen.add(position)
            kept.append((position, kind))
        self._writes[name] = kept
        return kept

    def _classify_write(self, name: str, position: int) -> str:
        """The write kind at a candidate position, or ``read`` for a plain read."""
        text = self.text
        n = len(text)
        i = self._skip_write_ws(position + len(name))
        if i >= n:
            return "read"
        c = text[i]
        optional_member = text[i:i + 2] == "?."
        if optional_member:
            i = self._skip_write_ws(i + 2)
            c = text[i] if i < n else ""
        if c == "=":
            return "assign" if text[i + 1:i + 2] not in ("=", ">") else "read"
        if text[i:i + 2] in ("++", "--"):
            return "update"
        for width in (4, 3, 2):
            if text[i:i + width] in _COMPOUND_PUNCT:
                return "compound"
        if c == "[":
            return self._index_write_kind(i)
        if c == "." or optional_member:
            j = i if optional_member else self._skip_write_ws(i + 1)
            m = _IDENT_RE.match(text, j)
            if m is not None and m.group(0) in _MUTATION_METHODS:
                k = self._skip_write_ws(m.end())
                if text[k:k + 2] == "?.":
                    k = self._skip_write_ws(k + 2)
                return "mutate" if k < n and text[k] == "(" else "read"
            return "read"
        return "read"

    def _index_write_kind(self, open_bracket: int) -> str:
        """Whether ``name[...]`` is written through; unreadable contexts write."""
        depth = 0
        i = open_bracket
        n = min(len(self.text), open_bracket + _MAX_GROUP_WINDOW)
        while i < n:
            if not self.regions.is_code(i):
                k = bisect.bisect_right(self.regions._starts, i) - 1
                i = self.regions.spans[k][1]
                continue
            c = self.text[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth == 0:
                    j = self._skip_write_ws(i + 1)
                    if j < n and self.text[j] == "=" and self.text[j + 1:j + 2] not in ("=", ">"):
                        return "assign"
                    if j < n and self.text[j:j + 2] in ("++", "--"):
                        return "update"
                    for width in (4, 3, 2):
                        if self.text[j:j + width] in _COMPOUND_PUNCT:
                            return "compound"
                    if self.text[j:j + 1] == "(":
                        # A computed method can mutate a proved Set/array; its
                        # dynamic key cannot establish a read-only operation.
                        return "mutate"
                    if (self.text[j:j + 2] == "?."
                            and self.text[self._skip_write_ws(j + 2):][:1] == "("):
                        return "mutate"
                    return "read"
            i += 1
        return "compound"  # the index could not be resolved: fail closed

    def _skip_write_ws(self, i: int) -> int:
        n = len(self.text)
        while i < n:
            if not self.regions.is_code(i):
                k = bisect.bisect_right(self.regions._starts, i) - 1
                i = self.regions.spans[k][1]
                continue
            if not self.text[i].isspace():
                return i
            i += 1
        return n

    def constant(self, name: str, depth: int = 0):
        """The single agreed value of a module constant: a string, list or Set.

        Every unshadowed write of the name must be a plain assignment whose
        complete right-hand side parses and resolves to the same value; any
        unrecognized, mutating or disagreeing write leaves it unsolvable
        instead of discarding the inconvenient one.
        """
        if name in self._constants:
            return self._constants[name]
        if depth > _MAX_CONST_DEPTH:
            return None
        writes = self.name_writes(name)
        values = set()
        for position, kind in writes:
            if kind != "assign":
                self._constants[name] = None
                return None
            value = self._constant_value(position, depth)
            if value is None:
                self._constants[name] = None
                return None
            values.add(value)
        resolved = next(iter(values)) if len(values) == 1 and values else None
        self._constants[name] = resolved
        return resolved

    def _constant_value(self, position: int, depth: int):
        """The parsed value of the assignment at ``position``, whole right-hand side.

        The expression must consume the entire right-hand side and stop at a
        terminator a real declarator list or call could put there; a truncated
        parse — ``"Re" + "ad"``, for one — resolves to nothing.
        """
        limit = min(len(self.text), position + 4096)
        try:
            parser = _Parser(_token_iter(self.text, position, limit))
            parser.next()  # the name itself
            parser.expect_punct("=")
            node = parser.parse_ternary()
            following = parser.peek()
        except _ParseError:
            return None
        if following is None and limit < len(self.text):
            return None
        if following is not None and not (following[0] == "punct"
                                          and following[1] in _RHS_TERMININATORS):
            return None
        env = self.chain_bindings(position)
        return None if env is None else self._constant_of_node(node, depth, env)

    def _constant_of_node(self, node, depth: int, env=frozenset()):
        if node[0] in ("str", "num"):
            return (node[0], node[1])
        if node[0] == "array":
            return self._constant_list(node, depth, env)
        if node[0] == "new" and node[1] == ("id", "Set") and len(node[2]) == 1:
            if "Set" in env:
                return None
            argument = node[2][0]
            inner = (self._constant_list(argument, depth, env) if argument[0] == "array"
                     else self.constant(argument[1], depth + 1) if argument[0] == "id" and argument[1] not in env else None)
            if inner is None or inner[0] != "list":
                return None
            return ("set", frozenset(inner[1]))
        if node[0] == "id":
            return None if node[1] in env else self.constant(node[1], depth + 1)
        return None

    def _constant_list(self, node, depth: int, env=frozenset()):
        elements = []
        for element in node[1]:
            if element[0] == "str":
                elements.append(element[1])
            elif element[0] == "id":
                if element[1] in env:
                    return None
                inner = self.constant(element[1], depth + 1)
                if inner is None or inner[0] != "str":
                    return None
                elements.append(inner[1])
            else:
                return None
        return ("list", tuple(elements))

    def constant_string(self, node, env=frozenset()):
        """A node's constant string: a literal, or an unshadowed identifier."""
        if node[0] == "str":
            return node[1]
        if node[0] == "id":
            if node[1] in env:
                return None  # a formal or local is never the module constant
            value = self.constant(node[1])
            if value is not None and value[0] == "str":
                return value[1]
        return None

    # -- helper purity --------------------------------------------------------

    def pure_function(self, name: str, depth: int = 0, call_env=frozenset()) -> bool:
        """Whether a helper's whole body is built from the pure grammar only.

        The reference must not be shadowed where it is made, and the helper's
        own environment — parameters, locals and every enclosing scope —
        contributes its bindings, so a shadowed helper or constant inside the
        chain never passes as the global one.
        """
        if call_env and name in call_env:
            return False
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
                parameters, defaults, statements, _, body_start = sites[0]
                chain = self.chain_bindings(body_start)
                if (any(default is not None for default in defaults)
                        or chain is None or statements is None):
                    verdict = False
                else:
                    env = frozenset(parameters) | chain
                    verdict = self._pure_statements(statements, env, depth)
        finally:
            self._pure_visiting.discard(name)
        self._pure[name] = verdict
        return verdict

    def _pure_statements(self, statements, env: frozenset, depth: int) -> bool:
        for statement in statements:
            kind = statement[0]
            if kind == "empty":
                continue
            if kind == "let":
                for name, initializer in statement[2]:
                    if initializer is not None and not self._pure_expression(initializer, env, depth):
                        return False
                    env = env | {name}
            elif kind == "return":
                if statement[1] is not None and not self._pure_expression(statement[1], env, depth):
                    return False
            elif kind == "if":
                if (statement[3] is not None
                        or not self._pure_expression(statement[1], env, depth)
                        or not self._pure_statements(statement[2], env, depth)):
                    return False
            else:
                return False
        return True

    def _pure_expression(self, node, env: frozenset, depth: int) -> bool:
        kind = node[0]
        if kind in ("str", "num", "lit", "id"):
            return True
        if kind == "member":
            return self._pure_expression(node[1], env, depth)
        if kind == "index":
            return (self._pure_expression(node[1], env, depth)
                    and self._pure_expression(node[2], env, depth))
        if kind == "unary":
            return self._pure_expression(node[2], env, depth)
        if kind == "bin":
            return (self._pure_expression(node[2], env, depth)
                    and self._pure_expression(node[3], env, depth))
        if kind in ("logic", "cond"):
            operands = node[2] if kind == "logic" else node[1:]
            return all(self._pure_expression(operand, env, depth) for operand in operands)
        if kind == "array":
            return all(self._pure_expression(element[1] if element[0] == "spread" else element,
                                             env, depth) for element in node[1])
        if kind == "object":
            return all(self._pure_expression(value[1] if value[0] == "spread" else value,
                                             env, depth) for _, value in node[1])
        if kind == "arrow":
            return self._pure_expression(node[2], env | set(node[1]), depth)
        if kind == "new":
            return node[1] == ("id", "Set") and all(
                self._pure_expression(argument, env, depth) for argument in node[2])
        if kind == "call":
            return self._pure_call(node, env, depth)
        return False

    def _pure_call(self, node, env: frozenset, depth: int) -> bool:
        callee, arguments = node[1], node[2]
        if not all(self._pure_expression(argument, env, depth) for argument in arguments):
            return False
        if callee[0] == "id":
            return self.pure_function(callee[1], depth + 1, env)
        if callee[0] == "member" and callee[2] in _STRING_METHODS:
            base = callee[1]
            while base[0] == "member":
                base = base[1]
            return base[0] == "id" and (base[1] in env or self.constant(base[1]) is not None)
        return False

    # -- recognized helper templates ------------------------------------------

    def set_builder(self, name: str, call_env=frozenset()) -> bool:
        """The pure disallowed-Set builder template over one parameter.

        The optional empty guard, the fresh Set, the single for-of that only
        adds normalized entries through a verified pure normalizer and the
        bounded return together admit exactly the installed builder's shape.
        """
        if name in call_env:
            return False
        sites = self.sites(name)
        if len(sites) != 1:
            return False
        parameters, defaults, statements, _, body_start = sites[0]
        chain = self.chain_bindings(body_start)
        if (not parameters or statements is None or chain is None
                or any(default is not None for default in defaults)):
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
        env = frozenset(parameters) | chain | {set_var, loop_var}
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
                    built = self.pure_function(norm_init[1][1], 0, env | {norm_var})
        if not built:
            return False
        index += 1
        if index != len(statements) - 1:
            return False
        return statements[-1] == (
            "return", ("cond", ("bin", ">", _member(set_id, "size"), ("num", "0")),
                       set_id, ("unary", "void", ("num", "0"))))


def _name_predicate_ok(bundle: _Bundle, name: str, call_env=frozenset()) -> bool:
    """The pure name predicate template: ``x ? SET.has(x) : !0``.

    The Set must be a resolved constant of strings that the predicate's own
    environment does not shadow; a helper that merely carries a plausible
    name, or one whose predicate can act, never qualifies.
    """
    if name in call_env:
        return False
    sites = bundle.sites(name)
    if len(sites) != 1:
        return False
    parameters, defaults, statements, _, body_start = sites[0]
    chain = bundle.chain_bindings(body_start)
    if (len(parameters) != 1 or statements is None or chain is None
            or any(default is not None for default in defaults)
            or len(statements) != 1 or statements[0][0] != "return"):
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
    if base[1] in frozenset(parameters) | chain:
        return False
    resolved = bundle.constant(base[1])
    return resolved is not None and resolved[0] == "set"


# -- the registration function -------------------------------------------------

def _registration_problem(bundle: _Bundle, name: str):
    """Verify the registration function; return its wiring context or a reason."""
    sites = bundle.sites(name)
    if len(sites) != 1:
        return None, _REGISTER_NOT_FUNCTION
    parameters, defaults, statements, body, body_start = sites[0]
    if any(default is not None and default != ("object", ()) for default in defaults):
        return None, _BAD_DEFAULT
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
    # Collect the shape first so a rewritten Set is named precisely before the
    # ordering verdicts: declarations and empty statements, then the one
    # for-of, then nothing but empty statements. ``var`` hoisting is why a
    # declaration after the loop — uninitialized at loop time — never qualifies.
    declarations, seen_loop, loops, misordered, order_problem = [], False, [], [], False
    for statement in statements:
        if statement[0] == "empty":
            continue
        if statement[0] == "let":
            declarations.extend(statement[2])
            if seen_loop:
                order_problem = True
        elif statement[0] == "forof":
            if seen_loop:
                return None, _UNRECOGNIZED_FLOW
            seen_loop, _ = True, loops.append(statement)
        else:
            misordered.append(statement)
    if not seen_loop or len(loops) != 1:
        return None, _UNRECOGNIZED_FLOW
    # Complete bindings: duplicate parameters, a local or parameter named Set,
    # a local redeclaring a parameter or an earlier local, and a loop variable
    # colliding with any of them, all leave the template's meaning unprovable.
    if len(set(parameters)) != len(parameters) or "Set" in parameters:
        return None, _BAD_BINDING
    bound = set(parameters)
    for declared, _initializer in declarations:
        if declared in bound or declared == "Set":
            return None, _BAD_BINDING
        bound.add(declared)
    _, loop_var, iterable, loop_body = loops[0]
    if loop_var in bound:
        return None, _BAD_BINDING
    bound.add(loop_var)
    set_var = options_param = None
    for declared, initializer in declarations:
        for parameter in parameters:
            if initializer == _set_declaration(parameter):
                if set_var is not None:
                    return None, _UNRECOGNIZED_FLOW
                set_var, options_param = declared, parameter
    if set_var is None:
        return None, _NO_SET_FILTER
    # Writes: the allowlist Set may be written exactly once — its declaration;
    # every other bound name — parameters, locals, the loop variable — and the
    # registry parameter may never be written anywhere in the body.
    write_counts = {}
    for index in range(len(body) - 1):
        token, following = body[index], body[index + 1]
        if token[0] != "id" or not (following[0] == "punct"
                                    and following[1] in _ASSIGNMENT_PUNCT):
            continue
        if index > 0 and body[index - 1][0] == "punct" and body[index - 1][1] in ("++", "--"):
            continue  # counted by the prefix pass below
        write_counts[token[1]] = write_counts.get(token[1], 0) + 1
    for index in range(len(body) - 1):
        token, previous = body[index], body[index - 1] if index else None
        if (token[0] == "id" and token[1] in bound and previous is not None
                and previous[0] == "punct" and previous[1] in ("++", "--")):
            write_counts[token[1]] = write_counts.get(token[1], 0) + 1
    if write_counts.get(set_var, 0) > 1:
        return None, _SET_REWRITTEN
    for parameter in parameters:
        if write_counts.get(parameter, 0):
            return None, _UNRECOGNIZED_FLOW
    for declared, _initializer in declarations:
        if declared != set_var and write_counts.get(declared, 0) > 1:
            return None, _UNRECOGNIZED_FLOW
    if write_counts.get(loop_var, 0) or write_counts.get(next(iter(receivers)), 0):
        return None, _UNRECOGNIZED_FLOW
    if misordered or order_problem:
        reason = _UNRECOGNIZED_FLOW if any(s[0] == "assign" for s in misordered) else _BAD_ORDER
        return None, reason
    chain = bundle.chain_bindings(body_start)
    if chain is None:
        return None, _UNRECOGNIZED_FLOW
    env = frozenset(bound) | chain
    local_kinds = {}
    for declared, initializer in declarations:
        if declared == set_var:
            continue
        usable, kind = _preamble_declaration(bundle, initializer, env)
        if not usable:
            return None, _BAD_PREAMBLE
        local_kinds[declared] = kind
    if not _pure_read(iterable):
        return None, _UNRECOGNIZED_FLOW
    registry = next(iter(receivers))
    environment = {"set_var": set_var, "set_locals": local_kinds, "loop_var": loop_var,
                   "parameters": parameters, "registry": registry, "options": options_param,
                   "env": env}
    problem = _loop_flow_problem(bundle, loop_body, environment)
    if problem is not None:
        return None, problem
    return (len(parameters), parameters.index(options_param)), None


def _preamble_declaration(bundle: _Bundle, initializer, env: frozenset):
    """A preamble declaration must be a pure read or a verified helper call."""
    if initializer is None:
        return True, "pure"  # a bare binding; nothing downstream may read it
    if _pure_read(initializer):
        return True, "pure"
    if (initializer[0] == "call" and initializer[1][0] == "id"
            and all(_pure_read(argument) for argument in initializer[2])):
        helper = initializer[1][1]
        if helper in env:
            return False, None  # a shadowed helper is not the verified global one
        if bundle.set_builder(helper, env):
            return True, "set"
        if bundle.pure_function(helper, 0, env):
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
    loop_var, options, env = environment["loop_var"], environment["options"], environment["env"]
    if first == ("id", loop_var):
        pass
    elif (first[0] == "call" and first[1][0] == "id"
          and tuple(first[2]) == (("id", loop_var), ("id", options))):
        if first[1][1] in env:
            return _TRANSFORM_PROBLEM
        problem = _transform_problem(bundle, first[1][1], env)
        if problem is not None:
            return problem
    else:
        return _BAD_REGISTER_ARG
    if not all(_pure_read(argument) for argument in arguments[1:]):
        return _BAD_REGISTER_ARG
    return None


def _rejection_operand_ok(bundle: _Bundle, node, environment) -> bool:
    """One rejection operand: pure comparison logic, a verified Set.has or predicate."""
    env = environment["env"]
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
            if base[0] == "id" and base[1] not in env:
                resolved = bundle.constant(base[1])
                return resolved is not None and resolved[0] == "set"
            return False
        if callee[0] == "id" and len(arguments) == 1 and _pure_read(arguments[0]):
            return _name_predicate_ok(bundle, callee[1], env)
        return False
    return False


def _transform_problem(bundle: _Bundle, name: str, call_env=frozenset()) -> str | None:
    """The transform returns every read-only tool unchanged, or fails closed."""
    if name in call_env:
        return _TRANSFORM_PROBLEM
    sites = bundle.sites(name)
    if len(sites) != 1:
        return _TRANSFORM_PROBLEM
    parameters, defaults, statements, _, body_start = sites[0]
    if any(default is not None for default in defaults):
        return _BAD_DEFAULT
    chain = bundle.chain_bindings(body_start)
    if (chain is None or len(parameters) != 2 or statements is None or len(statements) != 1
            or statements[0][0] != "return"):
        return _TRANSFORM_PROBLEM
    env = frozenset(parameters) | chain
    node = statements[0][1]
    if node is None:
        return _TRANSFORM_PROBLEM
    while node[0] == "cond":
        problem = _transform_condition_problem(bundle, node[1], parameters[0], env)
        if problem is not None:
            return problem
        node = node[3]
    return None if node == ("id", parameters[0]) else _TRANSFORM_PROBLEM


def _chain_within(node, name_chain) -> bool:
    """Whether the name chain appears anywhere inside ``node``."""
    if node == name_chain:
        return True
    kind = node[0]
    if kind == "member":
        return _chain_within(node[1], name_chain)
    if kind == "index":
        return _chain_within(node[1], name_chain) or _chain_within(node[2], name_chain)
    if kind == "unary":
        return _chain_within(node[2], name_chain)
    if kind == "bin":
        return _chain_within(node[2], name_chain) or _chain_within(node[3], name_chain)
    if kind in ("logic", "cond"):
        operands = node[2] if kind == "logic" else node[1:]
        return any(_chain_within(operand, name_chain) for operand in operands)
    if kind == "array":
        return any(_chain_within(element[1] if element[0] == "spread" else element, name_chain)
                   for element in node[1])
    if kind == "object":
        return any(_chain_within(value[1] if value[0] == "spread" else value, name_chain)
                   for _, value in node[1])
    return False


def _transform_condition_problem(bundle: _Bundle, condition, tool_parameter: str,
                                 env: frozenset) -> str | None:
    """A transform condition must imply the tool name is not a read-only one.

    Two facts are checked separately for every operand: it is supported —
    within the pure grammar, with the name chain appearing only as one side of
    a positive ``===`` whose other side resolves to a non-read-only string —
    and it constrains — its truth then implies the name is none of
    Read/Glob/Grep. An AND needs at least one constraining operand, an OR
    needs every operand constraining, and every operand is checked before
    either fact is concluded, so a later ``true`` or side effect never hides
    behind an earlier positive test. Unknown negations, ternaries and
    comparisons around the name support nothing.
    """
    supported, constrained = _condition_constraint(bundle, condition, tool_parameter, env)
    if not supported or not constrained:
        return _TRANSFORM_PROBLEM
    return None


def _condition_constraint(bundle: _Bundle, node, tool_parameter: str, env: frozenset):
    name_chain = _name_member(tool_parameter)
    kind = node[0]
    if node == name_chain:
        return False, False  # a bare name is truthy for every tool
    if kind == "bin":
        if node[1] in ("===", "!==", "==", "!="):
            matched = False
            for side, other in ((node[2], node[3]), (node[3], node[2])):
                if side == name_chain:
                    if node[1] != "===":
                        return False, False
                    key = bundle.constant_string(other, env)
                    if key is None or key in _READ_ONLY_TOOL_NAMES:
                        return False, False
                    matched = True
                elif _chain_within(side, name_chain):
                    return False, False
            if not matched:
                if not (_pure_read(node[2]) and _pure_read(node[3])):
                    return False, False
                return True, False
            return True, True
        if _chain_within(node, name_chain) or not (_pure_read(node[2]) and _pure_read(node[3])):
            return False, False
        return True, False
    if kind == "logic":
        parts = [_condition_constraint(bundle, operand, tool_parameter, env)
                 for operand in node[2]]
        if not all(part[0] for part in parts):
            return False, False
        combine = any if node[1] == "&&" else all
        return True, combine(part[1] for part in parts)
    if kind in ("unary", "cond"):
        # A negation or ternary around the name supports nothing; without the
        # name it may still sit in the condition as a pure operand.
        if _chain_within(node, name_chain):
            return False, False
        return (True, False) if _pure_read(node) else (False, False)
    if _chain_within(node, name_chain):
        return False, False
    return (True, False) if _pure_read(node) else (False, False)


# -- the resolver function -------------------------------------------------------

def _resolver_problem(bundle: _Bundle, name: str) -> str | None:
    """The resolver's complete return chain over the config toolAllowlist.

    Only three complete chains qualify: returning the read member directly,
    returning one same-value local, or the recognized alias-map, explore-filter
    and root-child helper chain with every helper verified to preserve the
    read-only names in its own bound environment.
    """
    sites = bundle.sites(name)
    if len(sites) != 1:
        return _RESOLVE_NOT_FUNCTION
    parameters, defaults, statements, _, body_start = sites[0]
    if any(default is not None for default in defaults):
        return _BAD_DEFAULT
    chain = bundle.chain_bindings(body_start)
    if not parameters or statements is None or chain is None:
        return _RES_NO_READ
    config_id = parameters[0]
    env = frozenset(parameters) | chain
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
    env = env | {local}
    if (initializer[0] != "call" or initializer[1][0] != "id" or initializer[2] != (read,)):
        return _RES_NO_READ
    alias_map = initializer[1][1]
    if alias_map in env:
        return _RES_HELPER
    if statements[1][0] != "return" or statements[1][1] is None:
        return _RES_NO_READ
    chain_node = statements[1][1]
    if chain_node[0] != "cond":
        return _RES_NO_READ
    if chain_node[1] != ("bin", "!==", _member(("id", config_id), "toolset"), ("str", "explore")):
        return _RES_NO_READ
    if not _config_list_call(chain_node[2], config_id, local):
        return _RES_NO_READ
    child = chain_node[2][1][1]
    tail = chain_node[3]
    if tail[0] != "cond" or tail[1] != ("id", local):
        return _RES_NO_READ
    filter_branch, default_branch = tail[2], tail[3]
    if not (filter_branch[0] == "call" and filter_branch[1][0] == "id"
            and len(filter_branch[2]) == 2 and filter_branch[2][0] == ("id", config_id)):
        return _RES_NO_READ
    filter_child = filter_branch[1][1]
    if filter_child in env:
        return _RES_HELPER
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
    arrow_env = env | set(arrow[1])
    explore_set = (None if set_base[0] != "id" or set_base[1] in arrow_env
                   else bundle.constant(set_base[1]))
    if explore_set is None or explore_set[0] != "set" or not _READ_ONLY_TOOL_NAMES <= explore_set[1]:
        return _RES_EXPLORE
    if not (default_branch[0] == "call" and default_branch[1][0] == "id"
            and len(default_branch[2]) == 2 and default_branch[2][0] == ("id", config_id)
            and default_branch[2][1][0] == "id"):
        return _RES_NO_READ
    if default_branch[1][1] in env or default_branch[2][1][1] in env:
        return _RES_NO_READ
    default_list = bundle.constant(default_branch[2][1][1])
    if default_list is None or default_list[0] != "list":
        return _RES_NO_READ
    problem = _alias_map_problem(bundle, alias_map, env)
    if problem is not None:
        return problem
    if _child_problem(bundle, child, env) is not None or _child_problem(bundle, filter_child, env) is not None:
        return _RES_HELPER
    return None


def _config_list_call(node, config_id: str, local: str) -> bool:
    return (node[0] == "call" and node[1][0] == "id"
            and tuple(node[2]) == (("id", config_id), ("id", local)))


def _alias_map_problem(bundle: _Bundle, name: str, caller_env=frozenset()) -> str | None:
    """The alias-map helper ``x?.map(v => alias(v))`` over a pure alias chain."""
    if name in caller_env:
        return _RES_HELPER
    sites = bundle.sites(name)
    if len(sites) != 1:
        return _RES_HELPER
    parameters, defaults, statements, _, body_start = sites[0]
    chain = bundle.chain_bindings(body_start)
    if (len(parameters) != 1 or statements is None or chain is None
            or any(default is not None for default in defaults)
            or len(statements) != 1 or statements[0][0] != "return"):
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
    env = frozenset(parameters) | chain | set(arrow[1])
    body = arrow[2]
    if body == ("id", arrow[1][0]):
        return None
    if body[0] == "call" and body[1][0] == "id" and body[2] == (("id", arrow[1][0]),):
        return _alias_chain_problem(bundle, body[1][1], env)
    return _alias_chain_on_node(bundle, body, arrow[1][0], env)


def _alias_chain_problem(bundle: _Bundle, name: str, caller_env=frozenset()) -> str | None:
    if name in caller_env:
        return _RES_HELPER
    sites = bundle.sites(name)
    if len(sites) != 1:
        return _RES_HELPER
    parameters, defaults, statements, _, body_start = sites[0]
    chain = bundle.chain_bindings(body_start)
    if (len(parameters) != 1 or statements is None or chain is None
            or any(default is not None for default in defaults)
            or len(statements) != 1 or statements[0][0] != "return"):
        return _RES_HELPER
    env = frozenset(parameters) | chain
    return _alias_chain_on_node(bundle, statements[0][1], parameters[0], env)


def _alias_chain_on_node(bundle: _Bundle, node, parameter: str, env: frozenset) -> str | None:
    """A chain of ``x === key ? value :`` conditions ending in the bare ``x``.

    No key may name a read-only tool, and every key and value must resolve to
    a string constant its own environment does not shadow, so the three
    read-only names always pass unchanged.
    """
    identity = ("id", parameter)
    while node[0] == "cond":
        test = node[1]
        if not (test[0] == "bin" and test[1] == "===" and test[2] == identity):
            return _RES_HELPER
        key = bundle.constant_string(test[3], env)
        if key is None:
            return _RES_HELPER
        if key in _READ_ONLY_TOOL_NAMES:
            return _RES_ALIAS
        if bundle.constant_string(node[2], env) is None:
            return _RES_HELPER
        node = node[3]
    return None if node == identity else _RES_HELPER


def _child_problem(bundle: _Bundle, name: str, caller_env=frozenset()) -> str | None:
    """The root-child helper: only the child branch appends one constant name."""
    if name in caller_env:
        return _RES_HELPER
    sites = bundle.sites(name)
    if len(sites) != 1:
        return _RES_HELPER
    parameters, defaults, statements, _, body_start = sites[0]
    chain = bundle.chain_bindings(body_start)
    if (len(parameters) != 2 or statements is None or chain is None
            or any(default is not None for default in defaults)
            or len(statements) != 1 or statements[0][0] != "return"):
        return _RES_HELPER
    config, list_param = parameters
    env = frozenset(parameters) | chain
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
    return None if bundle.constant_string(key, env) is not None else _RES_HELPER


# -- the call-site wiring ---------------------------------------------------------

def _wiring_problem(bundle: _Bundle, register: str, resolver: str, context) -> str | None:
    """One registration call wires the verified resolver at the options position.

    A qualifying call carries exactly the declared parameters' worth of
    arguments, its options-position argument is a direct object literal without
    spread or duplicate ``allowedTools`` keys, and that one value is the
    resolver called on a single ``.config`` member read — from a scope chain
    that rebinds neither verified function, ``var`` hoisting, later locals and
    default parameters included. Anything else — a shadowed callee, a third
    argument, a nested or spread decoy, a discarded resolver return — leaves
    the call unqualified.
    """
    parameter_count, options_index = context
    text = bundle.text
    pattern = re.compile(r"(?<![\w$.])" + re.escape(register) + r"\s*\(")
    for match in bundle.regions.code_matches(pattern):
        prefix = text[:match.start()].rstrip()
        if prefix.endswith("function") or prefix.endswith("new"):
            continue
        bindings = bundle.chain_bindings(match.start())
        if bindings is None or register in bindings or resolver in bindings:
            continue  # the call site's own scopes rebind a verified function
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


def allowlist_chain_problem(text: str, *, _bundle=None) -> str | None:
    """The first missing allowlist mechanism in the public CLI bundle text.

    This is the L6-A4 static core behind
    :func:`~buddy.adapters.zcode_read_only.native_contract_problem`: it locates
    the exported registration and resolver functions in the shared outer code
    context, verifies the complete ordered and bound Set-membership
    registration flow, the tool-preserving transform whose conditions imply
    the read-only names never match, the resolver's complete return chain over
    its own bound environment, the unwritten function names, and the
    call-site wiring resolved in its actual scope — fail-closed with a
    specific reason for every unrecognized shape. The caller supplies the
    bounded public bundle text; nothing here executes the bundle or reads any
    credential.
    """
    bundle = _bundle or _Bundle(text)
    if any(bundle.regions.code_matches(re.compile(r"\\[ux]"))):
        return "the public bundle has an unrecognized escaped identifier in code"
    exported = _exported_names(bundle)
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
    register, resolver = registers[0], resolvers[0]
    context, problem = _registration_problem(bundle, register)
    if problem is not None:
        return problem
    problem = _resolver_problem(bundle, resolver)
    if problem is not None:
        return problem
    if ("Set" in bundle.scopes().top_names or bundle.sites("Set")
            or any(bundle.name_writes(name) for name in bundle.proof_names | {"Set"})):
        return _REASSIGNED
    return _wiring_problem(bundle, register, resolver, context)


def _object_members(node):
    """A complete ordinary object with unique, known keys, or no proof."""
    if node[0] != "object" or any(key is None for key, _ in node[1]):
        return None
    members = dict(node[1])
    return members if len(members) == len(node[1]) else None


def _optional_value(node):
    if node[0] == "call" and node[2] == () and node[1][0] == "member" and node[1][2:] == ("optional", False):
        return node[1][1]
    return None


def _expression_at(text, start, limit=4096):
    try:
        end = min(len(text), start + limit)
        parser = _Parser(_token_iter(text, start, end))
        node = parser.parse_ternary()
        following = parser.peek()
        if following is None and end < len(text):
            return None
        if following is not None and not (following[0] == "punct" and following[1] in _RHS_TERMININATORS):
            return None
        return node
    except _ParseError:
        return None


def metadata_contract_problem(text: str, schema_fields, tool_names, *, _bundle=None) -> str | None:
    """Verify real strict schema members, enum literals and tool metadata tokens."""
    bundle = _bundle or _Bundle(text)
    regions = bundle.regions
    schema = None
    builder = None
    for match in regions.code_matches(re.compile(r"(?<![\w$.])([\w$]+)\.object\s*\(")):
        node = _expression_at(text, match.start(), 2048)
        if (node is None or node[0] != "call" or node[2] != ()
                or node[1][0] != "member" or node[1][2:] != ("strict", False)):
            continue
        object_call = node[1][1]
        candidate_builder = match.group(1)
        if (object_call[0] != "call" or object_call[1] != _member(("id", candidate_builder), "object")
                or len(object_call[2]) != 1):
            continue
        members = _object_members(object_call[2][0])
        if members is None or not set(schema_fields) <= set(members):
            continue
        booleans = ("titleGenerationEnabled", "offPeakToolEnabled", "dynamicWorkflowEnabled")
        arrays = ("mcpServers", "toolAllowlist", "toolDenylist")
        if any(_optional_value(members[key]) != ("call", _member(("id", candidate_builder), "boolean"), ()) for key in booleans):
            continue
        array_values = [_optional_value(members[key]) for key in arrays]
        if any(value is None or value[0] != "call"
               or value[1] != _member(("id", candidate_builder), "array")
               or len(value[2]) != 1 or value[2][0][0] != "id" for value in array_values):
            continue
        bindings = bundle.chain_bindings(match.start())
        if bindings is None or candidate_builder in bindings:
            continue
        schema, builder, schema_bindings = members, candidate_builder, bindings
        break
    if schema is None:
        return "the public bundle has no strict session/create schema carrying the restriction fields"
    mode = _optional_value(schema["mode"])
    if mode is None or mode[0] != "id":
        return "the session/create schema binds mode to no named enum schema"
    if mode[1] in schema_bindings:
        return "the session/create mode enum is shadowed in its schema scope"
    found_enum = False
    for position, kind in bundle.name_writes(mode[1]):
        if kind != "assign":
            return "the session/create mode enum has an unrecognized write"
        bindings = bundle.chain_bindings(position)
        if bindings is None or builder in bindings:
            return "the session/create mode enum builder is shadowed"
        start = bundle._skip_write_ws(position + len(mode[1]))
        node = _expression_at(text, bundle._skip_write_ws(start + 1))
        if (node is None or node[0] != "call" or node[1] != _member(("id", builder), "enum")
                or len(node[2]) != 1 or node[2][0][0] != "array"
                or any(value[0] != "str" for value in node[2][0][1])):
            return "the session/create mode is not bound to an enum in the public bundle"
        if "plan" not in {value[1] for value in node[2][0][1]}:
            return "the session/create mode enum does not offer plan"
        found_enum = True
    if not found_enum:
        return "the session/create mode is not bound to an enum in the public bundle"
    tools = {name: [] for name in tool_names}
    for match in regions.code_matches(re.compile(r"(?<![\w$.])metadata\s*:\s*")):
        node = _expression_at(text, match.end(), 1024)
        members = _object_members(node) if node is not None else None
        if members is None:
            continue
        name = members.get("name")
        if name is not None and name[0] == "str" and name[1] in tools:
            tools[name[1]].append(members.get("readOnly") in (("unary", "!", ("num", "0")), ("lit", "true")))
    for name, flags in tools.items():
        if not flags:
            return f"the {name} built-in tool is not registered in the public bundle"
        if not all(flags):
            return f"the {name} built-in tool is not registered as read-only in the public bundle"
    return None


def read_only_contract_problem(text: str, schema_fields, tool_names) -> str | None:
    """One shared lexical/binding context for both qualification mechanisms."""
    bundle = _Bundle(text)
    return (metadata_contract_problem(text, schema_fields, tool_names, _bundle=bundle)
            or allowlist_chain_problem(text, _bundle=bundle))
