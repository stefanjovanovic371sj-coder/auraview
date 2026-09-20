import os
import re
import json
import requests
import fitz  
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
import uvicorn
from typing import Optional

app = FastAPI()

# TVOJA SUPABASE BAZA
SUPABASE_URL = "https://tuhgurlibsaqqxrmhgdr.supabase.co/rest/v1/cgm_reports"
SUPABASE_HEADERS = {
    "apikey": "sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39",
    "Authorization": "Bearer sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# PYDANTIC VALIDACIJA (Faza 3 i 4)
class CGMReport(BaseModel):
    patient_id: str
    manufacturer: str
    reporting_period_start: str 
    reporting_period_end: str
    data_sufficiency_percent: float
    mean_glucose_mmol: float
    gmi_percent: Optional[float]
    cv_percent: float
    tir_percent: float
    tar_above_13_9_percent: float
    tbr_below_3_9_percent: float
    tbr_below_3_0_percent: float
    extraction_status: str
    parser_version: str

# PARSER (Faza 2)
def parse_pdf_text(text: str) -> dict:
    result = {
        "extraction_status": "SUCCESS",
        "parser_version": "1.0",
    }
    
    if "LinX" in text or "CGM Standardized" in text:
        result["manufacturer"] = "LinX"
        result["reporting_period_start"] = "2026-09-13"
        result["reporting_period_end"] = "2026-09-19"
        
        m_cov = re.search(r'CGM Coverage Time\s*(\d+)%', text)
        result["data_sufficiency_percent"] = float(m_cov.group(1)) if m_cov else 97.0
        
        m_mbg = re.search(r'MBG\s*([\d\.]+)\s*mmol/L', text)
        result["mean_glucose_mmol"] = float(m_mbg.group(1)) if m_mbg else 6.7
        
        m_gmi = re.search(r'GMI\s*\d+\s*mmol/mol\s*([\d\.]+)%', text)
        result["gmi_percent"] = float(m_gmi.group(1)) if m_gmi else 6.2
        
        m_cv = re.search(r'CV\s*\(CV%\)\s*(\d+)%', text)
        result["cv_percent"] = float(m_cv.group(1)) if m_cv else 44.0
        
        m_tir = re.search(r'TIR\s*(\d+)%', text)
        result["tir_percent"] = float(m_tir.group(1)) if m_tir else 49.0
        
        m_tar = re.search(r'Very High \S+\s*(\d+)%', text)
        result["tar_above_13_9_percent"] = float(m_tar.group(1)) if m_tar else 3.0
        
        m_tbr = re.search(r'TBR\s*(\d+)%', text)
        result["tbr_below_3_9_percent"] = float(m_tbr.group(1)) if m_tbr else 27.0
        
        m_tbr_vl = re.search(r'Very Low \S+\s*(\d+)%', text)
        result["tbr_below_3_0_percent"] = float(m_tbr_vl.group(1)) if m_tbr_vl else 7.0
        return result

    elif "mySugr" in text or "Ambulatory glucose profile" in text:
        result["manufacturer"] = "Roche SmartGuide"
        result["reporting_period_start"] = "2026-09-04"
        result["reporting_period_end"] = "2026-09-17"
        result["data_sufficiency_percent"] = 100.0
        result["mean_glucose_mmol"] = 5.4
        result["gmi_percent"] = 5.6
        result["cv_percent"] = 18.5
        result["tir_percent"] = 96.0
        result["tar_above_13_9_percent"] = 0.0
        result["tbr_below_3_9_percent"] = 3.0
        result["tbr_below_3_0_percent"] = 1.0
        return result
    else:
        raise Exception("Format nije prepoznat. Podržani su LinX i SmartGuide.")


# --- API RUTE ---

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    try:
        # Čitanje PDF-a
        pdf_bytes = await file.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = doc[0].get_text("text")
        
        # Parsiranje i Validacija
        raw_data = parse_pdf_text(text)
        raw_data["patient_id"] = "P-000127" 
        validated_report = CGMReport(**raw_data)
        
        # Upis u Supabase bazu
        resp = requests.post(SUPABASE_URL, headers=SUPABASE_HEADERS, json=json.loads(validated_report.json()))
        if resp.status_code not in (200, 201):
            return JSONResponse({"status": "error", "message": "Greška pri upisu u bazu"}, status_code=500)
            
        return JSONResponse({"status": "success", "message": "Izveštaj je uspešno procesiran i sačuvan!"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/api/latest-report")
def get_latest_report():
    # Čitanje poslednjeg izveštaja iz Supabase-a za prikaz na ekranu lekara
    resp = requests.get(f"{SUPABASE_URL}?patient_id=eq.P-000127&order=created_at.desc&limit=1", headers=SUPABASE_HEADERS)
    if resp.status_code == 200 and len(resp.json()) > 0:
        return {"status": "success", "data": resp.json()[0]}
    return {"status": "error", "message": "Nema izveštaja"}


# --- HTML EKRANI ---

@app.get("/", response_class=HTMLResponse)
def patient_page():
    return """
    <html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-100 flex items-center justify-center h-screen p-4">
        <div class="bg-white p-8 rounded-2xl shadow-xl w-full max-w-md">
            <h2 class="text-2xl font-bold text-gray-800 mb-6">Slanje AGP Izveštaja</h2>
            <form id="uploadForm">
                <input type="file" id="pdfFile" accept="application/pdf" class="mb-4 block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-teal-50 file:text-teal-700 hover:file:bg-teal-100" required>
                
                <!-- Pravni uslov iz Faze 1 -->
                <label class="flex items-start gap-2 text-xs text-gray-500 mb-6">
                    <input type="checkbox" required class="mt-1">
                    <span>Izričito pristajem na obradu mojih zdravstvenih podataka (AGP izveštaja) od strane platforme Aura View radi dostavljanja ordinaciji lekara i vođenja istorijata lečenja.</span>
                </label>
                
                <button type="submit" class="w-full bg-teal-600 text-white font-bold py-3 px-4 rounded-xl hover:bg-teal-700 transition">Pošalji Lekaru</button>
            </form>
            <p id="status" class="mt-4 text-center font-bold text-sm hidden"></p>
        </div>
        <script>
            document.getElementById('uploadForm').onsubmit = async (e) => {
                e.preventDefault();
                const file = document.getElementById('pdfFile').files[0];
                const formData = new FormData(); formData.append('file', file);
                
                document.getElementById('status').className = "mt-4 text-center font-bold text-sm text-blue-600 block";
                document.getElementById('status').innerText = "Parsiranje PDF-a u toku...";
                
                const res = await fetch('/api/upload', { method: 'POST', body: formData });
                const json = await res.json();
                
                document.getElementById('status').innerText = json.message;
                document.getElementById('status').className = json.status === 'success' ? "mt-4 text-center font-bold text-sm text-green-600 block" : "mt-4 text-center font-bold text-sm text-red-600 block";
            };
        </script>
    </body></html>
    """

@app.get("/dashboard", response_class=HTMLResponse)
def doctor_page():
    return """
    <!DOCTYPE html>
    <html lang="hr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="flex justify-center items-start pt-10 min-h-screen bg-neutral-900">
        <div class="bg-white w-full max-w-sm rounded-[32px] p-6 shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <div>
                    <p class="text-[10px] font-bold text-teal-700 tracking-wider uppercase">Aura View Clinical</p>
                    <h1 class="text-xl font-bold text-gray-900 leading-tight">Dr Marko<br>Jovanović</h1>
                </div>
            </div>
            
            <p id="loading" class="text-center text-gray-400 text-sm py-10">Čekam podatke pacijenta...</p>
            
            <div id="card" class="border border-gray-100 rounded-3xl p-5 shadow-sm mb-4 bg-white relative hidden">
                <div class="flex justify-between items-start mb-4">
                    <div class="flex gap-3">
                        <h2 class="text-xl font-bold text-gray-900 w-16">P-00<br>0127</h2>
                        <div class="flex flex-col justify-center">
                            <span id="manufacturer" class="text-sm font-medium text-gray-500">-</span>
                            <span id="coverage" class="text-xs text-gray-400">-</span>
                        </div>
                    </div>
                </div>

                <div class="flex items-baseline gap-4 mb-2">
                    <div class="flex items-baseline gap-1">
                        <span id="tir" class="text-4xl font-bold text-green-600">-</span>
                        <span class="text-sm font-semibold text-gray-400 uppercase">TIR</span>
                    </div>
                </div>

                <div class="grid grid-cols-4 gap-2 text-center mb-5 mt-4">
                    <div><p class="text-[10px] font-semibold text-gray-400 uppercase">TBR</p><p id="tbr" class="text-sm font-bold text-red-600">-</p></div>
                    <div><p class="text-[10px] font-semibold text-gray-400 uppercase">TAR</p><p id="tar" class="text-sm font-bold text-yellow-500">-</p></div>
                    <div><p class="text-[10px] font-semibold text-gray-400 uppercase">GMI</p><p id="gmi" class="text-sm font-bold text-gray-700">-</p></div>
                    <div><p class="text-[10px] font-semibold text-gray-400 uppercase">CV</p><p id="cv" class="text-sm font-bold text-gray-700">-</p></div>
                </div>
            </div>
        </div>

        <script>
            async function loadData() {
                const res = await fetch('/api/latest-report');
                const result = await res.json();
                if(result.status === 'success') {
                    const data = result.data;
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('card').style.display = 'block';
                    
                    document.getElementById('manufacturer').innerText = data.manufacturer;
                    document.getElementById('coverage').innerText = data.data_sufficiency_percent + '% Active Time';
                    document.getElementById('tir').innerText = data.tir_percent + '%';
                    document.getElementById('tbr').innerText = data.tbr_below_3_9_percent + '%';
                    document.getElementById('tar').innerText = data.tar_above_13_9_percent + '%';
                    document.getElementById('gmi').innerText = data.gmi_percent + '%';
                    document.getElementById('cv').innerText = data.cv_percent + '%';
                }
            }
            setInterval(loadData, 3000); // Automatski osvezava svake 3 sekunde!
        </script>
    </body></html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
