from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
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

# Univerzalni semantički rečnik
SEMANTIC_MAP = {
    "TIR": ["time in range", "u ciljnom opsegu", "target range", "in range", "ciljni opseg", "target"],
    "TAR": ["time above range", "above range", "iznad opsega", "high", "visoko", "iznad ciljnog"],
    "TBR": ["time below range", "below range", "ispod opsega", "low", "nisko", "ispod ciljnog"],
    "GMI": ["gmi", "glucose management indicator", "estimated a1c", "hba1c", "procenjeni a1c", "procijenjeni a1c"],
    "CV":  ["cv", "coefficient of variation", "varijabilnost", "glycemic variability"],
    "ACTIVE_TIME": ["active time", "vreme aktivnosti", "sensor active", "active", "time active"]
}

MANUFACTURERS = ["mysugr", "dexcom", "freestyle", "abbott", "medtronic", "roche", "linx", "sibionics"]

def extract_universal_cgm_data(doc):
    """
    UNIVERSAL PDF EXTRACTION LAYER: 
    Prostorno i semantičko mapiranje PDF-a bez pogađanja.
    """
    all_words = []
    full_text = ""
    
    # Fokusiramo se na prve dve stranice (AGP izveštaji tu drže summary)
    max_pages = min(2, len(doc))
    for page_num in range(max_pages):
        page = doc[page_num]
        full_text += page.get_text() + " "
        words = page.get_text("words")
        for w in words:
            text = w[4].strip()
            if text:
                cx = (w[0] + w[2]) / 2
                cy = (w[1] + w[3]) / 2
                all_words.append({
                    "text": text, "x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3],
                    "cx": cx, "cy": cy, "page": page_num + 1, "block": w[5], "line": w[6]
                })

    # Detekcija proizvođača iz samog teksta PDF-a
    detected_manuf = "Unknown"
    full_text_lower = full_text.lower()
    for m in MANUFACTURERS:
        if m in full_text_lower:
            detected_manuf = "Abbott" if m == "freestyle" else m.capitalize()
            if m == "mysugr": detected_manuf = "mySugr"
            break

    # Detekcija datuma (Reporting Period)
    dates = re.findall(r'\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b', full_text)
    period_start, period_end = None, None
    if len(dates) >= 2:
        period_start = dates[0]
        period_end = dates[-1]

    # Priprema kandidata
    candidates = []
    for w in all_words:
        t = w['text'].lower()
        if re.match(r'^\d{1,3}(?:[.,]\d{1,2})?%?$', t) and '%' in t:
            candidates.append({"type": "PERCENT", "data": w})
        elif re.match(r'^\d{1,2}[.,]\d{1,2}$', t):
            candidates.append({"type": "DECIMAL", "data": w})

    # Spajanje reči u vizuelne blokove radi traženja labela
    blocks = {}
    for w in all_words:
        key = (w['page'], w['block'], w['line'])
        if key not in blocks: blocks[key] = []
        blocks[key].append(w)

    # Standardizovani output objekat
    report = {
        "manufacturer": detected_manuf,
        "reporting_period_start": period_start,
        "reporting_period_end": period_end,
        "metrics": {
            "TIR": {"value": None, "confidence": 0.0, "source": "", "status": "NOT_FOUND"},
            "TAR": {"value": None, "confidence": 0.0, "source": "", "status": "NOT_FOUND"},
            "TBR": {"value": None, "confidence": 0.0, "source": "", "status": "NOT_FOUND"},
            "GMI": {"value": None, "confidence": 0.0, "source": "", "status": "NOT_FOUND"},
            "CV":  {"value": None, "confidence": 0.0, "source": "", "status": "NOT_FOUND"},
            "ACTIVE_TIME": {"value": None, "confidence": 0.0, "source": "", "status": "NOT_FOUND"}
        }
    }

    # Prostorno mapiranje (Raycasting)
    for key, line_words in blocks.items():
        line_text = " ".join([w['text'] for w in line_words]).lower()
        line_cx = sum(w['cx'] for w in line_words) / len(line_words)
        line_cy = sum(w['cy'] for w in line_words) / len(line_words)
        
        for metric, aliases in SEMANTIC_MAP.items():
            if report["metrics"][metric]["confidence"] > 0.9:
                continue # Već imamo odličan nalaz
                
            for alias in aliases:
                if alias in line_text:
                    # Našli smo labelu! Tražimo najbližeg kandidata na Y osi (levo ili desno)
                    valid_type = "DECIMAL" if metric in ["GMI"] else "PERCENT"
                    matches = []
                    
                    for c in candidates:
                        # GMI prihvata samo decimale, ostali procente. CV može i jedno i drugo zavisno od izveštaja, ali procent je sigurniji.
                        if metric == "GMI" and c['type'] != "DECIMAL": continue
                        if metric != "GMI" and c['type'] != "PERCENT": continue
                        
                        d = c['data']
                        if d['page'] != line_words[0]['page']: continue
                        
                        y_diff = abs(d['cy'] - line_cy)
                        if y_diff < 15.0: # Horizontalna tolerancija
                            dist_x = abs(d['cx'] - line_cx)
                            matches.append({"data": d, "dist_x": dist_x, "y_diff": y_diff})
                            
                    if matches:
                        # Sortiramo po apsolutnoj X udaljenosti (najbliži broj labeli na istoj liniji pobeđuje)
                        matches.sort(key=lambda m: m['dist_x'])
                        best = matches[0]
                        val_str = best['data']['text'].replace('%', '').replace(',', '.')
                        
                        try:
                            val_float = float(val_str)
                            # Logička provera
                            if metric == "GMI" and not (4.0 <= val_float <= 15.0): continue
                            if metric != "GMI" and not (0.0 <= val_float <= 100.0): continue
                            
                            # Računanje confidence-a na osnovu udaljenosti
                            conf = 0.95 if best['dist_x'] < 100 else 0.75
                            if best['y_diff'] > 5: conf -= 0.15 # Penal za blago ofsetovan Y
                            
                            if conf > report["metrics"][metric]["confidence"]:
                                report["metrics"][metric] = {
                                    "value": val_float,
                                    "confidence": round(conf, 2),
                                    "source": f"Labela: '{alias}', Vrednost: '{best['data']['text']}'",
                                    "status": "OK" if conf >= 0.8 else "MANUAL_REVIEW"
                                }
                        except:
                            pass
                    break

    # Validacija (TIR + TAR + TBR treba da bude ~100)
    tir = report["metrics"]["TIR"]["value"]
    tar = report["metrics"]["TAR"]["value"]
    tbr = report["metrics"]["TBR"]["value"]
    
    if tir is not None and tar is not None and tbr is not None:
        total = tir + tar + tbr
        if not (98 <= total <= 102):
            # Sistem NE ispravlja brojeve samoinicijativno, samo obara confidence
            for m in ["TIR", "TAR", "TBR"]:
                report["metrics"][m]["status"] = "CONFLICT"
                report["metrics"][m]["confidence"] = max(0.1, report["metrics"][m]["confidence"] - 0.3)

    return report

