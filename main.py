from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import requests
import pymupdf as fitz
import re
from datetime import datetime
from typing import Optional


# ============================================================
# CONFIG
# ============================================================

app = FastAPI(title="Universal CGM AGP Parser")


# Development / MVP configuration.
# For production these should be moved to environment variables.
SUPABASE_URL = "https://tuhgurlibsaqqxrmhgdr.supabase.co"

SUPABASE_KEY = "sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39"

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


# ============================================================
# UNIVERSAL CGM SEMANTIC ONTOLOGY
# ============================================================

METRIC_ONTOLOGY = {

    "VERY_LOW": {
        "aliases": [
            "very low",
            "very low glucose",
            "extremely low",
        ],
        "expected_unit": "%",
        "valid_range": (0, 100),
    },

    "LOW": {
        "aliases": [
            "low",
            "low glucose",
            "below range",
        ],
        "expected_unit": "%",
        "valid_range": (0, 100),
    },

    "IN_RANGE": {
        "aliases": [
            "in range",
            "time in range",
            "tir",
            "within range",
            "within target",
            "target range",
            "in target",
            "u ciljnom opsegu",
            "u opsegu",
        ],
        "expected_unit": "%",
        "valid_range": (0, 100),
    },

    "HIGH": {
        "aliases": [
            "high",
            "high glucose",
            "above range",
        ],
        "expected_unit": "%",
        "valid_range": (0, 100),
    },

    "VERY_HIGH": {
        "aliases": [
            "very high",
            "very high glucose",
            "extremely high",
        ],
        "expected_unit": "%",
        "valid_range": (0, 100),
    },

    "GMI": {
        "aliases": [
            "glucose management indicator",
            "gmi",
        ],
        "expected_unit": "%",
        "valid_range": (4, 15),
    },

    "CV": {
        "aliases": [
            "glucose variability",
            "coefficient of variation",
            "percent coefficient of variation",
            "cv",
        ],
        "expected_unit": "%",
        "valid_range": (0, 100),
    },

    "ACTIVE_TIME": {
        "aliases": [
            "time cgm active",
            "cgm active",
            "sensor active",
            "time active",
            "active time",
            "percent time active",
        ],
        "expected_unit": "%",
        "valid_range": (0, 100),
    },

    "AVG_GLUCOSE": {
        "aliases": [
            "average glucose",
            "mean glucose",
            "average sensor glucose",
            "mean sensor glucose",
        ],
        "expected_unit": "GLUCOSE",
        "valid_range": (1, 35),
    },
}


# ============================================================
# GENERAL CONSTANTS
# ============================================================

GOAL_TERMS = [
    "goal",
    "goals",
    "target",
    "recommended",
    "recommendation",
    "reference",
    "desired",
    "clinical target",
    "clinical targets",
    "aim",
]

DESCRIPTIVE_TERMS = [
    "median",
    "percentile",
    "percentiles",
    "of time in ranges",
    "defined as",
]

GLUCOSE_UNITS = [
    "mmol/l",
    "mmol",
    "mg/dl",
]

DATE_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
)


# ============================================================
# HELPERS
# ============================================================

def normalize_text(text: str) -> str:
    text = text.lower()
    text = text.replace("–", "-")
    text = text.replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_float(value: str) -> float:
    return float(value.replace(",", "."))


def bbox_from_words(words):
    if not words:
        return {
            "x0": 0,
            "y0": 0,
            "x1": 0,
            "y1": 0,
            "cx": 0,
            "cy": 0,
        }

    x0 = min(w["x0"] for w in words)
    y0 = min(w["y0"] for w in words)
    x1 = max(w["x1"] for w in words)
    y1 = max(w["y1"] for w in words)

    return {
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "cx": (x0 + x1) / 2,
        "cy": (y0 + y1) / 2,
    }


def bbox_distance(a, b):
    dx = abs(a["cx"] - b["cx"])
    dy = abs(a["cy"] - b["cy"])
    return dx + (dy * 2)


def alias_matches(line_text: str, alias: str) -> bool:
    """
    Semantic matching with word boundaries where possible.
    Prevents 'low' from accidentally matching arbitrary substrings.
    """

    alias = normalize_text(alias)

    pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"

    return re.search(pattern, line_text) is not None


