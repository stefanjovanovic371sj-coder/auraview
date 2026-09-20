from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import HTMLResponse
import fitz  # PyMuPDF
import re
import requests
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple


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
    title="SaaS CGM Platform - Robust Parser & Multi-Doctor Architecture",
    version="3.1.0"
)


# ============================================================
# METRIC DEFINITIONS (Expanded for Accuracy)
# ============================================================

Metrics = {
    "VERY_LOW": {
        "aliases": ["very low", "very-low", "vrlo nisko", "веома ниско", "veoma nizak nivo"],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },
    "LOW": {
        "aliases": ["low", "tbr", "below range", "nisko", "ниско", "nizak nivo"],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },
    "IN_RANGE": {
        "aliases": ["in range", "in-range", "time in range", "tir", "u opsegu", "у опсегу", "normalno"],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },
    "HIGH": {
        "aliases": ["high", "tar", "above range", "visoko", "високо"],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },
    "VERY_HIGH": {
        "aliases": ["very high", "very-high", "vrlo visoko", "веома високо", "veoma visoko"],
        "unit": "%",
        "type": "RANGE_COMPONENT"
    },
    "GMI": {
        "aliases": ["glucose management indicator", "gmi", "indikator upravljanja glukozom", "индикатор управљања глукозом"],
        "unit": "%",
        "type": "METRIC"
    },
    "CV": {
        "aliases": ["glucose variability", "coefficient of variation", "cv", "varijabilnost", "варијабилност", "varijabilnost glukoze", "koeficijent varijacije"],
        "unit": "%",
        "type": "METRIC"
    },
    "ACTIVE_TIME": {
        "aliases": ["time cgm active", "cgm active", "active time", "sensor active", "aktivno vreme cgm", "aktivno vreme", "активно време"],
        "unit": "%",
        "type": "METRIC"
    },
    "AVG_GLUCOSE": {
        "aliases": ["average glucose", "mean glucose", "mbg", "prosečna vrednost glukoze", "просечна вредност глукозе", "prosečna glukoza"],
        "unit": "GLUCOSE",
        "type": "METRIC"
    }
}

MONTHS_ENG = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
MONTHS_SRB = "jan|feb|mar|apr|maj|jun|jul|avg|sep|okt|nov|dec|јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец"
DATE_PATTERN = re.compile(rf"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Пон|Уто|Сре|Чет|Пет|Суб|Нед)?\s*(\d{{1,2}}\s+(?:{MONTHS_ENG}|{MONTHS_SRB})\.?\s+\d{{4}})", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"(?P<operator>[<>≤≥]?)\s*(?P<number>\d{1,3}(?:[.,]\d{1,2})?)\s*(?P<percent>%?)")


# ============================================================
# ROBUST PARSER ENGINE
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

class RobustCGMParser:
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
                    words.append({
                        "x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3],
                        "text": w[4].strip(), "normalized": normalize_text(w[4])
                    })
        doc.close()

        # Build lines and text representation for smart fallback search
        full_text = " ".join(w["text"] for w in words)
        norm_full = normalize_text(full_text)

        actuals = {}
        for metric, conf in Metrics.items():
            found_val = None
            for alias in conf["aliases"]:
                if alias in norm_full:
                    idx = norm_full.find(alias)
                    snippet = norm_full[idx:idx + 80]
                    nums = NUMBER_PATTERN.findall(snippet)
                    if nums:
                        # Skip the alias itself if captured, find the first valid number following it
                        for num_match in nums:
                            val = safe_float(num_match[1])
                            if val is not None:
                                found_val = val
                                break
                    if found_val is not None:
                        break
            actuals[metric] = found_val

        # Derived calculations
        very_low = actuals.get("VERY_LOW")
        low = actuals.get("LOW")
        in_range = actuals.get("IN_RANGE")
        high = actuals.get("HIGH")
        very_high = actuals.get("VERY_HIGH")

        tbr = round((very_low or 0) + (low or 0), 2) if (very_low is not None or low is not None) else None
        tir = in_range
        tar = round((high or 0) + (very_high or 0), 2) if (high is not None or very_high is not None) else None

        dates = [m.group(1) for m in DATE_PATTERN.finditer(full_text)]
        start_date, end_date = (dates[0], dates[-1]) if len(dates) >= 2 else (None, None)

        status = "SUCCESS" if tir is not None and actuals.get("GMI") is not None else "MANUAL_REVIEW"

        return {
            "status": status,
            "period": {"start": start_date, "end": end_date},
            "actual_components": actuals,
            "derived_metrics": {"TBR": tbr, "TIR": tir, "TAR": tar}
        }


