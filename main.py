import sys
import os
import traceback

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from parsers.factory import get_parser_for_device, ParserNotImplementedError
from database import save_report_to_db, fetch_reports_from_db

app = FastAPI(title="Modular CGM AGP Platform", version="3.6.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates_dir = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=templates_dir)

def safe_render(template_name: str, request: Request, context: dict = None):
    """Pruža kompatibilnost sa svim verzijama FastAPI / Starlette biblioteke."""
    if context is None:
        context = {}
    context["request"] = request
    try:
        # Nova sintaksa (Starlette >= 0.28)
        return templates.TemplateResponse(request, template_name, context)
    except Exception:
        # Stara sintaksa
        return templates.TemplateResponse(template_name, context)

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

    try:
        save_report_to_db(parsed_data)
    except Exception as e:
        print(f"Upozorenje pri upisu u bazu: {e}")

    return {"status": report["status"], "data": parsed_data, "report": report}

@app.get("/api/reports")
def get_reports():
    try:
        return fetch_reports_from_db()
    except Exception as e:
        print(f"Upozorenje pri čitanju iz baze: {e}")
        return []

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    try:
        return safe_render("index.html", request)
    except Exception as e:
        return HTMLResponse(f"<h3>Greska u ucitavanju index.html:</h3><pre>{traceback.format_exc()}</pre>", status_code=500)

@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/doctor", response_class=HTMLResponse)
async def doctor_dashboard(request: Request):
    try:
        return safe_render("dashboard.html", request)
    except Exception as e:
        return HTMLResponse(f"<h3>Greska u ucitavanju dashboard.html:</h3><pre>{traceback.format_exc()}</pre>", status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
