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

SEMANTIC_MAP = {
    "TIR": ["time in range", "u ciljnom opsegu", "target range", "in range", "within range", "ciljni opseg"],
    "TAR": ["time above range", "above range", "iznad opsega", "high", "visoko", "iznad ciljnog"],
    "TBR": ["time below range", "below range", "ispod opsega", "low", "nisko", "ispod ciljnog"],
    "GMI": ["gmi", "glucose management indicator", "estimated a1c", "hba1c", "procenjeni a1c", "procijenjeni a1c"],
    "CV":  ["cv", "coefficient of variation", "varijabilnost", "glycemic variability"],
    "ACTIVE_TIME": ["active time", "vreme aktivnosti", "sensor active", "active"]
}

# ---------------------------------------------------------
# NOVO: DEBUG RUTE ZA ANALIZU STRUKTURE PDF-a
# ---------------------------------------------------------

@app.get("/debug", response_class=HTMLResponse)
def debug_form():
    return """
    <!DOCTYPE html>
    <html lang="bs">
    <head>
        <meta charset="UTF-8">
        <title>Aura View - PDF Debugger</title>
        <style>
            body { font-family: monospace; background: #1e1e1e; color: #00ff00; padding: 20px; }
            .card { background: #000; padding: 20px; border: 1px solid #00ff00; }
            button { background: #00ff00; color: #000; padding: 10px; font-weight: bold; cursor: pointer; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>🛠️ RENDGEN PDF DOKUMENTA (DEBUGGER)</h2>
            <p>Ubaci onaj problematični PDF da vidimo njegove tačne koordinate.</p>
            <form action="/api/debug_pdf" method="post" enctype="multipart/form-data">
                <input type="file" name="file" accept=".pdf" required>
                <button type="submit">Skeniraj PDF</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.post("/api/debug_pdf", response_class=PlainTextResponse)
async def debug_pdf(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    
    output = []
    output.append(f"=== ANALIZA DOKUMENTA: {file.filename} ===")
    
    all_words = []
    for page_num, page in enumerate(doc):
        words = page.get_text("words")
        for w in words:
            text = w[4].strip()
            if not text:
                continue
            cx = (w[0] + w[2]) / 2
            cy = (w[1] + w[3]) / 2
            all_words.append({
                "text": text, "x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3],
                "cx": cx, "cy": cy, "page": page_num + 1, "block": w[5], "line": w[6]
            })
            
    output.append(f"Ukupno izvučeno reči: {len(all_words)}\n")
    
    candidates = []
    for w in all_words:
        t = w['text'].lower()
        if re.match(r'^\d{1,3}(?:[.,]\d{1,2})?%?$', t) and '%' in t:
            candidates.append({"type": "PERCENT", "data": w})
        elif re.match(r'^\d{1,2}[.,]\d{1,2}$', t):
             candidates.append({"type": "DECIMAL", "data": w})
             
    output.append("--- PRONAĐENI NUMERIČKI KANDIDATI ---")
    for c in candidates:
        d = c['data']
        output.append(f"[{c['type']}] '{d['text']}' -> Strana {d['page']} | Centar Y: {d['cy']:.1f}, Centar X: {d['cx']:.1f}")

    output.append("\n--- SEMANTIČKA SIDRA I POKUŠAJ SPAJANJA ---")
    
    blocks = {}
    for w in all_words:
        key = (w['page'], w['block'], w['line'])
        if key not in blocks:
            blocks[key] = []
        blocks[key].append(w)

    found_anchors = []
    for key, line_words in blocks.items():
        line_text = " ".join([w['text'] for w in line_words]).lower()
        line_cx = sum(w['cx'] for w in line_words) / len(line_words)
        line_cy = sum(w['cy'] for w in line_words) / len(line_words)
        
        for metric, aliases in SEMANTIC_MAP.items():
            for alias in aliases:
                if alias in line_text:
                    found_anchors.append({
                        "metric": metric, "alias_found": alias, "cx": line_cx, "cy": line_cy
                    })
                    break

    for anchor in found_anchors:
        output.append(f"\n📍 SIDRO: {anchor['metric']} (Prepoznato iz: '{anchor['alias_found']}') | Y = {anchor['cy']:.1f}")
        
        matches = []
        for c in candidates:
            d = c['data']
            y_diff = abs(d['cy'] - anchor['cy'])
            if y_diff < 15.0: # Tolerancija visine
                matches.append({"text": d['text'], "dist_x": d['cx'] - anchor['cx'], "y_diff": y_diff})
                
        if matches:
            matches.sort(key=lambda m: m['dist_x'] if m['dist_x'] > 0 else float('inf'))
            best = matches[0]
            output.append(f"   ✅ KANDIDAT: '{best['text']}' (Y odstupanje: {best['y_diff']:.1f}px, X udaljenost: {best['dist_x']:.1f}px)")
        else:
            output.append("   ⚠️ Nema kandidata u istom horizontalnom redu!")
            
    return "\n".join(output)

# ---------------------------------------------------------
# STARE RUTE (Ovo ostaje da ti sajt radi normalno)
# ---------------------------------------------------------

@app.get("/api/reports")
def get_reports():
    response = requests.get(f"{SUPABASE_URL}/rest/v1/cgm_reports?select=*&order=id.desc", headers=SUPABASE_HEADERS)
    return response.json()

@app.get("/", response_class=HTMLResponse)
def patient_form():
    return """
    <!DOCTYPE html>
    <html lang="bs">
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
                } else {
                    document.getElementById('statusMsg').style.color = "#dc2626";
                    document.getElementById('statusMsg').innerText = "Greška: " + (data.detail || "Došlo je do problema");
                }
            };
        </script>
    </body>
    </html>
    """

def _extract_number(text, is_gmi):
    if is_gmi:
        match = re.search(r'(\d{1,2}[.,]\d{1,2})\s*%?', text)
    else:
        match = re.search(r'(\d{1,3}(?:\.\d{1,2})?)\s*%', text)
    if match:
        val = float(match.group(1).replace(',', '.'))
        if not is_gmi and 0 <= val <= 100: return val
        if is_gmi and 4 <= val <= 15: return val
    return None

def extract_cgm_data_visual(doc):
    all_words = []
    for page_num, page in enumerate(doc):
        words = page.get_text("words")
        for w in words: all_words.append({"text": w[4], "x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3]})
    all_words.sort(key=lambda w: (w["y0"], w["x0"]))
    lines = []
    if all_words:
        current_line = [all_words[0]]
        for word in all_words[1:]:
            if abs(word["y0"] - current_line[-1]["y0"]) < 5:
                current_line.append(word)
            else:
                lines.append(current_line)
                current_line = [word]
        lines.append(current_line)
        
    results = {"TIR": None, "TAR": None, "TBR": None, "GMI": None, "CV": None}
    for i, line in enumerate(lines):
        line_text = " ".join([w["text"] for w in line]).lower()
        for metric, aliases in SEMANTIC_MAP.items():
            if metric == "ACTIVE_TIME" or results[metric] is not None: continue
            for alias in aliases:
                if alias in line_text:
                    is_gmi = metric == "GMI"
                    orig_line = " ".join([w["text"] for w in line])
                    val = _extract_number(orig_line, is_gmi)
                    if val is None and i + 1 < len(lines):
                        next_line = " ".join([w["text"] for w in lines[i+1]])
                        val = _extract_number(next_line, is_gmi)
                    if val is not None: results[metric] = val
                    break

    # Ostala je ona mala provera, nismo brisali dok ne popravimo glavni kod iz debagera
    tir, tar, tbr = results["TIR"], results["TAR"], results["TBR"]
    if tir is not None and tar is not None and tbr is None and (tir + tar <= 100): results["TBR"] = round(100.0 - tir - tar, 1)
    elif tir is not None and tbr is not None and tar is None and (tir + tbr <= 100): results["TAR"] = round(100.0 - tir - tbr, 1)
    return results

@app.post("/upload")
async def upload_report(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    patient_id = "P-000127"
    patient_check = requests.get(f"{SUPABASE_URL}/rest/v1/patients?patient_id=eq.{patient_id}", headers=SUPABASE_HEADERS)
    if not patient_check.json():
        requests.post(f"{SUPABASE_URL}/rest/v1/patients", headers=SUPABASE_HEADERS, json={"patient_id": patient_id, "name": "Stefan Jovanović"})
    
    extracted = extract_cgm_data_visual(doc)
    fname = file.filename.lower()
    if "mysugr" in fname: manuf, dname = "mySugr", "mySugr AGP Izveštaj"
    elif "dexcom" in fname: manuf, dname = "Dexcom", "Dexcom AGP Izveštaj"
    elif "agp" in fname or "libre" in fname: manuf, dname = "Abbott", "FreeStyle Libre AGP"
    else: manuf, dname = "Univerzalni CGM", "CGM Izveštaj"

    parsed_data = {
        "patient_id": patient_id, "device_name": dname, "manufacturer": manuf,
        "tir": extracted["TIR"], "tbr": extracted["TBR"], "tar": extracted["TAR"],
        "gmi_percent": extracted["GMI"], "cv": extracted["CV"], "active_time": "100%"
    }
    response = requests.post(f"{SUPABASE_URL}/rest/v1/cgm_reports", headers=SUPABASE_HEADERS, json=parsed_data)
    if response.status_code not in [200, 201]: raise HTTPException(status_code=500, detail=f"Baza odbila: {response.text}")
    return {"status": "success", "data": parsed_data}

@app.get("/dashboard", response_class=HTMLResponse)
def doctor_dashboard():
    return """
    <!DOCTYPE html>
    <html lang="bs">
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
