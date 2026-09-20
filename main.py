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

# 5. METRIC ONTOLOGY
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
        self.lines = []
        self.candidates = []
        self.anchors = []
        self.results = {key: None for key in METRIC_ONTOLOGY.keys()}
        self.reporting_period = {"start": None, "end": None}
        self.ocr_required = False

    def parse(self):
        self._ingest_pdf()
        if self.ocr_required:
            return self._generate_error_report("OCR_REQUIRED")
        
        self._build_layout()
        self._classify_regions()
        self._extract_candidates()
        self._extract_anchors()
        self._associate_labels_and_values()
        self._aggregate_metrics()
        self._validate_cluster()
        self._extract_reporting_period()
        return self._generate_final_report()

    # 1. PDF INGESTION & OCR CHECK
    def _ingest_pdf(self):
        total_words = 0
        for page_num, page in enumerate(self.doc):
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

    # 2. LAYOUT ENGINE
    def _build_layout(self):
        region_counter = 0
        for page in self.pages:
            # Grupisanje reči u linije na osnovu Y tolerancije
            page['words'].sort(key=lambda w: (w["y0"], w["x0"]))
            current_line = []
            page_lines = []
            
            for w in page['words']:
                if not current_line:
                    current_line.append(w)
                else:
                    last_w = current_line[-1]
                    if abs(w["cy"] - last_w["cy"]) < 6:
                        current_line.append(w)
                    else:
                        page_lines.append(current_line)
                        current_line = [w]
            if current_line:
                page_lines.append(current_line)

            # Grupisanje linija u regione na osnovu vertikalne udaljenosti (razmak veći od 20px)
            current_region = []
            for line in page_lines:
                if not current_region:
                    current_region.append(line)
                else:
                    last_line = current_region[-1]
                    y_diff = line[0]["cy"] - last_line[0]["cy"]
                    if y_diff < 25:
                        current_region.append(line)
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
            "region_id": f"reg_{page_num}_{reg_id}",
            "page": page_num,
            "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
            "text": text,
            "lines": lines,
            "context_type": "UNKNOWN"
        })

    def _classify_regions(self):
        goal_keywords = ["goal", "target", "cilj", "recommended", "reference"]
        for r in self.regions:
            if any(k in r["text"] for k in goal_keywords):
                r["context_type"] = "GOAL_ZONE"

    # 3 & 4. INTERMEDIATE REPRESENTATION & SEMANTIC ROLE CLASSIFICATION
    def _extract_candidates(self):
        for r in self.regions:
            for line in r["lines"]:
                line_text = " ".join([w["text"] for w in line]).lower()
                for w in line:
                    t = w["text"].lower()
                    
                    # Detekcija operatora
                    has_goal_operator = bool(re.search(r'[<>≤≥]', t))
                    clean_text = re.sub(r'[<>≤≥]', '', t)
                    
                    is_percent = "%" in clean_text
                    perc_match = re.search(r'(\d{1,3}(?:[.,]\d{1,2})?)', clean_text)
                    
                    val = None
                    unit = None
                    c_type = None
                    
                    if perc_match and is_percent:
                        val = float(perc_match.group(1).replace(',', '.'))
                        unit = "%"
                        c_type = "PERCENT"
                    elif re.match(r'^\d{1,3}[.,]\d{1,2}$', clean_text):
                        val = float(clean_text.replace(',', '.'))
                        unit = "DECIMAL"
                        c_type = "DECIMAL"
                    
                    if val is not None:
                        # SEMANTIC ROLE RULES
                        role = "UNKNOWN"
                        reason = []
                        
                        if has_goal_operator:
                            role = "GOAL"
                            reason.append("GOAL_OPERATOR_DETECTED")
                        elif "target" in line_text or "goal" in line_text:
                            role = "GOAL"
                            reason.append("INLINE_GOAL_CONTEXT")
                        elif r["context_type"] == "GOAL_ZONE":
                            role = "GOAL"
                            reason.append("REGION_GOAL_CONTEXT")
                        else:
                            role = "ACTUAL"
                            reason.append("DEFAULT_ACTUAL_NO_GOAL_INDICATORS")

                        self.candidates.append({
                            "raw_text": w["text"],
                            "value": val,
                            "unit": unit,
                            "type": c_type,
                            "bbox": {"x0": w["x0"], "y0": w["y0"], "x1": w["x1"], "y1": w["y1"], "cx": w["cx"], "cy": w["cy"]},
                            "page": w["page"],
                            "region_id": r["region_id"],
                            "semantic_role": role,
                            "reason_codes": reason,
                            "line_text": line_text
                        })

    def _extract_anchors(self):
        for r in self.regions:
            for line in r["lines"]:
                line_text = " ".join([w["text"] for w in line]).lower()
                line_y = line[0]["cy"]
                
                for m_key, m_data in METRIC_ONTOLOGY.items():
                    for alias in m_data["aliases"]:
                        if alias in line_text:
                            # Sprečavanje da se ista labela uhvati dvaput na istoj liniji
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
                                    "line_text": line_text
                                })

    # 6 & 7. LABEL → VALUE ASSOCIATION & EVIDENCE
    def _associate_labels_and_values(self):
        for anchor in self.anchors:
            valid_candidates = []
            
            for c in self.candidates:
                if c["page"] != anchor["page"]: continue
                # Odbacivanje GOAL kandidata ako anchor traži ACTUAL
                if anchor["expected_role"] == "ACTUAL" and c["semantic_role"] == "GOAL": continue
                if c["unit"] != anchor["expected_unit"]: continue
                
                # Provera dozvoljenog opsega metrike
                v_min, v_max = METRIC_ONTOLOGY[anchor["metric"]]["valid_range"]
                if not (v_min <= c["value"] <= v_max): continue
                
                y_diff = abs(c["bbox"]["cy"] - anchor["cy"])
                x_diff = abs(c["bbox"]["cx"] - anchor["cx"])
                
                if y_diff < 15 or (y_diff < 40 and c["region_id"] == anchor["region_id"]):
                    score = 0.5
                    evidence = ["ROLE_MATCH", "UNIT_MATCH"]
                    
                    if y_diff < 10: 
                        score += 0.3
                        evidence.append("SAME_LINE")
                    if c["region_id"] == anchor["region_id"]: 
                        score += 0.2
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
                
                current_result = self.results[anchor["metric"]]
                # Ako nađemo bolji rezultat na dokumentu (veći score), prepiši
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
                            "region_id": best["candidate"]["region_id"]
                        },
                        "reason_codes": best["evidence"] + best["candidate"]["reason_codes"]
                    }

    # 8. AGGREGATION
    def _aggregate_metrics(self):
        # TIR agregacija nije uobičajena, ali TAR (High + Very High) i TBR (Low + Very Low) jesu.
        tar_res = self.results["TAR"]
        vh_res = self.results["VERY_HIGH"]
        h_res = self.results.get("HIGH") # Nije u primarnoj ontologiji trenutno, ali logika ostaje

        # Agregacija TBR = Very Low + Low
        tbr_res = self.results["TBR"]
        vl_res = self.results["VERY_LOW"]
        
        # Ako nemamo čist TBR, ali imamo VERY_LOW (i pretpostavljeno LOW koje je možda pokupljeno kao TBR)
        # Ovde se držimo striktno pravila: spajamo ih samo ako pripadaju istom ACTUAL klasteru.
        if vl_res and tbr_res and vl_res["source"]["region_id"] == tbr_res["source"]["region_id"]:
            # Zbir komponenti
            self.results["TBR"]["value"] = round(vl_res["value"] + tbr_res["value"], 1)
            self.results["TBR"]["reason_codes"].append("AGGREGATED_VERY_LOW_AND_LOW")

    # 9. VALIDATION
    def _validate_cluster(self):
        tir = self.results["TIR"]
        tar = self.results["TAR"]
        tbr = self.results["TBR"]
        
        if tir and tar and tbr and all(v["status"] in ["OK", "MANUAL_REVIEW"] for v in [tir, tar, tbr]):
            total = tir["value"] + tar["value"] + tbr["value"]
            if 98 <= total <= 102:
                for metric in [tir, tar, tbr]:
                    metric["confidence"] = min(1.0, metric["confidence"] + 0.1)
                    metric["status"] = "OK"
                    metric["reason_codes"].append("VALIDATED_BY_CLUSTER_SUM_100")
            else:
                for metric in [tir, tar, tbr]:
                    metric["status"] = "CONFLICT_SUM"
                    metric["confidence"] = max(0.1, metric["confidence"] - 0.3)

    # 10. REPORTING PERIOD
    def _extract_reporting_period(self):
        date_pattern = r'\b\d{1,2}[\s./-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|[a-zA-Z]{3}|\d{1,2})[\s./-]\d{2,4}\b'
        for r in self.regions:
            if any(k in r["text"] for k in ["period", "date", "od", "do", "from", "to"]):
                dates = re.findall(date_pattern, r["text"])
                if len(dates) >= 2:
                    self.reporting_period["start"] = dates[0]
                    self.reporting_period["end"] = dates[1]
                    break

    def _generate_error_report(self, error_type):
        return {"status": "ERROR", "error_type": error_type}

    def _generate_final_report(self):
        # Formatiranje u standardizovani CGMReport
        final_metrics = {}
        for key, res in self.results.items():
            if res:
                final_metrics[key] = res
            else:
                final_metrics[key] = {
                    "value": None,
                    "confidence": 0.0,
                    "status": "NOT_FOUND",
                    "semantic_role": "UNKNOWN",
                    "reason_codes": []
                }
                
        return {
            "status": "SUCCESS",
            "manufacturer": "Unknown", # Ostavljeno kao metadata, više ne utiče na parsiranje
            "reporting_period": self.reporting_period,
            "metrics": final_metrics
        }


