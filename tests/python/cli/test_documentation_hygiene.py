"""Mechanical documentation rules, with the glossary as the terminology source."""
from __future__ import annotations

from html.parser import HTMLParser
import os
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.parser_inline import RuleFuncInlineType
from markdown_it.rules_inline import html_inline, image, link
from markdown_it.rules_inline.state_inline import StateInline

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


class _LinkParser(MarkdownIt):
    def normalizeLink(self, url: str) -> str:
        # Keep the decoded destination for diagnostics. This checker emits no HTML.
        return url


def _record_inline_line(rule: RuleFuncInlineType, token_type: str) -> RuleFuncInlineType:
    def parse(state: StateInline, silent: bool) -> bool:
        start, first = state.pos, len(state.tokens)
        matched = rule(state, silent)
        if matched and not silent:
            token = next(token for token in state.tokens[first:] if token.type == token_type)
            token.meta["line_offset"] = state.src.count("\n", 0, start)
        return matched

    return parse


_MARKDOWN = _LinkParser("commonmark")
# Block tokens have source maps. Mark inline tokens while their offsets are available.
_MARKDOWN.inline.ruler.at("link", _record_inline_line(link, "link_open"))
_MARKDOWN.inline.ruler.at("image", _record_inline_line(image, "image"))
_MARKDOWN.inline.ruler.at("html_inline", _record_inline_line(html_inline, "html_inline"))


class _HTMLLinks(HTMLParser):
    def __init__(self, start_line: int, links: list[tuple[int, str]]):
        super().__init__()
        self.start_line = start_line
        self.links = links

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value is not None and (tag, key) in (("a", "href"), ("img", "src")):
                self.links.append((self.start_line + self.getpos()[0] - 1, value))


def markdown_links(text: str) -> list[tuple[int, str]]:
    links = []
    for block in _MARKDOWN.parse(text):
        if block.type == "html_block":
            _HTMLLinks(block.map[0] + 1, links).feed(block.content)
        elif block.type == "inline":
            start_line = block.map[0] + 1
            html = _HTMLLinks(start_line, links)
            html_line = 0
            for token in block.children:
                if token.type == "link_open" and "line_offset" in token.meta:
                    links.append((start_line + token.meta["line_offset"], token.attrs["href"]))
                elif token.type == "image":
                    links.append((start_line + token.meta["line_offset"], token.attrs["src"]))
                elif token.type == "html_inline":
                    line_offset = token.meta["line_offset"]
                    # Preserve source line breaks from the Markdown between HTML tokens.
                    html.feed("\n" * (line_offset - html_line) + token.content)
                    html_line = line_offset + token.content.count("\n")
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
        text = '[full][Doc]\n[doc][]\n[doc]\n[unused]\n\n[doc]: <../target (one).md> "title"\n'
        text += '<a href="missing.md">bad</a>\n<img src="missing.png">\n'
        self.assertEqual(markdown_links(text), [(1, "../target (one).md"), (2, "../target (one).md"),
                                               (3, "../target (one).md"), (7, "missing.md"), (8, "missing.png")])

    def test_links_inside_html_blocks_keep_their_source_lines(self):
        text = '# Heading\n\n<div>\n<!-- <img src="ignored.png"> -->\n<a\n href="target&amp;amp;.md">a</a>\n</div>'
        self.assertEqual(markdown_links(text), [(5, "target&amp;.md")])

    def test_inline_script_does_not_report_html_in_its_raw_text(self):
        text = """Example <script>const s = "<img src='missing.png'>";</script>."""
        self.assertEqual(reference_violations(self.root, {"docs/note.md": text}), [])

    def test_multiline_inline_html_links_keep_their_source_lines(self):
        text = """# Heading

Example <a
 href="missing.md">link</a>
and <script>const s = "<img
 src='ignored.png'>";</script>
then <img
 src="missing.png">.
"""
        self.assertEqual(markdown_links(text), [(3, "missing.md"), (7, "missing.png")])

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