def contains_goal_context(text: str) -> bool:
    text = normalize_text(text)

    return any(
        alias_matches(text, term)
        for term in GOAL_TERMS
    )


def contains_descriptive_context(text: str) -> bool:
    text = normalize_text(text)

    return any(
        alias_matches(text, term)
        for term in DESCRIPTIVE_TERMS
    )


def metric_alias_match(line_text: str, aliases):
    for alias in aliases:
        if alias_matches(line_text, alias):
            return alias

    return None


# ============================================================
# UNIVERSAL PARSER
# ============================================================

class UniversalCGMParser:

    def __init__(self, doc):
        self.doc = doc

        self.pages = []
        self.regions = []
        self.candidates = []
        self.anchors = []
        self.associations = []

        self.results = {
            key: None
            for key in METRIC_ONTOLOGY.keys()
        }

        self.derived_metrics = {
            "TBR": None,
            "TIR": None,
            "TAR": None,
        }

        self.reporting_period = {
            "start": None,
            "end": None,
        }

        self.validation_warnings = []
        self.review_reasons = []

    # --------------------------------------------------------
    # MAIN PIPELINE
    # --------------------------------------------------------

    def parse(self):

        self._ingest_pdf()

        self._build_layout()

        self._extract_candidates()

        self._extract_anchors()

        self._associate_labels_and_values()

        self._derive_standardized_metrics()

        self._validate_cluster()

        self._extract_reporting_period()

        return self._generate_final_report()

    # ========================================================
    # 1. PDF INGESTION
    # ========================================================

    def _ingest_pdf(self):

        for page_number, page in enumerate(self.doc, start=1):

            raw_words = page.get_text("words")

            words = []

            for index, word in enumerate(raw_words):

                text = word[4].strip()

                if not text:
                    continue

                words.append({
                    "index": index,
                    "text": text,

                    "x0": float(word[0]),
                    "y0": float(word[1]),
                    "x1": float(word[2]),
                    "y1": float(word[3]),

                    "cx": (word[0] + word[2]) / 2,
                    "cy": (word[1] + word[3]) / 2,

                    "block": word[5],
                    "line": word[6],
                    "word": word[7],

                    "page": page_number,
                })

            self.pages.append({
                "page_num": page_number,
                "width": page.rect.width,
                "height": page.rect.height,
                "raw_words": words,
            })

    # ========================================================
    # 2. DOCUMENT LAYOUT
    # ========================================================

    def _build_layout(self):

        region_counter = 0

        for page in self.pages:

            words = sorted(
                page["raw_words"],
                key=lambda x: (x["y0"], x["x0"])
            )

            lines_map = {}

            for word in words:

                key = (
                    word["block"],
                    word["line"]
                )

                lines_map.setdefault(key, []).append(word)

            lines = list(lines_map.values())

            lines.sort(
                key=lambda line: (
                    min(w["y0"] for w in line),
                    min(w["x0"] for w in line)
                )
            )

            current_region = []

            for line in lines:

                if not current_region:

                    current_region = [line]
                    continue

                previous_y = min(
                    w["y0"]
                    for w in current_region[-1]
                )

                current_y = min(
                    w["y0"]
                    for w in line
                )

                # Spatial grouping only.
                # It does NOT decide semantic meaning.
                if current_y - previous_y <= 35:

                    current_region.append(line)

                else:

                    region_counter += 1

                    self._register_region(
                        region_counter,
                        current_region,
                        page["page_num"]
                    )

                    current_region = [line]

            if current_region:

                region_counter += 1

                self._register_region(
                    region_counter,
                    current_region,
                    page["page_num"]
                )

    def _register_region(
        self,
        region_id,
        lines,
        page_number
    ):

        words = [
            word
            for line in lines
            for word in line
        ]

        bbox = bbox_from_words(words)

        text = " ".join(
            word["text"]
            for word in words
        )

        self.regions.append({

            "region_id": f"reg_{page_number}_{region_id}",

            "page": page_number,

            "bbox": bbox,

            "text": normalize_text(text),

            "lines": lines,

            "words": words,
        })

    # ========================================================
    # 3. NUMERIC CANDIDATES
    # ========================================================

    def _extract_candidates(self):

        number_pattern = re.compile(
            r"(?P<operator>[<>]?)"
            r"\s*"
            r"(?P<number>\d{1,3}(?:[.,]\d{1,2})?)"
            r"\s*"
            r"(?P<unit>mmol/L|mg/dL|%)?",
            re.IGNORECASE
        )

        for region in self.regions:

            for line in region["lines"]:

                if not line:
                    continue

                line_words = sorted(
                    line,
                    key=lambda w: w["x0"]
                )

                # Build text with deterministic character offsets.
                text_parts = []
                token_spans = []

                cursor = 0

                for word in line_words:

                    if text_parts:
                        cursor += 1

                    start = cursor
                    end = start + len(word["text"])

                    text_parts.append(word["text"])

                    token_spans.append({
                        "start": start,
                        "end": end,
                        "word": word,
                    })

                    cursor = end

                line_text = " ".join(text_parts)
                normalized_line = normalize_text(line_text)

                descriptive = (
                    contains_descriptive_context(
                        normalized_line
                    )
                )

                goal_context = (
                    contains_goal_context(
                        normalized_line
                    )
                )

                for match in number_pattern.finditer(
                    line_text
                ):

                    number_text = match.group("number")

                    try:
                        value = safe_float(number_text)
                    except ValueError:
                        continue

                    operator = match.group("operator")
                    unit_raw = match.group("unit")

                    # ------------------------------------------------
                    # Determine exact numeric token bbox.
                    # ------------------------------------------------

                    match_start = match.start()
                    match_end = match.end()

                    overlapping = []

                    for token in token_spans:

                        if (
                            token["end"] > match_start
                            and token["start"] < match_end
                        ):
                            overlapping.append(
                                token["word"]
                            )

                    if not overlapping:
                        continue

                    bbox = bbox_from_words(
                        overlapping
                    )

                    # ------------------------------------------------
                    # Semantic context.
                    # ------------------------------------------------

                    if goal_context or operator:

                        semantic_role = "GOAL"

                    elif descriptive:

                        semantic_role = "UNKNOWN"

                    else:

                        semantic_role = "ACTUAL"

                    if unit_raw:

                        unit_normalized = unit_raw.lower()

                        if unit_normalized == "%":
                            unit = "%"

                        elif unit_normalized == "mmol/l":
                            unit = "mmol/L"

                        elif unit_normalized == "mg/dl":
                            unit = "mg/dL"

                        else:
                            unit = "UNKNOWN"

                    else:

                        unit = "DECIMAL"

                    self.candidates.append({

                        "value": value,

                        "raw_text": match.group(0),

                        "unit": unit,

                        "operator": operator,

                        "semantic_role": semantic_role,

                        "page": line_words[0]["page"],

                        "region_id": region["region_id"],

                        "line_id": (
                            f"{line_words[0]['block']}_"
                            f"{line_words[0]['line']}"
                        ),

                        "line_text": line_text,

                        "bbox": bbox,

                    })

    # ========================================================
    # 4. SEMANTIC ANCHORS
    # ========================================================

    def _extract_anchors(self):

        for region in self.regions:

            for line in region["lines"]:

                if not line:
                    continue

                line_words = sorted(
                    line,
                    key=lambda w: w["x0"]
                )

                line_text = normalize_text(
                    " ".join(
                        w["text"]
                        for w in line_words
                    )
                )

                line_bbox = bbox_from_words(
                    line_words
                )

                for metric, definition in METRIC_ONTOLOGY.items():

                    alias = metric_alias_match(
                        line_text,
                        definition["aliases"]
                    )

                    if not alias:
                        continue

                    # Avoid duplicate anchor for the same
                    # metric/line.
                    duplicate = any(
                        anchor["metric"] == metric
                        and anchor["page"] == region["page"]
                        and anchor["line_id"] == (
                            f"{line_words[0]['block']}_"
                            f"{line_words[0]['line']}"
                        )
                        for anchor in self.anchors
                    )

                    if duplicate:
                        continue

                    self.anchors.append({

                        "metric": metric,

                        "alias": alias,

                        "page": region["page"],

                        "region_id": region["region_id"],

                        "line_id": (
                            f"{line_words[0]['block']}_"
                            f"{line_words[0]['line']}"
                        ),

                        "line_text": line_text,

                        "bbox": line_bbox,

                    })

    # ========================================================
    # 5. LABEL → VALUE ASSOCIATION
    # ========================================================

    def _associate_labels_and_values(self):

        for anchor in self.anchors:

            metric = anchor["metric"]

            definition = METRIC_ONTOLOGY[metric]

            possible = []

            for candidate in self.candidates:

                # Must be same page.
                if candidate["page"] != anchor["page"]:
                    continue

                # Never associate goal data as patient actual.
                if candidate["semantic_role"] != "ACTUAL":

                    self._record_rejection(
                        anchor,
                        candidate,
                        "NON_ACTUAL_CONTEXT"
                    )

                    continue

                # ------------------------------------------------
                # Unit compatibility
                # ------------------------------------------------

                if metric != "AVG_GLUCOSE":

                    if candidate["unit"] != definition["expected_unit"]:

                        self._record_rejection(
                            anchor,
                            candidate,
                            "WRONG_UNIT"
                        )

                        continue

                else:

                    if candidate["unit"] not in (
                        "mmol/L",
                        "mg/dL",
                        "DECIMAL",
                    ):

                        self._record_rejection(
                            anchor,
                            candidate,
                            "WRONG_GLUCOSE_UNIT"
                        )

                        continue

                # ------------------------------------------------
                # Value range
                # ------------------------------------------------

                minimum, maximum = definition["valid_range"]

                if not (
                    minimum
                    <= candidate["value"]
                    <= maximum
                ):

                    self._record_rejection(
                        anchor,
                        candidate,
                        "OUT_OF_RANGE"
                    )

                    continue

                # ------------------------------------------------
                # Spatial relationship
                # ------------------------------------------------

                y_distance = abs(
                    candidate["bbox"]["cy"]
                    - anchor["bbox"]["cy"]
                )

                x_distance = abs(
                    candidate["bbox"]["cx"]
                    - anchor["bbox"]["cx"]
                )

                if y_distance > 80:
                    continue

                score = 0.0
                reasons = []

                # Same region.
                if (
                    candidate["region_id"]
                    == anchor["region_id"]
                ):
                    score += 0.25
                    reasons.append(
                        "SAME_REGION"
                    )

                # Same line.
                if (
                    candidate["line_id"]
                    == anchor["line_id"]
                ):
                    score += 0.45
                    reasons.append(
                        "SAME_LINE"
                    )

                elif y_distance <= 20:

                    score += 0.25
                    reasons.append(
                        "NEAR_VERTICAL"
                    )

                elif y_distance <= 45:

                    score += 0.15
                    reasons.append(
                        "MULTI_LINE"
                    )

                # Horizontal proximity.
                if x_distance <= 100:

                    score += 0.20
                    reasons.append(
                        "HORIZONTAL_PROXIMITY"
                    )

                elif x_distance <= 250:

                    score += 0.08
                    reasons.append(
                        "MODERATE_HORIZONTAL_PROXIMITY"
                    )

                # Small vertical distance bonus.
                if y_distance <= 10:

                    score += 0.10
                    reasons.append(
                        "VERY_CLOSE"
                    )

                possible.append({

                    "candidate": candidate,

                    "score": round(
                        min(score, 1.0),
                        3
                    ),

                    "distance": bbox_distance(
                        anchor["bbox"],
                        candidate["bbox"]
                    ),

                    "reason": " | ".join(reasons),

                })

            if not possible:
                continue

            # Highest semantic/spatial score first.
            possible.sort(
                key=lambda x: (
                    -x["score"],
                    x["distance"]
                )
            )

            best = possible[0]

            # ----------------------------------------------------
            # Ambiguity protection.
            # ----------------------------------------------------

            if len(possible) > 1:

                second = possible[1]

                score_difference = (
                    best["score"]
                    - second["score"]
                )

                if score_difference < 0.10:

                    self.results[metric] = {
                        "value": None,
                        "confidence": 0.0,
                        "status": "MANUAL_REVIEW",
                        "reason": "AMBIGUOUS_VALUE",
                    }

                    self.review_reasons.append(
                        f"{metric}: ambiguous candidates"
                    )

                    continue

            # ----------------------------------------------------
            # Confidence threshold.
            # ----------------------------------------------------

            if best["score"] < 0.30:

                self.results[metric] = {
                    "value": None,
                    "confidence": best["score"],
                    "status": "MANUAL_REVIEW",
                    "reason": "LOW_CONFIDENCE",
                }

                self.review_reasons.append(
                    f"{metric}: low confidence"
                )

                continue

            candidate = best["candidate"]

            result = {

                "value": candidate["value"],

                "unit": candidate["unit"],

                "confidence": best["score"],

                "status": "OK",

                "page": candidate["page"],

                "source": {

                    "alias": anchor["alias"],

                    "label_text": anchor["line_text"],

                    "value_text": candidate["raw_text"],

                    "label_bbox": anchor["bbox"],

                    "value_bbox": candidate["bbox"],

                    "region_id": candidate[
                        "region_id"
                    ],

                },

            }

            current = self.results.get(metric)

            if (
                current is None
                or current.get("confidence", 0)
                < result["confidence"]
            ):

                self.results[metric] = result

            self.associations.append({

                "metric": metric,

                "value": candidate["value"],

                "unit": candidate["unit"],

                "score": best["score"],

                "reason": best["reason"],

                "label_text": anchor["line_text"],

                "value_text": candidate["raw_text"],

                "rejected": False,

            })

    def _record_rejection(
        self,
        anchor,
        candidate,
        reason
    ):

        self.associations.append({

            "metric": anchor["metric"],

            "value": candidate["value"],

            "unit": candidate["unit"],

            "score": 0,

            "reason": reason,

            "label_text": anchor["line_text"],

            "value_text": candidate["raw_text"],

            "rejected": True,

        })

    # ========================================================
    # 6. STANDARDIZED METRICS
    # ========================================================

    def _derive_standardized_metrics(self):

        very_low = self._value(
            "VERY_LOW"
        )

        low = self._value(
            "LOW"
        )

        in_range = self._value(
            "IN_RANGE"
        )

        high = self._value(
            "HIGH"
        )

        very_high = self._value(
            "VERY_HIGH"
        )

        # TBR = Very Low + Low
        if (
            very_low is not None
            and low is not None
        ):

            self.derived_metrics["TBR"] = (
                very_low + low
            )

        # TIR = In Range
        if in_range is not None:

            self.derived_metrics["TIR"] = (
                in_range
            )

        # TAR = High + Very High
        if (
            high is not None
            and very_high is not None
        ):

            self.derived_metrics["TAR"] = (
                high + very_high
            )

    def _value(self, metric):

        result = self.results.get(metric)

        if not result:
            return None

        if result.get("status") != "OK":
            return None

        return result.get("value")

    # ========================================================
    # 7. RANGE VALIDATION
    # ========================================================

    def _validate_cluster(self):

        values = [
            self._value("VERY_LOW"),
            self._value("LOW"),
            self._value("IN_RANGE"),
            self._value("HIGH"),
            self._value("VERY_HIGH"),
        ]

        if not all(
            value is not None
            for value in values
        ):
            return

        total = sum(values)

        if abs(total - 100) <= 2:

            self.validation_warnings.append(
                f"RANGE_BREAKDOWN_VALID: {total}%"
            )

            for metric in [
                "VERY_LOW",
                "LOW",
                "IN_RANGE",
                "HIGH",
                "VERY_HIGH",
            ]:

                if self.results[metric]:

                    self.results[metric][
                        "confidence"
                    ] = min(
                        1.0,
                        self.results[metric][
                            "confidence"
                        ] + 0.05
                    )

        else:

            self.validation_warnings.append(
                f"RANGE_BREAKDOWN_WARNING: {total}%"
            )

            self.review_reasons.append(
                f"Range breakdown sum={total}%"
            )

    # ========================================================
    # 8. REPORTING PERIOD
    # ========================================================

    def _extract_reporting_period(self):

        date_pattern = re.compile(
            r"\b"
            r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})"
            r"\s*[-–—]\s*"
            r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})"
            r"\b"
        )

        for page in self.pages:

            text = " ".join(
                word["text"]
                for word in page["raw_words"]
            )

            match = date_pattern.search(text)

            if not match:
                continue

            first = match.group(1)
            second = match.group(2)

            try:

                d1 = datetime.strptime(
                    first,
                    "%d %b %Y"
                )

                d2 = datetime.strptime(
                    second,
                    "%d %b %Y"
                )

                if d1 <= d2:

                    start = first
                    end = second

                else:

                    start = second
                    end = first

                self.reporting_period = {
                    "start": start,
                    "end": end,
                }

            except ValueError:

                self.reporting_period = {
                    "start": first,
                    "end": second,
                }

            return

    # ========================================================
    # 9. FINAL STANDARDIZED REPORT
    # ========================================================

    def _generate_final_report(self):

        actual_components = {}

        for metric in METRIC_ONTOLOGY:

            result = self.results.get(metric)

            if (
                result
                and result.get("status") == "OK"
            ):

                actual_components[metric] = (
                    result["value"]
                )

            else:

                actual_components[metric] = None

        overall_status = (
            "MANUAL_REVIEW"
            if self.review_reasons
            else "SUCCESS"
        )

        return {

            "status": overall_status,

            "reporting_period":
                self.reporting_period,

            "actual_components":
                actual_components,

            "derived_metrics":
                self.derived_metrics,

            "review_reasons":
                self.review_reasons,

            "validation_warnings":
                self.validation_warnings,

            "debug_raw_data": {

                "pages": len(self.pages),

                "raw_words": [
                    word["text"]
                    for page in self.pages
                    for word in page["raw_words"]
                ][:500],

                "regions": [

                    {
                        "region_id":
                            region["region_id"],

                        "page":
                            region["page"],

                        "bbox":
                            region["bbox"],

                        "text":
                            region["text"][:300],

                    }

                    for region in self.regions
                ],

                "extracted_candidates":
                    self.candidates,

                "detected_anchors":
                    self.anchors,

                "associations":
                    self.associations,

                "metrics": {

                    metric: self.results[
                        metric
                    ]

                    for metric
                    in METRIC_ONTOLOGY

                },

                "derived_metrics":
                    self.derived_metrics,

                "validation_warnings":
                    self.validation_warnings,

                "review_reasons":
                    self.review_reasons,
            },
        }


