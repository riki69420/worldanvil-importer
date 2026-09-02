"""Render parsed Fantasia Archive documents as World Anvil articles.

World Anvil articles are BBCode. Cross-references use the mention system:
``@[Article Title]`` resolves to whichever article in the world carries that
title, so links survive an import without knowing article UUIDs in advance —
provided the referenced article exists by the time the page is viewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fa_parse import Document, split_qualifier
from .mapping import (
    CONSUMED,
    LINK_FIELDS,
    LOCATION_FIELDS,
    SIDEBAR_FIELDS,
    resolve_template,
    tags_for,
)

LOCATION_TEMPLATES = {"location", "settlement", "landmark"}
EXCERPT_LIMIT = 255  # World Anvil's excerpt column holds 255 characters


@dataclass
class Article:
    """A World Anvil article ready to be pasted or PUT to the API."""

    title: str
    template: str
    content: str
    excerpt: str
    tags: str
    sidebar: str
    template_fields: dict[str, str] = field(default_factory=dict)
    source: str = ""
    fa_uuid: str = ""
    mentions: list[str] = field(default_factory=list)

    def payload(self, world_id: str, use_template_fields: bool = True) -> dict:
        """The JSON body for ``PUT /article``."""
        body = {
            "title": self.title,
            "templateType": self.template,
            "world": {"id": world_id},
            "content": self.content,
            "excerpt": self.excerpt,
            "tags": self.tags,
            "state": "private",
            "isDraft": False,
        }
        if self.sidebar:
            body["sidebarcontent"] = self.sidebar
        if use_template_fields:
            body.update(self.template_fields)
        return body


def mention(name: str) -> str:
    """A World Anvil mention. Square brackets inside a title would break it."""
    safe = name.replace("[", "(").replace("]", ")")
    return f"@[{safe}]"


def _render_values(values: list[str], linked: bool) -> list[str]:
    out = []
    for value in values:
        if not linked:
            out.append(value)
            continue
        # "Aetherium Shard (10)" links the name and keeps the rate as text.
        name, qualifier = split_qualifier(value)
        out.append(f"{mention(name)} ({qualifier})" if qualifier else mention(name))
    return out


def _collect_mentions(doc: Document) -> list[str]:
    names: list[str] = []
    for f in doc.fields:
        if f.name in LINK_FIELDS:
            names.extend(split_qualifier(v)[0] for v in f.values)
    return list(dict.fromkeys(names))


def _sidebar(doc: Document, skip: set[str]) -> str:
    """Quick facts for the article sidebar.

    Fields already carried by a native template field are skipped: World Anvil
    renders those in its own sidebar, so repeating them here would double them.
    """
    rows = []
    for f in doc.fields:
        if f.name not in SIDEBAR_FIELDS or f.name in CONSUMED or f.name in skip:
            continue
        rows.append(f"[b]{f.name}[/b]\n{', '.join(f.values)}")
    return "\n\n".join(rows)


def _body(doc: Document, skip: set[str]) -> str:
    """Sections and fields as BBCode, in the order Fantasia Archive wrote them."""
    chunks: list[str] = []
    open_section = None

    for f in doc.without(CONSUMED | skip):
        if f.section != open_section:
            open_section = f.section
            if open_section:
                chunks.append(f"[h2]{open_section}[/h2]")
        values = _render_values(f.values, f.name in LINK_FIELDS)
        if len(values) == 1:
            chunks.append(f"[h3]{f.name}[/h3]\n{values[0]}")
        else:
            items = "\n".join(f"[li]{v}[/li]" for v in values)
            chunks.append(f"[h3]{f.name}[/h3]\n[ul]\n{items}\n[/ul]")

    return "\n\n".join(chunks)


def build_article(doc: Document) -> Article:
    template = resolve_template(doc)
    description = "\n\n".join(doc.values("Description & History"))

    template_fields: dict[str, str] = {}
    skip: set[str] = set()
    if template in LOCATION_TEMPLATES:
        for fa_name, wa_name in LOCATION_FIELDS.items():
            values = doc.values(fa_name)
            if values:
                template_fields[wa_name] = ", ".join(values)
                skip.add(fa_name)
        if description:
            template_fields["history"] = description

    sidebar = _sidebar(doc, skip)
    body = _body(doc, skip)

    parts = [p for p in (description, body) if p]
    content = "\n\n".join(parts) or "[i]Imported from Fantasia Archive.[/i]"

    excerpt = description.split("\n")[0] if description else ""
    if len(excerpt) > EXCERPT_LIMIT:
        excerpt = excerpt[: EXCERPT_LIMIT - 1].rsplit(" ", 1)[0] + "…"

    return Article(
        title=doc.title,
        template=template,
        content=content,
        excerpt=excerpt,
        tags=tags_for(doc, template),
        sidebar=sidebar,
        template_fields=template_fields,
        source=doc.source,
        fa_uuid=doc.uuid,
        mentions=_collect_mentions(doc),
    )


def build_all(docs: list[Document]) -> list[Article]:
    return [build_article(d) for d in docs]


def dangling_mentions(articles: list[Article]) -> dict[str, list[str]]:
    """Mentions that point at a title no article in the batch provides.

    World Anvil renders an unresolved mention as plain text, so these are the
    links that will silently not work after the import.
    """
    titles = {a.title.casefold() for a in articles}
    missing: dict[str, list[str]] = {}
    for article in articles:
        gone = [m for m in article.mentions if m.casefold() not in titles]
        if gone:
            missing[article.title] = gone
    return missing
