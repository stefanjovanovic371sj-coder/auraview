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
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

MAX_FILE_SIZE_MB = 15
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

app = FastAPI(
    title="Universal CGM AGP Parser",
    version="2.3.0"
)


# ============================================================
# METRIC DEFINITIONS (English + Serbian Cyrillic/Latin support)
# ============================================================

Metrics = {

    "VERY_LOW": {
        "aliases": [
            "very low",
            "very-low",
            "vrlo nisko",
            "веома ниско"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },

    "LOW": {
        "aliases": [
            "low",
            "tbr",
            "below range",
            "nisko",
            "ниско"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },

    "IN_RANGE": {
        "aliases": [
            "in range",
            "in-range",
            "time in range",
            "normal",
            "tir",
            "u opsegu",
            "у опсегу",
            "normalno",
            "нормално"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },

    "HIGH": {
        "aliases": [
            "high",
            "tar",
            "above range",
            "visoko",
            "високо"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },

    "VERY_HIGH": {
        "aliases": [
            "very high",
            "very-high",
            "vrlo visoko",
            "веома високо"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },

    "GMI": {
        "aliases": [
            "glucose management indicator",
            "gmi",
            "indikator upravljanja glukozom",
            "индикатор управљања глукозом"
        ],
        "unit": "%",
        "type": "METRIC"
    },

    "CV": {
        "aliases": [
            "glucose variability",
            "coefficient of variation",
            "percent coefficient of variation",
            "gv (cvs)",
            "gv (cv)",
            "gv",
            "cv",
            "varijabilnost",
            "варијабилност",
            "koeficijent varijacije",
            "коефицијент варијације"
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
            "sensor active",
            "cgm coverage time",
            "coverage time",
            "aktivno vreme",
            "активно време",
            "vreme rada senzora",
            "време рада сензора"
        ],
        "unit": "%",
        "type": "METRIC"
    },

    "AVG_GLUCOSE": {
        "aliases": [
            "average glucose",
            "mean glucose",
            "mbg",
            "mean blood glucose",
            "prosečna glukoza",
            "просечна глукоза",
            "srednja glukoza",
            "средња глукоза"
        ],
        "unit": "GLUCOSE",
        "type": "METRIC"
    }
}


# ============================================================
# DATE PATTERNS (Supporting English, Serbian Latin & Cyrillic)
# ============================================================

MONTHS_ENG = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
MONTHS_SRB_LAT = "jan|feb|mar|apr|maj|jun|jul|avg|sep|okt|nov|dec"
MONTHS_SRB_CYR = "јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец"

ALL_MONTHS = f"{MONTHS_ENG}|{MONTHS_SRB_LAT}|{MONTHS_SRB_CYR}"

DATE_PATTERN = re.compile(
    rf"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Пон|Уто|Сре|Чет|Пет|Суб|Нед)?"
    rf"\s*(\d{{1,2}}\s+(?:{ALL_MONTHS})\.?\s+\d{{4}})",
    re.IGNORECASE
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


def parse_date_flexible(date_str: str) -> Optional[datetime]:
    date_str = date_str.replace(".", "").strip()
    
    srb_to_eng_months = {
        "јан": "Jan", "feb": "Feb", "мар": "Mar", "апр": "Apr", "мај": "May", "јун": "Jun",
        "јул": "Jul", "авг": "Aug", "сеп": "Sep", "окт": "Oct", "нов": "Nov", "дец": "Dec",
        "jan": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr", "may": "May", "jun": "Jun",
        "jul": "Jul", "avg": "Aug", "sep": "Sep", "okt": "Oct", "nov": "Nov", "dec": "Dec"
    }

    for k, v in srb_to_eng_months.items():
        if k in date_str.lower():
            for part in date_str.split():
                if k in part.lower():
                    date_str = date_str.replace(part, v)
                    break

    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


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


def vertical_distance(a: List[float], b: List[float]) -> float:
    _, ay = bbox_center(a)
    _, by = bbox_center(b)
    return abs(ay - by)


def horizontal_distance(a: List[float], b: List[float]) -> float:
    ax, _ = bbox_center(a)
    bx, _ = bbox_center(b)
    return abs(ax - bx)


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
        validation_warnings = self._validate(actual_components, derived_metrics)
        review_reasons = self._review_reasons(actual_components, derived_metrics, reporting_period)

        status = "SUCCESS" if not review_reasons else "MANUAL_REVIEW"

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

    def _reset(self):
        self.pages = []
        self.words = []
        self.lines = []
        self.regions = []
        self.candidates = []
        self.anchors = []
        self.associations = []

    def _ingest_pdf(self, pdf_bytes: bytes):
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            raise ValueError(f"Cannot open PDF: {str(e)}")

        max_pages = min(2, len(document))

        for page_number in range(max_pages):
            page = document[page_number]
            page_words = page.get_text("words")
            self.pages.append({
                "page": page_number + 1,
                "width": page.rect.width,
                "height": page.rect.height,
            })

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
                    "bbox": [float(x0), float(y0), float(x1), float(y1)],
                    "text": text.strip(),
                    "normalized": normalize_text(text),
                    "block_no": block_no,
                    "line_no": line_no,
                    "word_no": word_no
                })
        document.close()

    def _build_lines(self):
        grouped = {}
        for word in self.words:
            key = (word["page"], word["block_no"], word["line_no"])
            grouped.setdefault(key, []).append(word)

        lines = []
        line_id = 0
        for key, words in grouped.items():
            words = sorted(words, key=lambda w: w["x0"])
            text = " ".join(w["text"] for w in words)
            bbox = bbox_union([w["bbox"] for w in words])
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

        self.lines = sorted(lines, key=lambda x: (x["page"], x["bbox"][1], x["bbox"][0]))

    def _build_regions(self):
        self.regions = []
        for page_number in range(1, len(self.pages) + 1):
            page_lines = [l for l in self.lines if l["page"] == page_number]
            if not page_lines:
                continue

            current = []
            region_id = 0
            for line in page_lines:
                if not current:
                    current = [line]
                    continue
                previous = current[-1]
                gap = line["bbox"][1] - previous["bbox"][3]
                same_block = line["block_no"] == previous["block_no"]

                if gap <= 45 or same_block:
                    current.append(line)
                else:
                    self._save_region(page_number, region_id, current)
                    region_id += 1
                    current = [line]
            if current:
                self._save_region(page_number, region_id, current)

    def _save_region(self, page_number: int, region_id: int, lines: List[Dict[str, Any]]):
        text = " ".join(l["text"] for l in lines)
        normalized = normalize_text(text)
        bbox = bbox_union([l["bbox"] for l in lines])

        self.regions.append({
            "region_id": f"{page_number}-{region_id}",
            "page": page_number,
            "text": text,
            "normalized": normalized,
            "bbox": bbox,
            "line_ids": [l["line_id"] for l in lines]
        })

    def _extract_candidates(self):
        candidates = []
        candidate_id = 0

        for line in self.lines:
            text = line["text"]
            normalized = line["normalized"]

            if self._is_descriptive_line(normalized):
                continue

            if DATE_PATTERN.search(text):
                continue

            for match in NUMBER_PATTERN.finditer(text):
                raw_number = match.group("number")
                operator = match.group("operator")
                percent = match.group("percent")

                value = safe_float(raw_number)
                if value is None:
                    continue

                token_boxes = []
                char_start = match.start()
                char_end = match.end()
                cursor = 0
                token_positions = []

                for word in line["words"]:
                    word_text = word["text"]
                    start = text.find(word_text, cursor)
                    if start < 0:
                        continue
                    end = start + len(word_text)
                    cursor = end
                    token_positions.append((start, end, word))

                for start, end, word in token_positions:
                    if end > char_start and start < char_end:
                        token_boxes.append(word["bbox"])

                bbox = bbox_union(token_boxes) if token_boxes else line["bbox"]

                is_goal_line = any(term in normalized for term in ["goal:", "target range", "reference value", "reference values", "above 10.0", "below 3.9", ">70%", "<25%", "<4%"])
                context = "GOAL" if (is_goal_line or operator) else "ACTUAL_RANGE"

                if context == "GOAL":
                    continue

                unit = "%"
                nearby_text = normalize_text(text)
                if "mmol/l" in nearby_text or "mmol" in nearby_text or "mg/dl" in nearby_text:
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
                    "region_id": self._region_for_line(line["line_id"]),
                    "line_text": text,
                    "context_type": context,
                    "has_percent": bool(percent),
                    "operator": operator,
                })
                candidate_id += 1

        self.candidates = candidates

    def _is_descriptive_line(self, normalized: str) -> bool:
        patterns = [
            "1% of time in ranges",
            "about",
            "defined as percent",
            "median",
            "percentile",
            "printing date",
            "reporting period",
            "measuring period",
            "serial number",
        ]
        return any(p in normalized for p in patterns)

    def _region_for_line(self, line_id: int) -> Optional[str]:
        for region in self.regions:
            if line_id in region["line_ids"]:
                return region["region_id"]
        return None

    def _extract_anchors(self):
        anchors = []
        anchor_id = 0

        for line in self.lines:
            normalized = line["normalized"]
            for metric, definition in Metrics.items():
                for alias in definition["aliases"]:
                    if alias in normalized:
                        alias_bbox = self._find_alias_bbox(line, alias)
                        anchors.append({
                            "anchor_id": anchor_id,
                            "metric": metric,
                            "alias": alias,
                            "page": line["page"],
                            "line_id": line["line_id"],
                            "region_id": self._region_for_line(line["line_id"]),
                            "bbox": alias_bbox,
                            "line_text": line["text"],
                        })
                        anchor_id += 1
                        break
        self.anchors = anchors

    def _find_alias_bbox(self, line: Dict[str, Any], alias: str) -> List[float]:
        alias_normalized = normalize_text(alias)
        matched_words = []
        for word in line["words"]:
            if normalize_text(word["text"]) in alias_normalized or normalize_text(word["text"]) in alias_normalized.split():
                matched_words.append(word["bbox"])
        return bbox_union(matched_words) if matched_words else line["bbox"]

    def _pair_range_components(self):
        range_metrics = ["VERY_LOW", "LOW", "IN_RANGE", "HIGH", "VERY_HIGH"]
        anchors = [a for a in self.anchors if a["metric"] in range_metrics]
        candidates = [c for c in self.candidates if c["unit"] == "%"]

        result = {metric: None for metric in range_metrics}
        if not anchors or not candidates:
            return result

        used_candidates = set()
        used_metrics = set()

        for anchor in anchors:
            best = None
            best_score = float("inf")
            for candidate in candidates:
                if candidate["candidate_id"] in used_candidates or candidate["page"] != anchor["page"]:
                    continue
                if candidate["line_id"] == anchor["line_id"]:
                    score = horizontal_distance(anchor["bbox"], candidate["bbox"]) * 0.1
                    if score < best_score:
                        best_score = score
                        best = candidate

            if best is not None:
                result[anchor["metric"]] = best["value"]
                used_candidates.add(best["candidate_id"])
                used_metrics.add(anchor["metric"])
                self.associations.append({
                    "type": "RANGE", "metric": anchor["metric"], "candidate_id": best["candidate_id"],
                    "value": best["value"], "method": "same_line"
                })

        remaining_anchors = [a for a in anchors if a["metric"] not in used_metrics]
        remaining_candidates = [c for c in candidates if c["candidate_id"] not in used_candidates]
        pairs = []

        for anchor in remaining_anchors:
            for candidate in remaining_candidates:
                if candidate["page"] != anchor["page"]:
                    continue
                dy = vertical_distance(anchor["bbox"], candidate["bbox"])
                dx = horizontal_distance(anchor["bbox"], candidate["bbox"])
                if dy > 180:
                    continue
                score = dy + (dx * 0.20)
                if anchor["region_id"] == candidate["region_id"]:
                    score -= 30

                pairs.append({
                    "metric": anchor["metric"], "candidate_id": candidate["candidate_id"],
                    "value": candidate["value"], "score": score, "anchor": anchor, "candidate": candidate
                })

        pairs.sort(key=lambda x: x["score"])
        for pair in pairs:
            metric = pair["metric"]
            candidate_id = pair["candidate_id"]
            if metric in used_metrics or candidate_id in used_candidates or pair["score"] > 220:
                continue

            result[metric] = pair["value"]
            used_metrics.add(metric)
            used_candidates.add(candidate_id)
            self.associations.append({
                "type": "RANGE", "metric": metric, "candidate_id": candidate_id,
                "value": pair["value"], "method": "geometric_1_to_1", "score": round(pair["score"], 2)
            })

        return result

    def _pair_standard_metrics(self):
        target_metrics = ["GMI", "CV", "ACTIVE_TIME", "AVG_GLUCOSE"]
        result = {metric: None for metric in target_metrics}

        for metric in target_metrics:
            anchors = [a for a in self.anchors if a["metric"] == metric]
            if not anchors:
                continue

            best_pair = None
            best_score = float("inf")

            for anchor in anchors:
                for candidate in self.candidates:
                    if candidate["page"] != anchor["page"] or candidate["context_type"] == "GOAL":
                        continue
                    if metric == "AVG_GLUCOSE":
                        if candidate["unit"] != "GLUCOSE":
                            continue
                    else:
                        if candidate["unit"] != "%":
                            continue

                    dy = vertical_distance(anchor["bbox"], candidate["bbox"])
                    dx = horizontal_distance(anchor["bbox"], candidate["bbox"])
                    if dx > 350 or dy > 180:
                        continue

                    score = dy + dx * 0.15
                    if anchor["region_id"] == candidate["region_id"]:
                        score -= 25
                    if anchor["line_id"] == candidate["line_id"]:
                        score -= 60

                    if score < best_score:
                        best_score = score
                        best_pair = (anchor, candidate)

            if best_pair:
                anchor, candidate = best_pair
                result[metric] = candidate["value"]
                self.associations.append({
                    "type": "STANDARD", "metric": metric, "candidate_id": candidate["candidate_id"],
                    "value": candidate["value"], "score": round(best_score, 2)
                })

        return result

    def _derive_metrics(self, actual: Dict[str, Any]):
        very_low = actual.get("VERY_LOW")
        low = actual.get("LOW")
        in_range = actual.get("IN_RANGE")
        high = actual.get("HIGH")
        very_high = actual.get("VERY_HIGH")

        tbr = round(very_low + low, 2) if (very_low is not None and low is not None) else None
        tir = round(in_range, 2) if in_range is not None else None
        tar = round(high + very_high, 2) if (high is not None and very_high is not None) else None

        return {"TBR": tbr, "TIR": tir, "TAR": tar}

    def _validate(self, actual: Dict[str, Any], derived: Dict[str, Any]) -> List[str]:
        warnings = []
        range_values = [actual.get("VERY_LOW"), actual.get("LOW"), actual.get("IN_RANGE"), actual.get("HIGH"), actual.get("VERY_HIGH")]
        if all(value is not None for value in range_values):
            total = sum(range_values)
            if abs(total - 100) > 3:
                warnings.append(f"Range components sum to {total}%, expected ~100%")
        return warnings

    def _review_reasons(self, actual: Dict[str, Any], derived: Dict[str, Any], reporting_period: Dict[str, Any]) -> List[str]:
        reasons = []
        for metric in ["VERY_LOW", "LOW", "IN_RANGE", "HIGH", "VERY_HIGH", "GMI", "CV", "ACTIVE_TIME", "AVG_GLUCOSE"]:
            if actual.get(metric) is None:
                reasons.append(f"Missing {metric}")
        if not reporting_period.get("start"):
            reasons.append("Reporting period not detected")
        return reasons

    def _extract_reporting_period(self):
        dates = []
        for page_number in range(1, len(self.pages) + 1):
            page_words = sorted([w for w in self.words if w["page"] == page_number], key=lambda w: (w["y0"], w["x0"]))
            page_text = " ".join(w["text"] for w in page_words)
            for match in DATE_PATTERN.finditer(page_text):
                date_text = match.group(1)
                if date_text not in dates:
                    dates.append(date_text)

        if len(dates) < 2:
            return {"start": None, "end": None}

        parsed = []
        for date_text in dates:
            dt = parse_date_flexible(date_text)
            if dt:
                parsed.append((dt, date_text))

        if len(parsed) >= 2:
            parsed.sort(key=lambda x: x[0])
            return {"start": parsed[0][1], "end": parsed[-1][1]}

        return {"start": dates[0], "end": dates[1]}


# ============================================================
# ENDPOINTS
# ============================================================

@app.post("/debug-parser-test")
async def debug_parser_test(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    pdf_bytes = await file.read()
    parser = UniversalCGMParser()
    return parser.parse(pdf_bytes)

@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    pdf_bytes = await file.read()
    parser = UniversalCGMParser()
    report = parser.parse(pdf_bytes)
    
    patient_id = "P-000127"
    actuals = report["actual_components"]
    derived = report["derived_metrics"]
    
    parsed_data = {
        "patient_id": patient_id,
        "device_name": "Universal CGM Report",
        "manufacturer": "Multilingual / Universal",
        "tir": derived.get("TIR"),
        "tbr": derived.get("TBR"),
        "tar": derived.get("TAR"),
        "gmi_percent": actuals.get("GMI"),
        "cv": actuals.get("CV"),
        "active_time": str(actuals.get("ACTIVE_TIME")) + "%" if actuals.get("ACTIVE_TIME") is not None else None,
    }
    
    try:
        requests.post(f"{SUPABASE_URL}/rest/v1/cgm_reports", headers=SUPABASE_HEADERS, json=parsed_data, timeout=10)
    except Exception:
        pass
        
    return {"status": report["status"], "data": parsed_data, "report": report}

@app.get("/api/reports")
def get_reports():
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/cgm_reports?select=*&order=id.desc", headers=SUPABASE_HEADERS, timeout=10)
        return res.json()
    except Exception:
        return []


# ============================================================
# FRONTEND / DASHBOARDS
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML

@app.get("/dashboard", response_class=HTMLResponse)
async def doctor_dashboard():
    return DASHBOARD_HTML


HTML = """
<!DOCTYPE html>
<html lang="sr">
<head>
<meta charset="UTF-8">
<title>Universal CGM AGP Parser</title>
<style>
body { font-family: Arial, sans-serif; background: #f5f7fa; padding: 30px; margin: 0; }
.container { max-width: 1100px; margin: auto; }
.card { background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
button { padding: 12px 20px; border: none; border-radius: 8px; cursor: pointer; background: #0d9488; color: white; font-size: 15px; font-weight: bold; }
pre { white-space: pre-wrap; word-break: break-word; background: #111; color: #eee; padding: 20px; border-radius: 8px; overflow-x: auto; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.metric { background: #f0f2f5; padding: 16px; border-radius: 8px; }
.metric strong { display: block; font-size: 12px; color: #666; margin-bottom: 5px; }
.metric span { font-size: 24px; font-weight: bold; }
.success { color: green; font-weight: bold; }
.review { color: #b36b00; font-weight: bold; }
</style>
</head>
<body>
<div class="container">
<div class="card">
<h1>Universal CGM / AGP Parser</h1>
<p>Unesite bilo koji podržani CGM AGP PDF (engleski ili srpski) za parsiranje i sinhronizaciju.</p>
<input id="file" type="file" accept=".pdf"><br><br>
<button onclick="parseFile()">Parsiraj & Sačuvaj PDF</button>
<a href="/dashboard" style="margin-left: 15px; color: #0d9488; font-weight: bold; text-decoration: none;">Idi na Lekarski Panel →</a>
</div>
<div id="result"></div>
<div class="card">
<h2>Debug JSON</h2>
<pre id="json">Još uvek nema rezultata.</pre>
</div>
</div>
<script>
async function parseFile() {
    const fileInput = document.getElementById("file");
    if (!fileInput.files.length) { alert("Izaberite PDF fajl."); return; }
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    document.getElementById("result").innerHTML = "<div class='card'>Parsiranje i sinhronizacija u toku...</div>";
    try {
        const response = await fetch("/upload", { method: "POST", body: formData });
        const data = await response.json();
        renderResult(data.report);
        document.getElementById("json").textContent = JSON.stringify(data, null, 2);
    } catch (error) {
        document.getElementById("result").innerHTML = "<div class='card'><b>Greška:</b> " + error + "</div>";
    }
}
function renderResult(data) {
    if (!data.actual_components) { document.getElementById("result").innerHTML = "<pre>" + JSON.stringify(data, null, 2) + "</pre>"; return; }
    const a = data.actual_components; const d = data.derived_metrics;
    let html = "<div class='card'><h2>Rezultat parsiranja</h2><p class='" + (data.status === "SUCCESS" ? "success" : "review") + "'>" + data.status + "</p>";
    html += "<p>Period: " + (data.reporting_period.start || "?") + " → " + (data.reporting_period.end || "?") + "</p><h3>Metrike</h3><div class='grid'>";
    html += metric("Prosečna glukoza", a.AVG_GLUCOSE, "mmol/L") + metric("GMI", a.GMI, "%") + metric("CV", a.CV, "%") + metric("Aktivno vreme", a.ACTIVE_TIME, "%");
    html += "</div><h3>Opsezi</h3><div class='grid'>";
    html += metric("Vrlo nisko", a.VERY_LOW, "%") + metric("Nisko", a.LOW, "%") + metric("U opsegu", a.IN_RANGE, "%") + metric("Visoko", a.HIGH, "%") + metric("Vrlo visoko", a.VERY_HIGH, "%");
    html += "</div><h3>Standardizovano</h3><div class='grid'>";
    html += metric("TBR", d.TBR, "%") + metric("TIR", d.TIR, "%") + metric("TAR", d.TAR, "%");
    html += "</div></div>";
    document.getElementById("result").innerHTML = html;
}
function metric(label, value, unit) {
    let display = (value === null || value === undefined) ? "—" : value + " " + unit;
    return "<div class='metric'><strong>" + label + "</strong><span>" + display + "</span></div>";
}
</script>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="sr">
<head>
<meta charset="UTF-8">
<title>Dr Marko Jovanović — Live Panel</title>
<style>
body { background: #0f172a; color: #f8fafc; font-family: sans-serif; padding: 25px; margin: 0; }
.container { max-width: 1000px; margin: auto; }
h2 { color: #38bdf8; border-bottom: 2px solid #334155; padding-bottom: 10px; }
.report-card { background: #1e293b; padding: 20px; margin-bottom: 15px; border-radius: 10px; border: 1px solid #334155; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
.nav { margin-bottom: 20px; }
.nav a { color: #38bdf8; text-decoration: none; font-weight: bold; }
</style>
</head>
<body>
<div class="container">
<div class="nav"><a href="/">← Nazad na Upload</a></div>
<h2>Dr Marko Jovanović — Live Panel</h2>
<div id="c">Učitavanje izveštaja iz baze...</div>
</div>
<script>
async function loadReports() {
    try {
        const res = await fetch('/api/reports');
        const data = await res.json();
        if(!data.length) {
            document.getElementById('c').innerHTML = "<p>Nema sačuvanih izveštaja u bazi.</p>";
            return;
        }
        document.getElementById('c').innerHTML = data.map(r => `
            <div class="report-card">
                <strong>Pacijent: ${r.patient_id}</strong> — <span style="color:#38bdf8;">${r.device_name || 'CGM'} (${r.manufacturer || ''})</span><br><br>
                TIR: <span style="color:#4ade80; font-size:20px; font-weight:bold;">${r.tir !== null ? r.tir + '%' : '-'}</span> | 
                TBR: <span style="color:#f87171; font-weight:bold;">${r.tbr !== null ? r.tbr + '%' : '-'}</span> | 
                TAR: <span style="color:#fbbf24; font-weight:bold;">${r.tar !== null ? r.tar + '%' : '-'}</span> | 
                GMI: ${r.gmi_percent !== null ? r.gmi_percent + '%' : '-'} | 
                CV: ${r.cv !== null ? r.cv + '%' : '-'} | 
                Aktivno: ${r.active_time || '-'}
            </div>
        `).join('');
    } catch(err) {
        document.getElementById('c').innerHTML = "<p style='color:#f87171;'>Greška pri učitavanju sa servera.</p>";
    }
}
loadReports();
setInterval(loadReports, 5000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
