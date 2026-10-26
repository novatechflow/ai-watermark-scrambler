"""Normalization must preserve literal text and well-formed document XML."""

import io
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/ai-watermark-scrambler/scripts"))
from container_meta import clean_docx, clean_odt


@pytest.mark.parametrize("cleaner,part", [(clean_docx, "word/document.xml"), (clean_odt, "content.xml")])
@pytest.mark.parametrize("text,expected", [
    ("price ＜ ５ ＆ ６", "price < 5 & 6"),
    ("Ａ &amp; B &lt; C &#xFF06; D", "A & B < C & D"),
    ("A&#x200B; &amp; B", "A & B"),
    ("Ａ &#x80; &#128;", "A \u0080 \u0080"),
    ("Ａ &#13; &#x1F600;", "A \r 😀"),
    ("Ａ \r B", "A \n B"),
    ("Ａ \r\n B", "A \n B"),
])
def test_normalized_xml_is_valid_and_preserves_text(cleaner, part, text, expected):
    xml = f'<doc xmlns="urn:test"><text>{text}</text></doc>'
    ET.fromstring(xml)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(part, xml)

    cleaned, _ = cleaner(data.getvalue(), nfkc=True)

    with zipfile.ZipFile(io.BytesIO(cleaned)) as archive:
        document = ET.fromstring(archive.read(part))
    assert document.find("{urn:test}text").text == expected
