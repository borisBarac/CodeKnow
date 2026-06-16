"""Tests for citation extraction from agent final answers."""

from evalkit.citations import extract_citations


def test_extracts_single_path_colon_line():
    text = "Token refresh lives in src/auth/oauth.py:144 inside the retry loop."
    assert extract_citations(text) == ["src/auth/oauth.py:144"]


def test_preserves_order_and_deduplicates():
    text = (
        "See src/a.py:10 and src/b.py:20, then src/a.py:10 again, "
        "and finally src/c.py:30."
    )
    assert extract_citations(text) == [
        "src/a.py:10",
        "src/b.py:20",
        "src/c.py:30",
    ]


def test_ignores_plain_prose_colons():
    text = "Note: this is important. Time: 12:30. See also foo:bar."
    assert extract_citations(text) == []


def test_strips_markdown_backticks():
    text = "Implemented in `src/auth/oauth.py:144` per the flow."
    assert extract_citations(text) == ["src/auth/oauth.py:144"]


def test_takes_start_line_from_a_range():
    text = "Logic spans src/auth/oauth.py:144-150 within the retry block."
    assert extract_citations(text) == ["src/auth/oauth.py:144"]


def test_extracts_comma_lines_form_with_endash():
    # The phrasing that broke the fastify grep run: "lib/route.js, lines 455-457"
    # (en dash in the source text). The string literal keeps the en dash on
    # purpose; extraction must still recognise it.
    text = "HEAD handling is in lib/route.js, lines 455–457 of the dispatch."
    assert extract_citations(text) == ["lib/route.js:455"]


def test_extracts_paren_line_form():
    text = "Reply cleanup lives in lib/reply.js (line 623) near the end hook."
    assert extract_citations(text) == ["lib/reply.js:623"]


def test_extracts_bare_lines_word_form():
    text = "See lib/req-id-gen.js lines 14 and 28 for the id factory."
    assert extract_citations(text)[0] == "lib/req-id-gen.js:14"


def test_continuation_range_captures_only_first():
    # "reply.js, lines 623-629 and 669-675": only the file's first range cites.
    text = "lib/reply.js, lines 623–629 and 669–675 both matter."
    assert extract_citations(text) == ["lib/reply.js:623"]


def test_no_false_positive_on_count_phrases():
    # "foo.js 30 times" must NOT parse (bare space, no "lines" word).
    text = "We called fetch.js 30 times during the load test."
    assert extract_citations(text) == []
