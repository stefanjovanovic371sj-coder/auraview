import fitz  # PyMuPDF
import re
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple

Metrics = {
    "VERY_LOW": {
        "aliases": ["very low", "very-low", "vrlo nisko", "веома ниско", "veoma nizak nivo", "veoma nizak", "веома низак ниво", "веома низак", "vrlo nizak nivo", "врло низак ниво", "veoma nisko"],
        "unit": "%", "type": "RANGE_COMPONENT"
    },
    "LOW": {
        "aliases": ["low", "tbr", "below range", "nisko", "ниско", "nizak nivo", "nizak", "низак ниво", "низак"],
        "unit": "%", "type": "RANGE_COMPONENT"
    },
    "IN_RANGE": {
        "aliases": ["in range", "in-range", "time in range", "normal", "tir", "u opsegu", "у опсегу", "normalno", "нормално"],
        "unit": "%", "type": "RANGE_COMPONENT"
    },
    "HIGH": {
        "aliases": ["high", "tar", "above range", "visoko", "високо", "visok nivo", "visok", "висок ниво", "висок"],
        "unit": "%", "type": "RANGE_COMPONENT"
    },
    "VERY_HIGH": {
        "aliases": ["very high", "very-high", "vrlo visoko", "веома високо", "veoma visoko", "veoma visok nivo", "veoma visok", "веома висок ниво", "веома висок", "vrlo visok nivo", "врло висок ниво"],
        "unit": "%", "type": "RANGE_COMPONENT"
    },
    "GMI": {
        "aliases": ["glucose management indicator", "gmi", "indikator upravljanja glukozom", "индикатор управљања глукозом"],
        "unit": "%", "type": "METRIC"
    },
    "CV": {
        "aliases": ["glucose variability", "coefficient of variation", "percent coefficient of variation", "gv (cvs)", "gv (cv)", "gv", "cv", "varijabilnost", "варијабилност", "varijabilnost glukoze", "варијабилност глукозе", "koeficijent varijacije", "коефицијент варијације"],
        "unit": "%", "type": "METRIC"
    },
    "ACTIVE_TIME": {
        "aliases": ["time cgm active", "time cgM active", "cgm active", "active time", "sensor active", "cgm coverage time", "coverage time", "aktivno vreme cgm", "aktivno vreme", "активно време", "активно време цгм"],
        "unit": "%", "type": "METRIC"
    },
    "AVG_GLUCOSE": {
        "aliases": ["average glucose", "mean glucose", "mbg", "mean blood glucose", "prosečna vrednost glukoze", "prosečna glukoza", "просечна вредност глукозе", "просечна глукоза"],
        "unit": "GLUCOSE", "type": "METRIC"
    }
}

MONTHS_ENG = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
MONTHS_SRB_LAT = "jan|feb|mar|apr|maj|jun|jul|avg|sep|okt|nov|dec"
MONTHS_SRB_CYR = "јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец"
ALL_MONTHS = f"{MONTHS_ENG}|{MONTHS_SRB_LAT}|{MONTHS_SRB_CYR}"

DATE_PATTERN = re.compile(rf"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Пон|Уто|Сре|Чет|Пет|Суб|Нед)?\s*(\d{{1,2}}\s+(?:{ALL_MONTHS})\.?\s+\d{{4}})", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"(?P<operator>[<>≤≥]?)\s*(?P<number>\d{1,3}(?:[.,]\d{1,2})?)\s*(?P<percent>%?)")

def normalize_text(text: str) -> str:
    if not text: return ""
    text = text.lower()
    for a, b in {"–": "-", "—": "-", "−": "-", "\u00a0": " ", "\u202f": " "}.items():
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()