# --- API RUTE (Nepromenjene spoljašnje strukture) ---

@app.post("/upload")
async def upload_report(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    
    patient_id = "P-000127"
    patient_check = requests.get(f"{SUPABASE_URL}/rest/v1/patients?patient_id=eq.{patient_id}", headers=SUPABASE_HEADERS)
    if not patient_check.json():
        requests.post(f"{SUPABASE_URL}/rest/v1/patients", headers=SUPABASE_HEADERS, json={"patient_id": patient_id, "name": "Stefan Jovanović"})
    
    # 🚀 Pokretanje novog UNIVERSAL PARSERA
    parser = UniversalCGMParser(doc)
    extracted = parser.parse()
    
    if extracted.get("status") == "ERROR" and extracted.get("error_type") == "OCR_REQUIRED":
        raise HTTPException(status_code=422, detail="PDF ne sadrži tekst (skenirana slika). OCR nije podržan u ovoj verziji.")

    # Detekcija imena za dashboard (Metadata)
    fname = file.filename.lower()
    manuf = "Standardni CGM"
    for m in MANUFACTURERS:
        if m in fname: manuf = m.capitalize(); break
    if "mysugr" in fname: manuf = "mySugr"
    
    dname = f"{manuf} AGP Izveštaj"

    # Preslikavanje rich objekta u ravnu Supabase bazu
    metrics = extracted["metrics"]
    parsed_data = {
        "patient_id": patient_id, 
        "device_name": dname, 
        "manufacturer": manuf,
        "tir": metrics["TIR"]["value"], 
        "tbr": metrics["TBR"]["value"], 
        "tar": metrics["TAR"]["value"],
        "gmi_percent": metrics["GMI"]["value"], 
        "cv": metrics["CV"]["value"], 
        "active_time": str(metrics["ACTIVE_TIME"]["value"]) + "%" if metrics["ACTIVE_TIME"]["value"] is not None else None
    }
    
    response = requests.post(f"{SUPABASE_URL}/rest/v1/cgm_reports", headers=SUPABASE_HEADERS, json=parsed_data)
    if response.status_code not in [200, 201]: 
        raise HTTPException(status_code=500, detail=f"Baza odbila: {response.text}")
        
    return {"status": "success", "data": parsed_data, "engine_report": extracted}

@app.get("/api/reports")
def get_reports():
    response = requests.get(f"{SUPABASE_URL}/rest/v1/cgm_reports?select=*&order=id.desc", headers=SUPABASE_HEADERS)
    return response.json()

@app.get("/", response_class=HTMLResponse)
def patient_form():
    return """
    <!DOCTYPE html>
    <html lang="sr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Aura View - Slanje Izveštaja</title>
        <style>
            body { font-family: system-ui, sans-serif; background: #f4f6f9; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .card { background: white; padding: 30px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); width: 100%; max-width: 400px; }
            h2 { color: #111827; margin-bottom: 20px; font-size: 22px; }
            .file-upload { border: 2px dashed #d1d5db; padding: 20px; text-align: center; border-radius: 8px; margin-bottom: 15px; cursor: pointer; }
            button { background: #0d9488; color: white; border: none; width: 100%; padding: 14px; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; }
            button:hover { background: #0f766e; }
            .msg { margin-top: 15px; font-weight: 500; text-align: center; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>Slanje AGP Izveštaja</h2>
            <form id="uploadForm">
                <div class="file-upload"><input type="file" id="pdfFile" name="file" accept=".pdf" required></div>
                <div style="margin-bottom: 15px; font-size: 14px; color: #4b5563;"><input type="checkbox" id="consent" required> Pristajem na obradu podataka.</div>
                <button type="submit">Pošalji Lekaru</button>
            </form>
            <div id="statusMsg" class="msg"></div>
        </div>
        <script>
            document.getElementById('uploadForm').onsubmit = async (e) => {
                e.preventDefault();
                const formData = new FormData();
                formData.append('file', document.getElementById('pdfFile').files[0]);
                document.getElementById('statusMsg').innerText = "Slanje i obrada u toku...";
                const res = await fetch('/upload', { method: 'POST', body: formData });
                const data = await res.json();
                if(res.ok) {
                    document.getElementById('statusMsg').style.color = "#059669";
                    document.getElementById('statusMsg').innerText = "Izveštaj uspešno sačuvan i prosleđen lekaru!";
                    console.log("🛠️ Detaljna analiza univerzalnog parsera:", data.engine_report);
                } else {
                    document.getElementById('statusMsg').style.color = "#dc2626";
                    document.getElementById('statusMsg').innerText = "Greška: " + (data.detail || "Došlo je do problema");
                }
            };
        </script>
    </body>
    </html>
    """

@app.get("/dashboard", response_class=HTMLResponse)
def doctor_dashboard():
    return """
    <!DOCTYPE html>
    <html lang="sr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Aura View - Doktorski Panel</title>
        <style>
            body { font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; margin: 0; }
            .header { font-size: 20px; font-weight: bold; color: #38bdf8; margin-bottom: 20px; }
            .card { background: #1e293b; padding: 20px; border-radius: 12px; margin-bottom: 15px; border: 1px solid #334155; }
            .tir { font-size: 32px; font-weight: bold; color: #4ade80; }
            .metrics { display: flex; gap: 15px; margin-top: 10px; font-size: 14px; color: #94a3b8; }
        </style>
    </head>
    <body>
        <div class="header">Dr Marko Jovanović — Live Panel</div>
        <div id="reportsContainer">Učitavanje izveštaja...</div>
        <script>
            async function loadReports() {
                try {
                    const res = await fetch('/api/reports');
                    const reports = await res.json();
                    const container = document.getElementById('reportsContainer');
                    if(!Array.isArray(reports) || reports.length === 0) {
                        container.innerHTML = "<p>Nema sačuvanih izveštaja.</p>";
                        return;
                    }
                    container.innerHTML = reports.map(r => `
                        <div class="card">
                            <div style="display:flex; justify-content:space-between;">
                                <strong>Pacijent: ${r.patient_id ? r.patient_id : 'Nepoznat'}</strong>
                                <span style="color:#38bdf8;">${r.device_name || 'CGM'}</span>
                            </div>
                            <div style="margin: 10px 0;">
                                <span class="tir">${r.tir !== null && r.tir !== undefined ? r.tir + '%' : '-'}</span> TIR
                            </div>
                            <div class="metrics">
                                <div>TBR: <span style="color:#f87171;">${r.tbr !== null && r.tbr !== undefined ? r.tbr + '%' : '-'}</span></div>
                                <div>TAR: <span style="color:#fbbf24;">${r.tar !== null && r.tar !== undefined ? r.tar + '%' : '-'}</span></div>
                                <div>GMI: ${r.gmi_percent !== null && r.gmi_percent !== undefined ? r.gmi_percent + '%' : (r.gmi ? r.gmi + '%' : '-')}</div>
                                <div>CV: ${r.cv !== null && r.cv !== undefined ? r.cv + '%' : '-'}</div>
                                <div style="margin-left:auto; color:#64748b;">Aktivno: ${r.active_time || '-'}</div>
                            </div>
                        </div>
                    `).join('');
                } catch(err) {
                    console.error(err);
                }
            }
            loadReports();
            setInterval(loadReports, 5000);
        </script>
    </body>
    </html>
    """
