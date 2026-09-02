"""End-to-end checks against the real export and map in ``data/``.

These run in CI on Windows before the executable is built, so anything that
would break the packaged app breaks the build instead.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

from elaris_import import azgaar
from elaris_import.bbcode import build_all, dangling_mentions
from elaris_import.fa_parse import parse_export, split_qualifier, strip_html
from elaris_import.mapping import TEMPLATE_TYPES, resolve_template
from elaris_import.pipeline import PipelineError, convert, find_export_root

ROOT = Path(__file__).resolve().parent.parent
EXPORT = ROOT / "data" / "Elaris - Export"
MAP = ROOT / "data" / "Lenyhaha.map"

# Every BBCode tag the renderer emits, so balance can be checked.
TAGS = ("h2", "h3", "ul", "li", "b", "i")


@pytest.fixture(scope="module")
def docs():
    return parse_export(EXPORT)


@pytest.fixture(scope="module")
def articles(docs):
    return build_all(docs)


@pytest.fixture(scope="module")
def map_data():
    return azgaar.load(MAP)


# -- parser -----------------------------------------------------------------

def test_every_document_is_parsed(docs):
    files = list(EXPORT.rglob("*.md"))
    assert len(docs) == len(files) == 126


def test_every_document_has_title_and_type(docs):
    for d in docs:
        assert d.title.strip(), d.source
        assert d.doc_type, d.source
        assert d.uuid and len(d.uuid) == 36, d.source


def test_no_html_survives(docs):
    for d in docs:
        for f in d.fields:
            for v in f.values:
                assert "<" not in v and ">" not in v, (d.title, f.name, v)


def test_strip_html_keeps_paragraph_breaks():
    raw = '<font face="x">One.</font><div><font>Two.</font></div><b>Three</b>'
    assert strip_html(raw) == "One.\nTwo.\nThree"


def test_qualifier_split():
    assert split_qualifier("Gold (1.46)") == ("Gold", "1.46")
    assert split_qualifier("Gold") == ("Gold", "")
    assert split_qualifier("Weird (a) (b)") == ("Weird (a)", "b")


def test_export_root_rejects_non_folder(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("nope")
    with pytest.raises(PipelineError):
        find_export_root(f)
    with pytest.raises(PipelineError):
        find_export_root(tmp_path)  # empty dir, no categories


# -- mapping + rendering ----------------------------------------------------

def test_templates_are_valid(articles):
    for a in articles:
        assert a.template in TEMPLATE_TYPES, a.title


def test_template_distribution(articles):
    counts = Counter(a.template for a in articles)
    assert counts == {
        "location": 28, "settlement": 28, "item": 24, "profession": 10,
        "species": 8, "organization": 7, "language": 5, "landmark": 5,
        "article": 4, "person": 3, "spell": 3, "material": 1,
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
        for tag in TAGS:
            opened = len(re.findall(rf"\[{tag}\]", a.content + a.sidebar))
            closed = len(re.findall(rf"\[/{tag}\]", a.content + a.sidebar))
            assert opened == closed, (a.title, tag)


def test_mentions_are_well_formed(articles):
    for a in articles:
        for m in re.findall(r"@\[([^\]]*)\]", a.content):
            assert m.strip() and "[" not in m, (a.title, m)


def test_no_article_has_empty_content(articles):
    for a in articles:
        assert a.content.strip(), a.title
        assert len(a.excerpt) <= 300, a.title


def test_location_template_fields(articles):
    for a in articles:
        if a.template in {"location", "settlement", "landmark"}:
            assert set(a.template_fields) <= {
                "population", "areaSize", "alternativename",
                "naturalresources", "locationTemplateType", "history",
            }, a.title
        else:
            assert not a.template_fields, a.title


def test_dangling_links_are_the_known_ones(articles):
    missing = dangling_mentions(articles)
    assert sum(len(v) for v in missing.values()) == 12
    # These are typos in the source data, not conversion bugs.
    assert "Mistriver Gorge:" in missing["ELANDOR"]


def test_titles_unique_within_template(articles):
    seen = Counter((a.template, a.title.casefold()) for a in articles)
    assert not [k for k, n in seen.items() if n > 1]


# -- map --------------------------------------------------------------------

def test_map_collections_identified(map_data):
    for kind in ("burgs", "states", "cultures", "religions", "provinces", "rivers"):
        assert map_data.entities.get(kind), kind
    assert map_data.width == 1536 and map_data.height == 695


def test_burgs_are_inside_the_canvas(map_data):
    burgs = azgaar.burg_markers(map_data)
    assert len(burgs) == 28
    for b in burgs:
        assert 0 <= b["x"] <= map_data.width and 0 <= b["y"] <= map_data.height, b
        assert 0 <= b["fx"] <= 1 and 0 <= b["fy"] <= 1, b
        assert b["name"]


def test_states_exclude_neutrals(map_data):
    names = {s["name"] for s in azgaar.state_rows(map_data)}
    assert "Neutrals" not in names
    assert {"MYR", "Aelwyndor", "Zandaris"} <= names


def test_cleaned_svg_is_valid_xml_and_masked(map_data):
    svg = azgaar.clean_svg(map_data.svg)
    ET.fromstring(svg)  # raises on malformed markup
    assert 'id="landmass"' in svg
    assert re.search(r'<g id="landmass"[^>]*mask="url\(#land\)"', svg)
    assert "./images/" not in svg
    # Self-closing groups must stay self-closing after attribute injection.
    assert '<g id="lake_island"' in svg
    assert "/ fill=" not in svg


# -- pipeline ---------------------------------------------------------------

def test_convert_from_folder(tmp_path):
    result = convert(EXPORT, MAP, tmp_path, render_png=False, log=lambda _: None)
    assert result.article_count == 126
    assert result.burg_count == 28
    assert (tmp_path / "articles.json").exists()
    assert (tmp_path / "map" / "elaris.svg").exists()
    assert len(list((tmp_path / "bbcode").rglob("*.txt"))) == 126

    payloads = json.loads((tmp_path / "articles.json").read_text(encoding="utf-8"))
    assert {p["_faUuid"] for p in payloads} == {
        re.search(r"([0-9a-f-]{36})\.md$", str(f)).group(1)
        for f in EXPORT.rglob("*.md")
    }


def test_convert_from_zip(tmp_path):
    import shutil
    archive = shutil.make_archive(str(tmp_path / "export"), "zip", EXPORT.parent, EXPORT.name)
    out = tmp_path / "out"
    result = convert(Path(archive), None, out, render_png=False, log=lambda _: None)
    assert result.article_count == 126


def test_convert_needs_something(tmp_path):
    with pytest.raises(PipelineError):
        convert(tmp_path / "missing", None, tmp_path / "out", render_png=False,
                log=lambda _: None)
