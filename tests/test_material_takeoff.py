"""Per-item material take-off -> vendor demand lines.

Contract pinned here:
  * A take-off row only becomes demand when its quantity is positive.
  * The broadcast line carries the material identity (key, spec, unit, city), so
    the same physical item always lands on the same dedupe key.
  * Synonym matching expands one take-off phrase into every catalogue wording
    ("9-inch sandcrete block" must also search "225mm", "block", "blocks"), which
    is what stops a correct product being missed.
"""

from app.services.demand_broadcast_service import (
    _SPEC_BY_KEY,
    _dedupe_key,
    _match_terms,
    material_demand_lines,
    normalise_line,
)


def test_engine_enrich_emits_take_off_rows():
    """`MITMEngine.enrich` must actually build the take-off rows.

    Regression: the take-off call sites used `self._material_line(...)` while the
    helper lives at module level, so every parameter/drawing BOQ raised
    AttributeError before pricing or AI ran. Pinned here because the tests below
    only feed hand-built `material_counts` dicts into the demand service and so
    never exercised the engine itself.
    """
    from app.schemas.boq import (
        BOQGenerationRequest,
        FloorInput,
        NigerianCity,
        ProjectInfoInput,
    )
    from app.services.mitm_engine import MITMEngine

    request = BOQGenerationRequest(
        project_info=ProjectInfoInput(project_title="Take-off smoke", city=NigerianCity.abuja),
        floors=[
            FloorInput(
                floor_id="GF",
                level=1,
                floor_area_m2=120.0,
                wall_height_m=3.0,
                perimeter_m=48.0,
            )
        ],
    )

    result = MITMEngine().enrich(request)
    rows = result["enriched"]["derived_quantities"]["material_counts"]

    keys = [row["material_key"] for row in rows]
    assert "blocks_225" in keys and "blocks_150" in keys
    assert len(keys) == len(set(keys)), "material keys must be unique"
    assert all(row["quantity"] >= 0 for row in rows)


def test_material_demand_lines_skip_zero_quantities():
    derived = {
        "material_counts": [
            {"material_key": "blocks_225", "description": "9-inch sandcrete block (225mm)",
             "quantity": 8400, "unit": "nr"},
            {"material_key": "roof_trusses", "description": "Roof trusses", "quantity": 0, "unit": "nr"},
            {"material_key": "floor_tiles_600", "description": "Floor tiles 600x600mm",
             "quantity": None, "unit": "box"},
        ]
    }

    lines = material_demand_lines(derived, city="Abuja")

    assert len(lines) == 1
    assert lines[0]["material_key"] == "blocks_225"
    assert lines[0]["quantity"] == 8400.0
    assert lines[0]["unit"] == "nr"
    assert lines[0]["city"] == "Abuja"
    assert lines[0]["spec"] == "225mm"
    assert lines[0]["source_ref"] == "takeoff:blocks_225"


def test_material_demand_lines_are_empty_without_a_takeoff():
    assert material_demand_lines(None) == []
    assert material_demand_lines({}) == []


def test_material_demand_lines_carry_the_spec_for_every_known_key():
    derived = {
        "material_counts": [
            {"material_key": key, "description": key, "quantity": 1, "unit": "nr"}
            for key in _SPEC_BY_KEY
        ]
    }

    assert all(line["spec"] for line in material_demand_lines(derived))


def test_dedupe_key_prefers_the_material_identity():
    line = {"material_key": "blocks_225", "description": "9-inch sandcrete block (225mm)"}
    assert _dedupe_key(line, "Abuja") == "abuja:blocks_225"


def test_dedupe_key_falls_back_to_a_slugged_description():
    line = {"description": "9-inch sandcrete block (225mm)"}
    assert _dedupe_key(line, " Abuja ") == "abuja:9-inch-sandcrete-block-225mm"


def test_normalise_line_keeps_identity_and_computes_the_key():
    line = normalise_line(
        {"description": "Floor tiles 600x600mm", "quantity": "12", "unit": "box",
         "material_key": "floor_tiles_600", "spec": "600x600mm"},
        default_city="Abuja",
    )
    assert line["quantity"] == 12.0
    assert line["material_key"] == "floor_tiles_600"
    assert line["spec"] == "600x600mm"
    assert line["dedupe_key"] == "abuja:floor_tiles_600"


def test_match_terms_expands_block_synonyms():
    terms = _match_terms("9-inch sandcrete block wall")

    # The catalogue may call the same item "225mm", "blocks" or "blockwork".
    assert "225mm" in terms
    assert "block" in terms
    assert "blocks" in terms


def test_match_terms_expands_roofing_synonyms():
    terms = _match_terms("0.55mm aluminium longspan roofing sheet")

    assert "roofing" in terms
    assert "aluminium" in terms
    assert "longspan" in terms
