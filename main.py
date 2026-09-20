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

# Primarna ontologija strogih pacijent metrika i komponenti
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
        "aliases": ["glucose management indicator", "gmi", "estimated a1c", "hba1c"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (4, 15)
    },
    "CV": {
        "aliases": ["glucose variability", "coefficient of variation", "cv"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "ACTIVE_TIME": {
        "aliases": ["time cgm active", "active time", "sensor active"],
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
        # 1. RAW PDF EXTRACTION
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
                    if curr_line_y - last_line_y < 30:
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
            elif any(k in r["text"] for k in ["in range", "very low", "low", "high", "very high"]):
                r["context_type"] = "ACTUAL_ZONE"

    def _is_descriptive_context(self, line_text):
        """Precizna identifikacija deskriptivnih konteksta (medijana, percentila, osa, formula)."""
        lower = line_text.lower()
        if "median" in lower or "percentile" in lower or "min" in lower:
            return True
        if "of time in ranges" in lower or "=" in line_text:
            return True
        if re.search(r'\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b', line_text):
            return True
        return False

    def _extract_candidates(self):
        for r in self.regions:
            for line in r["lines"]:
                line_text = " ".join([w["text"] for w in line])
                line_text_lower = line_text.lower()
                
                # Filtriranje deskriptivnih konteksta umesto glupog brisanja svih zagrada
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
                    
                    role = "UNKNOWN"
                    reason = []
                    
                    if operator or any(k in line_text_lower for k in ["goal", "target", "recommended", "reference", "desired", "clinical target"]):
                        role = "GOAL"
                        reason.append("GOAL_OR_REFERENCE_CONTEXT")
                    elif r["context_type"] == "GOAL_ZONE":
                        role = "GOAL"
                        reason.append("GOAL_ZONE_CONTEXT")
                    elif r["context_type"] == "ACTUAL_ZONE":
                        role = "ACTUAL"
                        reason.append("ACTUAL_ZONE_MATCH")
                    else:
                        # Broj bez operatora i bez eksplicitnog konteksta NIJE automatski patient result
                        role = "UNKNOWN"
                        reason.append("UNVERIFIED_CONTEXT_UNKNOWN")
                    
                    if role != "UNKNOWN":
                        self.candidates.append({
                            "raw_text": match.group(0),
                            "value": val,
                            "unit": "%" if has_percent else "DECIMAL",
                            "semantic_role": role,
                            "bbox": {"x0": bx0, "y0": by0, "x1": bx1, "y1": by1, "cx": (bx0+bx1)/2, "cy": (by0+by1)/2},
                            "page": line[0]["page"],
                            "region_id": r["region_id"],
                            "reason_codes": reason,
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
                            if not any(a["metric"] == m_key and a["page"] == r["page"] and abs(a["cy"] - line_y) < 10 for a in self.anchors):
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
        """Stroga semantička kompatibilnost uz prostornu proveru."""
        for anchor in self.anchors:
            valid_candidates = []
            for c in self.candidates:
                if c["page"] != anchor["page"]: continue
                if c["semantic_role"] != "ACTUAL": continue
                if c["unit"] != anchor["expected_unit"]: continue
                
                v_min, v_max = METRIC_ONTOLOGY[anchor["metric"]]["valid_range"]
                if not (v_min <= c["value"] <= v_max): continue
                
                y_diff = abs(c["bbox"]["cy"] - anchor["cy"])
                x_diff = abs(c["bbox"]["cx"] - anchor["cx"])
                
                # Osnovni uslov: semantička kompatibilnost + bliska blizina unutar regije
                if y_diff < 30 and (c["region_id"] == anchor["region_id"] or x_diff < 150):
                    score = 0.7
                    evidence = ["SEMANTIC_AND_UNIT_MATCH"]
                    if y_diff < 12: 
                        score += 0.2
                        evidence.append("CLOSE_VERTICAL_PROXIMITY")
                    if c["region_id"] == anchor["region_id"]:
                        score += 0.1
                        evidence.append("SAME_REGION")
                        
                    valid_candidates.append({
                        "candidate": c,
                        "score": score,
                        "distance": x_diff + (y_diff * 2),
                        "evidence": evidence
                    })
            
            if valid_candidates:
                valid_candidates.sort(key=lambda x: (-x["score"], x["distance"]))
                best = valid_candidates[0]
                
                # Ambiguitet check
                if len(valid_candidates) > 1 and (best["score"] - valid_candidates[1]["score"] < 0.1):
                    self.results[anchor["metric"]] = {
                        "value": None, "confidence": best["score"], "status": "MANUAL_REVIEW",
                        "semantic_role": "UNKNOWN", "page": anchor["page"],
                        "source": {"label_text": anchor["raw_text"], "region_id": best["candidate"]["region_id"]},
                        "reason_codes": ["AMBIGUOUS_CANDIDATES_MANUAL_REVIEW"]
                    }
                    continue

                current_result = self.results[anchor["metric"]]
                if not current_result or best["score"] > current_result["confidence"]:
                    self.results[anchor["metric"]] = {
                        "value": best["candidate"]["value"],
                        "confidence": round(best["score"], 2),
                        "status": "OK" if best["score"] >= 0.8 else "MANUAL_REVIEW",
                        "semantic_role": best["candidate"]["semantic_role"],
                        "page": anchor["page"],
                        "source": {
                            "label_text": anchor["raw_text"],
                            "value_text": best["candidate"]["raw_text"],
                            "label_bbox": anchor["bbox"],
                            "value_bbox": best["candidate"]["bbox"],
                            "region_id": best["candidate"]["region_id"]
                        },
                        "reason_codes": best["evidence"] + best["candidate"]["reason_codes"]
                    }

    def _derive_standardized_metrics(self):
        """2. DERIVED STANDARDIZED METRICS iz Actual Range Breakdown-a."""
        self.derived_metrics = {}
        
        vl = self.results.get("VERY_LOW")
        low = self.results.get("LOW")
        in_range = self.results.get("IN_RANGE")
        high = self.results.get("HIGH")
        vh = self.results.get("VERY_HIGH")

        # TBR = Very Low + Low
        if vl and low and vl.get("value") is not None and low.get("value") is not None:
            self.derived_metrics["TBR"] = round(vl["value"] + low["value"], 1)
        else:
            self.derived_metrics["TBR"] = None

        # TIR = In Range
        if in_range and in_range.get("value") is not None:
            self.derived_metrics["TIR"] = in_range["value"]
        else:
            self.derived_metrics["TIR"] = None

        # TAR = High + Very High
        if high and vh and high.get("value") is not None and vh.get("value") is not None:
            self.derived_metrics["TAR"] = round(high["value"] + vh["value"], 1)
        else:
            self.derived_metrics["TAR"] = None

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
            "derived_metrics": self.derived_metrics
        }

# --- TEST ENDPOINT ZA PROVERU PRE SLANJA U BAZU ---
@app.post("/debug-parser-test")
async def debug_parser_test(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    parser = UniversalCGMParser(doc)
    report = parser.parse()
    return JSONResponse(content=report)

@app.post("/upload")
async def upload_report(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    patient_id = "P-000127"
    
    parser = UniversalCGMParser(doc)
    extracted = parser.parse()
    
    actuals = extracted["actual_components"]
    derived = extracted["derived_metrics"]
    
    parsed_data = {
        "patient_id": patient_id, 
        "device_name": "mySugr AGP Izveštaj", 
        "manufacturer": "mySugr",
        "tir": derived.get("TIR"), 
        "tbr": derived.get("TBR"), 
        "tar": derived.get("TAR"),
        "gmi_percent": actuals.get("GMI"), 
        "cv": actuals.get("CV"), 
        "active_time": str(actuals.get("ACTIVE_TIME")) + "%" if actuals.get("ACTIVE_TIME") is not None else None
    }
    
    response = requests.post(f"{SUPABASE_URL}/rest/v1/cgm_reports", headers=SUPABASE_HEADERS, json=parsed_data)
    if response.status_code not in [200, 201]: 
        raise HTTPException(status_code=500, detail=f"Baza odbila: {response.text}")
        
    return {"status": "success", "data": parsed_data, "engine_report": extracted}

@app.get("/", response_class=HTMLResponse)
def patient_form():
    return """
<!DOCTYPE html>
<html lang="sr"><head><meta charset="UTF-8"><title>Parser Test Mode</title></head>
<body style="font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh; background:#0f172a; color:#f8fafc;">
<div style="background:#1e293b; padding:30px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.3); width:100%; max-width:450px;">
<h2>CGM Parser Test Mode</h2>
<form id="f"><input type="file" id="fi" accept=".pdf" required style="margin:20px 0; color:#cbd5e1;"><br>
<button type="submit" style="background:#0d9488; color:white; border:none; padding:12px 20px; border-radius:6px; width:100%; font-weight:bold; cursor:pointer;">Testiraj Izveštaj</button></form>
<pre id="s" style="margin-top:15px; font-size:11px; max-height:250px; overflow:auto; background:#030712; padding:10px; border-radius:6px; color:#34d399;"></pre></div>
<script>
document.getElementById('f').onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(); fd.append('file', document.getElementById('fi').files[0]);
    document.getElementById('s').innerText = "Analiza u toku...";
    const res = await fetch('/debug-parser-test', { method: 'POST', body: fd });
    const data = await res.json();
    document.getElementById('s').innerText = JSON.stringify(data, null, 2);
};
</script></body></html>
"""