# ============================================================
# DEBUG ENDPOINT
# ============================================================

@app.post("/debug-parser-test")
async def debug_parser_test(
    file: UploadFile = File(...)
):

    if not file.filename.lower().endswith(
        ".pdf"
    ):

        raise HTTPException(
            status_code=400,
            detail="File mora biti PDF."
        )

    content = await file.read()

    if not content:

        raise HTTPException(
            status_code=400,
            detail="PDF je prazan."
        )

    try:

        doc = fitz.open(
            stream=content,
            filetype="pdf"
        )

    except Exception as exc:

        raise HTTPException(
            status_code=400,
            detail=f"PDF nije moguće otvoriti: {exc}"
        )

    try:

        parser = UniversalCGMParser(doc)

        report = parser.parse()

        return JSONResponse(
            content=report
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=f"Parser error: {exc}"
        )

    finally:

        doc.close()


# ============================================================
# DATABASE UPLOAD ENDPOINT
# ============================================================

@app.post("/upload")
async def upload_report(
    file: UploadFile = File(...)
):

    if not file.filename.lower().endswith(
        ".pdf"
    ):

        raise HTTPException(
            status_code=400,
            detail="File mora biti PDF."
        )

    content = await file.read()

    try:

        doc = fitz.open(
            stream=content,
            filetype="pdf"
        )

        parser = UniversalCGMParser(doc)

        extracted = parser.parse()

        doc.close()

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=f"Parsing failed: {exc}"
        )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Patient identification is intentionally NOT derived
    # from manufacturer name.
    #
    # For this MVP a temporary ID is used.
    # Production version should obtain this from authenticated
    # patient/session mapping.
    # --------------------------------------------------------

    patient_id = "P-000127"

    actuals = extracted[
        "actual_components"
    ]

    derived = extracted[
        "derived_metrics"
    ]

    parsed_data = {

        "patient_id":
            patient_id,

        "device_name":
            "Universal CGM Report",

        "manufacturer":
            None,

        "tir":
            derived.get("TIR"),

        "tbr":
            derived.get("TBR"),

        "tar":
            derived.get("TAR"),

        "gmi_percent":
            actuals.get("GMI"),

        "cv":
            actuals.get("CV"),

        "active_time":
            (
                str(
                    actuals["ACTIVE_TIME"]
                ) + "%"
                if actuals.get(
                    "ACTIVE_TIME"
                ) is not None
                else None
            ),
    }

    # Do not write uncertain reports automatically.
    if extracted["status"] != "SUCCESS":

        return {

            "status":
                "manual_review",

            "data":
                parsed_data,

            "engine_report":
                extracted,

        }

    try:

        response = requests.post(

            f"{SUPABASE_URL}"
            "/rest/v1/cgm_reports",

            headers=SUPABASE_HEADERS,

            json=parsed_data,

            timeout=20,

        )

    except requests.RequestException as exc:

        raise HTTPException(
            status_code=502,
            detail=f"Supabase connection failed: {exc}"
        )

    if response.status_code not in (
        200,
        201,
    ):

        raise HTTPException(

            status_code=500,

            detail=(
                "Supabase odbila zahtev: "
                + response.text
            ),

        )

    return {

        "status":
            "success",

        "data":
            parsed_data,

        "engine_report":
            extracted,

    }


