from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
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
    "TIR": {
        "aliases": ["in range", "time in range", "target range", "u ciljnom opsegu"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "TAR": {
        "aliases": ["high", "above range", "very high", "iznad opsega"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "TBR": {
        "aliases": ["low", "below range", "very low", "ispod opsega"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "VERY_LOW": {
        "aliases": ["very low"],
        "expected_role": "ACTUAL", "expected_unit": "%", "valid_range": (0, 100)
    },
    "GMI": {
        "aliases": ["glucose management indicator", "gmi", "estimated a1c", "hba1c"],
        "expected_role": "ACTUAL", "expected_unit": "DECIMAL", "valid_range": (4, 15)
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
        self._aggregate_metrics()
        self._validate_cluster()
        self._extract_reporting_period()
        return self._generate_final_report()

    def _ingest_pdf(self):
        for page_num, page in enumerate(self.doc):
            words = page.get_text("words")
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

    def _build_layout(self):
        region_counter = 0
        for page in self.pages:
            page['words'].sort(key=lambda w: (w["y0"], w["x0"]))
            current_line, page_lines = [], []
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
            if any(k in r["text"] for k in ["goal", "target range 3.9", "cilj", "recommended", "reference"]):
                r["context_type"] = "GOAL_ZONE"

    def _extract_candidates(self):
        for r in self.regions:
            for line in r["lines"]:
                line_full_text = " ".join([w["text"] for w in line])
                line_text_lower = line_full_text.lower()
                matches = re.finditer(r'([<>]?)\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*(%)?', line_full_text)
                
                for match in matches:
                    operator, val_str, has_percent = match.group(1), match.group(2), bool(match.group(3))
                    val = float(val_str.replace(',', '.'))
                    
                    role, reason = "UNKNOWN", []
                    if operator: role, reason = "GOAL", ["GOAL_OPERATOR"]
                    elif "goal:" in line_text_lower or "target range 3.9" in line_text_lower: role, reason = "GOAL", ["INLINE_GOAL"]
                    elif r["context_type"] == "GOAL_ZONE": role, reason = "GOAL", ["REGION_GOAL"]
                    else: role, reason = "ACTUAL", ["DEFAULT_ACTUAL"]

                    w_ref = line[0]
                    self.candidates.append({
                        "raw_text": match.group(0), "value": val, "unit": "%" if has_percent else "DECIMAL",
                        "type": "PERCENT" if has_percent else "DECIMAL",
                        "bbox": {"x0": w_ref["x0"], "y0": w_ref["y0"], "x1": w_ref["x1"], "y1": w_ref["y1"], "cx": w_ref["cx"], "cy": w_ref["cy"]},
                        "page": w_ref["page"], "region_id": r["region_id"], "semantic_role": role,
                        "reason_codes": reason, "line_text": line_text_lower
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
                
                if y_diff < 40 or x_diff < 150:
                    score, evidence = 0.6, ["ROLE_MATCH", "UNIT_MATCH"]
                    if y_diff < 15: score += 0.2; evidence.append("CLOSE_Y")
                    if x_diff < 100: score += 0.2; evidence.append("CLOSE_X")
                        
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
        # Agregacija TBR = Very Low + Low
        tbr_res = self.results["TBR"]
        vl_res = self.results["VERY_LOW"]
        if vl_res and tbr_res and vl_res["value"] is not None:
            # Ako je TBR pokupio samo Low, dodajemo Very Low
            self.results["TBR"]["value"] = round(vl_res["value"] + (tbr_res["value"] if tbr_res["value"] else 0), 1)
            self.results["TBR"]["reason_codes"].append("AGGREGATED_VERY_LOW_AND_LOW")

    def _validate_cluster(self):
        tir, tar, tbr = self.results["TIR"], self.results["TAR"], self.results["TBR"]
        if tir and tar and tbr and all(v and v["value"] is not None for v in [tir, tar, tbr]):
            total = tir["value"] + tar["value"] + tbr["value"]
            if 98 <= total <= 102:
                for metric in [tir, tar, tbr]:
                    metric["confidence"] = min(1.0, metric["confidence"] + 0.1)
                    metric["status"] = "OK"
                    metric["reason_codes"].append("VALIDATED_BY_CLUSTER_SUM_100")

    def _extract_reporting_period(self):
        date_pattern = r'\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b'
        for page in self.pages:
            full_page_text = " ".join([w["text"] for w in page["words"]])
            dates = re.findall(date_pattern, full_page_text)
            if len(dates) >= 2:
                self.reporting_period["start"] = dates[0]
                self.reporting_period["end"] = dates[1]
                break

    def _generate_final_report(self):
        final_metrics = {}
        for key, res in self.results.items():
            if key == "VERY_LOW": continue
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
    
    metrics = extracted["metrics"]
    parsed_data = {
        "patient_id": patient_id, "device_name": "CGM Izveštaj", "manufacturer": "mySugr / Standardni CGM",
        "tir": metrics["TIR"]["value"] if metrics["TIR"] else None, 
        "tbr": metrics["TBR"]["value"] if metrics["TBR"] else None, 
        "tar": metrics["TAR"]["value"] if metrics["TAR"] else None,
        "gmi_percent": metrics["GMI"]["value"] if metrics["GMI"] else None, 
        "cv": metrics["CV"]["value"] if metrics["CV"] else None, 
        "active_time": str(metrics["ACTIVE_TIME"]["value"]) + "%" if metrics["ACTIVE_TIME"] and metrics["ACTIVE_TIME"]["value"] is not None else None
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
<body style="font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh; background:#f4f6f9;">
<div style="background:white; padding:30px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.1); width:100%; max-width:400px; text-align:center;">
<h2>Slanje AGP Izveštaja</h2>
<form id="f"><input type="file" id="fi" accept=".pdf" required style="margin:20px 0;"><br>
<button type="submit" style="background:#0d9488; color:white; border:none; padding:12px 20px; border-radius:6px; width:100%; font-weight:bold; cursor:pointer;">Pošalji Lekaru</button></form>
<p id="s" style="margin-top:15px; font-weight:500;"></p></div>
<script>
document.getElementById('f').onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(); fd.append('file', document.getElementById('fi').files[0]);
    document.getElementById('spx' in window ? '' : 's').innerText = "Obrada u toku...";
    const res = await fetch('/upload', { method: 'POST', body: fd });
    if(res.ok) document.getElementById('s').innerText = "Izveštaj uspešno sačuvan!";
    else document.getElementById('s').innerText = "Greška pri obradi.";
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
    document.getElementById('c').innerHTML = data.map(r => `<div style="background:#1e293b; padding:15px; margin-bottom:10px; border-radius:8px; border:1px solid #334155;">
    <strong>Pacijent: ${r.patient_id}</strong> — <span style="color:#38bdf8;">${r.device_name || 'CGM'}</span><br><br>
    TIR: <span style="color:#4ade80; font-size:20px; font-weight:bold;">${r.tir !== null ? r.tir + '%' : '-'}</span> | 
    TBR: <span style="color:#f87171;">${r.tbr !== null ? r.tbr + '%' : '-'}</span> | 
    TAR: <span style="color:#fbbf24;">${r.tar !== null ? r.tar + '%' : '-'}</span> | 
    GMI: ${r.gmi_percent !== null ? r.gmi_percent + '%' : '-'} | 
    CV: ${r.cv !== null ? r.cv + '%' : '-'} | 
    Aktivno: ${r.active_time || '-'}
    </div>`).join('');
}
load();
setInterval(load, 5000);
</script></body></html>
"""
