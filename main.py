from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request

from parser import UniversalCGMParser
from database import save_report_to_db, fetch_reports_from_db

app = FastAPI(
    title="Universal CGM AGP Modular System",
    version="3.0.0"
)

templates = Jinja2Templates(directory="templates")

@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Dozvoljeni su samo PDF fajlovi.")
    
    pdf_bytes = await file.read()
    parser = UniversalCGMParser()
    report = parser.parse(pdf_bytes)
    
    actuals = report["actual_components"]
    derived = report["derived_metrics"]
    
    parsed_data = {
        "patient_id": "P-000127",
        "device_name": "Accu-Check SmartGuide Predict",
        "manufacturer": "Accu-Check / Universal",
        "tir": derived.get("TIR"),
        "tbr": derived.get("TBR"),
        "tar": derived.get("TAR"),
        "gmi_percent": actuals.get("GMI"),
        "cv": actuals.get("CV"),
        "active_time": str(actuals.get("ACTIVE_TIME")) + "%" if actuals.get("ACTIVE_TIME") is not None else None,
    }
    
    # Upis u bazu preko izdvojenog modula
    save_report_to_db(parsed_data)
        
    return {"status": report["status"], "data": parsed_data, "report": report}

@app.get("/api/reports")
def get_reports():
    return fetch_reports_from_db()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/dashboard", response_class=HTMLResponse)
async def doctor_dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

