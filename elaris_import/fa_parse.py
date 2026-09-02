"""Parse a Fantasia Archive markdown export into structured records.

Fantasia Archive writes one ``.md`` per document, named ``<Title>-<uuid>.md``.
The body is a flat outline:

    # <Title>
    ## Document type
     - Location/Geography
    ---
    # <Section>
    ## <Field>
     - value
     - value

Every field value is a bullet list, even single-valued ones, and free text
fields carry leftover WYSIWYG HTML (``<font>``, ``<div>``, ``<b>``).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

# ``Name-<uuid>.md`` -> ("Name", "<uuid>")
FILENAME_RE = re.compile(
    r"^(?P<name>.+?)-(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})\.md$",
    re.IGNORECASE,
)

# "Aetherium Shard (10)" -> name + parenthetical qualifier
QUALIFIED_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<qualifier>[^()]*)\)$")


@dataclass
class Field:
    """One ``## Heading`` and the bullet values under it."""

    section: str
    name: str
    values: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.values)


@dataclass
class Document:
    uuid: str
    title: str
    folder: str
    source: str
    doc_type: str = ""
    fields: list[Field] = field(default_factory=list)

    def get(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def first(self, name: str, default: str = "") -> str:
        f = self.get(name)
        return f.values[0] if f and f.values else default

    def values(self, name: str) -> list[str]:
        f = self.get(name)
        return list(f.values) if f else []

    def without(self, names: set[str]) -> list[Field]:
        """Every field except the named ones, in document order."""
        return [f for f in self.fields if f.name not in names]


def strip_html(raw: str) -> str:
    """Drop Fantasia Archive's editor markup, keeping paragraph breaks.

    ``<div>`` and ``<br>`` are the only tags that carry meaning here: they are
    line breaks. ``<font>`` and ``<b>`` are styling noise from the WYSIWYG
    editor and are discarded rather than translated, because the styling was
    never intentional.
    """
    text = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    text = re.sub(r"(?i)</(div|p)>", "\n", text)
    text = re.sub(r"(?i)<(div|p)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_qualifier(value: str) -> tuple[str, str]:
    """``"Gold (1.46)"`` -> ``("Gold", "1.46")``; otherwise ``(value, "")``."""
    m = QUALIFIED_RE.match(value)
    return (m.group("name").strip(), m.group("qualifier").strip()) if m else (value, "")


def parse_file(path: Path, root: Path) -> Document | None:
    m = FILENAME_RE.match(path.name)
    if not m:
        return None

    doc = Document(
        uuid=m.group("uuid"),
        title=m.group("name").strip(),
        folder=path.parent.relative_to(root).as_posix(),
        source=path.relative_to(root).as_posix(),
    )

    section = ""
    current: Field | None = None
    body: list[str] = []

    def flush() -> None:
        """Attach the buffered lines to the open field."""
        nonlocal body
        if current is not None:
            for line in body:
                line = line.strip()
                if not line or line == "---":
                    continue
                # Bullets are values; anything else is a free-text paragraph.
                cleaned = strip_html(line[1:] if line.startswith("-") else line)
                if cleaned:
                    current.values.append(cleaned)
        body = []

    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    for line in text.split("\n"):
        if line.startswith("## "):
            flush()
            current = Field(section=section, name=line[3:].strip())
            doc.fields.append(current)
        elif line.startswith("# "):
            flush()
            current = None
            heading = line[2:].strip()
            # The first H1 repeats the title; later ones are section dividers.
            section = "" if heading == doc.title else heading
        else:
            body.append(line)
    flush()

    # Drop headings that had no values at all (Fantasia Archive emits some).
    doc.fields = [f for f in doc.fields if f.values]
    doc.doc_type = doc.first("Document type")
    return doc


def parse_export(root: Path) -> list[Document]:
    """Parse every ``.md`` under ``root``, sorted by folder then title."""
    docs = [d for p in sorted(root.rglob("*.md")) if (d := parse_file(p, root))]
    docs.sort(key=lambda d: (d.folder, d.title.lower()))
    return docs
