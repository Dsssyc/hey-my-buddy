"""Mechanical documentation rules, with the glossary as the terminology source."""
from __future__ import annotations

from bisect import bisect_right
from html import unescape
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

from cli.test_repository_hygiene import ROOT, _git


def tracked_texts(root: Path, names: list[str]) -> dict[str, str]:
    texts = {}
    for name in names:
        data = (root / name).read_bytes()
        if b"\0" in data:
            continue
        try:
            texts[name] = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return texts


def glossary_rules(context: str) -> tuple[dict[str, list[str]], list[str]]:
    avoided: dict[str, list[str]] = {}
    names = []
    entry = ""
    for line in context.splitlines():
        heading = re.fullmatch(r"\*\*([^*]+)\*\*:\s*", line)
        if heading:
            entry = heading[1]
            if " / " in entry:
                names.extend(entry.split(" / "))
        elif line.startswith("_Avoid_:"):
            for word in re.findall(r"`([^`]+)`", line):
                avoided.setdefault(word, []).append(entry)
    return avoided, names


def terminology_violations(texts: dict[str, str]) -> list[str]:
    avoided, names = glossary_rules(texts["CONTEXT.md"])
    violations = []
    for name, text in texts.items():
        if (name == "CONTEXT.md" or name.startswith("src/hey_my_buddy/console/assets/")
                or re.search(r"(?:^|[.-])lock(?:\.[^.]+)?$", Path(name).name)):
            continue
        for word, entries in avoided.items():
            if word not in text:
                continue
            allowed = [term for term in names if word in term]
            quoted = re.compile(r"(?<!`)(`+)" + re.escape(word) + r"\1(?!`)|" + "|".join(
                re.escape(left + word + right) for left, right in
                (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"))))
            for number, line in enumerate(text.splitlines(), 1):
                protected = [match.span() for term in allowed for match in re.finditer(re.escape(term), line)]
                protected.extend(match.span() for match in quoted.finditer(line))
                for match in re.finditer(re.escape(word), line):
                    start, end = match.span()
                    if any(left <= start and end <= right for left, right in protected):
                        continue
                    violations.append(f"{name}:{number}: avoid `{word}` (CONTEXT.md: {'; '.join(entries)})")
    return violations


def _blank(text: str) -> str:
    return re.sub(r"[^\n]+", lambda match: " " * len(match[0]), text)


def _escaped(text: str, start: int) -> bool:
    cursor = start
    while cursor and text[cursor - 1] == "\\":
        cursor -= 1
    return (start - cursor) % 2 == 1


def prose(text: str) -> str:
    """Mask Markdown code without moving any source locations."""
    lines = []
    fence = None
    list_indent = 0
    previous_blank = True
    indented = False
    for line in text.splitlines(keepends=True):
        indent = len(line) - len(line.lstrip(" "))
        item = re.match(r" *(?:[-+*]|\d+[.)]) +", line)
        if fence is None and item and indent < list_indent + 4:
            list_indent = item.end()
        elif fence is None and line.strip() and indent == 0:
            list_indent = 0
        marker = re.match(r"(?: {0,3}>[ \t]?)* {0,3}(`{3,}|~{3,})(.*)", line)
        if fence is not None:
            lines.append(_blank(line))
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif marker:
            fence = marker[1]
            lines.append(_blank(line))
        elif (indent >= list_indent + 4 or line.startswith("\t")) and (previous_blank or indented):
            lines.append(_blank(line))
            indented = True
        else:
            lines.append(line)
            if line.strip():
                indented = False
        previous_blank = not line.strip() or marker is not None
    text = "".join(lines)
    ticks = [match for match in re.finditer(r"`+", text) if not _escaped(text, match.start())]
    cursor = 0
    chunks = []
    i = 0
    while i < len(ticks):
        opening = ticks[i]
        closing = next((j for j in range(i + 1, len(ticks)) if ticks[j][0] == opening[0]), None)
        if closing is None:
            i += 1
            continue
        end = ticks[closing].end()
        chunks.extend((text[cursor:opening.start()], _blank(text[opening.start():end])))
        cursor = end
        i = closing + 1
    chunks.append(text[cursor:])
    return "".join(chunks)


def _destination(text: str, start: int) -> tuple[str, int] | None:
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text):
        return None
    if text[start] == "<":
        end = text.find(">", start + 1)
        if end == -1 or "\n" in text[start:end]:
            return None
        return text[start + 1:end], end + 1
    end = start
    depth = 0
    while end < len(text):
        char = text[end]
        if char == "\\" and end + 1 < len(text):
            end += 2
            continue
        if char.isspace() or (char == ")" and depth == 0):
            break
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        end += 1
    return text[start:end], end


def markdown_links(text: str) -> list[tuple[int, str]]:
    text = prose(text)
    newlines = [match.start() for match in re.finditer("\n", text)]
    definitions = {}
    for match in re.finditer(r"^ {0,3}\[([^\]\n]+)\]:[ \t]*", text, re.M):
        destination = _destination(text, match.end())
        if destination:
            definitions[" ".join(match[1].split()).casefold()] = destination[0]
    links = []
    cursor = 0
    for opening in re.finditer(r"\[", text):
        start = opening.start()
        if start < cursor or _escaped(text, start):
            continue
        end = start + 1
        depth = 1
        while end < len(text) and depth:
            if text[end] == "\\":
                end += 2
                continue
            if text[end] == "[":
                depth += 1
            elif text[end] == "]":
                depth -= 1
            end += 1
        if depth:
            continue
        label = text[start + 1:end - 1]
        target = None
        if text[end:end + 1] == "(":
            destination = _destination(text, end + 1)
            if destination and re.match(r'''\s*(?:(?:"[^"\n]*"|'[^'\n]*'|\([^()\n]*\))\s*)?\)''', text[destination[1]:]):
                target = destination[0]
        elif text[end:end + 1] != ":":
            reference = re.match(r"[ \t]*\[([^\]\n]*)\]", text[end:])
            if reference:
                label = reference[1] or label
                end += reference.end()
            target = definitions.get(" ".join(label.split()).casefold())
        if target is not None:
            links.append((bisect_right(newlines, start) + 1, target))
            cursor = end

    class HTMLLinks(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if value is not None and (tag, key) in (("a", "href"), ("img", "src")):
                    links.append((self.getpos()[0], value))

    HTMLLinks().feed(text)
    return links


def reference_violations(root: Path, texts: dict[str, str]) -> list[str]:
    root = root.absolute()
    decisions = {Path(name).name[:3] for name in texts if re.fullmatch(r"docs/decisions/\d{3}-.+\.md", name)}
    violations = []
    for name, text in texts.items():
        for match in re.finditer(r"ADR-(\d{3})(?!\d)", text):
            if match[1] not in decisions:
                number = text.count("\n", 0, match.start()) + 1
                violations.append(f"{name}:{number}: {match[0]} has no docs/decisions/{match[1]}-*.md")
        if Path(name).suffix.lower() not in (".md", ".markdown"):
            continue
        for number, target in markdown_links(text):
            target = unescape(re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])", r"\1", target))
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path or url.path.startswith("/"):
                continue
            path = Path(os.path.abspath((root / name).parent / unquote(url.path)))
            if path.is_relative_to(root) and not path.exists():
                violations.append(f"{name}:{number}: missing relative link target {target}")
    return violations


class TerminologyTests(unittest.TestCase):
    CONTEXT = "**完整短语 / full phrase**:\n_Avoid_: `短语`; human judgment\n\n**另一项 / other**:\n_Avoid_: `短语`\n"

    def test_rules_come_only_from_backticks_and_keep_both_entries(self):
        rules, names = glossary_rules(self.CONTEXT)
        self.assertEqual(rules, {"短语": ["完整短语 / full phrase", "另一项 / other"]})
        self.assertIn("完整短语", names)

    def test_bare_use_reports_file_line_word_and_glossary_entries(self):
        self.assertEqual(terminology_violations({"CONTEXT.md": self.CONTEXT, "note.txt": "正常。\n请用短语。\n"}),
                         ["note.txt:2: avoid `短语` (CONTEXT.md: 完整短语 / full phrase; 另一项 / other)"])

    def test_term_names_and_exact_quoted_mentions_are_allowed(self):
        allowed = "完整短语，`短语`、``短语``、“短语”、‘短语’、「短语」、『短语』、\"短语\"、'短语'。"
        self.assertEqual(terminology_violations({"CONTEXT.md": self.CONTEXT, "note.md": allowed}), [])

    def test_quoting_a_sentence_does_not_exempt_a_use(self):
        self.assertEqual(len(terminology_violations({"CONTEXT.md": self.CONTEXT, "note.md": "“请用短语” `一个短语`"})), 2)

    def test_mismatched_backticks_do_not_exempt_a_use(self):
        self.assertEqual(len(terminology_violations({"CONTEXT.md": self.CONTEXT, "note.md": "``短语`"})), 1)

    def test_only_the_documented_file_exclusions_apply(self):
        texts = {"CONTEXT.md": self.CONTEXT, "uv.lock": "短语", "apps/console/package-lock.json": "短语",
                 "src/hey_my_buddy/console/assets/index.js": "短语", "src/locking.py": "短语",
                 "docs/research/note.md": "短语"}
        self.assertEqual([v.split(":")[0] for v in terminology_violations(texts)],
                         ["src/locking.py", "docs/research/note.md"])

    def test_binary_files_are_not_decoded_as_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "text.md").write_text("example", encoding="utf-8")
            (root / "nul.bin").write_bytes(b"x" * 9000 + b"\0text")
            (root / "invalid.bin").write_bytes(b"\xff\xfe")
            self.assertEqual(tracked_texts(root, ["text.md", "nul.bin", "invalid.bin"]), {"text.md": "example"})


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "docs").mkdir()
        (self.root / "target (one).md").write_text("# target", encoding="utf-8")
        (self.root / "target(one).md").write_text("# target", encoding="utf-8")

    def test_missing_targets_have_source_locations_and_fragments_are_ignored(self):
        text = "# title\n[bad](missing.md#section)\n![image](missing.png)\n"
        self.assertEqual(reference_violations(self.root, {"docs/note.md": text}),
                         ["docs/note.md:2: missing relative link target missing.md#section",
                          "docs/note.md:3: missing relative link target missing.png"])

    def test_valid_relative_encoded_angle_and_parenthesized_targets(self):
        text = '[a](../target%20%28one%29.md#missing) [b](<../target (one).md> "title") [c](../target\\(one\\).md)'
        text += '\n[d](<../target (one).md>) [dir](../docs/) [self](#missing) [empty]()'
        self.assertEqual(reference_violations(self.root, {"docs/note.md": text}), [])
        self.assertEqual(markdown_links("[nested](one(two(three)).md)"), [(1, "one(two(three)).md")])

    def test_external_and_absolute_locations_are_not_relative_links(self):
        text = '[a](https://example.invalid/x) [b](mailto:a@example.invalid) [c](//example.invalid/x) [d](/other/file.md)'
        self.assertEqual(reference_violations(self.root, {"note.md": text}), [])
        self.assertEqual(reference_violations(self.root, {"note.md": "[outside](../elsewhere.md)"}), [])

    def test_fenced_indented_and_inline_code_is_ignored(self):
        text = '```md\n[bad](missing.md)\n````\n~~~\n[bad](missing.md)\n~~~\n    [bad](missing.md)\n'
        text += '`[bad](missing.md)` ``code ` [bad](missing.md)``\n`multi\n[bad](missing.md)`\n'
        text += '[real](missing.md)\n'
        self.assertEqual(markdown_links(text), [(11, "missing.md")])

    def test_unclosed_inline_backticks_do_not_hide_links(self):
        self.assertEqual(markdown_links("` unfinished [real](missing.md)"), [(1, "missing.md")])

    def test_escaped_backticks_do_not_hide_links(self):
        self.assertEqual(markdown_links(r"\` [real](missing.md) \`"), [(1, "missing.md")])

    def test_nested_labels_and_list_continuations_are_still_links(self):
        text = '- item\n    [see [nested] label](missing.md)\n\n    [continued](missing.md)\n'
        self.assertEqual(markdown_links(text), [(2, "missing.md"), (4, "missing.md")])

    def test_code_blocks_can_contain_list_items_or_be_inside_a_quote(self):
        text = '    - [code](missing.md)\n\n> ```md\n> [code](missing.md)\n> ````\n[real](missing.md)'
        self.assertEqual(markdown_links(text), [(6, "missing.md")])

    def test_escaped_opening_brackets_are_not_links(self):
        self.assertEqual(markdown_links(r"\[literal](missing.md) \\[real](missing.md)"), [(1, "missing.md")])

    def test_reference_links_and_html_links(self):
        text = '[full][Doc]\n[doc][]\n[doc]\n[unused]\n[doc]: <../target (one).md> "title"\n'
        text += '<a href="missing.md">bad</a>\n<img src="missing.png">\n'
        self.assertEqual(markdown_links(text), [(1, "../target (one).md"), (2, "../target (one).md"),
                                               (3, "../target (one).md"), (6, "missing.md"), (7, "missing.png")])

    def test_adr_mentions_in_any_text_include_code_and_need_a_decision_file(self):
        missing = "ADR-" + "999"
        texts = {"example.py": f"ADR-031 decision 10\n中文{missing}第 1 条\n",
                 "docs/decisions/031-example.md": "# decision"}
        self.assertEqual(reference_violations(self.root, texts),
                         [f"example.py:2: {missing} has no docs/decisions/999-*.md"])


class TrackedDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        listed = _git("ls-files", "-z")
        if listed is None:
            raise unittest.SkipTest("not a Git checkout")
        cls.texts = tracked_texts(ROOT, [name for name in listed.split("\0") if name])

    def test_tracked_text_uses_glossary_terms(self):
        violations = terminology_violations(self.texts)
        self.assertEqual(violations, [], "\n".join(violations))

    def test_repository_references_exist(self):
        violations = reference_violations(ROOT, self.texts)
        self.assertEqual(violations, [], "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
