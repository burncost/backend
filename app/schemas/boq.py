from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum


# ─── Drawing input classification ────────────────────────────────────────────

class DrawingType(str, Enum):
    """Governs which BOQ elements can be computed with high confidence."""
    FLOOR_PLAN_ONLY    = "floor_plan_only"       # good for finishes only (~70-85%)
    FLOOR_AND_SECTIONS = "floor_and_sections"    # adds wall heights, lintels (~80-88%)
    COMPLETE_SET       = "complete_set"          # architectural + structural (~88-95%)
    MANUAL_ENTRY       = "manual_entry"          # user typed values, no image
    UNKNOWN            = "unknown"


class DrawingFormat(str, Enum):
    PDF   = "pdf"    # BEST — vector, scalable, preserves dimensions
    IMAGE = "image"  # OK   — raster; quality depends on scan resolution
    CAD   = "cad"    # N/A  — not accepted by this system


class DrawingQuality(BaseModel):
    """Output of the CV/OCR preprocessing stage."""
    drawing_type: DrawingType = DrawingType.UNKNOWN
    drawing_format: DrawingFormat = DrawingFormat.IMAGE
    has_dimensions: bool = False          # True if OCR found dimension text
    has_scale_bar: bool = False           # True if scale indicator detected
    has_room_labels: bool = False         # True if room names found
    has_structural_elements: bool = False # True if columns/beams/sections found
    image_quality_score: float = 0.5     # 0–1 from CV sharpness/contrast check
    ocr_dimension_count: int = 0         # number of numeric dimensions extracted
    extracted_dimensions: List[float] = []   # raw dimension values from OCR (mm or m)
    cv_wall_length_estimate_m: Optional[float] = None  # from line detection
    notes: List[str] = []
    # Accuracy caps per category based on drawing type
    accuracy_caps: Dict[str, float] = {}


class BuildingType(str, Enum):
    residential = "residential"
    commercial = "commercial"
    mixed = "mixed"


class FoundationType(str, Enum):
    strip = "strip"
    raft = "raft"
    pile = "pile"


class RoofType(str, Enum):
    hip = "hip"
    gable = "gable"
    flat_slab = "flat_slab"
    mansard = "mansard"


class RoofCovering(str, Enum):
    aluminium_longspan = "aluminium_longspan"
    stone_coated_tiles = "stone_coated_tiles"
    zinc = "zinc"
    concrete_tiles = "concrete_tiles"



class CeilingType(str, Enum):
    pop = "POP"
    pvc = "PVC"
    none = "none"
    gypsum = "gypsum"


class FloorFinish(str, Enum):
    ceramic_600 = "ceramic_tile_600x600"
    ceramic_400 = "ceramic_tile_400x400"
    porcelain_600 = "porcelain_tile_600x600"
    vitrified_600 = "vitrified_tile_600x600"
    marble = "marble"
    screed = "screed"
    terrazzo = "terrazzo"



class FinishLevel(str, Enum):
    standard = "standard"
    medium = "medium"
    luxury = "luxury"


class NigerianCity(str, Enum):
    abuja = "Abuja"
    lagos = "Lagos"
    port_harcourt = "Port Harcourt"
    kano = "Kano"
    ibadan = "Ibadan"
    enugu = "Enugu"
    benin = "Benin City"
    kaduna = "Kaduna"
    jos = "Jos"
    calabar = "Calabar"


class RoomInput(BaseModel):
    name: str
    area_m2: float
    perimeter_m: Optional[float] = None


class OpeningDetail(BaseModel):
    type: str
    width_m: float
    height_m: float
    count: int


class OpeningsInput(BaseModel):
    doors: List[OpeningDetail] = []
    windows: List[OpeningDetail] = []


class FloorInput(BaseModel):
    floor_id: str
    level: int
    floor_area_m2: float
    wall_height_m: float = 3.0
    perimeter_m: float
    rooms: List[RoomInput] = []
    openings: OpeningsInput = OpeningsInput()
    slab_thickness_mm: int = 150


class StaircaseInput(BaseModel):
    id: str = "ST1"
    type: str = "dog_legged"
    width_m: float = 1.0
    floor_to_floor_height_m: float = 3.3
    riser_mm: int = 165
    tread_mm: int = 300
    landing_length_m: float = 1.2
    material: str = "reinforced_concrete"
    handrail: str = "mild_steel"


class ProjectInfoInput(BaseModel):
    project_title: str
    location: str = "Nigeria"
    city: NigerianCity = NigerianCity.abuja
    building_type: BuildingType = BuildingType.residential
    client_name: Optional[str] = None
    description: Optional[str] = None
    num_floors: int = 1


class SubstructureInput(BaseModel):
    foundation_type: FoundationType = FoundationType.strip
    foundation_depth_m: float = 1.2
    blinding_thickness_mm: int = 50
    hardcore_thickness_mm: int = 150
    oversite_thickness_mm: int = 100
    concrete_grade: str = "C25"
    dpc: bool = True
    soil_type: str = "laterite"