# ============================================================
# SIMPLE TEST FRONTEND
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def patient_form():

    return """
<!DOCTYPE html>

<html lang="sr">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>Universal CGM Parser</title>

<style>

body {
    margin: 0;
    padding: 25px;
    background: #0f172a;
    color: #f8fafc;
    font-family: Arial, sans-serif;
}

.container {
    width: 100%;
    max-width: 1000px;
    margin: auto;
}

.card {
    background: #1e293b;
    padding: 20px;
    border-radius: 12px;
}

button {
    background: #0d9488;
    color: white;
    border: none;
    padding: 10px 18px;
    border-radius: 7px;
    cursor: pointer;
    font-weight: bold;
}

button.secondary {
    background: #334155;
}

pre {
    margin-top: 20px;
    background: #030712;
    padding: 15px;
    border-radius: 8px;
    overflow: auto;
    max-height: 650px;
    font-size: 12px;
}

.tabs {
    display: flex;
    gap: 8px;
    margin-bottom: 15px;
}

input {
    margin-bottom: 15px;
}

.status {
    margin-top: 12px;
    font-weight: bold;
}

</style>

</head>

<body>

<div class="container">

<h2>
Universal CGM / AGP Parser
</h2>

<div class="card">

<form id="form">

<input
    type="file"
    id="file"
    accept=".pdf"
    required
>

<br>

<button type="submit">
Pokreni analizu
</button>

</form>

<div class="tabs">

<button
    class="secondary"
    onclick="showResult()"
>
Result
</button>

<button
    class="secondary"
    onclick="showDebug()"
>
Debug
</button>

</div>

<div id="status"
     class="status">
</div>

<pre id="output"></pre>

</div>

</div>

<script>

let lastData = null;

function showResult() {

    if (!lastData) return;

    const result = {

        status:
            lastData.status,

        reporting_period:
            lastData.reporting_period,

        actual_components:
            lastData.actual_components,

        derived_metrics:
            lastData.derived_metrics,

        review_reasons:
            lastData.review_reasons,

        validation_warnings:
            lastData.validation_warnings

    };

    document.getElementById(
        "output"
    ).textContent =
        JSON.stringify(
            result,
            null,
            2
        );
}

function showDebug() {

    if (!lastData) return;

    document.getElementById(
        "output"
    ).textContent =
        JSON.stringify(
            lastData.debug_raw_data,
            null,
            2
        );
}

document.getElementById(
    "form"
).addEventListener(
    "submit",
    async function(event) {

        event.preventDefault();

        const file =
            document.getElementById(
                "file"
            ).files[0];

        if (!file) return;

        document.getElementById(
            "status"
        ).textContent =
            "Analiza u toku...";

        document.getElementById(
            "output"
        ).textContent =
            "";

        const formData =
            new FormData();

        formData.append(
            "file",
            file
        );

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

            lastData = data;

            document.getElementById(
                "status"
            ).textContent =
                data.status || "DONE";

            showResult();

        } catch (error) {

            document.getElementById(
                "status"
            ).textContent =
                "Greška: " + error;

        }

    }
);

</script>

</body>

</html>
"""


# ============================================================
# LOCAL RUN
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )
