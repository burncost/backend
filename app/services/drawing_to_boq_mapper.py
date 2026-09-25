"""Drawing → BOQ request mapper — builds a BOQGenerationRequest from
Gemini Vision extraction output so the drawing→quantity pipeline is automatic.

Reuses the existing BOQGenerationRequest schema and MITM/price enrichment flow.
The only provenance concern is marking the source as "drawing" so consumers
can trace quantities back to the drawing (quantity_source provenance).
"""
from typing import Dict, Any, Optional, Tuple, List
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

# An extracted area below these floors is not credible for the building type,
# which in practice means the vision model read a single room or a partial
# outline. Billing such a drawing as a whole building understates every
# quantity and the contract sum.
MIN_PLAUSIBLE_AREA_M2 = {
    "residential": 30.0,
    "commercial": 50.0,
    "institutional": 80.0,
    "industrial": 100.0,
}
DEFAULT_MIN_PLAUSIBLE_AREA_M2 = 25.0

# Gross-area uplift: extracted areas are net room areas, so the bill has to
# carry wall thickness, circulation and openings.
DRAWING_AREA_UPLIFT_PCT = 10.0

# Nigerian practice for rooms the drawing did not dimension — assuming a size is
# closer to how a QS would bill than dropping the room entirely.
DEFAULT_ROOM_AREAS_M2 = {
    "master": 18.0,
    "bedroom": 12.0,
    "living": 20.0,
    "sitting": 20.0,
    "lounge": 20.0,
    "dining": 12.0,
    "kitchen": 8.0,
    "bath": 4.0,
    "toilet": 2.5,
    "wc": 2.5,
    "store": 3.0,
    "corridor": 4.0,
    "passage": 4.0,
    "balcony": 5.0,
    "garage": 16.0,
    "porch": 4.0,
    "laundry": 4.0,
}
DEFAULT_ROOM_AREA_M2 = 9.0


def _room_area_or_default(room: Dict[str, Any]) -> Tuple[float, bool]:
    """Area for a room, substituting a Nigerian default when none was extracted.

    Returns (area_m2, used_default). Rooms the drawing left undimensioned are
    assumed rather than dropped, otherwise wet areas and finishes are billed as
    zero.
    """
    area = float(room.get("area_m2") or 0)
    if area > 0:
        return area, False
    name = str(room.get("name") or "").lower()
    for key, default in DEFAULT_ROOM_AREAS_M2.items():
        if key in name:
            return default, True
    return DEFAULT_ROOM_AREA_M2, True


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

        # Derive perimeter from rooms when available; else estimate from the
        # gross area (the final value is settled in the geometry check below).
        measured_perimeter = sum(
            float(r.get("perimeter_m") or 0)
            for r in rooms
            if float(r.get("perimeter_m") or 0) > 0
        )

        floor_rooms = []
        defaulted_rooms = 0
        for r in rooms:
            area, used_default = _room_area_or_default(r)
            if used_default:
                defaulted_rooms += 1
            if area <= 0:
                continue
            floor_rooms.append(RoomInput(
                name=r.get("name", "Room"),
                area_m2=round(area, 2),
                perimeter_m=float(r.get("perimeter_m")) if r.get("perimeter_m") else None,
            ))

        building_type = project_meta.get("building_type", "residential")
        city = project_meta.get("city", "Abuja")

        # Geometry check: an area the vision model misread (a single room read
        # off a whole floor plan) must not be billed as a complete building.
        geometry_warnings: List[str] = []
        min_area = MIN_PLAUSIBLE_AREA_M2.get(
            str(building_type).lower(), DEFAULT_MIN_PLAUSIBLE_AREA_M2
        )
        implausible_area = total_area < min_area
        if implausible_area:
            geometry_warnings.append(
                f"Extracted floor area ({round(total_area, 2)} m²) is implausibly small for a "
                f"{building_type} building (minimum {min_area:g} m²): the drawing may have been "
                "misread or cover only part of the plan. Confirm the dimensions before pricing."
            )

        room_total = sum(float(r.get("area_m2") or 0) for r in rooms)
        if not rooms:
            geometry_warnings.append(
                "No room-level dimensions were extracted, so quantities were derived from the "
                "overall area alone."
            )
        if measured_perimeter <= 0:
            geometry_warnings.append(
                "Room perimeters were not extracted; the external perimeter was estimated from "
                "the floor area."
            )
        if rooms and room_total > 0 and abs(room_total - total_area) / total_area > 0.25:
            geometry_warnings.append(
                f"Room areas total {round(room_total, 2)} m² against a stated floor area of "
                f"{round(total_area, 2)} m² — one of the readings is likely wrong."
            )
        if defaulted_rooms:
            geometry_warnings.append(
                f"{defaulted_rooms} room(s) had no extracted area, so Nigerian default room sizes "
                "were assumed for them."
            )

        # Gross-area uplift: extracted areas are net room areas, so the bill has
        # to carry wall thickness, circulation and openings.
        uplift_pct = float(project_meta.get("area_uplift_pct", DRAWING_AREA_UPLIFT_PCT))
        gross_area = round(total_area * (1 + uplift_pct / 100.0), 2)
        perimeter = (
            round(measured_perimeter, 2)
            if measured_perimeter > 0
            else round(math.sqrt(gross_area) * 4, 2)
        )

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
                    floor_area_m2=round(gross_area, 2),
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
            # Carry the drawing provenance into generation (raw measurement kept
            # alongside the uplifted area used for billing).
            drawing_extracted_data={
                **extracted_geometry,
                "raw_area_m2": round(total_area, 2),
                "gross_area_m2": gross_area,
                "area_uplift_pct": uplift_pct,
            },
            drawing_quality=drawing_quality,
            drawing_extracted=True,
        )

        # Confidence from drawing quality if provided, else from geometry.
        confidence = 0.6
        if drawing_quality is not None:
            confidence = float(getattr(drawing_quality, "image_quality_score", 0.6))
        elif extracted_geometry.get("source") == "gemini_vision":
            confidence = 0.6

        # Geometry constrains confidence: a credible area backed by room-level
        # dimensions cannot score the same as a single room read off a plan.
        if implausible_area:
            confidence = min(confidence, 0.3)
        elif not rooms:
            confidence = min(confidence, 0.45)
        elif measured_perimeter <= 0:
            confidence = min(confidence, 0.5)
        elif room_total > 0 and abs(room_total - total_area) / total_area > 0.25:
            confidence = min(confidence, 0.45)

        needs_fallback = (
            implausible_area or confidence < DRAWING_TARGETED_FALLBACK_THRESHOLD
        )
        return {
            "request": request,
            "confidence": round(confidence, 2),
            "needs_manual_fallback": needs_fallback,
            "fallback_reason": (
                geometry_warnings[0]
                if implausible_area
                else (
                    "Drawing confidence is low. Recommend targeted manual review "
                    "of extracted dimensions before generating the BOQ."
                    if needs_fallback
                    else None
                )
            ),
            "geometry_warnings": geometry_warnings,
            "raw_area_m2": round(total_area, 2),
            "gross_area_m2": gross_area,
            "area_uplift_pct": uplift_pct,
        }