class SuperstructureInput(BaseModel):
    external_wall_block_mm: int = 225
    internal_wall_block_mm: int = 150
    mortar_ratio: str = "1:6"
    lintel_type: str = "reinforced_concrete"
    columns: bool = True
    beams: bool = True
    column_size_mm: List[int] = [225, 225]
    column_count: Optional[int] = None
    beam_size_mm: List[int] = [225, 450]
    beam_total_length_m: Optional[float] = None


class RoofingInput(BaseModel):
    roof_type: RoofType = RoofType.hip
    truss_type: str = "timber"
    roof_covering: RoofCovering = RoofCovering.aluminium_longspan
    ceiling_type: CeilingType = CeilingType.pop
    roof_pitch_deg: float = 30.0


class FinishesInput(BaseModel):
    level: FinishLevel = FinishLevel.standard
    floor_finish: FloorFinish = FloorFinish.ceramic_600
    wall_finish: str = "plaster_and_paint"
    ceiling_finish: CeilingType = CeilingType.pop
    external_finish: str = "paint"
    wet_areas_tiled: bool = True


class PlumbingFixtures(BaseModel):
    wc: int = 2
    wash_hand_basin: int = 2
    shower: int = 2
    kitchen_sink: int = 1
    bathtub: int = 0


class ServicesInput(BaseModel):
    electrical_points_per_room: int = 6
    plumbing_fixtures: PlumbingFixtures = PlumbingFixtures()
    include_borehole: bool = False
    overhead_tank: bool = True
    generator_provision: bool = False


class BOQGenerationRequest(BaseModel):
    project_info: ProjectInfoInput
    floors: List[FloorInput]
    substructure: Optional[SubstructureInput] = None
    superstructure: Optional[SuperstructureInput] = None
    roofing: Optional[RoofingInput] = None
    finishes: Optional[FinishesInput] = None
    services: Optional[ServicesInput] = None
    staircases: List[StaircaseInput] = []
    drawing_extracted_data: Optional[Dict[str, Any]] = None
    drawing_quality: Optional[DrawingQuality] = None
    risk_profile: str = "medium"
    drawing_extracted: bool = False
    user_overrides: Dict[str, Any] = {}


class BOQItem(BaseModel):
    item_code: str
    description: str
    unit: str
    quantity: float
    rate: float
    amount: float
    cost_scenarios: Optional[Dict[str, float]] = None
    data_source: Optional[str] = None
    # Per-item confidence
    confidence: float = 1.0
    confidence_reason: Optional[str] = None
    estimated: bool = False


class BOQElement(BaseModel):
    element_name: str
    items: List[BOQItem]
    element_total: float
    cost_percentage_of_total: Optional[float] = None
    floor_breakdown: Optional[List[Dict[str, Any]]] = None


class ConfidenceBreakdown(BaseModel):
    geometry: float
    specification: float
    pricing: float
    completeness: float


class ConfidenceScore(BaseModel):
    overall_score_pct: float
    breakdown: ConfidenceBreakdown
    penalties_applied: List[str]
    confidence_level: str
    notes: List[str]


class CostSummary(BaseModel):
    sub_total: float
    contingency: float
    vat: float
    total_contract_sum: float
    total_low: float
    total_expected: float
    total_high: float
    cost_per_sqm: float


class BOQUpdate(BaseModel):
    """Schema for updating an existing BOQ (all fields optional)."""
    title: Optional[str] = None
    status: Optional[str] = None
    items: Optional[List[BOQItem]] = None
    elements: Optional[List[BOQElement]] = None
    summary: Optional[CostSummary] = None
    project_info: Optional[Dict[str, Any]] = None
    assumptions_used: Optional[Dict[str, Any]] = None
    warnings: Optional[List[str]] = None


class BOQListResponse(BaseModel):
    """Paginated list of BOQs for a project."""
    boqs: List[Dict[str, Any]]
    total: int
    page: int
    page_size: int
    total_pages: int


class DrawingAnalysisResponse(BaseModel):
    """Returned from /api/v1/boqs/analyze-drawing"""
    success: bool
    drawing_quality: Optional[DrawingQuality] = None
    extracted_geometry: Optional[Dict[str, Any]] = None
    confidence: float = 0.0
    notes: List[str] = []
    error: Optional[str] = None
    upgrade_prompt: Optional[str] = None
    upload_guidance: Optional[str] = None


class BOQResponse(BaseModel):
    project_info: Dict[str, Any]
    elements: List[BOQElement]
    summary: CostSummary
    confidence: ConfidenceScore
    assumptions_used: Dict[str, Any]
    generated_at: str
    warnings: List[str] = []


# ─── Order schemas ────────────────────────────────────────────────────────────

class OrderItemRequest(BaseModel):
    """An item selected for ordering from a BOQ."""
    item_code: str
    description: str
    quantity: float
    unit: str
    rate: float


class BOQOrderRequest(BaseModel):
    """Request to place an order from BOQ items."""
    items: List[OrderItemRequest]
    shipping_address: Optional[str] = None
    notes: Optional[str] = None


class BOQOrderResponse(BaseModel):
    """Response after placing an order from BOQ items."""
    success: bool
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    message: str
    items_ordered: int = 0
    total_amount: float = 0.0