# ---------------------------------------------------------
# API RUTE
# ---------------------------------------------------------

@app.post("/upload")
async def upload_report(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    
    patient_id = "P-000127"
    patient_check = requests.get(f"{SUPABASE_URL}/rest/v1/patients?patient_id=eq.{patient_id}", headers=SUPABASE_HEADERS)
    if not patient_check.json():
        requests.post(f"{SUPABASE_URL}/rest/v1/patients", headers=SUPABASE_HEADERS, json={"patient_id": patient_id, "name": "Stefan Jovanović"})
    
    # Pokretanje univerzalnog parsera
    extracted = extract_universal_cgm_data(doc)
    
    # Priprema podataka za bazu iz standardizovanog formata
    manuf = extracted["manufacturer"]
    dname = f"{manuf} AGP Izveštaj" if manuf != "Unknown" else "CGM Izveštaj"

    parsed_data = {
        "patient_id": patient_id, 
        "device_name": dname, 
        "manufacturer": manuf,
        "tir": extracted["metrics"]["TIR"]["value"], 
        "tbr": extracted["metrics"]["TBR"]["value"], 
        "tar": extracted["metrics"]["TAR"]["value"],
        "gmi_percent": extracted["metrics"]["GMI"]["value"], 
        "cv": extracted["metrics"]["CV"]["value"], 
        "active_time": str(extracted["metrics"]["ACTIVE_TIME"]["value"]) + "%" if extracted["metrics"]["ACTIVE_TIME"]["value"] is not None else None
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
                    console.log("Detalji parsera:", data.engine_report);
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


