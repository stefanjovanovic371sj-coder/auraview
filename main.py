from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os

from parsers.factory import get_parser_for_device, ParserNotImplementedError
from database import save_report_to_db, fetch_reports_from_db

app = FastAPI(title="Modular CGM AGP Platform", version="3.2.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    device: str = Form(...)
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Dozvoljeni su samo PDF fajlovi.")

    try:
        parser = get_parser_for_device(device)
    except ParserNotImplementedError as err:
        raise HTTPException(status_code=400, detail=str(err))

    pdf_bytes = await file.read()
    report = parser.parse(pdf_bytes)

    actuals = report["actual_components"]
    derived = report["derived_metrics"]

    parsed_data = {
        "patient_id": "P-000127",
        "device_name": device.upper(),
        "manufacturer": "Universal / " + device.capitalize(),
        "tir": derived.get("TIR"),
        "tbr": derived.get("TBR"),
        "tar": derived.get("TAR"),
        "gmi_percent": actuals.get("GMI"),
        "cv": actuals.get("CV"),
        "active_time": str(actuals.get("ACTIVE_TIME")) + "%" if actuals.get("ACTIVE_TIME") is not None else None,
    }

    save_report_to_db(parsed_data)

    return {"status": report["status"], "data": parsed_data, "report": report}

@app.get("/api/reports")
def get_reports():
    return fetch_reports_from_db()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {})

@app.get("/dashboard", response_class=HTMLResponse)
async def doctor_dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
