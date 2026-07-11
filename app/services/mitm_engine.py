"""MITM (Missing-Item-Then-Model) Engine
Enriches incomplete user inputs with Nigerian construction defaults,
computes derived quantities, and scores confidence.
"""
from typing import Dict, Any, List, Optional, Tuple
import math
import logging

from app.schemas.boq import (
    BOQGenerationRequest, ProjectInfoInput, FloorInput,
    SubstructureInput, SuperstructureInput, RoofingInput,
    FinishesInput, ServicesInput, StaircaseInput,
)
from app.services.price_service import PriceService


logger = logging.getLogger(__name__)

# ── Nigerian construction defaults ──────────────────────────────────────────

DEFAULT_WALL_HEIGHT_M = 3.0
DEFAULT_EXTERNAL_BLOCK_MM = 225
DEFAULT_INTERNAL_BLOCK_MM = 150
DEFAULT_FOUNDATION_DEPTH_M = 1.2
DEFAULT_CONCRETE_GRADE = "C25"
DEFAULT_ROOF_PITCH_DEG = 30
DEFAULT_FINISH_GRADE = "standard"
DEFAULT_ELECTRICAL_POINTS_PER_ROOM = 6
DEFAULT_WC_COUNT = 2

# Block face area including mortar joint (450mm x 225mm = 0.10125 m²)
BLOCK_FACE_AREA_M2 = 0.10125
BLOCKS_PER_M2 = 1 / BLOCK_FACE_AREA_M2  # ~9.88

# Wastage factors (Nigerian standard)
WASTAGE = {
    "blocks": 0.05,
    "concrete": 0.05,
    "tiles": 0.10,
    "roofing": 0.12,
    "reinforcement": 0.05,
}

# Accuracy caps per drawing type
ACCURACY_CAPS: Dict[str, Dict[str, float]] = {
    "complete_set": {
        "geometry": 0.95, "specification": 0.90,
        "pricing": 0.85, "completeness": 0.95,
    },
    "floor_and_sections": {
        "geometry": 0.85, "specification": 0.80,
        "pricing": 0.80, "completeness": 0.85,
    },
    "floor_plan_only": {
        "geometry": 0.70, "specification": 0.65,
        "pricing": 0.75, "completeness": 0.70,
    },
    "manual_entry": {
        "geometry": 0.80, "specification": 0.85,
        "pricing": 0.80, "completeness": 0.85,
    },
    "unknown": {
        "geometry": 0.60, "specification": 0.60,
        "pricing": 0.70, "completeness": 0.60,
    },
}