def parse_date_flexible(date_str: str) -> Optional[datetime]:
    date_str = date_str.replace(".", "").strip()
    srb_to_eng = {"јан": "Jan", "feb": "Feb", "мар": "Mar", "апр": "Apr", "мај": "May", "јун": "Jun", "јул": "Jul", "авг": "Aug", "сеп": "Sep", "окт": "Oct", "нов": "Nov", "дец": "Dec", "jan": "Jan", "mar": "Mar", "apr": "Apr", "maj": "May", "jun": "Jun", "jul": "Jul", "avg": "Aug", "sep": "Sep", "okt": "Oct", "nov": "Nov", "dec": "Dec"}
    for k, v in srb_to_eng.items():
        if k in date_str.lower():
            parts = date_str.split()
            for i, part in enumerate(parts):
                if k in part.lower(): parts[i] = v
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

        range_values = self._parse_mysugr_direct_ranges()
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
        self.pages = [], self.words = [], self.lines = [], self.regions = [], self.candidates = [], self.anchors = []

    def _ingest_pdf(self, pdf_bytes: bytes):
        try: document = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e: raise ValueError(f"Cannot open PDF: {str(e)}")
        for page_number in range(min(2, len(document))):
            page = document[page_number]
            for index, item in enumerate(page.get_text("words")):
                if len(item) < 8 or not item[4].strip(): continue
                self.words.append({
                    "page": page_number + 1, "index": index,
                    "bbox": [float(item[0]), float(item[1]), float(item[2]), float(item[3])],
                    "text": item[4].strip(), "normalized": normalize_text(item[4]),
                    "block_no": item[5], "line_no": item[6], "word_no": item[7]
                })
        document.close()

    def _build_lines(self):
        lines = []
        line_id = 0
        for page_number in range(1, len(set(w["page"] for w in self.words)) + 1):
            page_words = [w for w in self.words if w["page"] == page_number]
            page_words.sort(key=lambda w: (w["bbox"][1], w["bbox"][0]))
            current_line, current_y_mid = [], None
            for w in page_words:
                w_y_mid = (w["bbox"][1] + w["bbox"][3]) / 2
                if current_y_mid is None:
                    current_line.append(w), current_y_mid = w_y_mid
                elif abs(w_y_mid - current_y_mid) <= 6.0:
                    current_line.append(w)
                    current_y_mid = sum((x["bbox"][1] + x["bbox"][3]) / 2 for x in current_line) / len(current_line)
                else:
                    current_line.sort(key=lambda x: x["bbox"][0])
                    text = " ".join(x["text"] for x in current_line)
                    lines.append({"line_id": line_id, "page": page_number, "text": text, "normalized": normalize_text(text), "bbox": bbox_union([x["bbox"] for x in current_line]), "words": current_line})
                    line_id += 1, current_line = [w], current_y_mid = w_y_mid
            if current_line:
                current_line.sort(key=lambda x: x["bbox"][0])
                text = " ".join(x["text"] for x in current_line)
                lines.append({"line_id": line_id, "page": page_number, "text": text, "normalized": normalize_text(text), "bbox": bbox_union([x["bbox"] for x in current_line]), "words": current_line})
                line_id += 1
        self.lines = lines

    def _build_regions(self):
        self.regions = []

    def _parse_mysugr_direct_ranges(self) -> Dict[str, Optional[float]]:
        results = {"VERY_LOW": None, "LOW": None, "IN_RANGE": None, "HIGH": None, "VERY_HIGH": None}
        for line in self.lines:
            norm = line["normalized"]
            if any(term in norm for term in ["target range", "ciljni opseg", "goal", "cilj", "above 10.0", "below 3.9", "each 5%", "svako povecanje", "свако повећање"]): continue
            
            if any(t in norm for t in ["very high", "веома високо", "veoma visoko", "veoma visok", "веома висок", "vrlo visoko", "врло високо"]):
                m = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:very\s*high|веома\s*високо|veoma\s*visoko|veoma\s*visok|веома\s*висок|vrlo\s*visoko|врло\s*високо)", norm)
                if m: results["VERY_HIGH"] = safe_float(m.group(1))
            elif any(t in norm for t in ["very low", "vrlo nisko", "врло ниско", "веома ниско", "veoma nizak", "веома низак"]):
                m = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:very\s*low|vrlo\s*nisko|врло\s*ниско|веома\s*ниско|veoma\s*nizak|веома\s*низак)", norm)
                if m: results["VERY_LOW"] = safe_float(m.group(1))
            elif any(t in norm for t in ["in range", "u opsegu", "у опсегу", "normalno", "нормално"]):
                m = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:in\s*range|u\s*opsegu|у\s*опсегу|normalno|нормално)", norm)
                if m: results["IN_RANGE"] = safe_float(m.group(1))
            elif any(t in norm for t in ["high", "високо", "visoko", "visok", "висок"]):
                m = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?<!very\s)(?<!veoma\s)(?<!веома\s)(?<!vrlo\s)(?<!врло\s)(?:high|високо|visoko|visok|висок)\b", norm)
                if m: results["HIGH"] = safe_float(m.group(1))
            elif any(t in norm for t in ["low", "nisko", "ниско", "nizak", "низак"]):
                m = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?<!very\s)(?<!veoma\s)(?<!веома\s)(?<!vrlo\s)(?<!врло\s)(?:low|nisko|ниско|nizak|низак)\b", norm)
                if m: results["LOW"] = safe_float(m.group(1))
        return results

    def _extract_candidates(self):
        self.candidates = []
        for line in self.lines:
            text, normalized = line["text"], line["normalized"]
            if any(p in normalized for p in ["1% of time", "about 15 min", "defined as percent", "median", "percentile", "printing date", "datum stampanja", "svako povecanje", "each 5% increase"]) or DATE_PATTERN.search(text): continue
            for match in NUMBER_PATTERN.finditer(text):
                val = safe_float(match.group("number"))
                if val is None: continue
                after_idx = match.end()
                if "min" in text[after_idx:after_idx+10].lower() or "h" in text[after_idx:after_idx+10].lower(): continue
                unit = "GLUCOSE" if ("mmol/l" in normalized or "mmol" in normalized) and "%" not in match.group(0) else "%"
                self.candidates.append({"value": val, "unit": unit, "bbox": line["bbox"], "page": line["page"], "line_id": line["line_id"]})

    def _extract_anchors(self):
        self.anchors = []
        for line in self.lines:
            norm = line["normalized"]
            for metric, definition in Metrics.items():
                for alias in definition["aliases"]:
                    if re.search(rf"\b{re.escape(alias)}\b", norm):
                        self.anchors.append({"metric": metric, "page": line["page"], "line_id": line["line_id"], "bbox": line["bbox"]})
                        break

    def _pair_range_components(self):
        anchors = [a for a in self.anchors if a["metric"] in ["VERY_LOW", "LOW", "IN_RANGE", "HIGH", "VERY_HIGH"]]
        candidates = [c for c in self.candidates if c["unit"] == "%"]
        result, used = {m: None for m in ["VERY_LOW", "LOW", "IN_RANGE", "HIGH", "VERY_HIGH"]}, set()
        for anchor in anchors:
            for c in candidates:
                if c["candidate_id"] in used if "candidate_id" in c else False or c["page"] != anchor["page"]: continue
                if vertical_distance(anchor["bbox"], c["bbox"]) <= 15:
                    result[anchor["metric"]] = c["value"]
                    break
        return result

    def _pair_standard_metrics(self):
        result = {"GMI": None, "CV": None, "ACTIVE_TIME": None, "AVG_GLUCOSE": None}
        for line in self.lines:
            norm = line["normalized"]
            text = line["text"]
            # Direktno prepoznavanje po linijama za GMI, CV, Aktivno vreme i Prosek da nema greške
            nums = [safe_float(m.group("number")) for m in NUMBER_PATTERN.finditer(text) if safe_float(m.group("number")) is not None]
            if not nums: continue
            
            if "glucose management indicator" in norm or "gmi" in norm or "indikator upravljanja" in norm:
                for n in nums:
                    if 3 <= n <= 15: result["GMI"] = n
            elif "variability" in norm or "coefficient of variation" in norm or "cv" in norm or "varijabilnost" in norm:
                for n in nums:
                    if n > 5: result["CV"] = n # CV je obično iznad 5% (kod tebe 21.2%)
            elif "cgm active" in norm or "active time" in norm or "aktivno vreme" in norm:
                for n in nums:
                    if n > 50: result["ACTIVE_TIME"] = n # Aktivno vreme je blizu 99%
            elif "average glucose" in norm or "mean glucose" in norm or "prosečna glukoza" in norm or "mbg" in norm:
                for n in nums:
                    if 2 <= n <= 30: result["AVG_GLUCOSE"] = n
        return result

    def _derive_metrics(self, actual: Dict[str, Any]):
        vl, l, ir, h, vh = actual.get("VERY_LOW"), actual.get("LOW"), actual.get("IN_RANGE"), actual.get("HIGH"), actual.get("VERY_HIGH")
        return {
            "TBR": round((vl or 0) + (l or 0), 2) if (vl is not None or l is not None) else None,
            "TIR": ir,
            "TAR": round((h or 0) + (vh or 0), 2) if (h is not None or vh is not None) else None
        }

    def _validate(self, actual, derived): return []
    def _review_reasons(self, actual, derived, period):
        return [f"Missing {m}" for m in ["VERY_LOW", "LOW", "IN_RANGE", "HIGH", "VERY_HIGH", "GMI", "CV", "ACTIVE_TIME", "AVG_GLUCOSE"] if actual.get(m) is None]

    def _extract_reporting_period(self):
        dates = []
        for line in self.lines:
            for m in DATE_PATTERN.finditer(line["text"]):
                if m.group(1) not in dates: dates.append(m.group(1))
        parsed = [(parse_date_flexible(d), d) for d in dates if parse_date_flexible(d)]
        if len(parsed) >= 2:
            parsed.sort(key=lambda x: x[0])
            return {"start": parsed[0][1], "end": parsed[1][1]}
        return {"start": dates[0] if dates else None, "end": dates[1] if len(dates) > 1 else None}
