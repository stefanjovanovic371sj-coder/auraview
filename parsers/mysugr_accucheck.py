import fitz  # PyMuPDF
import re
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple

# ============================================================
# DEFINICIJE METRIKA (Ažurirano sa tačnim srpskim izrazima)
# ============================================================
Metrics = {
    "VERY_LOW": {"aliases": ["very low", "very-low", "vrlo nisko", "веома ниско", "veoma nizak nivo"], "unit": "%", "type": "RANGE_COMPONENT"},
    "LOW": {"aliases": ["low", "tbr", "below range", "nisko", "ниско", "nizak nivo"], "unit": "%", "type": "RANGE_COMPONENT"},
    "IN_RANGE": {"aliases": ["in range", "in-range", "time in range", "normal", "tir", "u opsegu", "у опсегу", "normalno", "нормално"], "unit": "%", "type": "RANGE_COMPONENT"},
    "HIGH": {"aliases": ["high", "tar", "above range", "visoko", "високо", "visok nivo"], "unit": "%", "type": "RANGE_COMPONENT"},
    "VERY_HIGH": {"aliases": ["very high", "very-high", "vrlo visoko", "веома високо", "veoma visoko", "veoma visok nivo"], "unit": "%", "type": "RANGE_COMPONENT"},
    "GMI": {"aliases": ["glucose management indicator", "gmi", "indikator upravljanja glukozom", "индикатор управљања глукозом"], "unit": "%", "type": "METRIC"},
    "CV": {"aliases": ["glucose variability", "coefficient of variation", "percent coefficient of variation", "gv (cvs)", "gv (cv)", "gv", "cv", "varijabilnost", "варијабилност", "varijabilnost glukoze", "варијабилност глукозе", "koeficijent varijacije"], "unit": "%", "type": "METRIC"},
    "ACTIVE_TIME": {"aliases": ["time cgm active", "time cgM active", "cgm active", "active time", "sensor active", "cgm coverage time", "coverage time", "aktivno vreme cgm", "aktivno vreme", "активно време"], "unit": "%", "type": "METRIC"},
    "AVG_GLUCOSE": {"aliases": ["average glucose", "mean glucose", "mbg", "mean blood glucose", "prosečna vrednost glukoze", "просечна вредност глукозе", "prosečna glukoza", "просечна глукоза"], "unit": "GLUCOSE", "type": "METRIC"}
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
    srb_to_eng = {
        "јан": "Jan", "feb": "Feb", "мар": "Mar", "апр": "Apr", "мај": "May", "јун": "Jun", 
        "јул": "Jul", "авг": "Aug", "сеп": "Sep", "окт": "Oct", "нов": "Nov", "дец": "Dec", 
        "jan": "Jan", "mar": "Mar", "apr": "Apr", "maj": "May", "jun": "Jun", "jul": "Jul", 
        "avg": "Aug", "sep": "Sep", "okt": "Oct", "nov": "Nov", "dec": "Dec"
    }
    for k, v in srb_to_eng.items():
        if k in date_str.lower():
            parts = date_str.split()
            for i, part in enumerate(parts):
                if k in part.lower():
                    parts[i] = v
            date_str = " ".join(parts)
            break
    for fmt in ("%d %b %Y", "%d %B %Y", "%d %m %Y"):
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
            "AVG_GLUCOSE": metric_values.get("AVG_GLUCOSE")
        }

        derived_metrics = self._derive_metrics(actual_components)
        reporting_period = self._extract_reporting_period()
        
        review_reasons = []
        status = "SUCCESS" if not review_reasons else "MANUAL_REVIEW"

        return {
            "status": status, 
            "reporting_period": reporting_period,
            "actual_components": actual_components, 
            "derived_metrics": derived_metrics,
            "validation_warnings": [], 
            "review_reasons": review_reasons
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
        
        for page_number in range(min(2, len(document))):
            page = document[page_number]
            for index, item in enumerate(page.get_text("words")):
                if len(item) < 8 or not item[4].strip(): continue
                self.words.append({
                    "page": page_number + 1, 
                    "index": index, 
                    "bbox": [float(item[0]), float(item[1]), float(item[2]), float(item[3])], 
                    "text": item[4].strip(), 
                    "normalized": normalize_text(item[4]), 
                    "block_no": item[5], 
                    "line_no": item[6], 
                    "word_no": item[7]
                })
        document.close()

    def _build_lines(self):
        grouped = {}
        for word in self.words:
            grouped.setdefault((word["page"], word["block_no"], word["line_no"]), []).append(word)
        lines = []
        for key, words in grouped.items():
            words = sorted(words, key=lambda w: w["x0"])
            text = " ".join(w["text"] for w in words)
            lines.append({
                "line_id": len(lines), 
                "page": key[0], 
                "block_no": key[1], 
                "line_no": key[2], 
                "text": text, 
                "normalized": normalize_text(text), 
                "bbox": bbox_union([w["bbox"] for w in words]), 
                "words": words
            })
        self.lines = sorted(lines, key=lambda x: (x["page"], x["bbox"][1], x["bbox"][0]))

    def _build_regions(self):
        self.regions = []
        for page_number in range(1, 3):
            page_lines = [l for l in self.lines if l["page"] == page_number]
            if not page_lines: continue
            current, region_id = [], 0
            for line in page_lines:
                if not current or line["bbox"][1] - current[-1]["bbox"][3] <= 45 or line["block_no"] == current[-1]["block_no"]:
                    current.append(line)
                else:
                    self.regions.append({"region_id": f"{page_number}-{region_id}", "page": page_number, "line_ids": [l["line_id"] for l in current]})
                    region_id += 1
                    current = [line]
            if current:
                self.regions.append({"region_id": f"{page_number}-{region_id}", "page": page_number, "line_ids": [l["line_id"] for l in current]})

    def _extract_candidates(self):
        self.candidates = []
        for line in self.lines:
            if DATE_PATTERN.search(line["text"]): continue
            for match in NUMBER_PATTERN.finditer(line["text"]):
                val = safe_float(match.group("number"))
                if val is None: continue
                unit = "GLUCOSE" if ("mmol" in line["normalized"] and "%" not in match.group(0)) else "%"
                self.candidates.append({
                    "candidate_id": len(self.candidates), 
                    "value": val, 
                    "unit": unit, 
                    "bbox": line["bbox"], 
                    "page": line["page"], 
                    "line_id": line["line_id"], 
                    "region_id": self._region_for_line(line["line_id"])
                })

    def _region_for_line(self, line_id: int) -> Optional[str]:
        for r in self.regions:
            if line_id in r["line_ids"]: return r["region_id"]
        return None

    def _extract_anchors(self):
        self.anchors = []
        for line in self.lines:
            for metric, definition in Metrics.items():
                for alias in definition["aliases"]:
                    if alias in line["normalized"]:
                        self.anchors.append({
                            "anchor_id": len(self.anchors), 
                            "metric": metric, 
                            "page": line["page"], 
                            "line_id": line["line_id"], 
                            "region_id": self._region_for_line(line["line_id"]), 
                            "bbox": line["bbox"]
                        })
                        break

    def _pair_range_components(self):
        range_metrics = ["VERY_LOW", "LOW", "IN_RANGE", "HIGH", "VERY_HIGH"]
        anchors = [a for a in self.anchors if a["metric"] in range_metrics]
        candidates = [c for c in self.candidates if c["unit"] == "%"]
        result, used = {m: None for m in range_metrics}, set()
        for anchor in anchors:
            for c in candidates:
                if c["candidate_id"] in used or c["page"] != anchor["page"]: continue
                if c["line_id"] == anchor["line_id"]:
                    result[anchor["metric"]] = c["value"]
                    used.add(c["candidate_id"])
                    break
        return result

    def _pair_standard_metrics(self):
        target_metrics = ["GMI", "CV", "ACTIVE_TIME", "AVG_GLUCOSE"]
        result = {metric: None for metric in target_metrics}
        for metric in target_metrics:
            anchors = [a for a in self.anchors if a["metric"] == metric]
            if not anchors: continue
            for a in anchors:
                valid_candidates = []
                for c in self.candidates:
                    if c["page"] != a["page"]: continue
                    if metric == "AVG_GLUCOSE" and c["unit"] != "GLUCOSE": continue
                    if metric != "AVG_GLUCOSE" and c["unit"] != "%": continue
                    
                    vd = vertical_distance(a["bbox"], c["bbox"])
                    if vd <= 180:
                        valid_candidates.append((vd, c))
                
                if valid_candidates:
                    valid_candidates.sort(key=lambda x: x[0])
                    result[metric] = valid_candidates[0][1]["value"]
                    break
            if metric in result: break
        return result

    def _derive_metrics(self, actual: Dict[str, Any]):
        vl = actual.get("VERY_LOW")
        l = actual.get("LOW")
        ir = actual.get("IN_RANGE")
        h = actual.get("HIGH")
        vh = actual.get("VERY_HIGH")
        
        # Bezbedno računanje čak i ako je neka vrednost ostala neprepoznata
        tbr_val = round((vl or 0) + (l or 0), 2) if (vl is not None or l is not None) else None
        tar_val = round((h or 0) + (vh or 0), 2) if (h is not None or vh is not None) else None
        
        return {
            "TBR": tbr_val,
            "TIR": ir,
            "TAR": tar_val
        }

    def _extract_reporting_period(self):
        dates = []
        for page_number in range(1, 3):
            page_text = " ".join(w["text"] for w in self.words if w["page"] == page_number)
            for m in DATE_PATTERN.finditer(page_text):
                if m.group(1) not in dates: 
                    dates.append(m.group(1))
        
        parsed = [(parse_date_flexible(d), d) for d in dates if parse_date_flexible(d)]
        
        if len(parsed) >= 2:
            parsed.sort(key=lambda x: x[0])
            return {"start": parsed[0][1], "end": parsed[1][1]}
        
        return {"start": dates[0] if dates else None, "end": dates[1] if len(dates) > 1 else None}