# ============================================================
# API ENDPOINTS (Multi-Doctor & Token Uploads)
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
    
    # 1. Pronađi pacijenta preko jedinstvenog tokena u Supabase
    pat_res = requests.get(f"{SUPABASE_URL}/rest/v1/patients?token=eq.{token}&select=id,name,doctor_id", headers=SUPABASE_HEADERS)
    patients = pat_res.json()
    if not patients:
        raise HTTPException(status_code=404, detail="Nevažeći ili istekao link za upload.")
    
    patient = patients[0]
    
    # 2. Parsiraj PDF lokalno i bezbedno
    pdf_bytes = await file.read()
    parser = RobustCGMParser()
    report = parser.parse(pdf_bytes)
    
    act = report["actual_components"]
    der = report["derived_metrics"]
    
    # 3. Zapiši izveštaj u bazu vezan za pacijenta (Timeline)
    payload = {
        "patient_id": patient["id"],
        "doctor_id": patient["doctor_id"],
        "device_name": "CGM AGP Report",
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
        raise HTTPException(status_code=500, detail="Greška pri upisu izveštaja u bazu.")
        
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
# FRONTEND INTERFACES
# ============================================================

LANDING_HTML = """
<!DOCTYPE html>
<html lang="sr">
<head><meta charset="UTF-8"><title>CGM Platforma za Lekare</title>
<style>body{font-family:sans-serif;background:#0f172a;color:#fff;text-align:center;padding:60px;}
a{background:#0d9488;color:#fff;padding:15px 30px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:18px;}
</style></head>
<body>
<h1>Platforma za Praćenje CGM Izveštaja</h1>
<p>Bezbedna lokalna obrada AGP nalaza sa podrškom za više lekara i pacijenata.</p><br><br>
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
.link-box{background:#0f172a;padding:8px;border-radius:4px;color:#38bdf8;font-family:monospace;margin-top:5px;word-break:break-all;}
</style></head>
<body>
<div class="container">
<h2>Lekarski Panel — Upravljanje Pacijentima</h2>
<div class="card">
<h3>Dodaj novog pacijenta</h3>
<input id="pname" placeholder="Ime i prezime pacijenta">
<input id="ptoken" placeholder="Jedinstveni kod (npr. STEFAN-01)">
<button onclick="addPatient()">Kreiraj Profil & Link</button>
</div>
<div id="patientsList">Učitavanje pacijenata...</div>
</div>
<script>
async function loadPatients() {
    const res = await fetch('/api/doctor/1/patients');
    const data = await res.json();
    if(!data.length) { document.getElementById('patientsList').innerHTML = "<div class='card'>Nema unetih pacijenata.</div>"; return; }
    document.getElementById('patientsList').innerHTML = data.map(p => `
        <div class="card">
            <strong>${p.name}</strong><br>
            <small style="color:#94a3b8;">Personalizovani link za slanje izveštaja:</small>
            <div class="link-box">${window.location.origin}/upload/${p.token}</div>
            <p style="margin-top:10px;">Broj izveštaja na timeline-u: <b>${p.cgm_reports ? p.cgm_reports.length : 0}</b></p>
        </div>
    `).join('');
}
async function addPatient() {
    const name = document.getElementById('pname').value;
    const token = document.getElementById('ptoken').value;
    if(!name || !token) { alert('Unesite ime i jedinstveni token.'); return; }
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
<p style="color:#64748b;font-size:14px;">Izaberite PDF izveštaj sa vašeg uređaja. Podaci se automatski upisuju na vaš istorijski profil.</p>
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
    msg.textContent = "Obrada i slanje u toku...";
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
