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

# Univerzalni semantički rečnik
SEMANTIC_MAP = {
    "TIR": ["time in range", "u ciljnom opsegu", "target range", "in range", "within range", "ciljni opseg"],
    "TAR": ["time above range", "above range", "iznad opsega", "high", "visoko", "iznad ciljnog"],
    "TBR": ["time below range", "below range", "ispod opsega", "low", "nisko", "ispod ciljnog"],
    "GMI": ["gmi", "glucose management indicator", "estimated a1c", "hba1c", "procenjeni a1c", "procijenjeni a1c"],
    "CV":  ["cv", "coefficient of variation", "varijabilnost", "glycemic variability"]
}

def extract_cgm_data_visual(doc):
    """
    Vizuelni parser: vadi reči sa koordinatama, spaja ih u redove i traži parove labela-broj
    """
    all_words = []
    for page_num, page in enumerate(doc):
        # get_text("words") -> (x0, y0, x1, y1, word, block_no, line_no, word_no)
        words = page.get_text("words")
        for w in words:
            all_words.append({
                "text": w[4], "x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3]
            })
            
    # Sortiramo po Y osi (redovi odozgo na dole) pa po X osi (sleva na desno)
    all_words.sort(key=lambda w: (w["y0"], w["x0"]))
    
    # Grupisanje u vizuelne redove
    lines = []
    if all_words:
        current_line = [all_words[0]]
        for word in all_words[1:]:
            last_word = current_line[-1]
            # Ako je Y koordinata unutar 5 piksela, računamo da je isti red
            if abs(word["y0"] - last_word["y0"]) < 5:
                current_line.append(word)
            else:
                lines.append(current_line)
                current_line = [word]
        lines.append(current_line)
        
    results = {
        "TIR": None, "TAR": None, "TBR": None, "GMI": None, "CV": None
    }
    
    # Pretraga za svaku metriku
    for i, line in enumerate(lines):
        line_text = " ".join([w["text"] for w in line]).lower()
        
        for metric, aliases in SEMANTIC_MAP.items():
            if results[metric] is not None:
                continue # Već smo našli vrednost
                
            for alias in aliases:
                if alias in line_text:
                    # Našli smo labelu, tražimo broj (u istom redu ili redu ispod)
                    is_gmi = metric == "GMI"
                    
                    # 1. Traži u istom redu
                    orig_line = " ".join([w["text"] for w in line])
                    val = _extract_number(orig_line, is_gmi)
                    
                    # 2. Ako nema u istom redu, traži u redu ispod (čest dizajn kod AGP)
                    if val is None and i + 1 < len(lines):
                        next_line = " ".join([w["text"] for w in lines[i+1]])
                        val = _extract_number(next_line, is_gmi)
                        
                    if val is not None:
                        results[metric] = val
                    break

    # Matematička validacija/korekcija ako fali neka osnovna AGP vrednost (3 vrednosti daju 100)
    # Ovo spašava stvar ako je prepoznao TIR i TAR, a TBR je zabačen.
    tir, tar, tbr = results["TIR"], results["TAR"], results["TBR"]
    if tir is not None and tar is not None and tbr is None and (tir + tar <= 100):
        results["TBR"] = round(100.0 - tir - tar, 1)
    elif tir is not None and tbr is not None and tar is None and (tir + tbr <= 100):
        results["TAR"] = round(100.0 - tir - tbr, 1)
        
    return results

def _extract_number(text, is_gmi):
    if is_gmi:
        match = re.search(r'(\d{1,2}[.,]\d{1,2})\s*%?', text)
    else:
        match = re.search(r'(\d{1,3}(?:\.\d{1,2})?)\s*%', text)
        
    if match:
        val = float(match.group(1).replace(',', '.'))
        # Logička provera za TIR/TAR/TBR (ne može preko 100%)
        if not is_gmi and 0 <= val <= 100:
            return val
        # Logička provera za GMI (A1c je obično između 4 i 15)
        if is_gmi and 4 <= val <= 15:
            return val
    return None

@app.get("/api/reports")
def get_reports():
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/cgm_reports?select=*&order=id.desc",
        headers=SUPABASE_HEADERS
    )
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
                <div class="file-upload">
                    <input type="file" id="pdfFile" name="file" accept=".pdf" required>
                </div>
                <div style="margin-bottom: 15px; font-size: 14px; color: #4b5563;">
                    <input type="checkbox" id="consent" required> Pristajem na obradu podataka.
                </div>
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

@app.post("/upload")
async def upload_report(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    
    # Provera pacijenta
    patient_id = "P-000127"
    patient_check = requests.get(
        f"{SUPABASE_URL}/rest/v1/patients?patient_id=eq.{patient_id}",
        headers=SUPABASE_HEADERS
    )
    if not patient_check.json():
        requests.post(
            f"{SUPABASE_URL}/rest/v1/patients",
            headers=SUPABASE_HEADERS,
            json={"patient_id": patient_id, "name": "Stefan Jovanović"}
        )

    # 🚀 Pokretanje našeg VIZUELNOG parsera
    extracted = extract_cgm_data_visual(doc)

    # Lepša detekcija imena proizvođača/uređaja
    fname = file.filename.lower()
    if "mysugr" in fname:
        manuf = "mySugr"
        dname = "mySugr AGP Izveštaj"
    elif "dexcom" in fname:
        manuf = "Dexcom"
        dname = "Dexcom AGP Izveštaj"
    elif "agp" in fname or "libre" in fname:
        manuf = "Abbott"
        dname = "FreeStyle Libre AGP"
    else:
        manuf = "Univerzalni CGM"
        dname = "CGM Izveštaj"

    # Ako parser iz nekog razloga ipak vrati None (da ne bi pucao frontend), prosleđujemo null Supabase-u.
    # Frontend iz dashboard-a će elegantno prepoznati null i prikazati "-" (crticu).
    parsed_data = {
        "patient_id": patient_id,
        "device_name": dname,
        "manufacturer": manuf,
        "tir": extracted["TIR"],
        "tbr": extracted["TBR"],
        "tar": extracted["TAR"],
        "gmi_percent": extracted["GMI"],
        "cv": extracted["CV"],
        "active_time": "100%"
    }
    
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/cgm_reports",
        headers=SUPABASE_HEADERS,
        json=parsed_data
    )
    
    if response.status_code not in [200, 201]:
        raise HTTPException(status_code=500, detail=f"Baza odbila: {response.text}")
        
    return {"status": "success", "data": parsed_data}

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
