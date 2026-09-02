"""End-to-end checks against the synthetic export and map in ``tests/fixtures``.

The fixtures are invented data shaped like a real Fantasia Archive export and
a real Azgaar ``.map``, so the repository carries no one's worldbuilding.
These run in CI on Windows before the executable is built.
"""

from __future__ import annotations

import json
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

from elaris_import import azgaar
from elaris_import.bbcode import build_all, dangling_mentions
from elaris_import.fa_parse import parse_export, split_qualifier, strip_html
from elaris_import.mapping import TEMPLATE_TYPES, resolve_template
from elaris_import.pipeline import PipelineError, convert, find_export_root

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPORT = FIXTURES / "export"  # the category folders sit one level down
MAP = FIXTURES / "sample.map"

TAGS = ("h2", "h3", "ul", "li", "b", "i")


@pytest.fixture(scope="module")
def docs():
    return parse_export(find_export_root(EXPORT))


@pytest.fixture(scope="module")
def articles(docs):
    return build_all(docs)


@pytest.fixture(scope="module")
def map_data():
    return azgaar.load(MAP)


# -- parser -----------------------------------------------------------------

def test_every_document_is_parsed(docs):
    assert len(docs) == len(list(EXPORT.rglob("*.md"))) == 8


def test_every_document_has_title_and_type(docs):
    for d in docs:
        assert d.title.strip(), d.source
        assert d.doc_type, d.source
        assert len(d.uuid) == 36, d.source


def test_no_html_survives(docs):
    for d in docs:
        for f in d.fields:
            for v in f.values:
                assert "<" not in v and ">" not in v, (d.title, f.name, v)


def test_paragraph_breaks_are_kept(docs):
    testland = next(d for d in docs if d.title == "Testland")
    assert testland.values("Description & History") == [
        "A country that exists only in the test suite.\nSecond paragraph."
    ]


def test_strip_html_keeps_paragraph_breaks():
    raw = '<font face="x">One.</font><div><font>Two.</font></div><b>Three</b>'
    assert strip_html(raw) == "One.\nTwo.\nThree"


def test_qualifier_split():
    assert split_qualifier("Gold (1.46)") == ("Gold", "1.46")
    assert split_qualifier("Gold") == ("Gold", "")
    assert split_qualifier("Weird (a) (b)") == ("Weird (a)", "b")


def test_export_root_is_found_one_level_down():
    assert find_export_root(EXPORT).name == "Mock World - Export"


