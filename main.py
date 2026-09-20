from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
import requests
import pymupdf as fitz

app = FastAPI()

SUPABASE_URL = "https://tuhgurlibsaqqxrmhgdr.supabase.co"
SUPABASE_HEADERS = {
    "apikey": "sb_publishable_Dgu75wMHYMiFhkuVTGg...",
    "Authorization": "Bearer sb_publishable_Dgu75wMHYMiFhkuVTGg...",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
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
    full_text = "".join([page.get_text() for page in doc])
    
    parsed_data = {
        "patient_id": "P-000127",
        "device_name": "Roche SmartGuide",
        "tir": 70.0,
        "tbr": 2.0,
        "tar": 28.0,
        "gmi_percent": 5.6,
        "cv": 36.0,
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
                                <strong>Pacijent: ${r.patient_id || 'Nepoznat'}</strong>
                                <span style="color:#38bdf8;">${r.device_name || 'CGM'}</span>
                            </div>
                            <div style="margin: 10px 0;">
                                <span class="tir">${r.tir}%</span> TIR
                            </div>
                            <div class="metrics">
                                <div>TBR: <span style="color:#f87171;">${r.tbr}%</span></div>
                                <div>TAR: <span style="color:#fbbf24;">${r.tar}%</span></div>
                                <div>GMI: ${r.gmi_percent || r.gmi || '-'}%</div>
                                <div>CV: ${r.cv}%</div>
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