class MITMEngine:
    """
    Enriches user inputs with defaults, computes derived quantities,
    generates MITM flags, and scores confidence.
    """

    def __init__(self, price_service: Optional[PriceService] = None):
        self.price_service = price_service or PriceService()

    def enrich(self, request: BOQGenerationRequest) -> Dict[str, Any]:
        """
        Main entry point. Takes raw user input, returns enriched data
        with defaults filled, geometry computed, flags generated.
        """
        enriched = {}
        flags = []
        assumptions = {}

        # 1. Enrich project info
        proj = request.project_info
        city_str = proj.city.value if hasattr(proj.city, 'value') else str(proj.city)
        bt_str = proj.building_type.value if hasattr(proj.building_type, 'value') else str(proj.building_type)
        enriched["project_info"] = {
            "project_title": proj.project_title or "Untitled Project",
            "location": proj.location or "Nigeria",
            "city": city_str,
            "building_type": bt_str,
            "client_name": proj.client_name,
        }

        # 2. Enrich floors
        enriched_floors = []
        total_area = 0.0
        total_perimeter = 0.0
        for f in request.floors:
            ef = {
                "floor_id": f.floor_id,
                "level": f.level,
                "floor_area_m2": f.floor_area_m2,
                "wall_height_m": f.wall_height_m or DEFAULT_WALL_HEIGHT_M,
                "perimeter_m": f.perimeter_m or self._estimate_perimeter(f.floor_area_m2),
                "rooms": [{"name": r.name, "area_m2": r.area_m2, "is_wet_area": False} for r in f.rooms],
                "openings": {
                    "doors": [{"type": d.type, "width_m": d.width_m, "height_m": d.height_m, "count": d.count} for d in f.openings.doors],
                    "windows": [{"type": w.type, "width_m": w.width_m, "height_m": w.height_m, "count": w.count} for w in f.openings.windows],
                },
                "has_slab": f.level > 0,
                "slab_thickness_mm": f.slab_thickness_mm or 150,
            }
            total_area += f.floor_area_m2
            total_perimeter += ef["perimeter_m"]
            enriched_floors.append(ef)

            if not f.floor_area_m2 or f.floor_area_m2 <= 0:
                flags.append({
                    "field": f"floors[{f.floor_id}].floor_area_m2",
                    "severity": "critical",
                    "message": f"Floor area missing for {f.floor_id}. BOQ will be inaccurate.",
                    "confidence_penalty": 0.15,
                })

        enriched["floors"] = enriched_floors
        enriched["total_floor_area_m2"] = total_area
        enriched["total_perimeter_m"] = total_perimeter

        # 3. Enrich substructure
        sub = request.substructure or SubstructureInput()
        ft_str = sub.foundation_type.value if hasattr(sub.foundation_type, 'value') else str(sub.foundation_type)
        enriched["substructure"] = {
            "foundation_type": ft_str,
            "foundation_depth_m": sub.foundation_depth_m or DEFAULT_FOUNDATION_DEPTH_M,
            "blinding_thickness_mm": sub.blinding_thickness_mm or 50,
            "hardcore_thickness_mm": sub.hardcore_thickness_mm or 150,
            "oversite_thickness_mm": sub.oversite_thickness_mm or 100,
            "concrete_grade": sub.concrete_grade or DEFAULT_CONCRETE_GRADE,
            "dpc": sub.dpc if sub.dpc is not None else True,
            "soil_type": sub.soil_type or "laterite",
        }

        # 4. Enrich superstructure
        sup = request.superstructure or SuperstructureInput()
        enriched["superstructure"] = {
            "external_wall_block_mm": sup.external_wall_block_mm or DEFAULT_EXTERNAL_BLOCK_MM,
            "internal_wall_block_mm": sup.internal_wall_block_mm or DEFAULT_INTERNAL_BLOCK_MM,
            "mortar_ratio": sup.mortar_ratio or "1:6",
            "lintel_type": sup.lintel_type or "reinforced_concrete",
            "has_columns": sup.columns,
            "has_beams": sup.beams,
            "column_size_mm": sup.column_size_mm or [225, 225],
            "column_count": sup.column_count,
            "beam_size_mm": sup.beam_size_mm or [225, 450],
            "beam_total_length_m": sup.beam_total_length_m,
        }

        # 5. Enrich roofing
        roof = request.roofing or RoofingInput()
        rt_str = roof.roof_type.value if hasattr(roof.roof_type, 'value') else str(roof.roof_type)
        rc_str = roof.roof_covering.value if hasattr(roof.roof_covering, 'value') else str(roof.roof_covering)
        ct_str = roof.ceiling_type.value if hasattr(roof.ceiling_type, 'value') else str(roof.ceiling_type)
        enriched["roofing"] = {
            "roof_type": rt_str,
            "truss_type": roof.truss_type or "timber",
            "roof_covering": rc_str,
            "ceiling_type": ct_str,
            "roof_pitch_deg": roof.roof_pitch_deg or DEFAULT_ROOF_PITCH_DEG,
        }

        # 6. Enrich finishes
        fin = request.finishes or FinishesInput()
        fl_str = fin.level.value if hasattr(fin.level, 'value') else str(fin.level)
        ff_str = fin.floor_finish.value if hasattr(fin.floor_finish, 'value') else str(fin.floor_finish)
        enriched["finishes"] = {
            "finish_grade": fl_str,
            "floor_finish": ff_str,
            "wall_finish": fin.wall_finish or "plaster_and_paint",
            "ceiling_finish": fin.ceiling_finish.value if hasattr(fin.ceiling_finish, 'value') else str(fin.ceiling_finish),
            "external_finish": fin.external_finish or "paint",
            "wet_areas_tiled": fin.wet_areas_tiled if fin.wet_areas_tiled is not None else True,
        }

        # 7. Enrich services
        svc = request.services or ServicesInput()
        pf = svc.plumbing_fixtures
        enriched["services"] = {
            "electrical_points_per_room": svc.electrical_points_per_room or DEFAULT_ELECTRICAL_POINTS_PER_ROOM,
            "wc_count": pf.wc if pf else DEFAULT_WC_COUNT,
            "wash_hand_basin_count": pf.wash_hand_basin if pf else 2,
            "shower_count": pf.shower if pf else 2,
            "has_kitchen_sink": pf.kitchen_sink > 0 if pf else True,
            "has_borehole": svc.include_borehole,
            "has_overhead_tank": svc.overhead_tank,
            "generator_provision": svc.generator_provision,
        }

        # 8. Enrich staircases
        enriched_stairs = []
        for s in request.staircases:
            enriched_stairs.append({
                "type": s.type or "dog_legged",
                "width_m": s.width_m or 1.0,
                "floor_to_floor_height_m": s.floor_to_floor_height_m or 3.3,
                "riser_mm": s.riser_mm or 165,
                "tread_mm": s.tread_mm or 300,
                "landing_length_m": s.landing_length_m or 1.2,
                "material": s.material or "reinforced_concrete",
                "handrail": s.handrail or "mild_steel",
            })
        enriched["staircases"] = enriched_stairs

        # 9. Compute derived quantities
        derived = self._compute_derived_quantities(enriched)
        enriched["derived_quantities"] = derived

        # 10. Generate assumptions
        assumptions = self._generate_assumptions(enriched, request)

        # 11. Score confidence
        drawing_type = self._resolve_drawing_type(request)
        confidence = self._score_confidence(enriched, drawing_type, flags)

        return {
            "enriched": enriched,
            "flags": flags,
            "assumptions": assumptions,
            "confidence": confidence,
            "drawing_type": drawing_type,
        }

    def _estimate_perimeter(self, area_m2: float) -> float:
        """Estimate perimeter from area assuming roughly square."""
        if area_m2 <= 0:
            return 0
        side = math.sqrt(area_m2)
        return side * 4

    def _compute_derived_quantities(self, enriched: Dict) -> Dict:
        """Compute derived quantities from enriched inputs."""
        total_area = enriched["total_floor_area_m2"]
        total_perimeter = enriched["total_perimeter_m"]
        floors = enriched["floors"]
        sub = enriched["substructure"]
        sup = enriched["superstructure"]
        roof = enriched["roofing"]

        # Wall areas
        gross_wall_area = 0.0
        opening_area = 0.0
        for f in floors:
            h = f["wall_height_m"]
            p = f["perimeter_m"]
            gross_wall_area += p * h

            # Deduct openings
            for d in f["openings"]["doors"]:
                opening_area += d["width_m"] * d["height_m"] * d["count"]
            for w in f["openings"]["windows"]:
                opening_area += w["width_m"] * w["height_m"] * w["count"]

        net_wall_area = gross_wall_area - opening_area

        # Block quantities
        ext_wall_ratio = 0.6  # assume 60% external walls
        int_wall_ratio = 0.4
        ext_blocks = (net_wall_area * ext_wall_ratio) * BLOCKS_PER_M2 * (1 + WASTAGE["blocks"])
        int_blocks = (net_wall_area * int_wall_ratio) * BLOCKS_PER_M2 * (1 + WASTAGE["blocks"])

        # Concrete volumes
        foundation_vol = self._calc_foundation_volume(total_area, sub)
        slab_vol = sum(
            f["floor_area_m2"] * (f["slab_thickness_mm"] / 1000)
            for f in floors if f["has_slab"]
        )

        # Roof area (slope)
        pitch_rad = math.radians(roof["roof_pitch_deg"])
        slope_factor = 1 / math.cos(pitch_rad) if math.cos(pitch_rad) > 0 else 1.3
        roof_slope_area = total_area * slope_factor

        # Reinforcement estimate (kg)
        rebar_kg = self._estimate_rebar(total_area, sub, sup, floors)

        return {
            "gross_wall_area_m2": round(gross_wall_area, 2),
            "net_wall_area_m2": round(net_wall_area, 2),
            "opening_area_m2": round(opening_area, 2),
            "external_blocks_225mm": round(ext_blocks),
            "internal_blocks_150mm": round(int_blocks),
            "foundation_concrete_m3": round(foundation_vol, 2),
            "slab_concrete_m3": round(slab_vol, 2),
            "roof_slope_area_m2": round(roof_slope_area, 2),
            "estimated_rebar_kg": round(rebar_kg),
            "floor_finish_area_m2": round(total_area, 2),
            "wall_finish_area_m2": round(net_wall_area, 2),
        }

    def _calc_foundation_volume(self, area: float, sub: Dict) -> float:
        """Estimate foundation concrete volume."""
        ft = sub.get("foundation_type", "strip")
        depth = sub.get("foundation_depth_m", 1.2)
        if ft == "strip":
            perimeter = math.sqrt(area) * 4
            return perimeter * 0.6 * depth
        elif ft == "raft":
            return area * 0.3
        elif ft == "pile":
            return area * 0.15
        return area * 0.25

    def _estimate_rebar(self, area: float, sub: Dict, sup: Dict, floors: List) -> float:
        """Estimate total rebar weight in kg."""
        foundation_vol = self._calc_foundation_volume(area, sub)
        foundation_rebar = foundation_vol * 80

        col_count = sup.get("column_count") or max(4, int(math.sqrt(area) * 2))
        col_size = sup.get("column_size_mm", [225, 225])
        col_height = sum(f["wall_height_m"] for f in floors)
        col_vol = col_count * (col_size[0] / 1000) * (col_size[1] / 1000) * col_height
        col_rebar = col_vol * 120

        slab_vol = sum(
            f["floor_area_m2"] * (f["slab_thickness_mm"] / 1000)
            for f in floors if f["has_slab"]
        )
        slab_rebar = slab_vol * 100

        return foundation_rebar + col_rebar + slab_rebar

    def _resolve_drawing_type(self, request: BOQGenerationRequest) -> str:
        """Determine drawing type from request."""
        if request.drawing_quality and request.drawing_quality.drawing_type:
            return request.drawing_quality.drawing_type.value
        if request.drawing_extracted_data:
            return "floor_plan_only"
        return "manual_entry"

    def _generate_assumptions(self, enriched: Dict, request: BOQGenerationRequest) -> Dict[str, Any]:
        """Generate list of assumptions used."""
        return {
            "wall_height_m": enriched["floors"][0]["wall_height_m"] if enriched["floors"] else DEFAULT_WALL_HEIGHT_M,
            "external_wall_ratio": 0.6,
            "internal_wall_ratio": 0.4,
            "block_wastage_pct": WASTAGE["blocks"] * 100,
            "concrete_wastage_pct": WASTAGE["concrete"] * 100,
            "tile_wastage_pct": WASTAGE["tiles"] * 100,
            "roofing_wastage_pct": WASTAGE["roofing"] * 100,
            "rebar_density_foundation_kg_per_m3": 80,
            "rebar_density_column_kg_per_m3": 120,
            "rebar_density_slab_kg_per_m3": 100,
            "drawing_type": self._resolve_drawing_type(request),
            "city": enriched["project_info"]["city"],
            "building_type": enriched["project_info"]["building_type"],
        }

    def _score_confidence(self, enriched: Dict, drawing_type: str, flags: List) -> Dict:
        """Score confidence based on input completeness and drawing type."""
        caps = ACCURACY_CAPS.get(drawing_type, ACCURACY_CAPS["unknown"])

        geometry_score = caps["geometry"]
        specification_score = caps["specification"]
        pricing_score = caps["pricing"]
        completeness_score = caps["completeness"]

        penalties = []
        for flag in flags:
            penalty = flag.get("confidence_penalty", 0)
            if flag["severity"] == "critical":
                geometry_score -= penalty * 0.5
                completeness_score -= penalty * 0.5
                penalties.append(flag["message"])

        geometry_score = max(0.1, min(1.0, geometry_score))
        specification_score = max(0.1, min(1.0, specification_score))
        pricing_score = max(0.1, min(1.0, pricing_score))
        completeness_score = max(0.1, min(1.0, completeness_score))

        overall = (geometry_score + specification_score + pricing_score + completeness_score) / 4

        if overall >= 0.80:
            level = "HIGH"
        elif overall >= 0.55:
            level = "MEDIUM"
        else:
            level = "LOW"

        return {
            "overall_score_pct": round(overall * 100, 1),
            "breakdown": {
                "geometry": round(geometry_score * 100, 1),
                "specification": round(specification_score * 100, 1),
                "pricing": round(pricing_score * 100, 1),
                "completeness": round(completeness_score * 100, 1),
            },
            "penalties_applied": penalties,
            "confidence_level": level,
            "notes": [
                f"Drawing type: {drawing_type.replace('_', ' ').title()}",
                f"Geometry confidence: {geometry_score:.0%}",
                f"Specification confidence: {specification_score:.0%}",
            ],
        }

    def preview(self, request: BOQGenerationRequest) -> Dict[str, Any]:
        """Generate MITM preview without calling Gemini."""
        result = self.enrich(request)
        return {
            "flags": result["flags"],
            "assumptions": result["assumptions"],
            "drawing_quality": request.drawing_quality,
            "accuracy_caps": ACCURACY_CAPS.get(result["drawing_type"], ACCURACY_CAPS["unknown"]),
            "estimated_confidence": {
                "estimated_score_pct": result["confidence"]["overall_score_pct"],
                "level": result["confidence"]["confidence_level"],
                "drawing_type": result["drawing_type"],
                "warning_count": len([f for f in result["flags"] if f["severity"] != "info"]),
                "critical_count": len([f for f in result["flags"] if f["severity"] == "critical"]),
                "notes": result["confidence"]["notes"],
            },
        }