def test_export_root_rejects_bad_input(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("nope")
    with pytest.raises(PipelineError):
        find_export_root(f)
    with pytest.raises(PipelineError):
        find_export_root(tmp_path)


# -- mapping + rendering ----------------------------------------------------

def test_templates_are_valid(articles):
    for a in articles:
        assert a.template in TEMPLATE_TYPES, a.title


def test_template_distribution(articles):
    assert Counter(a.template for a in articles) == {
        "location": 1, "settlement": 1, "person": 1, "item": 2,
        "language": 1, "species": 1, "spell": 1,
    }


def test_unknown_document_type_falls_back_to_article(docs):
    d = docs[0]
    original = d.doc_type
    d.doc_type = "Something Fantasia Archive adds next year"
    try:
        assert resolve_template(d) == "article"
    finally:
        d.doc_type = original


def test_bbcode_tags_balance(articles):
    for a in articles:
        text = a.content + a.sidebar
        for tag in TAGS:
            assert text.count(f"[{tag}]") == text.count(f"[/{tag}]"), (a.title, tag)


def test_mentions_and_qualifiers(articles):
    coin = next(a for a in articles if a.title == "Mockcoin")
    assert "@[Gold] (2)" in coin.content
    assert "@[Testland]" in coin.content
    for a in articles:
        for m in re.findall(r"@\[([^\]]*)\]", a.content):
            assert m.strip() and "[" not in m, (a.title, m)


def test_stub_article_gets_placeholder_content(articles):
    wrench = next(a for a in articles if a.title == "Wrench")
    assert wrench.content == "[i]Imported from Fantasia Archive.[/i]"


def test_location_template_fields(articles):
    testland = next(a for a in articles if a.title == "Testland")
    assert testland.template_fields == {
        "population": "12.345", "areaSize": "999 km²",
        "locationTemplateType": "Country",
        "history": "A country that exists only in the test suite.\nSecond paragraph.",
    }
    city = next(a for a in articles if a.title == "Mockshire")
    assert city.template_fields["alternativename"] == "The Fixture City"
    # Fields carried natively by the template are not repeated in the sidebar.
    assert "Population" not in testland.sidebar
    for a in articles:
        if a.template not in {"location", "settlement", "landmark"}:
            assert not a.template_fields, a.title


def test_dangling_links_are_reported(articles):
    missing = dangling_mentions(articles)
    assert missing == {"Mockcoin": ["Gold"], "Testland": ["Nowhere Isle"]}


def test_excerpt_is_first_line(articles):
    testland = next(a for a in articles if a.title == "Testland")
    assert testland.excerpt == "A country that exists only in the test suite."


# -- map --------------------------------------------------------------------

def test_map_collections_identified(map_data):
    for kind in ("burgs", "states", "cultures", "religions", "provinces", "rivers", "features"):
        assert map_data.entities.get(kind), kind
    assert (map_data.width, map_data.height) == (400, 200)
    assert map_data.distance_unit == "km" and map_data.distance_scale == 5.0


def test_burgs_skip_tombstones_and_carry_positions(map_data):
    burgs = azgaar.burg_markers(map_data)
    assert [b["name"] for b in burgs] == ["Mockshire", "Stubton"]
    first = burgs[0]
    assert (first["x"], first["y"]) == (100.5, 80.25)
    assert (first["fx"], first["fy"]) == (0.25125, 0.40125)
    assert first["state"] == "Testland" and first["culture"] == "Mockfolk"
    assert first["is_capital"] and not first["is_port"]
    assert burgs[1]["is_port"]


def test_states_exclude_neutrals_and_tombstones(map_data):
    rows = azgaar.state_rows(map_data)
    assert [r["name"] for r in rows] == ["Testland"]
    assert rows[0]["full_name"] == "Republic of Testland"


def test_all_removed_religions_still_classified(map_data):
    assert len(map_data.entities["religions"]) == 2
    assert [r["name"] for r in map_data.named("religions")] == ["No religion"]


def test_cleaned_svg_is_valid_xml_and_masked(map_data):
    svg = azgaar.clean_svg(map_data.svg)
    ET.fromstring(svg)
    assert re.search(r'<g id="landmass"[^>]*mask="url\(#land\)"', svg)
    assert "./images/" not in svg
    assert re.search(r'<g id="sea_island"[^>]*fill="none"', svg)
    # Self-closing groups must stay self-closing after attribute injection.
    assert re.search(r'<g id="lake_island"[^>]*fill="none"/>', svg)
    assert "/ fill=" not in svg


# -- pipeline ---------------------------------------------------------------

def test_convert_from_folder(tmp_path):
    result = convert(EXPORT, MAP, tmp_path, render_png=False, log=lambda _: None)
    assert result.article_count == 8
    assert result.burg_count == 2 and result.state_count == 1
    assert (tmp_path / "map" / "elaris.svg").exists()
    assert len(list((tmp_path / "bbcode").rglob("*.txt"))) == 8

    payloads = json.loads((tmp_path / "articles.json").read_text(encoding="utf-8"))
    assert {p["_faUuid"] for p in payloads} == {
        re.search(r"([0-9a-f-]{36})\.md$", str(f)).group(1) for f in EXPORT.rglob("*.md")
    }
    burgs = (tmp_path / "map" / "burgs.csv").read_text(encoding="utf-8").splitlines()
    assert burgs[0].startswith("name,x,y,fx,fy,state")
    assert len(burgs) == 3


def test_convert_from_zip(tmp_path):
    archive = shutil.make_archive(str(tmp_path / "export"), "zip", EXPORT)
    result = convert(Path(archive), None, tmp_path / "out", render_png=False, log=lambda _: None)
    assert result.article_count == 8


def test_convert_needs_something(tmp_path):
    with pytest.raises(PipelineError):
        convert(tmp_path / "missing", None, tmp_path / "out", render_png=False, log=lambda _: None)
