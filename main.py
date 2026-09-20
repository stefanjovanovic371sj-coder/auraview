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

def extract_cgm_data(full_text):
    text = re.sub(r"\s+", " ", full_text).strip()

    def extract_percentage(anchors, window=100):
        anchor_pattern = "|".join(re.escape(anchor) for anchor in anchors)
        pattern = re.compile(
            rf"(?i)(?:"
            rf"(?:{anchor_pattern}).{{0,{window}}}?([0-9]+(?:[.,][0-9]+)?)\s*%"
            rf"|"
            rf"([0-9]+(?:[.,][0-9]+)?)\s*%.{{0,{window}}}?(?:{anchor_pattern})"
            rf")"
        )
        match = pattern.search(text)
        if not match:
            return None
        value = match.group(1) or match.group(2)
        try:
            value = float(value.replace(",", "."))
        except (ValueError, AttributeError):
            return None
        if not 0 <= value <= 100:
            return None
        return value

    def extract_number(anchors, window=100, require_percent=False):
        anchor_pattern = "|".join(re.escape(anchor) for anchor in anchors)
        if require_percent:
            number_pattern = r"([0-9]+(?:[.,][0-9]+)?)\s*%"
        else:
            number_pattern = r"([0-9]+(?:[.,][0-9]+)?)\s*%?"
        pattern = re.compile(
            rf"(?i)(?:"
            rf"(?:{anchor_pattern}).{{0,{window}}}?{number_pattern}"
            rf"|"
            rf"{number_pattern}.{{0,{window}}}?(?:{anchor_pattern})"
            rf")"
        )
        match = pattern.search(text)
        if not match:
            return None
        values = [g for g in match.groups() if g is not None]
        if not values:
            return None
        value = values[0]
        try:
            return float(value.replace(",", "."))
        except (ValueError, AttributeError):
            return None

    tir = extract_percentage([
        "Time in Range", "U ciljnom opsegu", "U ciljanom opsegu", 
        "Target range", "In range", "Within range"
    ])
    tbr = extract_percentage([
        "Time Below Range", "Below range", "Ispod opsega", 
        "Ispod ciljnog opsega", "Nisko", "Low"
    ])
    tar = extract_percentage([
        "Time Above Range", "Above range", "Iznad opsega", 
        "Iznad ciljnog opsega", "Visoko", "High"
    ])
    gmi = extract_number([
        "GMI", "Glucose Management Indicator", "Estimated A1c", 
        "Estimated HbA1c", "Procijenjeni A1c", "Procenjeni A1c"
    ], require_percent=True)
    cv = extract_percentage([
        "Coefficient of Variation", "Coefficient of Variability", 
        "CV", "Varijabilnost", "Glycemic Variability"
    ])

    return {
        "tir_percent": tir,
        "tbr_percent": tbr,
        "tar_percent": tar,
        "gmi_percent": gmi,
        "cv_percent": cv
    }

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
    full_text = " ".join([page.get_text() for page in doc])
    
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

    # Pozivanje pametne funkcije za ekstrakciju podataka
    extracted_data = extract_cgm_data(full_text)

    # Detekcija proizvođača iz naziva fajla
    filename_lower = file.filename.lower()
    if "mysugr" in filename_lower:
        manufacturer = "mySugr"
    elif "dexcom" in filename_lower:
        manufacturer = "Dexcom"
    elif "freestyle" in filename_lower or "abbott" in filename_lower:
        manufacturer = "Abbott"
    else:
        manufacturer = "Standardni CGM"

    parsed_data = {
        "patient_id": patient_id,
        "device_name": file.filename,
        "manufacturer": manufacturer,
        "tir": extracted_data["tir_percent"],
        "tbr": extracted_data["tbr_percent"],
        "tar": extracted_data["tar_percent"],
        "gmi_percent": extracted_data["gmi_percent"],
        "cv": extracted_data["cv_percent"],
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
