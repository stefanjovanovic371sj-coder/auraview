import fitz  # PyMuPDF
import re
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple

# ============================================================
# METRIC DEFINITIONS (Podržava i Engleski i Srpski)
# ============================================================

Metrics = {
    "VERY_LOW": {
        "aliases": [
            "very low", "very-low", "vrlo nisko", "веома ниско", 
            "veoma nizak nivo", "veoma nizak", "веома низак ниво", "веома низак"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },
    "LOW": {
        "aliases": [
            "low", "tbr", "below range", "nisko", "ниско", 
            "nizak nivo", "nizak", "низак ниво", "низак"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },
    "IN_RANGE": {
        "aliases": [
            "in range", "in-range", "time in range", "normal", 
            "tir", "u opsegu", "у опсегу", "normalno", "нормално"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },
    "HIGH": {
        "aliases": [
            "high", "tar", "above range", "visoko", "високо", 
            "visok nivo", "visok", "висок ниво", "висок"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },
    "VERY_HIGH": {
        "aliases": [
            "very high", "very-high", "vrlo visoko", "веома високо", 
            "veoma visoko", "veoma visok nivo", "veoma visok", "веома висок ниво", "веома висок"
        ],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },
    "GMI": {
        "aliases": [
            "glucose management indicator", "gmi", 
            "indikator upravljanja glukozom", "индикатор управљања глукозом"
        ],
        "unit": "%",
        "type": "METRIC"
    },
    "CV": {
        "aliases": [
            "glucose variability", "coefficient of variation", "percent coefficient of variation",
            "gv (cvs)", "gv (cv)", "gv", "cv", "varijabilnost", "варијабилност",
            "varijabilnost glukoze", "варијабилност глукозе", "koeficijent varijacije"
        ],
        "unit": "%",
        "type": "METRIC"
    },
    "ACTIVE_TIME": {
        "aliases": [
            "time cgm active", "time cgM active", "cgm active", "active time",
            "sensor active", "cgm coverage time", "coverage time",
            "aktivno vreme cgm", "aktivno vreme", "активно време"
        ],
        "unit": "%",
        "type": "METRIC"
    },
    "AVG_GLUCOSE": {
        "aliases": [
            "average glucose", "mean glucose", "mbg", "mean blood glucose",
            "prosečna vrednost glukoze", "prosečna glukoza", "просечна вредност глукозе"
        ],
        "unit": "GLUCOSE",
        "type": "METRIC"
    }
}

MONTHS_ENG = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
MONTHS_SRB_LAT = "jan|feb|mar|apr|maj|jun|jul|avg|sep|okt|nov|dec"
MONTHS_SRB_CYR = "јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец"
ALL_MONTHS = f"{MONTHS_ENG}|{MONTHS_SRB_LAT}|{MONTHS_SRB_CYR}"

DATE_PATTERN = re.compile(
    rf"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Пон|Уто|Сре|Чет|Пет|Суб|Нед)?\s*(\d{{1,2}}\s+(?:{ALL_MONTHS})\.?\s+\d{{4}})",
    re.IGNORECASE
)

NUMBER_PATTERN = re.compile(
    r"(?P<operator>[<>≤≥]?)\s*(?P<number>\d{1,3}(?:[.,]\d{1,2})?)\s*(?P<percent>%?)"
)

def normalize_text(text: str) -> str:
    if not text: return ""
    text = text.lower()
    replacements = {"–": "-", "—": "-", "−": "-", "\u00a0": " ", "\u202f": " "}
    for a, b in replacements.items():
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()

def parse_date_flexible(date_str: str) -> Optional[datetime]:
    date_str = date_str.replace(".", "").strip()
    srb_to_eng = {
        "јан": "Jan", "feb": "Feb", "мар": "Mar", "апр": "Apr", "мај": "May", "јун": "Jun",
        "јул": "Jul", "авг": "Aug", "сеп": "Sep", "окт": "Oct", "нов": "Nov", "дец": "Dec",
        "jan": "Jan", "mar": "Mar", "apr": "Apr", "maj": "May", "jun": "Jun",
        "jul": "Jul", "avg": "Aug", "sep": "Sep", "okt": "Oct", "nov": "Nov", "dec": "Dec"
    }
    for k, v in srb_to_eng.items():
        if k in date_str.lower():
            parts = date_str.split()
            for i, part in enumerate(parts):
                if k in part.lower():
                    parts[i] = v
            date_str = " ".join(parts)
            break
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try: return datetime.strptime(date_str, fmt)
        except ValueError: continue
    return None

def safe_float(value: str) -> Optional[float]:
    try: return float(value.replace(",", "."))
    except Exception: return None

def bbox_union(boxes: List[List[float]]) -> List[float]:
    if not boxes: return [0, 0, 0, 0]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]

def bbox_center(bbox: List[float]) -> Tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

def vertical_distance(a: List[float], b: List[float]) -> float:
    return abs(bbox_center(a)[1] - bbox_center(b)[1])

def horizontal_distance(a: List[float], b: List[float]) -> float:
    return abs(bbox_center(a)[0] - bbox_center(b)[0])


class UniversalCGMParser:
    def __init__(self):
        self.pages = []
        self.words = []
        self.lines = []
        self.regions = []
        self.candidates = []
        self.anchors = []

    def parse(self, pdf_bytes: bytes) -> Dict[str, Any]:
        self._reset()
        self._ingest_pdf(pdf_bytes)
        self._build_lines()
        self._build_regions()
        self._extract_candidates()
        self._extract_anchors()

        # Direktno regex čitanje za mySugr
        range_values = self._parse_mysugr_direct_ranges()
        
        # Geometrijska rezerva
        geom_range_values = self._pair_range_components()
        for k in range_values:
            if range_values[k] is None:
                range_values[k] = geom_range_values.get(k)

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
            "review_reasons": review_reasons
        }

    def _reset(self):
        self.pages = []
        self.words = []
        self.lines = []
        self.regions = []
        self.candidates = []
        self.anchors = []

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
                if len(item) < 8: continue
                x0, y0, x1, y1, text, block_no, line_no, word_no = item[:8]
                if not text or not text.strip(): continue

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
            if not page_lines: continue

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
                    self.regions.append({
                        "region_id": f"{page_number}-{region_id}",
                        "page": page_number,
                        "line_ids": [l["line_id"] for l in current]
                    })
                    region_id += 1
                    current = [line]
            if current:
                self.regions.append({
                    "region_id": f"{page_number}-{region_id}",
                    "page": page_number,
                    "line_ids": [l["line_id"] for l in current]
                })

    def _parse_mysugr_direct_ranges(self) -> Dict[str, Optional[float]]:
        results = {"VERY_LOW": None, "LOW": None, "IN_RANGE": None, "HIGH": None, "VERY_HIGH": None}
        
        # Redoslijed: prvo dvočlani pojmovi, pa onda jednostruki
        patterns = {
            "VERY_HIGH": [
                r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:very high|веома високо|veoma visok)",
                r"(?:very high|веома високо|veoma visok)\s*(\d{1,2}(?:[.,]\d+)?)\s*%"
            ],
            "VERY_LOW": [
                r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:very low|vrlo nisko|веома ниско|veoma nizak)",
                r"(?:very low|vrlo nisko|веома ниско|veoma nizak)\s*(\d{1,2}(?:[.,]\d+)?)\s*%"
            ],
            "HIGH": [
                r"(?<!very\s)(?<!веома\s)(?<!veoma\s)(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:high|високо|visok)",
                r"(?<!very\s)(?<!веома\s)(?<!veoma\s)(?:high|високо|visok)\s*(\d{1,2}(?:[.,]\d+)?)\s*%"
            ],
            "LOW": [
                r"(?<!very\s)(?<!веома\s)(?<!veoma\s)(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:low|nisko|ниско|nizak)",
                r"(?<!very\s)(?<!веома\s)(?<!veoma\s)(?:low|nisko|ниско|nizak)\s*(\d{1,2}(?:[.,]\d+)?)\s*%"
            ],
            "IN_RANGE": [
                r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:in range|u opsegu|у опсегу)",
                r"(?:in range|u opsegu|у опсегу)\s*(\d{1,2}(?:[.,]\d+)?)\s*%"
            ]
        }

        for line in self.lines:
            if any(term in line["normalized"] for term in ["target range", "ciljni opseg", "goal", "cilj", "above 10.0", "below 3.9"]):
                continue
            
            for metric, regex_list in patterns.items():
                if results[metric] is not None:
                    continue
                for reg in regex_list:
                    m = re.search(reg, line["normalized"])
                    if m:
                        val = safe_float(m.group(1))
                        if val is not None and val <= 100:
                            results[metric] = val
                            break
        return results

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

                # Ignorisanje minuta i sati npr. '(0h 43min)'
                after_idx = match.end()
                rest_of_text = text[after_idx:after_idx+10].lower()
                if "min" in rest_of_text or "h" in rest_of_text:
                    continue

                is_goal = bool(operator) or any(term in normalized for term in [">70%", "<25%", "<4%", "goal: <", "cilj: <"])
                context = "GOAL" if is_goal else "ACTUAL"

                unit = "%"
                if "mmol/l" in normalized or "mmol" in normalized:
                    if "%" not in match.group(0):
                        unit = "GLUCOSE"

                candidates.append({
                    "candidate_id": candidate_id,
                    "value": value,
                    "unit": unit,
                    "bbox": line["bbox"],
                    "page": line["page"],
                    "line_id": line["line_id"],
                    "region_id": self._region_for_line(line["line_id"]),
                    "context_type": context
                })
                candidate_id += 1

        self.candidates = candidates

    def _is_descriptive_line(self, normalized: str) -> bool:
        patterns = [
            "1% of time", "about 15 min", "defined as percent",
            "median", "percentile", "printing date", "datum stampanja", 
            "datum štampanja", "svako povecanje", "svako povećanje",
            "each 5% increase", "clinically beneficial", "klinički korisnim"
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
                        anchors.append({
                            "anchor_id": anchor_id,
                            "metric": metric,
                            "page": line["page"],
                            "line_id": line["line_id"],
                            "region_id": self._region_for_line(line["line_id"]),
                            "bbox": line["bbox"],
                        })
                        anchor_id += 1
                        break
        self.anchors = anchors

    def _pair_range_components(self):
        range_metrics = ["VERY_LOW", "LOW", "IN_RANGE", "HIGH", "VERY_HIGH"]
        anchors = [a for a in self.anchors if a["metric"] in range_metrics]
        candidates = [c for c in self.candidates if c["unit"] == "%" and c["context_type"] != "GOAL"]

        result = {metric: None for metric in range_metrics}
        used_candidates = set()

        for anchor in anchors:
            best_cand = None
            min_dist = float("inf")
            for c in candidates:
                if c["candidate_id"] in used_candidates or c["page"] != anchor["page"]:
                    continue
                if vertical_distance(anchor["bbox"], c["bbox"]) <= 15:
                    h_dist = horizontal_distance(anchor["bbox"], c["bbox"])
                    if h_dist < min_dist:
                        min_dist = h_dist
                        best_cand = c
            
            if best_cand:
                result[anchor["metric"]] = best_cand["value"]
                used_candidates.add(best_cand["candidate_id"])

        return result

    def _pair_standard_metrics(self):
        target_metrics = ["GMI", "CV", "ACTIVE_TIME", "AVG_GLUCOSE"]
        result = {metric: None for metric in target_metrics}

        for metric in target_metrics:
            anchors = [a for a in self.anchors if a["metric"] == metric]
            if not anchors: continue

            best_cand = None
            best_score = float("inf")

            for anchor in anchors:
                for c in self.candidates:
                    if c["page"] != anchor["page"] or c["context_type"] == "GOAL":
                        continue
                    if metric == "AVG_GLUCOSE" and c["unit"] != "GLUCOSE":
                        continue
                    if metric != "AVG_GLUCOSE" and c["unit"] != "%":
                        continue

                    dy = vertical_distance(anchor["bbox"], c["bbox"])
                    dx = horizontal_distance(anchor["bbox"], c["bbox"])
                    if dy > 50 or dx > 350:
                        continue

                    score = dy * 2.0 + dx * 0.1
                    if score < best_score:
                        best_score = score
                        best_cand = c

            if best_cand:
                result[metric] = best_cand["value"]

        return result

    def _derive_metrics(self, actual: Dict[str, Any]):
        vl = actual.get("VERY_LOW")
        l = actual.get("LOW")
        ir = actual.get("IN_RANGE")
        h = actual.get("HIGH")
        vh = actual.get("VERY_HIGH")

        tbr = round((vl or 0) + (l or 0), 2) if (vl is not None or l is not None) else None
        tir = round(ir, 2) if ir is not None else None
        tar = round((h or 0) + (vh or 0), 2) if (h is not None or vh is not None) else None

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
            return {"start": parsed[0][1], "end": parsed[1][1]}

        return {"start": dates[0], "end": dates[1]}
