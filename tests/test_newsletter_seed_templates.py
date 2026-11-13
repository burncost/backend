"""The built-in newsletter templates (seed migration) stay renderable.

A seed body with a typo, an unknown ``{{merge_tag}}`` or an audience the admin
API rejects would only surface in production, after the campaign was mailed.
"""
import importlib.util
import re
from pathlib import Path

from app.services.newsletter_render import MERGE_TAGS

SEED = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20261029_newsletter_default_templates.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("seed_newsletter_templates", SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seeded_templates_are_complete_and_distinct():
    templates = _load().TEMPLATES
    slugs = [t[0] for t in templates]
    assert len(slugs) == len(set(slugs)), "slugs must be unique (they key the upsert)"
    for slug, name, audience, subject, body_html in templates:
        assert slug and name and subject and body_html
        assert audience in {"vendors", "customers", "custom"}


def test_seeded_templates_only_use_known_merge_tags():
    for _, _, _, subject, body_html in _load().TEMPLATES:
        used = set(re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", f"{subject}\n{body_html}"))
        assert used, "a template with no merge tag is suspicious"
        assert used <= set(MERGE_TAGS), f"unknown merge tags: {used - set(MERGE_TAGS)}"
