from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import requests
import pymupdf as fitz
import re

app = FastAPI()

SUPABASE_URL = "https://tuhgurlibsaqqxrmhgdr.supabase.co"
SUPABASE_HEADERS = {
    "apikey": "sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39",
    "Authorization": "Bearer sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

METRIC_ONTOLOGY = {
    "VERY_LOW": {
        "aliases": ["very low"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "LOW": {
        "aliases": ["low"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "IN_RANGE": {
        "aliases": ["in range"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "HIGH": {
        "aliases": ["high"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "VERY_HIGH": {
        "aliases": ["very high"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "GMI": {
        "aliases": ["glucose management indicator", "(gmi)", "gmi"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (4, 15)
    },
    "CV": {
        "aliases": ["glucose variability", "coefficient of variation", "cv"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "ACTIVE_TIME": {
        "aliases": ["time cgm active", "active time"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "AVG_GLUCOSE": {
        "aliases": ["average glucose", "mean glucose"],
        "expected_role": "ACTUAL", "expected_unit": "DECIMAL", "valid_range": (2, 25)
    }
}

class UniversalCGMParser:
    def __init__(self, doc):
        self.doc = doc
        self.pages = []
        self.regions = []
        self.candidates = []
        self.anchors = []
        self.results = {key: None for key in METRIC_ONTOLOGY.keys()}
        self.reporting_period = {"start": None, "end": None}

    def parse(self):
        self._ingest_pdf()
        self._build_layout()
        self._classify_regions()
        self._extract_candidates()
        self._extract_anchors()
        self._associate_labels_and_values()
        self._derive_standardized_metrics()
        self._extract_reporting_period()
        return self._generate_final_report()

    def _ingest_pdf(self):
        for page_num, page in enumerate(self.doc):
            words_raw = page.get_text("words")
            page_words = []
            for idx, w in enumerate(words_raw):
                text = w[4].strip()
                if text:
                    page_words.append({
                        "word_no": idx,
                        "text": text,
                        "x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3],
                        "cx": (w[0] + w[2]) / 2, "cy": (w[1] + w[3]) / 2,
                        "block": w[5], "line": w[6], "page": page_num + 1
                    })
            self.pages.append({
                "page_num": page_num + 1,
                "width": page.rect.width,
                "height": page.rect.height,
                "raw_words": page_words
            })

    def _build_layout(self):
        region_counter = 0
        for page in self.pages:
            sorted_words = sorted(page["raw_words"], key=lambda w: (w["y0"], w["x0"]))
            lines_map = {}
            for w in sorted_words:
                line_key = (w["block"], w["line"])
                if line_key not in lines_map:
                    lines_map[line_key] = []
                lines_map[line_key].append(w)
            
            lines = list(lines_map.values())
            lines.sort(key=lambda l: l[0]["y0"])

            current_region_lines = []
            for line in lines:
                if not current_region_lines:
                    current_region_lines.append(line)
                else:
                    last_line_y = current_region_lines[-1][0]["y0"]
                    curr_line_y = line[0]["y0"]
                    if curr_line_y - last_line_y < 35:
                        current_region_lines.append(line)
                    else:
                        region_counter += 1
                        self._register_region(region_counter, current_region_lines, page["page_num"])
                        current_region_lines = [line]
            if current_region_lines:
                region_counter += 1
                self._register_region(region_counter, current_region_lines, page["page_num"])

    def _register_region(self, reg_id, lines, page_num):
        x0 = min(w["x0"] for line in lines for w in line)
        y0 = min(w["y0"] for line in lines for w in line)
        x1 = max(w["x1"] for line in lines for w in line)
        y1 = max(w["y1"] for line in lines for w in line)
        
        flat_words = [w for line in lines for w in line]
        text = " ".join([w["text"] for w in flat_words]).lower()
        
        self.regions.append({
            "region_id": f"reg_{page_num}_{reg_id}",
            "page": page_num,
            "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
            "text": text,
            "lines": lines,
            "words": flat_words,
            "context_type": "UNKNOWN"
        })

    def _classify_regions(self):
        for r in self.regions:
            if any(k in r["text"] for k in ["goal", "goals", "target", "recommended", "reference", "desired", "clinical target"]):
                r["context_type"] = "GOAL_ZONE"
            else:
                r["context_type"] = "ACTUAL_ZONE"

    def _is_descriptive_context(self, line_text):
        lower = line_text.lower()
        if "median" in lower or "percentile" in lower:
            return True
        if "of time in ranges" in lower or ("=" in line_text and "min" in lower):
            return True
        if re.search(r'\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b', line_text):
            return True
        return False

    def _extract_candidates(self):
        for r in self.regions:
            for line in r["lines"]:
                line_text = " ".join([w["text"] for w in line])
                line_text_lower = line_text.lower()
                
                if self._is_descriptive_context(line_text):
                    continue

                matches = re.finditer(r'([<>]?)\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*(%)?', line_text)
                for match in matches:
                    operator = match.group(1)
                    val_str = match.group(2)
                    has_percent = bool(match.group(3))
                    val = float(val_str.replace(',', '.'))
                    
                    matched_words = [w for w in line if val_str in w["text"] or (has_percent and "%" in w["text"])]
                    if not matched_words:
                        matched_words = line
                    
                    bx0 = min(w["x0"] for w in matched_words)
                    by0 = min(w["y0"] for w in matched_words)
                    bx1 = max(w["x1"] for w in matched_words)
                    by1 = max(w["y1"] for w in matched_words)
                    
                    if operator or any(k in line_text_lower for k in ["goal", "target", "recommended", "reference", "desired", "clinical target"]):
                        continue
                    elif r["context_type"] == "GOAL_ZONE":
                        continue
                    else:
                        role = "ACTUAL"
                    
                    self.candidates.append({
                        "raw_text": match.group(0),
                        "value": val,
                        "unit": "%" if has_percent else "DECIMAL",
                        "semantic_role": role,
                        "bbox": {"x0": bx0, "y0": by0, "x1": bx1, "y1": by1, "cx": (bx0+bx1)/2, "cy": (by0+by1)/2},
                        "page": line[0]["page"],
                        "region_id": r["region_id"],
                        "line_text": line_text_lower
                    })

    def _extract_anchors(self):
        for r in self.regions:
            for line in r["lines"]:
                line_text = " ".join([w["text"] for w in line]).lower()
                line_y = line[0]["cy"]
                for m_key, m_data in METRIC_ONTOLOGY.items():
                    for alias in m_data["aliases"]:
                        if alias in line_text:
                            if not any(a["metric"] == m_key and a["page"] == r["page"] and abs(a["cy"] - line_y) < 20 for a in self.anchors):
                                self.anchors.append({
                                    "metric": m_key,
                                    "expected_role": m_data["expected_role"],
                                    "expected_unit": m_data["expected_unit"],
                                    "raw_text": alias,
                                    "page": r["page"],
                                    "region_id": r["region_id"],
                                    "cy": line_y,
                                    "cx": sum(w["cx"] for w in line) / len(line),
                                    "bbox": {"x0": line[0]["x0"], "y0": line[0]["y0"], "x1": line[-1]["x1"], "y1": line[-1]["y1"]}
                                })

    def _associate_labels_and_values(self):
        for anchor in self.anchors:
            valid_candidates = []
            for c in self.candidates:
                if c["page"] != anchor["page"]: continue
                if c["unit"] != anchor["expected_unit"]:
                    if anchor["metric"] != "GMI": continue
                
                v_min, v_max = METRIC_ONTOLOGY[anchor["metric"]]["valid_range"]
                if not (v_min <= c["value"] <= v_max): continue
                
                y_diff = abs(c["bbox"]["cy"] - anchor["cy"])
                x_diff = abs(c["bbox"]["cx"] - anchor["cx"])
                
                if y_diff < 40 or c["region_id"] == anchor["region_id"]:
                    score = 0.7
                    if y_diff < 15: score += 0.2
                    if c["region_id"] == anchor["region_id"]: score += 0.1
                        
                    valid_candidates.append({
                        "candidate": c,
                        "score": score,
                        "distance": x_diff + (y_diff * 2)
                    })
            
            if valid_candidates:
                valid_candidates.sort(key=lambda x: (-x["score"], x["distance"]))
                best = valid_candidates[0]
                
                current_result = self.results[anchor["metric"]]
                if not current_result or best["score"] > current_result["confidence"]:
                    self.results[anchor["metric"]] = {
                        "value": best["candidate"]["value"],
                        "confidence": round(best["score"], 2),
                        "status": "OK",
                        "semantic_role": "ACTUAL",
                        "page": anchor["page"],
                        "source": {
                            "label_text": anchor["raw_text"],
                            "value_text": best["candidate"]["raw_text"],
                            "region_id": best["candidate"]["region_id"]
                        }
                    }

    def _derive_standardized_metrics(self):
        self.derived_metrics = {}
        
        vl = self.results.get("VERY_LOW")
        low = self.results.get("LOW")
        in_range = self.results.get("IN_RANGE")
        high = self.results.get("HIGH")
        vh = self.results.get("VERY_HIGH")

        self.derived_metrics["TBR"] = round(vl["value"] + low["value"], 1) if vl and low and vl.get("value") is not None and low.get("value") is not None else None
        self.derived_metrics["TIR"] = in_range["value"] if in_range and in_range.get("value") is not None else None
        self.derived_metrics["TAR"] = round(high["value"] + vh["value"], 1) if high and vh and high.get("value") is not None and vh.get("value") is not None else None

    def _extract_reporting_period(self):
        date_pattern = r'\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b'
        for page in self.pages:
            full_page_text = " ".join([w["text"] for w in page["raw_words"]])
            dates = re.findall(date_pattern, full_page_text)
            if len(dates) >= 2:
                self.reporting_period["start"] = dates[0]
                self.reporting_period["end"] = dates[1]
                break

    def _generate_final_report(self):
        actual_metrics = {}
        for key, res in self.results.items():
            if res and res.get("value") is not None:
                actual_metrics[key] = res["value"]
            else:
                actual_metrics[key] = None

        return {
            "status": "SUCCESS",
            "reporting_period": self.reporting_period,
            "actual_components": actual_metrics,
            "derived_metrics": self.derived_metrics,
            "debug_raw_data": {
                "total_pages": len(self.pages),
                "pages": [{
                    "page_number": p["page_num"],
                    "width": p["width"],
                    "height": p["height"],
                    "word_count": len(p["raw_words"]),
                    "words": p["raw_words"][:100]
                } for p in self.pages],
                "extracted_candidates": self.candidates,
                "detected_anchors": self.anchors
            }
        }

@app.post("/debug-parser-test")
async def debug_parser_test(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    parser = UniversalCGMParser(doc)
    report = parser.parse()
    return JSONResponse(content=report)

@app.get("/", response_class=HTMLResponse)
def patient_form():
    return """
<!DOCTYPE html>
<html lang="sr"><head><meta charset="UTF-8"><title>Parser Test & Debug Mode</title></head>
<body style="font-family:sans-serif; background:#0f172a; color:#f8fafc; padding:20px; display:flex; justify-content:center;">
<div style="width:100%; max-width:800px;">
<h2>CGM Parser Test & Debug Inspector</h2>
<div style="display:flex; gap:10px; margin-bottom:15px;">
    <button onclick="switchTab('result')" id="btnRes" style="background:#0d9488; color:white; border:none; padding:8px 16px; border-radius:6px; cursor:pointer; font-weight:bold;">Parsed Result</button>
    <button onclick="switchTab('debug')" id="btnDbg" style="background:#334155; color:#cbd5e1; border:none; padding:8px 16px; border-radius:6px; cursor:pointer; font-weight:bold;">Debug Inspector (Raw Words & BBoxes)</button>
</div>
<div style="background:#1e293b; padding:20px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.3);">
<form id="f"><input type="file" id="fi" accept=".pdf" required style="margin-bottom:15px; color:#cbd5e1;"><br>
<button type="submit" style="background:#0d9488; color:white; border:none; padding:10px 20px; border-radius:6px; font-weight:bold; cursor:pointer;">Pokreni Analizu Izveštaja</button></form>
<pre id="s" style="margin-top:15px; font-size:11px; max-height:450px; overflow:auto; background:#030712; padding:15px; border-radius:6px; color:#34d399;"></pre>
</div></div>
<script>
let lastData = null;
let currentTab = 'result';

function switchTab(tab) {
    currentTab = tab;
    document.getElementById('btnRes').style.background = tab === 'result' ? '#0d9488' : '#334155';
    document.getElementById('btnRes').style.color = tab === 'result' ? 'white' : '#cbd5e1';
    document.getElementById('btnDbg').style.background = tab === 'debug' ? '#0d9488' : '#334155';
    document.getElementById('btnDbg').style.color = tab === 'debug' ? 'white' : '#cbd5e1';
    renderOutput();
}

function renderOutput() {
    if(!lastData) return;
    if(currentTab === 'result') {
        const displayObj = {
            status: lastData.status,
            reporting_period: lastData.reporting_period,
            actual_components: lastData.actual_components,
            derived_metrics: lastData.derived_metrics
        };
        document.getElementById('s').innerText = JSON.stringify(displayObj, null, 2);
    } else {
        document.getElementById('s').innerText = JSON.stringify(lastData.debug_raw_data, null, 2);
    }
}

document.getElementById('f').onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(); fd.append('file', document.getElementById('fi').files[0]);
    document.getElementById('s').innerText = "Analiza u toku...";
    const res = await fetch('/debug-parser-test', { method: 'POST', body: fd });
    lastData = await res.json();
    renderOutput();
};
</script></body></html>
"""
