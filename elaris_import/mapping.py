"""Fantasia Archive -> World Anvil template and field mapping.

World Anvil's article templates are fixed; ``entityClass`` in the Boromir
OpenAPI spec enumerates them. ``templateType`` on the article payload is the
lower-camel form of that enum. Only the ``location`` template has its custom
fields published in the spec, so every other template is filled through the
generic article fields (``content``, ``excerpt``, ``sidebarcontent``, ``tags``)
and the structured data is rendered as BBCode sections inside them.
"""

from __future__ import annotations

# Lower-camel templateType values, from the entityClass enum in
# swagger/parts/article/schemas/article.yml#/ArticleRefCore.
TEMPLATE_TYPES = {
    "article", "ethnicity", "landmark", "location", "ritual", "myth",
    "technology", "spell", "law", "prose", "militaryConflict", "language",
    "document", "person", "organization", "plot", "species", "vehicle",
    "profession", "item", "formation", "rank", "condition", "material",
    "settlement", "report",
}

# Fantasia Archive "Document type" -> World Anvil templateType.
DOC_TYPE_TEMPLATE = {
    "Character": "person",
    "Currency": "item",
    "Item": "item",
    "Language": "language",
    "Location/Geography": "location",  # refined by LOCATION_TYPE_TEMPLATE
    "Occupation/Class": "profession",
    "Organization/Other group": "organization",
    "Resource/Material": "material",
    "School of Magic/Magical group": "organization",
    "Skill/Spell/Other": "article",  # refined by SKILL_TYPE_TEMPLATE
    "Species/Race/Flora/Fauna": "species",
    "Teaching/Religious group": "organization",
}

# Fantasia Archive "Location type" -> a more specific World Anvil template.
LOCATION_TYPE_TEMPLATE = {
    "City": "settlement",
    "Town": "settlement",
    "Village": "settlement",
    "Building": "landmark",
    "Structure": "landmark",
    "Continent": "location",
    "Country": "location",
    "Area": "location",
    "Terrain formation": "location",
}

# Fantasia Archive "Type" on a Skill/Spell/Other -> template.
SKILL_TYPE_TEMPLATE = {
    "Spell": "spell",
    "Blessing": "spell",
    "Magical skill": "spell",
    "School of magic": "organization",
}

# Fields that map onto real World Anvil ``location`` template fields.
# Applied only when the resolved template is location/settlement/landmark.
LOCATION_FIELDS = {
    "Population": "population",
    "Size": "areaSize",
    "Other Names & Epithets": "alternativename",
    "Local Resources/Materials": "naturalresources",
    "Location type": "locationTemplateType",
}

# Fields consumed elsewhere and therefore never repeated in the body.
CONSUMED = {"Document type", "Description & History"}

# Fields whose values are article titles, so they render as @[mentions].
# Everything else renders as literal text.
LINK_FIELDS = {
    "Characters of Species/Races/Flora/Fauna",
    "Characters of the Occupation/Class",
    "Characters originated from the location",
    "Common Languages",
    "Common Occupations/Classes",
    "Common Species/Races/Flora/Fauna",
    "Common in Organizations/Other groups",
    "Commonly spoken Languages",
    "Commonly used by Occupations/Classes",
    "Commonly used Currencies",
    "Commonly used Items",
    "Commonly used Skills/Spells/Other",
    "Connected Characters",
    "Connected Locations",
    "Connected Schools of Magic/Magical groups",
    "Connected Teachings/Religious groups",
    "Connected to Characters",
    "Connected to Locations",
    "Connected to Locations/Geography",
    "Connected to Schools of Magic/Magical groups",
    "Connected to Skills/Spells/Other",
    "Connected to Species/Races/Flora/Fauna",
    "Exchange rates to other Currencies",
    "Found in Locations",
    "Governing Schools of Magic/Magical groups",
    "Governing Teachings/Religious groups",
    "Headquarters",
    "Inhabited Locations",
    "Leading Figure of Organizations/Other groups",
    "Leading Figure of Teachings/Religious groups",
    "Leading Figures",
    "Local Currencies",
    "Local Languages",
    "Local Resources/Materials",
    "Local Species/Races/Flora/Fauna",
    "Neighbouring Locations",
    "Occupation/Class",
    "Other connected Locations",
    "Place of origin",
    "Prerequisites Skills/Spells/Other",
    "Related Occupations/Classes",
    "Related Schools of Magic",
    "Related Species/Races/Flora/Fauna",
    "Required by Skills/Spells/Other",
    "Ruled/Influenced Locations",
    "Species/Races",
    "Spoken in Magical groups",
    "Used by Organizations/Other groups",
    "Used by Races",
    "Used in Locations",
    "Used Languages",
}

# Short, at-a-glance fields that belong in the sidebar rather than the body.
SIDEBAR_FIELDS = {
    "Status",
    "Location type", "Population", "Size", "Type", "Occupation/Class type",
    "Type of group", "Form of religion", "Type of religion",
    "Follower/Subject count", "Average lifespan", "Average adulthood",
    "Average size", "Complexity to use", "Date of creation", "Sex", "Age",
    "Height", "Weight", "Other Names & Epithets",
}


def resolve_template(doc) -> str:
    """Pick the World Anvil template for a parsed Fantasia Archive document."""
    template = DOC_TYPE_TEMPLATE.get(doc.doc_type, "article")
    if doc.doc_type == "Location/Geography":
        template = LOCATION_TYPE_TEMPLATE.get(doc.first("Location type"), template)
    elif doc.doc_type == "Skill/Spell/Other":
        template = SKILL_TYPE_TEMPLATE.get(doc.first("Type"), template)
    if template not in TEMPLATE_TYPES:
        raise ValueError(f"{template!r} is not a World Anvil templateType")
    return template


def tags_for(doc, template: str) -> str:
    """A comma-separated tag string that keeps the original taxonomy findable."""
    tags = ["imported", "fantasia-archive", template]
    if doc.doc_type:
        tags.append(doc.doc_type.replace("/", "-").lower())
    subtype = doc.first("Location type") or doc.first("Type") or doc.first(
        "Occupation/Class type"
    )
    if subtype:
        tags.append(subtype.lower())
    # dict.fromkeys keeps first-seen order while de-duplicating.
    return ",".join(dict.fromkeys(t.replace(" ", "-") for t in tags))
