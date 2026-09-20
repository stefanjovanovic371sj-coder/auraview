from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
import fitz  # PyMuPDF
import re
import requests
from datetime import datetime
from typing import Optional, Dict, List, Any


# ============================================================
# CONFIG (Supabase)
# ============================================================

SUPABASE_URL = "https://tuhgurlibsaqqxrmhgdr.supabase.co"
SUPABASE_KEY = "sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39"
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

app = FastAPI(
    title="SaaS CGM Platform - Multi-Doctor & Patient Tokens",
    version="3.0.0"
)


# ============================================================
# METRIC DEFINITIONS (Accu-Check, Linx, mySugr)
# ============================================================

Metrics = {
    "VERY_LOW": {"aliases": ["very low", "vrlo nisko", "веома ниско"], "unit": "%"},
    "LOW": {"aliases": ["low", "tbr", "below range", "nisko", "ниско"], "unit": "%"},
    "IN_RANGE": {"aliases": ["in range", "time in range", "tir", "u opsegu", "у опсегу"], "unit": "%"},
    "HIGH": {"aliases": ["high", "tar", "above range", "visoko", "високо"], "unit": "%"},
    "VERY_HIGH": {"aliases": ["very high", "vrlo visoko", "веома високо"], "unit": "%"},
    "GMI": {"aliases": ["glucose management indicator", "gmi", "indikator upravljanja glukozom"], "unit": "%"},
    "CV": {"aliases": ["coefficient of variation", "cv", "varijabilnost", "varijabilnost glukoze"], "unit": "%"},
    "ACTIVE_TIME": {"aliases": ["cgm active", "active time", "aktivno vreme cgm", "aktivno vreme"], "unit": "%"},
    "AVG_GLUCOSE": {"aliases": ["average glucose", "mean glucose", "prosečna vrednost glukoze", "prosečna glukoza"], "unit": "GLUCOSE"}
}

MONTHS_ENG = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
MONTHS_SRB = "jan|feb|mar|apr|maj|jun|jul|avg|sep|okt|nov|dec|јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец"
DATE_PATTERN = re.compile(rf"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Пон|Уто|Сре|Чет|Пет|Суб|Нед)?\s*(\d{{1,2}}\s+(?:{MONTHS_ENG}|{MONTHS_SRB})\.?\s+\d{{4}})", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"(?P<operator>[<>≤≥]?)\s*(?P<number>\d{1,3}(?:[.,]\d{1,2})?)\s*(?P<percent>%?)")


# ============================================================
# HELPERS & PARSER
# ============================================================

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower().replace("–", "-").replace("—", "-").replace("−", "-")
    return re.sub(r"\s+", " ", text).strip()

def parse_date_flexible(date_str: str) -> Optional[datetime]:
    date_str = date_str.replace(".", "").strip()
    srb_to_eng = {"јан":"Jan","мар":"Mar","апр":"Apr","мај":"May","јун":"Jun","јул":"Jul","авг":"Aug","сеп":"Sep","окт":"Oct","нов":"Nov","дец":"Dec","jan":"Jan","feb":"Feb","mar":"Mar","apr":"Apr","may":"May","jun":"Jun","jul":"Jul","avg":"Aug","sep":"Sep","okt":"Oct","nov":"Nov","dec":"Dec"}
    for k, v in srb_to_eng.items():
        if k in date_str.lower():
            for part in date_str.split():
                if k in part.lower():
                    date_str = date_str.replace(part, v)
                    break
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None

def safe_float(value: str) -> Optional[float]:
    try:
        return float(value.replace(",", "."))
    except Exception:
        return None

