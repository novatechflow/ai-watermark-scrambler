"""Valid HTML attribute syntax must not bypass provenance removal."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/ai-watermark-scrambler/scripts"))
from container_meta import clean_html, inspect_html


@pytest.mark.parametrize("tag", [
    '<meta name=generator content=ChatGPT>',
    '<META NAME=generator CONTENT="ChatGPT">',
    '<meta content=Chat&#71;PT name=generator>',
    '<meta name=generator content="ChatGPT > test">',
    '<meta property=contentCredentials content=https://example.test/manifest>',
])
def test_provenance_meta_with_valid_attribute_syntax(tag):
    html = tag + '<p title="a > b">Hello</p>'
    assert inspect_html(html)[1]
    cleaned, _ = clean_html(html)
    assert cleaned == '<p title="a > b">Hello</p>'
    assert not inspect_html(cleaned)[1]


def test_unquoted_and_boolean_data_ai_attributes():
    html = '<div data-ai-model=gpt data-ai-generated class=article>Hello</div>'
    assert inspect_html(html)[1]
    cleaned, _ = clean_html(html)
    assert cleaned == '<div class=article>Hello</div>'
    assert not inspect_html(cleaned)[1]


def test_attribute_like_text_is_preserved():
    html = (
        '<meta name=description content="Notes on Claude">'
        '<meta name=generator content=Blogger>'
        '<p title=" data-ai-model=gpt">Hello</p>'
        '<script>const example = "<meta name=generator content=ChatGPT>";</script>'
        '<!-- <meta name=generator content=ChatGPT> -->'
    )
    assert not inspect_html(html)[1]
    assert clean_html(html)[0] == html
