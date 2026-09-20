from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
import fitz  # PyMuPDF
import re
import math
import os
import requests
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple


# ============================================================
# CONFIG
# ============================================================

SUPABASE_URL = "https://tuhgurlibsaqqxrmhgdr.supabase.co"
SUPABASE_KEY = "sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39"

MAX_FILE_SIZE_MB = 15
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

app = FastAPI(
    title="Universal CGM AGP Parser",
    version="2.0.0"
)


# ============================================================
# METRIC DEFINITIONS
# ============================================================

METRICS = {

    "VERY_LOW": {
        "aliases": [
            "very low",
            "very-low"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },

    "LOW": {
        "aliases": [
            "low"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },

    "IN_RANGE": {
        "aliases": [
            "in range",
            "in-range",
            "time in range"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },

    "HIGH": {
        "aliases": [
            "high"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },

    "VERY_HIGH": {
        "aliases": [
            "very high",
            "very-high"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },

    "GMI": {
        "aliases": [
            "glucose management indicator",
            "gmi"
        ],
        "unit": "%",
        "type": "METRIC"
    },

    "CV": {
        "aliases": [
            "glucose variability",
            "coefficient of variation",
            "percent coefficient of variation",
            "cv"
        ],
        "unit": "%",
        "type": "METRIC"
    },

    "ACTIVE_TIME": {
        "aliases": [
            "time cgm active",
            "time cgM active",
            "cgm active",
            "active time",
            "sensor active"
        ],
        "unit": "%",
        "type": "METRIC"
    },

    "AVG_GLUCOSE": {
        "aliases": [
            "average glucose",
            "mean glucose"
        ],
        "unit": "GLUCOSE",
        "type": "METRIC"
    }
}


# ============================================================
# DATE PATTERNS
# ============================================================

MONTHS = (
    "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
)

DATE_PATTERN = re.compile(
    rf"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)?"
    rf"\s*(\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})"
)


# ============================================================
# NUMERIC PATTERN
# ============================================================

NUMBER_PATTERN = re.compile(
    r"(?P<operator>[<>≤≥]?)"
    r"\s*"
    r"(?P<number>\d{1,3}(?:[.,]\d{1,2})?)"
    r"\s*"
    r"(?P<percent>%?)"
)


# ============================================================
# HELPERS
# ============================================================

def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = text.lower()

    replacements = {
        "–": "-",
        "—": "-",
        "−": "-",
        "\u00a0": " ",
        "\u202f": " ",
    }

    for a, b in replacements.items():
        text = text.replace(a, b)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def safe_float(value: str) -> Optional[float]:
    try:
        value = value.replace(",", ".")
        return float(value)
    except Exception:
        return None


def bbox_union(boxes: List[List[float]]) -> List[float]:
    if not boxes:
        return [0, 0, 0, 0]

    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def bbox_center(bbox: List[float]) -> Tuple[float, float]:
    return (
        (bbox[0] + bbox[2]) / 2,
        (bbox[1] + bbox[3]) / 2
    )


def bbox_distance(a: List[float], b: List[float]) -> float:
    ax, ay = bbox_center(a)
    bx, by = bbox_center(b)

    return math.sqrt(
        ((ax - bx) ** 2) +
        ((ay - by) ** 2)
    )


def vertical_distance(a: List[float], b: List[float]) -> float:
    _, ay = bbox_center(a)
    _, by = bbox_center(b)
    return abs(ay - by)


def horizontal_distance(a: List[float], b: List[float]) -> float:
    ax, _ = bbox_center(a)
    bx, _ = bbox_center(b)
    return abs(ax - bx)


def is_zero(value: Optional[float]) -> bool:
    return value is not None and abs(value) < 0.00001


# ============================================================
# UNIVERSAL PARSER
# ============================================================

class UniversalCGMParser:

    def __init__(self):
        self.pages = []
        self.words = []
        self.lines = []
        self.regions = []
        self.candidates = []
        self.anchors = []
        self.associations = []

    # --------------------------------------------------------
    # MAIN
    # --------------------------------------------------------

    def parse(self, pdf_bytes: bytes) -> Dict[str, Any]:

        self._reset()

        self._ingest_pdf(pdf_bytes)
        self._build_lines()
        self._build_regions()
        self._extract_candidates()
        self._extract_anchors()

        range_values = self._pair_range_components()
        metric_values = self._pair_standard_metrics()

        actual_components = {
            "VERY_LOW": range_values.get("VERY_LOW"),
            "LOW": range_values.get("LOW"),
            "IN_RANGE": range_values.get("IN_RANGE"),
            "HIGH": range_values.get("HIGH"),
            "VERY_HIGH": range_values.get("VERY_HIGH"),

            "GMI": metric_values.get("GMI"),
            "CV": metric_values.get("CV"),
            "ACTIVE_TIME": metric_values.get("ACTIVE_TIME"),
            "AVG_GLUCOSE": metric_values.get("AVG_GLUCOSE"),
        }

        derived_metrics = self._derive_metrics(actual_components)

        reporting_period = self._extract_reporting_period()

        validation_warnings = self._validate(
            actual_components,
            derived_metrics
        )

        review_reasons = self._review_reasons(
            actual_components,
            derived_metrics,
            reporting_period
        )

        status = (
            "SUCCESS"
            if not review_reasons
            else "MANUAL_REVIEW"
        )

        return {
            "status": status,

            "reporting_period": reporting_period,

            "actual_components": actual_components,

            "derived_metrics": derived_metrics,

            "validation_warnings": validation_warnings,

            "review_reasons": review_reasons,

            "debug_raw_data": {
                "pages": len(self.pages),

                "raw_words": self.words[:500],

                "lines": self.lines,

                "regions": self.regions,

                "numeric_candidates": self.candidates,

                "anchors": self.anchors,

                "associations": self.associations,
            }
        }

    # --------------------------------------------------------
    # RESET
    # --------------------------------------------------------

    def _reset(self):

        self.pages = []
        self.words = []
        self.lines = []
        self.regions = []
        self.candidates = []
        self.anchors = []
        self.associations = []

    # --------------------------------------------------------
    # PDF INGESTION
    # --------------------------------------------------------

    def _ingest_pdf(self, pdf_bytes: bytes):

        try:
            document = fitz.open(
                stream=pdf_bytes,
                filetype="pdf"
            )
        except Exception as e:
            raise ValueError(
                f"Cannot open PDF: {str(e)}"
            )

        for page_number, page in enumerate(document):

            page_words = page.get_text("words")

            page_data = {
                "page": page_number + 1,
                "width": page.rect.width,
                "height": page.rect.height,
            }

            self.pages.append(page_data)

            for index, item in enumerate(page_words):

                if len(item) < 8:
                    continue

                x0, y0, x1, y1, text, block_no, line_no, word_no = item[:8]

                if not text or not text.strip():
                    continue

                self.words.append({
                    "page": page_number + 1,
                    "index": index,
                    "x0": float(x0),
                    "y0": float(y0),
                    "x1": float(x1),
                    "y1": float(y1),
                    "bbox": [
                        float(x0),
                        float(y0),
                        float(x1),
                        float(y1)
                    ],
                    "text": text.strip(),
                    "normalized": normalize_text(text),
                    "block_no": block_no,
                    "line_no": line_no,
                    "word_no": word_no
                })

        document.close()

    # --------------------------------------------------------
    # BUILD LINES
    # --------------------------------------------------------

    def _build_lines(self):

        grouped = {}

        for word in self.words:

            key = (
                word["page"],
                word["block_no"],
                word["line_no"]
            )

            grouped.setdefault(key, []).append(word)

        lines = []

        line_id = 0

        for key, words in grouped.items():

            words = sorted(
                words,
                key=lambda w: w["x0"]
            )

            text = " ".join(
                w["text"] for w in words
            )

            bbox = bbox_union(
                [w["bbox"] for w in words]
            )

            lines.append({
                "line_id": line_id,
                "page": key[0],
                "block_no": key[1],
                "line_no": key[2],
                "text": text,
                "normalized": normalize_text(text),
                "bbox": bbox,
                "words": words
            })

            line_id += 1

        self.lines = sorted(
            lines,
            key=lambda x: (
                x["page"],
                x["bbox"][1],
                x["bbox"][0]
            )
        )

    # --------------------------------------------------------
    # BUILD SEMANTIC REGIONS
    # --------------------------------------------------------

    def _build_regions(self):

        self.regions = []

        for page_number in range(
            1,
            len(self.pages) + 1
        ):

            page_lines = [
                l for l in self.lines
                if l["page"] == page_number
            ]

            if not page_lines:
                continue

            current = []
            region_id = 0

            for line in page_lines:

                if not current:
                    current = [line]
                    continue

                previous = current[-1]

                gap = (
                    line["bbox"][1]
                    -
                    previous["bbox"][3]
                )

                same_block = (
                    line["block_no"]
                    ==
                    previous["block_no"]
                )

                if gap <= 45 or same_block:
                    current.append(line)
                else:
                    self._save_region(
                        page_number,
                        region_id,
                        current
                    )

                    region_id += 1
                    current = [line]

            if current:
                self._save_region(
                    page_number,
                    region_id,
                    current
                )

    def _save_region(
        self,
        page_number: int,
        region_id: int,
        lines: List[Dict[str, Any]]
    ):

        text = " ".join(
            l["text"] for l in lines
        )

        normalized = normalize_text(text)

        bbox = bbox_union(
            [l["bbox"] for l in lines]
        )

        region_type = self._classify_region(
            normalized
        )

        self.regions.append({
            "region_id": (
                f"{page_number}-{region_id}"
            ),
            "page": page_number,
            "text": text,
            "normalized": normalized,
            "bbox": bbox,
            "type": region_type,
            "line_ids": [
                l["line_id"]
                for l in lines
            ]
        })

    # --------------------------------------------------------
    # REGION CLASSIFICATION
    # --------------------------------------------------------

    def _classify_region(
        self,
        text: str
    ) -> str:

        goal_terms = [
            "goal:",
            "goals:",
            "goal ",
            "target:",
            "target range",
            "recommended",
            "recommendation",
            "reference",
            "desired",
            "clinical target",
            "glucose ranges goals",
        ]

        for term in goal_terms:
            if term in text:
                return "GOAL"

        actual_terms = [
            "very low",
            "very high",
            "in range",
            "high",
            "low",
        ]

        hits = sum(
            1 for term in actual_terms
            if term in text
        )

        if hits >= 1:
            return "ACTUAL_RANGE"

        return "NEUTRAL"

    # --------------------------------------------------------
    # NUMERIC CANDIDATES
    # --------------------------------------------------------

    def _extract_candidates(self):

        candidates = []

        candidate_id = 0

        for line in self.lines:

            text = line["text"]
            normalized = line["normalized"]

            if self._is_descriptive_line(
                normalized
            ):
                continue

            # Do not parse obvious date lines
            if re.search(
                r"\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b",
                text
            ):
                continue

            for match in NUMBER_PATTERN.finditer(text):

                raw_number = match.group(
                    "number"
                )

                operator = match.group(
                    "operator"
                )

                percent = match.group(
                    "percent"
                )

                value = safe_float(
                    raw_number
                )

                if value is None:
                    continue

                # Find exact word tokens overlapping
                # the numeric match.
                token_boxes = []

                char_start = match.start()
                char_end = match.end()

                reconstructed = ""

                token_positions = []

                cursor = 0

                for word in line["words"]:

                    word_text = word["text"]

                    start = text.find(
                        word_text,
                        cursor
                    )

                    if start < 0:
                        continue

                    end = start + len(
                        word_text
                    )

                    cursor = end

                    token_positions.append(
                        (
                            start,
                            end,
                            word
                        )
                    )

                for start, end, word in token_positions:

                    if (
                        end > char_start
                        and
                        start < char_end
                    ):
                        token_boxes.append(
                            word["bbox"]
                        )

                if not token_boxes:
                    bbox = line["bbox"]
                else:
                    bbox = bbox_union(
                        token_boxes
                    )

                context = self._candidate_context(
                    line,
                    match
                )

                # Ignore explicit goals.
                if context == "GOAL":
                    continue

                # Operators are almost always goals
                # in this type of report.
                if operator:
                    continue

                unit = "%"

                # Check for glucose units nearby.
                nearby_text = normalize_text(
                    text
                )

                if (
                    "mmol/l" in nearby_text
                    or "mmol" in nearby_text
                    or "mg/dl" in nearby_text
                    or "mg dl" in nearby_text
                ):
                    if "%" not in match.group(0):
                        unit = "GLUCOSE"

                candidates.append({
                    "candidate_id": candidate_id,
                    "value": value,
                    "unit": unit,
                    "raw": match.group(0).strip(),
                    "bbox": bbox,
                    "page": line["page"],
                    "line_id": line["line_id"],
                    "region_id": self._region_for_line(
                        line["line_id"]
                    ),
                    "line_text": text,
                    "context_type": context,
                    "has_percent": bool(percent),
                    "operator": operator,
                })

                candidate_id += 1

        self.candidates = candidates

    # --------------------------------------------------------
    # CANDIDATE CONTEXT
    # --------------------------------------------------------

    def _candidate_context(
        self,
        line: Dict[str, Any],
        match
    ) -> str:

        region_id = self._region_for_line(
            line["line_id"]
        )

        region = next(
            (
                r for r in self.regions
                if r["region_id"] == region_id
            ),
            None
        )

        if region:
            if region["type"] == "GOAL":
                return "GOAL"

            if region["type"] == "ACTUAL_RANGE":
                return "ACTUAL_RANGE"

        return "UNKNOWN"

    # --------------------------------------------------------
    # DESCRIPTIVE TEXT
    # --------------------------------------------------------

    def _is_descriptive_line(
        self,
        normalized: str
    ) -> bool:

        patterns = [
            "1% of time in ranges",
            "about",
            "defined as percent",
            "median",
            "percentile",
            "printing date",
            "reporting period",
        ]

        for p in patterns:
            if p in normalized:
                return True

        return False

    # --------------------------------------------------------
    # REGION FOR LINE
    # --------------------------------------------------------

    def _region_for_line(
        self,
        line_id: int
    ) -> Optional[str]:

        for region in self.regions:

            if line_id in region["line_ids"]:
                return region["region_id"]

        return None

    # --------------------------------------------------------
    # EXTRACT ANCHORS
    # --------------------------------------------------------

    def _extract_anchors(self):

        anchors = []

        anchor_id = 0

        for line in self.lines:

            normalized = line["normalized"]

            for metric, definition in METRICS.items():

                aliases = definition["aliases"]

                matched_alias = None

                for alias in aliases:

                    if alias in normalized:
                        matched_alias = alias
                        break

                if not matched_alias:
                    continue

                # Find bbox of alias approximately.
                alias_bbox = self._find_alias_bbox(
                    line,
                    matched_alias
                )

                anchors.append({
                    "anchor_id": anchor_id,
                    "metric": metric,
                    "alias": matched_alias,
                    "page": line["page"],
                    "line_id": line["line_id"],
                    "region_id": self._region_for_line(
                        line["line_id"]
                    ),
                    "bbox": alias_bbox,
                    "line_text": line["text"],
                })

                anchor_id += 1

        self.anchors = anchors

    # --------------------------------------------------------
    # FIND ALIAS BBOX
    # --------------------------------------------------------

    def _find_alias_bbox(
        self,
        line: Dict[str, Any],
        alias: str
    ) -> List[float]:

        alias_normalized = normalize_text(
            alias
        )

        matched_words = []

        for word in line["words"]:

            if (
                normalize_text(word["text"])
                in alias_normalized
                or
                normalize_text(word["text"])
                in alias_normalized.split()
            ):
                matched_words.append(
                    word["bbox"]
                )

        if matched_words:
            return bbox_union(
                matched_words
            )

        return line["bbox"]

    # ========================================================
    # RANGE PAIRING
    # ========================================================

    def _pair_range_components(self):

        range_metrics = [
            "VERY_LOW",
            "LOW",
            "IN_RANGE",
            "HIGH",
            "VERY_HIGH"
        ]

        anchors = [
            a for a in self.anchors
            if a["metric"] in range_metrics
        ]

        candidates = [
            c for c in self.candidates
            if c["unit"] == "%"
            and c["context_type"] == "ACTUAL_RANGE"
        ]

        result = {
            metric: None
            for metric in range_metrics
        }

        if not anchors or not candidates:
            return result

        # ----------------------------------------------------
        # STEP 1:
        # Strong same-line pairing
        # ----------------------------------------------------

        used_candidates = set()
        used_metrics = set()

        for anchor in anchors:

            best = None
            best_score = float("inf")

            for candidate in candidates:

                if candidate["candidate_id"] in used_candidates:
                    continue

                if candidate["page"] != anchor["page"]:
                    continue

                if candidate["line_id"] == anchor["line_id"]:

                    score = horizontal_distance(
                        anchor["bbox"],
                        candidate["bbox"]
                    )

                    # Very strong preference for same line.
                    score *= 0.1

                    if score < best_score:
                        best_score = score
                        best = candidate

            if best is not None:

                result[
                    anchor["metric"]
                ] = best["value"]

                used_candidates.add(
                    best["candidate_id"]
                )

                used_metrics.add(
                    anchor["metric"]
                )

                self.associations.append({
                    "type": "RANGE",
                    "metric": anchor["metric"],
                    "candidate_id": best["candidate_id"],
                    "value": best["value"],
                    "method": "same_line"
                })

        # ----------------------------------------------------
        # STEP 2:
        # Pair remaining items using geometry.
        # ----------------------------------------------------

        remaining_anchors = [
            a for a in anchors
            if a["metric"] not in used_metrics
        ]

        remaining_candidates = [
            c for c in candidates
            if c["candidate_id"] not in used_candidates
        ]

        pairs = []

        for anchor in remaining_anchors:

            for candidate in remaining_candidates:

                if candidate["page"] != anchor["page"]:
                    continue

                dy = vertical_distance(
                    anchor["bbox"],
                    candidate["bbox"]
                )

                dx = horizontal_distance(
                    anchor["bbox"],
                    candidate["bbox"]
                )

                if dy > 100:
                    continue

                score = (
                    dy
                    +
                    (dx * 0.25)
                )

                # Same region gets a bonus.
                if (
                    anchor["region_id"]
                    ==
                    candidate["region_id"]
                ):
                    score -= 25

                pairs.append({
                    "metric": anchor["metric"],
                    "candidate_id": candidate["candidate_id"],
                    "value": candidate["value"],
                    "score": score,
                    "anchor": anchor,
                    "candidate": candidate
                })

        # ----------------------------------------------------
        # Greedy 1:1 assignment.
        # ----------------------------------------------------

        pairs.sort(
            key=lambda x: x["score"]
        )

        for pair in pairs:

            metric = pair["metric"]
            candidate_id = pair["candidate_id"]

            if metric in used_metrics:
                continue

            if candidate_id in used_candidates:
                continue

            # Prevent absurdly distant matches.
            if pair["score"] > 120:
                continue

            result[metric] = pair["value"]

            used_metrics.add(metric)
            used_candidates.add(candidate_id)

            self.associations.append({
                "type": "RANGE",
                "metric": metric,
                "candidate_id": candidate_id,
                "value": pair["value"],
                "method": "geometric_1_to_1",
                "score": round(
                    pair["score"],
                    2
                )
            })

        return result

    # ========================================================
    # STANDARD METRICS
    # ========================================================

    def _pair_standard_metrics(self):

        target_metrics = [
            "GMI",
            "CV",
            "ACTIVE_TIME",
            "AVG_GLUCOSE"
        ]

        result = {
            metric: None
            for metric in target_metrics
        }

        for metric in target_metrics:

            anchors = [
                a for a in self.anchors
                if a["metric"] == metric
            ]

            if not anchors:
                continue

            best_pair = None
            best_score = float("inf")

            for anchor in anchors:

                for candidate in self.candidates:

                    if candidate["page"] != anchor["page"]:
                        continue

                    if candidate["context_type"] == "GOAL":
                        continue

                    if metric == "AVG_GLUCOSE":

                        if candidate["unit"] != "GLUCOSE":
                            continue

                    else:

                        if candidate["unit"] != "%":
                            continue

                    dy = vertical_distance(
                        anchor["bbox"],
                        candidate["bbox"]
                    )

                    dx = horizontal_distance(
                        anchor["bbox"],
                        candidate["bbox"]
                    )

                    if dy > 150:
                        continue

                    score = (
                        dy
                        +
                        dx * 0.20
                    )

                    if (
                        anchor["region_id"]
                        ==
                        candidate["region_id"]
                    ):
                        score -= 20

                    # Same line is extremely strong.
                    if (
                        anchor["line_id"]
                        ==
                        candidate["line_id"]
                    ):
                        score -= 50

                    if score < best_score:
                        best_score = score
                        best_pair = (
                            anchor,
                            candidate
                        )

            if best_pair:

                anchor, candidate = best_pair

                result[metric] = candidate["value"]

                self.associations.append({
                    "type": "STANDARD",
                    "metric": metric,
                    "candidate_id": candidate[
                        "candidate_id"
                    ],
                    "value": candidate["value"],
                    "score": round(
                        best_score,
                        2
                    )
                })

        return result

    # ========================================================
    # DERIVED METRICS
    # ========================================================

    def _derive_metrics(
        self,
        actual: Dict[str, Any]
    ):

        very_low = actual.get(
            "VERY_LOW"
        )

        low = actual.get(
            "LOW"
        )

        in_range = actual.get(
            "IN_RANGE"
        )

        high = actual.get(
            "HIGH"
        )

        very_high = actual.get(
            "VERY_HIGH"
        )

        tbr = None
        tir = None
        tar = None

        if (
            very_low is not None
            and
            low is not None
        ):
            tbr = round(
                very_low + low,
                2
            )

        if in_range is not None:
            tir = round(
                in_range,
                2
            )

        if (
            high is not None
            and
            very_high is not None
        ):
            tar = round(
                high + very_high,
                2
            )

        return {
            "TBR": tbr,
            "TIR": tir,
            "TAR": tar
        }

    # ========================================================
    # VALIDATION
    # ========================================================

    def _validate(
        self,
        actual: Dict[str, Any],
        derived: Dict[str, Any]
    ) -> List[str]:

        warnings = []

        # -----------------------------------------------
        # Range components
        # -----------------------------------------------

        range_values = [
            actual.get("VERY_LOW"),
            actual.get("LOW"),
            actual.get("IN_RANGE"),
            actual.get("HIGH"),
            actual.get("VERY_HIGH")
        ]

        if all(
            value is not None
            for value in range_values
        ):

            total = sum(range_values)

            if abs(total - 100) > 2:
                warnings.append(
                    f"Range components sum to "
                    f"{total}%, expected approximately 100%"
                )

            for value in range_values:

                if value < 0 or value > 100:
                    warnings.append(
                        "Range component outside 0-100%"
                    )

        # -----------------------------------------------
        # GMI
        # -----------------------------------------------

        gmi = actual.get("GMI")

        if gmi is not None:

            if gmi <= 0 or gmi > 20:
                warnings.append(
                    "GMI value outside plausible range"
                )

        # -----------------------------------------------
        # CV
        # -----------------------------------------------

        cv = actual.get("CV")

        if cv is not None:

            if cv < 0 or cv > 200:
                warnings.append(
                    "CV value outside plausible range"
                )

        # -----------------------------------------------
        # Active time
        # -----------------------------------------------

        active = actual.get(
            "ACTIVE_TIME"
        )

        if active is not None:

            if active < 0 or active > 100:
                warnings.append(
                    "Active time outside 0-100%"
                )

        return warnings

    # ========================================================
    # REVIEW REASONS
    # ========================================================

    def _review_reasons(
        self,
        actual: Dict[str, Any],
        derived: Dict[str, Any],
        reporting_period: Dict[str, Any]
    ) -> List[str]:

        reasons = []

        range_metrics = [
            "VERY_LOW",
            "LOW",
            "IN_RANGE",
            "HIGH",
            "VERY_HIGH"
        ]

        missing_range = [
            metric
            for metric in range_metrics
            if actual.get(metric) is None
        ]

        if missing_range:

            reasons.append(
                "Missing actual range components: "
                +
                ", ".join(missing_range)
            )

        standard_metrics = [
            "GMI",
            "CV",
            "ACTIVE_TIME",
            "AVG_GLUCOSE"
        ]

        for metric in standard_metrics:

            if actual.get(metric) is None:

                reasons.append(
                    f"Missing {metric}"
                )

        if derived["TBR"] is None:
            reasons.append(
                "TBR cannot be derived"
            )

        if derived["TIR"] is None:
            reasons.append(
                "TIR cannot be derived"
            )

        if derived["TAR"] is None:
            reasons.append(
                "TAR cannot be derived"
            )

        if (
            reporting_period.get("start")
            is None
            or
            reporting_period.get("end")
            is None
        ):
            reasons.append(
                "Reporting period not detected"
            )

        return reasons

    # ========================================================
    # REPORTING PERIOD
    # ========================================================

    def _extract_reporting_period(self):

        dates = []

        # Use raw word order.
        for page_number in range(
            1,
            len(self.pages) + 1
        ):

            page_words = [
                w for w in self.words
                if w["page"] == page_number
            ]

            page_words = sorted(
                page_words,
                key=lambda w: (
                    w["y0"],
                    w["x0"]
                )
            )

            page_text = " ".join(
                w["text"]
                for w in page_words
            )

            matches = DATE_PATTERN.findall(
                page_text
            )

            for date_text in matches:

                if date_text not in dates:
                    dates.append(
                        date_text
                    )

        if len(dates) < 2:

            return {
                "start": None,
                "end": None
            }

        parsed = []

        for date_text in dates:

            try:

                dt = datetime.strptime(
                    date_text,
                    "%d %b %Y"
                )

                parsed.append(
                    (
                        dt,
                        date_text
                    )
                )

            except Exception:
                pass

        if len(parsed) >= 2:

            parsed.sort(
                key=lambda x: x[0]
            )

            return {
                "start": parsed[0][1],
                "end": parsed[-1][1]
            }

        return {
            "start": dates[0],
            "end": dates[1]
        }


# ============================================================
# SUPABASE
# ============================================================

def supabase_headers():

    return {
        "apikey": SUPABASE_KEY,
        "Authorization": (
            f"Bearer {SUPABASE_KEY}"
        ),
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }


def supabase_insert(
    table: str,
    data: Dict[str, Any]
):

    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    url = (
        f"{SUPABASE_URL}"
        f"/rest/v1/{table}"
    )

    try:

        response = requests.post(
            url,
            headers=supabase_headers(),
            json=data,
            timeout=15
        )

        if response.status_code >= 400:
            return {
                "status": "ERROR",
                "http_status": response.status_code,
                "response": response.text
            }

        return response.json()

    except Exception as e:

        return {
            "status": "ERROR",
            "error": str(e)
        }


# ============================================================
# DEBUG ENDPOINT
# ============================================================

@app.post(
    "/debug-parser-test"
)
async def debug_parser_test(
    file: UploadFile = File(...)
):

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="File name missing"
        )

    if not file.filename.lower().endswith(
        ".pdf"
    ):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are supported"
        )

    pdf_bytes = await file.read()

    if len(pdf_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large. "
                f"Maximum is {MAX_FILE_SIZE_MB} MB."
            )
        )

    try:

        parser = UniversalCGMParser()

        result = parser.parse(
            pdf_bytes
        )

        return result

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail={
                "status": "ERROR",
                "error": str(e)
            }
        )


# ============================================================
# UPLOAD ENDPOINT
# ============================================================

@app.post(
    "/upload"
)
async def upload_pdf(
    file: UploadFile = File(...)
):

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="File name missing"
        )

    if not file.filename.lower().endswith(
        ".pdf"
    ):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are supported"
        )

    pdf_bytes = await file.read()

    if len(pdf_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large. "
                f"Maximum is {MAX_FILE_SIZE_MB} MB."
            )
        )

    parser = UniversalCGMParser()

    try:

        report = parser.parse(
            pdf_bytes
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    # --------------------------------------------------------
    # IMPORTANT:
    # The original PDF is NOT stored here.
    # Only the parsed standardized data should persist.
    # --------------------------------------------------------

    patient_id = None

    # For MVP:
    # patient_id should later come from authenticated
    # physician/patient workflow rather than being hardcoded.

    return {
        "status": report["status"],
        "filename": file.filename,
        "patient_id": patient_id,
        "report": report
    }


# ============================================================
# FRONTEND
# ============================================================

HTML = """
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>Universal CGM AGP Parser</title>

<style>

body {
    font-family: Arial, sans-serif;
    background: #f5f7fa;
    margin: 0;
    padding: 30px;
}

.container {
    max-width: 1100px;
    margin: auto;
}

.card {
    background: white;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 20px;
    box-shadow:
        0 2px 10px rgba(0,0,0,0.08);
}

h1 {
    margin-top: 0;
}

button {
    padding: 12px 20px;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    background: #222;
    color: white;
    font-size: 15px;
}

input[type=file] {
    margin: 15px 0;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: #111;
    color: #eee;
    padding: 20px;
    border-radius: 8px;
    overflow-x: auto;
}

.grid {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
}

.metric {
    background: #f0f2f5;
    padding: 16px;
    border-radius: 8px;
}

.metric strong {
    display: block;
    font-size: 12px;
    color: #666;
    margin-bottom: 5px;
}

.metric span {
    font-size: 24px;
    font-weight: bold;
}

.success {
    color: green;
    font-weight: bold;
}

.review {
    color: #b36b00;
    font-weight: bold;
}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>Universal CGM / AGP Parser</h1>

<p>
Upload any supported CGM AGP PDF.
The parser attempts to identify the
standardized glucose metrics without relying
on a manufacturer-specific parser.
</p>

<input
    id="file"
    type="file"
    accept=".pdf"
>

<br>

<button onclick="parseFile()">
    Parse PDF
</button>

</div>


<div id="result"></div>


<div class="card">

<h2>Debug JSON</h2>

<pre id="json">
No result yet.
</pre>

</div>

</div>


<script>

async function parseFile() {

    const fileInput =
        document.getElementById("file");

    if (!fileInput.files.length) {

        alert("Choose a PDF first.");

        return;
    }

    const file =
        fileInput.files[0];

    const formData =
        new FormData();

    formData.append(
        "file",
        file
    );

    document.getElementById(
        "result"
    ).innerHTML =
        "<div class='card'>Parsing...</div>";

    try {

        const response =
            await fetch(
                "/debug-parser-test",
                {
                    method: "POST",
                    body: formData
                }
            );

        const data =
            await response.json();

        renderResult(data);

        document.getElementById(
            "json"
        ).textContent =
            JSON.stringify(
                data,
                null,
                2
            );

    } catch (error) {

        document.getElementById(
            "result"
        ).innerHTML =
            "<div class='card'>" +
            "<b>Error:</b> " +
            error +
            "</div>";

    }

}


function renderResult(data) {

    if (!data.actual_components) {

        document.getElementById(
            "result"
        ).innerHTML =
            "<div class='card'>" +
            "<h2>Parser Error</h2>" +
            "<pre>" +
            JSON.stringify(
                data,
                null,
                2
            ) +
            "</pre>" +
            "</div>";

        return;
    }

    const a =
        data.actual_components;

    const d =
        data.derived_metrics;

    const statusClass =
        data.status === "SUCCESS"
            ? "success"
            : "review";

    let html = "";

    html +=
        "<div class='card'>";

    html +=
        "<h2>Parser result</h2>";

    html +=
        "<p class='" +
        statusClass +
        "'>" +
        data.status +
        "</p>";

    html +=
        "<p>" +
        "Reporting period: " +
        (data.reporting_period.start || "?") +
        " → " +
        (data.reporting_period.end || "?") +
        "</p>";

    html +=
        "<h3>Glucose metrics</h3>";

    html +=
        "<div class='grid'>";

    html += metric(
        "Average glucose",
        a.AVG_GLUCOSE,
        "mmol/L"
    );

    html += metric(
        "GMI",
        a.GMI,
        "%"
    );

    html += metric(
        "CV",
        a.CV,
        "%"
    );

    html += metric(
        "CGM active",
        a.ACTIVE_TIME,
        "%"
    );

    html += "</div>";

    html +=
        "<h3>Actual glucose ranges</h3>";

    html +=
        "<div class='grid'>";

    html += metric(
        "Very low",
        a.VERY_LOW,
        "%"
    );

    html += metric(
        "Low",
        a.LOW,
        "%"
    );

    html += metric(
        "In range",
        a.IN_RANGE,
        "%"
    );

    html += metric(
        "High",
        a.HIGH,
        "%"
    );

    html += metric(
        "Very high",
        a.VERY_HIGH,
        "%"
    );

    html += "</div>";

    html +=
        "<h3>Standardized</h3>";

    html +=
        "<div class='grid'>";

    html += metric(
        "TBR",
        d.TBR,
        "%"
    );

    html += metric(
        "TIR",
        d.TIR,
        "%"
    );

    html += metric(
        "TAR",
        d.TAR,
        "%"
    );

    html += "</div>";

    if (
        data.review_reasons &&
        data.review_reasons.length
    ) {

        html +=
            "<h3>Manual review</h3>";

        html += "<ul>";

        data.review_reasons.forEach(
            function(reason) {

                html +=
                    "<li>" +
                    reason +
                    "</li>";

            }
        );

        html += "</ul>";
    }

    html += "</div>";

    document.getElementById(
        "result"
    ).innerHTML = html;
}


function metric(
    label,
    value,
    unit
) {

    let display =
        value === null ||
        value === undefined
            ? "—"
            : value + " " + unit;

    return (
        "<div class='metric'>" +
        "<strong>" +
        label +
        "</strong>" +
        "<span>" +
        display +
        "</span>" +
        "</div>"
    );
}

</script>

</body>

</html>
"""


# ============================================================
# HOME
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
async def home():

    return HTML


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get(
    "/health"
)
async def health():

    return {
        "status": "ok",
        "parser": "UniversalCGMParser",
        "version": "2.0.0"
    }


# ============================================================
# RUN:
#
# uvicorn app:app --reload
# ============================================================
