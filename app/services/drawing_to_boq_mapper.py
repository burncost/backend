"""Drawing → BOQ request mapper — builds a BOQGenerationRequest from
Gemini Vision extraction output so the drawing→quantity pipeline is automatic.

Reuses the existing BOQGenerationRequest schema and MITM/price enrichment flow.
The only provenance concern is marking the source as "drawing" so consumers
can trace quantities back to the drawing (quantity_source provenance).
"""
from typing import Dict, Any, Optional
import logging
import math

from app.schemas.boq import (
    BOQGenerationRequest,
    ProjectInfoInput,
    FloorInput,
    RoomInput,
    FloorFinish,
    FinishLevel,
    ServicesInput,
    SubstructureInput,
    SuperstructureInput,
    RoofingInput,
    FinishesInput,
    PlumbingFixtures,
)

logger = logging.getLogger(__name__)

# Minimum confidence below which we recommend targeted manual input.
DRAWING_TARGETED_FALLBACK_THRESHOLD = 0.4


class DrawingToBOQMapper:
    """Maps Gemini Vision extraction (extracted_geometry) into a BOQGenerationRequest."""

    def map(
        self,
        extracted_geometry: Dict[str, Any],
        drawing_quality: Optional[Any] = None,
        project_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build a BOQGenerationRequest dict plus a confidence assessment.

        Returns:
            {
                "request": BOQGenerationRequest,
                "confidence": float (0-1),
                "needs_manual_fallback": bool,
                "fallback_reason": Optional[str],
            }
        """
        project_meta = project_meta or {}
        rooms = extracted_geometry.get("rooms", [])
        total_area = float(extracted_geometry.get("floor_area_m2") or 0)

        # If extraction failed to give a usable area, we cannot auto-generate.
        if total_area <= 0:
            return {
                "request": None,
                "confidence": 0.0,
                "needs_manual_fallback": True,
                "fallback_reason": (
                    "No usable floor area could be extracted from the drawing. "
                    "Please enter dimensions manually."
                ),
            }

        # Derive perimeter from rooms when available; else estimate from area.
        perimeter = 0.0
        for r in rooms:
            r_perim = float(r.get("perimeter_m") or 0)
            if r_perim > 0:
                perimeter += r_perim
        if perimeter <= 0:
            perimeter = round(math.sqrt(total_area) * 4, 2)

        floor_rooms = []
        for r in rooms:
            name = r.get("name", "Room")
            area = float(r.get("area_m2") or 0)
            if area <= 0:
                continue
            floor_rooms.append(RoomInput(
                name=name,
                area_m2=round(area, 2),
                perimeter_m=float(r.get("perimeter_m")) if r.get("perimeter_m") else None,
            ))

        building_type = project_meta.get("building_type", "residential")
        city = project_meta.get("city", "Abuja")

        request = BOQGenerationRequest(
            project_info=ProjectInfoInput(
                project_title=project_meta.get("project_title", "Drawing Project"),
                location=project_meta.get("location", "Nigeria"),
                city=city,
                building_type=building_type,
                num_floors=project_meta.get("num_floors", 1),
            ),
            floors=[
                FloorInput(
                    floor_id="GF",
                    level=0,
                    floor_area_m2=round(total_area, 2),
                    perimeter_m=round(perimeter, 2),
                    rooms=floor_rooms,
                    wall_height_m=float(project_meta.get("wall_height_m", 3.0)),
                )
            ],
            finishes=FinishesInput(
                level=project_meta.get("finish_level", FinishLevel.standard),
                floor_finish=project_meta.get("floor_finish", FloorFinish.ceramic_600),
            ),
            services=ServicesInput(
                plumbing_fixtures=PlumbingFixtures(
                    wc=project_meta.get("wc_count", 2),
                    wash_hand_basin=project_meta.get("whb_count", 2),
                )
            ),
            # Carry the drawing provenance into generation.
            drawing_extracted_data=extracted_geometry,
            drawing_quality=drawing_quality,
            drawing_extracted=True,
        )

        # Confidence from drawing quality if provided, else from geometry.
        confidence = 0.6
        if drawing_quality is not None:
            confidence = float(getattr(drawing_quality, "image_quality_score", 0.6))
        elif extracted_geometry.get("source") == "gemini_vision":
            confidence = 0.6

        needs_fallback = confidence < DRAWING_TARGETED_FALLBACK_THRESHOLD
        return {
            "request": request,
            "confidence": round(confidence, 2),
            "needs_manual_fallback": needs_fallback,
            "fallback_reason": (
                "Drawing confidence is low. Recommend targeted manual review "
                "of extracted dimensions before generating the BOQ."
                if needs_fallback else None
            ),
        }