class UniversalCGMParser:
    def parse(self, pdf_bytes: bytes) -> Dict[str, Any]:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            raise ValueError(f"Cannot open PDF: {str(e)}")
        
        words = []
        for page_num in range(min(2, len(doc))):
            page = doc[page_num]
            for w in page.get_text("words"):
                if len(w) >= 8 and w[4].strip():
                    words.append({"x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3], "text": w[4].strip(), "normalized": normalize_text(w[4])})
        doc.close()

        full_text = " ".join(w["text"] for w in words)
        norm_full = normalize_text(full_text)

        actuals = {}
        for metric, conf in Metrics.items():
            found_val = None
            for alias in conf["aliases"]:
                if alias in norm_full:
                    idx = norm_full.find(alias)
                    snippet = norm_full[idx:idx + 60]
                    nums = NUMBER_PATTERN.findall(snippet)
                    if nums:
                        val = safe_float(nums[0][1])
                        if val is not None:
                            found_val = val
                            break
            actuals[metric] = found_val

        tbr = round(actuals.get("VERY_LOW", 0) or 0 + actuals.get("LOW", 0) or 0, 2)
        if actuals.get("VERY_LOW") is None and actuals.get("LOW") is None: tbr = None

        tir = actuals.get("IN_RANGE")
        tar = round(actuals.get("HIGH", 0) or 0 + actuals.get("VERY_HIGH", 0) or 0, 2)
        if actuals.get("HIGH") is None and actuals.get("VERY_HIGH") is None: tar = None

        dates = [m.group(1) for m in DATE_PATTERN.finditer(full_text)]
        start_date, end_date = (dates[0], dates[-1]) if len(dates) >= 2 else (None, None)

        status = "SUCCESS" if all(x is not None for x in [tir, actuals.get("GMI"), actuals.get("CV")]) else "MANUAL_REVIEW"

        return {
            "status": status,
            "period": {"start": start_date, "end": end_date},
            "actual_components": actuals,
            "derived_metrics": {"TBR": tbr, "TIR": tir, "TAR": tar}
        }


# ============================================================
# API ENDPOINTS & WEB PAGES
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def landing_page():
    return LANDING_HTML

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_HTML

@app.get("/doctor/dashboard", response_class=HTMLResponse)
async def doctor_dashboard(doctor_id: int = 1):
    return DOCTOR_DASHBOARD_HTML

@app.get("/upload/{token}", response_class=HTMLResponse)
async def patient_upload_page(token: str):
    return PATIENT_UPLOAD_HTML.replace("{{TOKEN}}", token)

@app.post("/api/upload-report/{token}")
async def upload_report_by_token(token: str, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Samo PDF fajlovi su podržani.")
    
    # 1. Pronađi pacijenta preko tokena u Supabase
    pat_res = requests.get(f"{SUPABASE_URL}/rest/v1/patients?token=eq.{token}&select=id,name,doctor_id", headers=SUPABASE_HEADERS)
    patients = pat_res.json()
    if not patients:
        raise HTTPException(status_code=404, detail="Nevažeći ili istekao link za upload.")
    
    patient = patients[0]
    
    # 2. Parsiraj PDF lokalno bezbedno
    pdf_bytes = await file.read()
    parser = UniversalCGMParser()
    report = parser.parse(pdf_bytes)
    
    act = report["actual_components"]
    der = report["derived_metrics"]
    
    # 3. Sačuvaj izveštaj u Supabase povezan sa pacijentom
    payload = {
        "patient_id": patient["id"],
        "doctor_id": patient["doctor_id"],
        "device_name": "Universal CGM / AGP Report",
        "tir": der.get("TIR"),
        "tbr": der.get("TBR"),
        "tar": der.get("TAR"),
        "gmi_percent": act.get("GMI"),
        "cv": act.get("CV"),
        "active_time": str(act.get("ACTIVE_TIME")) + "%" if act.get("ACTIVE_TIME") else None,
        "avg_glucose": act.get("AVG_GLUCOSE")
    }
    
    ins_res = requests.post(f"{SUPABASE_URL}/rest/v1/cgm_reports", headers=SUPABASE_HEADERS, json=payload)
    if ins_res.status_code not in [200, 201]:
        raise HTTPException(status_code=500, detail="Greška pri upisu u bazu.")
        
    return {"status": "SUCCESS", "message": "Izveštaj uspešno sačuvan na Vašem profilu!", "data": payload}

@app.get("/api/doctor/{doctor_id}/patients")
def get_doctor_patients(doctor_id: int):
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/patients?doctor_id=eq.{doctor_id}&select=*,cgm_reports(*)", headers=SUPABASE_HEADERS)
        return res.json()
    except Exception:
        return []

@app.post("/api/doctor/patients")
def create_patient(doctor_id: int = Form(...), name: str = Form(...), token: str = Form(...)):
    payload = {"doctor_id": doctor_id, "name": name, "token": token}
    res = requests.post(f"{SUPABASE_URL}/rest/v1/patients", headers=SUPABASE_HEADERS, json=payload)
    return res.json()


# ============================================================
# HTML INTERFACES (SaaS Frontend UI)
# ============================================================

LANDING_HTML = """
<!DOCTYPE html>
<html lang="sr">
<head><meta charset="UTF-8"><title>CGM Platforma za Lekare</title>
<style>body{font-family:sans-serif;background:#0f172a;color:#fff;text-align:center;padding:50px;}
a{background:#0d9488;color:#fff;padding:15px 30px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:18px;}
</style></head>
<body>
<h1>Sistem za Praćenje CGM Izveštaja</h1>
<p>Platforma namenjena lekarima i pacijentima za bezbednu sinhronizaciju AGP nalaza.</p><br><br>
<a href="/login">Prijava za Lekare →</a>
</body></html>
"""

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="sr">
<head><meta charset="UTF-8"><title>Prijava Lekara</title>
<style>body{font-family:sans-serif;background:#f1f5f9;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}
.card{background:#fff;padding:30px;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,0.1);width:320px;text-align:left;}
input,button{width:100%;padding:12px;margin-top:10px;border-radius:6px;border:1px solid #cbd5e1;box-sizing:border-box;}
button{background:#0d9488;color:#fff;font-weight:bold;border:none;cursor:pointer;}
</style></head>
<body>
<div class="card">
<h2>Lekarska Prijava</h2>
<input type="email" id="email" value="dr.marko@ordinacija.rs" placeholder="Email">
<input type="password" id="pass" value="******" placeholder="Lozinka">
<button onclick="location.href='/doctor/dashboard?doctor_id=1'">Prijavi se</button>
</div></body></html>
"""

DOCTOR_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="sr">
<head><meta charset="UTF-8"><title>Lekarski Panel</title>
<style>body{background:#0f172a;color:#f8fafc;font-family:sans-serif;padding:30px;margin:0;}
.container{max-width:900px;margin:auto;}
.card{background:#1e293b;padding:20px;margin-bottom:15px;border-radius:10px;border:1px solid #334155;}
button{background:#38bdf8;color:#0f172a;border:none;padding:10px 15px;font-weight:bold;border-radius:6px;cursor:pointer;}
input{padding:10px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#fff;margin-right:10px;}
.link-box{background:#0f172a;padding:8px;border-radius:4px;color:#38bdf8;font-family:monospace;margin-top:5px;}
</style></head>
<body>
<div class="container">
<h2>Lekarski Panel — Moji Pacijenti</h2>
<div class="card">
<h3>Dodaj novog pacijenta</h3>
<input id="pname" placeholder="Ime i prezime pacijenta">
<input id="ptoken" placeholder="Jedinstveni token (npr. STEFAN-123)">
<button onclick="addPatient()">Kreiraj Pacijenta & Link</button>
</div>
<div id="patientsList">Učitavanje...</div>
</div>
<script>
async function loadPatients() {
    const res = await fetch('/api/doctor/1/patients');
    const data = await res.json();
    if(!data.length) { document.getElementById('patientsList').innerHTML = "<p>Nema unetih pacijenata.</p>"; return; }
    document.getElementById('patientsList').innerHTML = data.map(p => `
        <div class="card">
            <strong>${p.name}</strong><br>
            <small>Personalizovani link za pacijenta:</small>
            <div class="link-box">window.location.origin + "/upload/${p.token}"</div>
            <p>Broj izveštaja na timeline-u: <b>${p.cgm_reports ? p.cgm_reports.length : 0}</b></p>
        </div>
    `).join('');
}
async function addPatient() {
    const name = document.getElementById('pname').value;
    const token = document.getElementById('ptoken').value;
    if(!name || !token) { alert('Unesite ime i token.'); return; }
    const formData = new FormData();
    formData.append('doctor_id', 1);
    formData.append('name', name);
    formData.append('token', token);
    await fetch('/api/doctor/patients', { method: 'POST', body: formData });
    document.getElementById('pname').value = '';
    document.getElementById('ptoken').value = '';
    loadPatients();
}
loadPatients();
</script>
</body></html>
"""

PATIENT_UPLOAD_HTML = """
<!DOCTYPE html>
<html lang="sr">
<head><meta charset="UTF-8"><title>Portal za Upload Izveštaja</title>
<style>body{font-family:sans-serif;background:#f8fafc;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}
.card{background:#fff;padding:40px;border-radius:12px;box-shadow:0 4px 15px rgba(0,0,0,0.08);width:400px;text-align:center;}
input,button{width:100%;padding:12px;margin-top:15px;border-radius:8px;box-sizing:border-box;}
button{background:#0d9488;color:#fff;font-weight:bold;border:none;cursor:pointer;}
</style></head>
<body>
<div class="card">
<h2>Pošaljite Vaš CGM Izveštaj</h2>
<p style="color:#64748b;font-size:14px;">Izaberite PDF izveštaj sa vašeg uređaja. Podaci se automatski šalju vašem lekaru.</p>
<input id="file" type="file" accept=".pdf">
<button onclick="upload()">Pošalji Izveštaj</button>
<p id="msg" style="margin-top:15px;font-weight:bold;"></p>
</div>
<script>
async function upload() {
    const fileInput = document.getElementById("file");
    const msg = document.getElementById("msg");
    if (!fileInput.files.length) { alert("Izaberite fajl."); return; }
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    msg.style.color = "#0284c7";
    msg.textContent = "Slanje i obrada u toku...";
    try {
        const res = await fetch("/api/upload-report/{{TOKEN}}", { method: "POST", body: formData });
        const data = await res.json();
        if(res.ok) {
            msg.style.color = "#16a34a";
            msg.textContent = data.message;
        } else {
            msg.style.color = "#dc2626";
            msg.textContent = data.detail || "Greška pri slanju.";
        }
    } catch(err) {
        msg.style.color = "#dc2626";
        msg.textContent = "Došlo je do mrežne greške.";
    }
}
</script>
</body></html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
