from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
import requests
import pymupdf as fitz
import re
import math

app = FastAPI()

SUPABASE_URL = "https://tuhgurlibsaqqxrmhgdr.supabase.co"
SUPABASE_HEADERS = {
    "apikey": "sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39",
    "Authorization": "Bearer sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

METRIC_ONTOLOGY = {
    "TIR": {
        "canonical": "TIR",
        "aliases": ["time in range", "in range", "target range", "within range", "u ciljnom opsegu", "ciljni opseg"],
        "expected_role": "ACTUAL",
        "expected_unit": "%",
        "valid_range": (0, 100)
    },
    "TAR": {
        "canonical": "TAR",
        "aliases": ["time above range", "above range", "high", "iznad opsega", "visoko"],
        "expected_role": "ACTUAL",
        "expected_unit": "%",
        "valid_range": (0, 100)
    },
    "TBR": {
        "canonical": "TBR",
        "aliases": ["time below range", "below range", "low", "ispod opsega", "nisko"],
        "expected_role": "ACTUAL",
        "expected_unit": "%",
        "valid_range": (0, 100)
    },
    "VERY_HIGH": {
        "canonical": "VERY_HIGH",
        "aliases": ["very high", "veoma visoko"],
        "expected_role": "ACTUAL",
        "expected_unit": "%",
        "valid_range": (0, 100)
    },
    "VERY_LOW": {
        "canonical": "VERY_LOW",
        "aliases": ["very low", "veoma nisko"],
        "expected_role": "ACTUAL",
        "expected_unit": "%",
        "valid_range": (0, 100)
    },
    "GMI": {
        "canonical": "GMI",
        "aliases": ["glucose management indicator", "gmi", "estimated a1c", "hba1c"],
        "expected_role": "ACTUAL",
        "expected_unit": "DECIMAL",
        "valid_range": (4, 15)
    },
    "CV": {
        "canonical": "CV",
        "aliases": ["glucose variability", "coefficient of variation", "cv", "varijabilnost"],
        "expected_role": "ACTUAL",
        "expected_unit": "%",
        "valid_range": (0, 100)
    },
    "ACTIVE_TIME": {
        "canonical": "ACTIVE_TIME",
        "aliases": ["time cgm active", "active time", "sensor active", "vreme aktivnosti"],
        "expected_role": "ACTUAL",
        "expected_unit": "%",
        "valid_range": (0, 100)
    },
    "AVG_GLUCOSE": {
        "canonical": "AVG_GLUCOSE",
        "aliases": ["average glucose", "mean glucose", "prosečna glukoza"],
        "expected_role": "ACTUAL",
        "expected_unit": "DECIMAL",
        "valid_range": (2, 25)
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
        self.ocr_required = False

    def parse(self):
        print("--- POČETAK PARSIRANJA ---")
        self._ingest_pdf()
        if self.ocr_required:
            return {"status": "ERROR", "error_type": "OCR_REQUIRED"}
        
        self._build_layout()
        self._classify_regions()
        self._extract_candidates()
        self._extract_anchors()
        self._associate_labels_and_values()
        self._aggregate_metrics()
        self._validate_cluster()
        self._extract_reporting_period()
        print("--- KRAJ PARSIRANJA ---")
        return self._generate_final_report()

    def _ingest_pdf(self):
        total_words = 0
        # Optimizacija: čitamo samo prve 2 stranice da ne gušimo Render server
        max_pages = min(2, len(self.doc))
        for page_num in range(max_pages):
            page = self.doc[page_num]
            words = page.get_text("words")
            total_words += len(words)
            page_data = {"page_num": page_num + 1, "words": []}
            for w in words:
                text = w[4].strip()
                if text:
                    page_data["words"].append({
                        "text": text, "x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3],
                        "cx": (w[0] + w[2]) / 2, "cy": (w[1] + w[3]) / 2,
                        "block": w[5], "line": w[6], "page": page_num + 1
                    })
            self.pages.append(page_data)
        if total_words < 10:
            self.ocr_required = True

    def _build_layout(self):
        region_counter = 0
        for page in self.pages:
            page['words'].sort(key=lambda w: (w["y0"], w["x0"]))
            current_line = []
            page_lines = []
            for w in page['words']:
                if not current_line: current_line.append(w)
                else:
                    if abs(w["cy"] - current_line[-1]["cy"]) < 6: current_line.append(w)
                    else: page_lines.append(current_line); current_line = [w]
            if current_line: page_lines.append(current_line)

            current_region = []
            for line in page_lines:
                if not current_region: current_region.append(line)
                else:
                    if line[0]["cy"] - current_region[-1][0]["cy"] < 25: current_region.append(line)
                    else:
                        region_counter += 1
                        self._register_region(region_counter, current_region, page["page_num"])
                        current_region = [line]
            if current_region:
                region_counter += 1
                self._register_region(region_counter, current_region, page["page_num"])

    def _register_region(self, reg_id, lines, page_num):
        x0 = min(w["x0"] for line in lines for w in line)
        y0 = min(w["y0"] for line in lines for w in line)
        x1 = max(w["x1"] for line in lines for w in line)
        y1 = max(w["y1"] for line in lines for w in line)
        text = " ".join([" ".join([w["text"] for w in line]) for line in lines]).lower()
        self.regions.append({
            "region_id": f"reg_{page_num}_{reg_id}", "page": page_num,
            "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
            "text": text, "lines": lines, "context_type": "UNKNOWN"
        })

    def _classify_regions(self):
        for r in self.regions:
            if any(k in r["text"] for k in ["goal", "target", "cilj", "recommended", "reference"]):
                r["context_type"] = "GOAL_ZONE"

    def _extract_candidates(self):
        for r in self.regions:
            for line in r["lines"]:
                line_text = " ".join([w["text"] for w in line]).lower()
                for w in line:
                    t = w["text"].lower()
                    has_goal_operator = bool(re.search(r'[<>≤≥]', t))
                    clean_text = re.sub(r'[<>≤≥]', '', t)
                    is_percent = "%" in clean_text
                    perc_match = re.search(r'(\d{1,3}(?:[.,]\d{1,2})?)', clean_text)
                    
                    val, unit, c_type = None, None, None
                    if perc_match and is_percent:
                        val = float(perc_match.group(1).replace(',', '.'))
                        unit = "%"; c_type = "PERCENT"
                    elif re.match(r'^\d{1,3}[.,]\d{1,2}$', clean_text):
                        val = float(clean_text.replace(',', '.'))
                        unit = "DECIMAL"; c_type = "DECIMAL"
                    
                    if val is not None:
                        role, reason = "UNKNOWN", []
                        if has_goal_operator: role, reason = "GOAL", ["GOAL_OPERATOR"]
                        elif "target" in line_text or "goal" in line_text: role, reason = "GOAL", ["INLINE_GOAL"]
                        elif r["context_type"] == "GOAL_ZONE": role, reason = "GOAL", ["REGION_GOAL"]
                        else: role, reason = "ACTUAL", ["DEFAULT_ACTUAL"]

                        self.candidates.append({
                            "raw_text": w["text"], "value": val, "unit": unit, "type": c_type,
                            "bbox": {"x0": w["x0"], "y0": w["y0"], "x1": w["x1"], "y1": w["y1"], "cx": w["cx"], "cy": w["cy"]},
                            "page": w["page"], "region_id": r["region_id"], "semantic_role": role,
                            "reason_codes": reason, "line_text": line_text
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
                                    "metric": m_key, "expected_role": m_data["expected_role"],
                                    "expected_unit": m_data["expected_unit"], "raw_text": alias,
                                    "page": r["page"], "region_id": r["region_id"], "cy": line_y,
                                    "cx": sum(w["cx"] for w in line) / len(line), "line_text": line_text
                                })

    def _associate_labels_and_values(self):
        for anchor in self.anchors:
            valid_candidates = []
            for c in self.candidates:
                if c["page"] != anchor["page"]: continue
                if anchor["expected_role"] == "ACTUAL" and c["semantic_role"] == "GOAL": continue
                if c["unit"] != anchor["expected_unit"]: continue
                
                v_min, v_max = METRIC_ONTOLOGY[anchor["metric"]]["valid_range"]
                if not (v_min <= c["value"] <= v_max): continue
                
                y_diff = abs(c["bbox"]["cy"] - anchor["cy"])
                x_diff = abs(c["bbox"]["cx"] - anchor["cx"])
                
                if y_diff < 15 or (y_diff < 40 and c["region_id"] == anchor["region_id"]):
                    score, evidence = 0.5, ["ROLE_MATCH", "UNIT_MATCH"]
                    if y_diff < 10: score += 0.3; evidence.append("SAME_LINE")
                    if c["region_id"] == anchor["region_id"]: score += 0.2; evidence.append("SAME_REGION")
                        
                    valid_candidates.append({"candidate": c, "score": score, "distance": x_diff + (y_diff * 2), "evidence": evidence})
            
            if valid_candidates:
                valid_candidates.sort(key=lambda x: (-x["score"], x["distance"]))
                best = valid_candidates[0]
                current_result = self.results[anchor["metric"]]
                if not current_result or best["score"] > current_result["confidence"]:
                    self.results[anchor["metric"]] = {
                        "value": best["candidate"]["value"], "confidence": round(best["score"], 2),
                        "status": "OK" if best["score"] >= 0.8 else "MANUAL_REVIEW",
                        "semantic_role": best["candidate"]["semantic_role"], "page": anchor["page"],
                        "source": {"label_text": anchor["raw_text"], "value_text": best["candidate"]["raw_text"], "region_id": best["candidate"]["region_id"]},
                        "reason_codes": best["evidence"] + best["candidate"]["reason_codes"]
                    }

    def _aggregate_metrics(self):
        tbr_res = self.results["TBR"]
        vl_res = self.results["VERY_LOW"]
        if vl_res and tbr_res and vl_res["source"]["region_id"] == tbr_res["source"]["region_id"]:
            self.results["TBR"]["value"] = round(vl_res["value"] + tbr_res["value"], 1)
            self.results["TBR"]["reason_codes"].append("AGGREGATED_VERY_LOW_AND_LOW")

    def _validate_cluster(self):
        tir, tar, tbr = self.results["TIR"], self.results["TAR"], self.results["TBR"]
        if tir and tar and tbr and all(v["status"] in ["OK", "MANUAL_REVIEW"] for v in [tir, tar, tbr]):
            total = tir["value"] + tar["value"] + tbr["value"]
            if 98 <= total <= 102:
                for metric in [tir, tar, tbr]:
                    metric["confidence"] = min(1.0, metric["confidence"] + 0.1)
                    metric["status"] = "OK"
                    metric["reason_codes"].append("VALIDATED_BY_CLUSTER_SUM_100")

    def _extract_reporting_period(self):
        date_pattern = r'\b\d{1,2}[\s./-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|[a-zA-Z]{3}|\d{1,2})[\s./-]\d{2,4}\b'
        for r in self.regions:
            if any(k in r["text"] for k in ["period", "date", "od", "do", "from", "to"]):
                dates = re.findall(date_pattern, r["text"])
                if len(dates) >= 2:
                    self.reporting_period["start"] = dates[0]
                    self.reporting_period["end"] = dates[1]
                    break

    def _generate_final_report(self):
        final_metrics = {}
        for key, res in self.results.items():
            if res: final_metrics[key] = res
            else: final_metrics[key] = {"value": None, "confidence": 0.0, "status": "NOT_FOUND", "semantic_role": "UNKNOWN", "reason_codes": []}
        return {"status": "SUCCESS", "manufacturer": "Unknown", "reporting_period": self.reporting_period, "metrics": final_metrics}

@app.post("/upload")
async def upload_report(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    patient_id = "P-000127"
    
    patient_check = requests.get(f"{SUPABASE_URL}/rest/v1/patients?patient_id=eq.{patient_id}", headers=SUPABASE_HEADERS)
    if not patient_check.json():
        requests.post(f"{SUPABASE_URL}/rest/v1/patients", headers=SUPABASE_HEADERS, json={"patient_id": patient_id, "name": "Stefan Jovanović"})
    
    parser = UniversalCGMParser(doc)
    extracted = parser.parse()
    
    if extracted.get("status") == "ERROR" and extracted.get("error_type") == "OCR_REQUIRED":
        raise HTTPException(status_code=422, detail="PDF ne sadrži tekst.")

    metrics = extracted["metrics"]
    parsed_data = {
        "patient_id": patient_id, "device_name": "CGM Izveštaj", "manufacturer": "Standardni CGM",
        "tir": metrics["TIR"]["value"], "tbr": metrics["TBR"]["value"], "tar": metrics["TAR"]["value"],
        "gmi_percent": metrics["GMI"]["value"], "cv": metrics["CV"]["value"], 
        "active_time": str(metrics["ACTIVE_TIME"]["value"]) + "%" if metrics["ACTIVE_TIME"]["value"] is not None else None
    }
    
    response = requests.post(f"{SUPABASE_URL}/rest/v1/cgm_reports", headers=SUPABASE_HEADERS, json=parsed_data)
    if response.status_code not in [200, 201]: raise HTTPException(status_code=500, detail=f"Baza odbila: {response.text}")
    return {"status": "success", "data": parsed_data, "engine_report": extracted}

@app.get("/api/reports")
def get_reports():
    return requests.get(f"{SUPABASE_URL}/rest/v1/cgm_reports?select=*&order=id.desc", headers=SUPABASE_HEADERS).json()

@app.get("/", response_class=HTMLResponse)
def patient_form():
    return """
    <!DOCTYPE html>
    <html lang="sr"><head><meta charset="UTF-8"><title>Aura View</title></head>
    <body style="font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh;">
    <div style="background:white; padding:30px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.1);">
    <h2>Slanje Izveštaja</h2>
    <form id="f"><input type="file" id="fi" accept=".pdf" required><br><br>
    <button type="submit" style="background:#0d9488; color:white; border:none; padding:10px 20px; border-radius:6px;">Pošalji</button></form>
    <p id="s"></p></div>
    <script>
    document.getElementById('f').onsubmit = async (e) => {
        e.preventDefault();
        const fd = new FormData(); fd.append('file', document.getElementById('fi').files[0]);
        document.getElementById('s').innerText = "Obrada u toku...";
        const res = await fetch('/upload', { method: 'POST', body: fd });
        if(res.ok) document.getElementById('s').innerText = "Uspešno poslato!";
        else document.getElementById('s').innerText = "Greška pri slanju.";
    };
    </script></body></html>
    """

@app.get("/dashboard", response_class=HTMLResponse)
def doctor_dashboard():
    return """
    <!DOCTYPE html><html lang="sr"><head><meta charset="UTF-8"><title>Panel</title></head>
    <body style="background:#0f172a; color:#f8fafc; padding:20px;">
    <h2>Dr Marko Jovanović — Live Panel</h2>
    <div id="c">Učitavanje...</div>
    <script>
    async function load() {
        const res = await fetch('/api/reports');
        const data = await res.json();
        document.getElementById('c').innerHTML = data.map(r => `<div style="background:#1e293b; padding:15px; margin-bottom:10px; border-radius:8px;">
        <strong>Pacijent: ${r.patient_id}</strong> | TIR: <span style="color:#4ade80;">${r.tir ?? '-'リル}%</span>
        </div>`).join('');
    }
    load();
    </script></body></html>
    """
