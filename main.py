from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
import fitz  # PyMuPDF
import re
from datetime import datetime
from typing import Optional, Dict, Any

app = FastAPI(
    title="CGM Parser & Inspector",
    version="2.0.0"
)

# ============================================================
# METRIC DEFINITIONS (Robust Aliases)
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
    "AVG_GLUCOSE": {"aliases": ["average glucose", "mean glucose", "prosečna vrednost glukoze", "prosečna glukoza"], "unit": "mg/dL"}
}

MONTHS_ENG = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
MONTHS_SRB = "jan|feb|mar|apr|maj|jun|jul|avg|sep|okt|nov|dec|јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец"
DATE_PATTERN = re.compile(rf"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Пон|Уто|Сре|Чет|Пет|Суб|Нед)?\s*(\d{{1,2}}\s+(?:{MONTHS_ENG}|{MONTHS_SRB})\.?\s+\d{{4}})", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"(?P<operator>[<>≤≥]?)\s*(?P<number>\d{1,3}(?:[.,]\d{1,2})?)\s*(?P<percent>%?)")

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower().replace("–", "-").replace("—", "-").replace("−", "-")
    return re.sub(r"\s+", " ", text).strip()

def safe_float(value: str) -> Optional[float]:
    try:
        return float(value.replace(",", "."))
    except Exception:
        return None

class CGMParser:
    def parse(self, pdf_bytes: bytes) -> Dict[str, Any]:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            raise ValueError(f"Neuspešno otvaranje PDF-a: {str(e)}")
        
        words = []
        for page_num in range(min(2, len(doc))):
            page = doc[page_num]
            for w in page.get_text("words"):
                if len(w) >= 8 and w[4].strip():
                    words.append({"text": w[4].strip(), "normalized": normalize_text(w[4])})
        doc.close()

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
                        for num_match in nums:
                            val = safe_float(num_match[1])
                            if val is not None:
                                found_val = val
                                break
                    if found_val is not None:
                        break
            actuals[metric] = found_val

        # Izvedene vrednosti
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

        return {
            "period": {"start": start_date, "end": end_date},
            "actual_components": actuals,
            "derived_metrics": {"TBR": tbr, "TIR": tir, "TAR": tar}
        }

# ============================================================
# API ENDPOINT
# ============================================================

@app.post("/api/parse")
async def parse_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Dozvoljeni su samo PDF fajlovi.")
    
    pdf_bytes = await file.read()
    parser = CGMParser()
    result = parser.parse(pdf_bytes)
    return result

# ============================================================
# FRONTEND UI (Clean & Modern Dashboard)
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    return """
<!DOCTYPE html>
<html lang="sr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CGM Parser - Test Workbench</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen flex flex-col items-center py-10 px-4">
    <div class="w-full max-w-3xl">
        <header class="text-center mb-10">
            <h1 class="text-3xl font-bold tracking-tight text-teal-400">CGM Izveštaj — Testni Parser</h1>
            <p class="text-slate-400 mt-2">Prevucite ili izaberite PDF izveštaj da proverite preciznost očitavanja.</p>
        </header>

        <!-- Upload Box -->
        <div class="bg-slate-800 border-2 border-dashed border-slate-700 rounded-2xl p-8 text-center hover:border-teal-500 transition-all shadow-xl">
            <input type="file" id="fileInput" accept=".pdf" class="hidden" onchange="uploadFile(this.files[0])">
            <label for="fileInput" class="cursor-pointer flex flex-col items-center">
                <svg class="w-12 h-12 text-teal-400 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path></svg>
                <span class="text-lg font-medium text-slate-200">Kliknite ovde da izaberete PDF</span>
                <span class="text-sm text-slate-500 mt-1">Podržava mySugr, Accu-Check, Linx i druge AGP formate</span>
            </label>
        </div>

        <!-- Loading state -->
        <div id="loader" class="hidden text-center my-8">
            <div class="inline-block animate-spin rounded-full h-8 w-8 border-4 border-teal-500 border-t-transparent"></div>
            <p class="text-slate-400 mt-2">Analiza dokumenta u toku...</p>
        </div>

        <!-- Results Container -->
        <div id="results" class="hidden mt-8 space-y-6">
            <div class="bg-slate-800 border border-slate-700 rounded-xl p-6 shadow-lg">
                <h3 class="text-lg font-semibold text-teal-300 mb-4 border-b border-slate-700 pb-2">Glavni parametri (AGP)</h3>
                <div class="grid grid-cols-3 gap-4 text-center">
                    <div class="bg-slate-900/50 p-4 rounded-lg border border-slate-700/50">
                        <div class="text-xs text-slate-400 uppercase font-semibold">TBR (Nisko)</div>
                        <div id="res-tbr" class="text-2xl font-bold text-amber-400 mt-1">-</div>
                    </div>
                    <div class="bg-slate-900/50 p-4 rounded-lg border border-slate-700/50">
                        <div class="text-xs text-slate-400 uppercase font-semibold">TIR (U opsegu)</div>
                        <div id="res-tir" class="text-2xl font-bold text-emerald-400 mt-1">-</div>
                    </div>
                    <div class="bg-slate-900/50 p-4 rounded-lg border border-slate-700/50">
                        <div class="text-xs text-slate-400 uppercase font-semibold">TAR (Visoko)</div>
                        <div id="res-tar" class="text-2xl font-bold text-rose-400 mt-1">-</div>
                    </div>
                </div>
            </div>

            <div class="bg-slate-800 border border-slate-700 rounded-xl p-6 shadow-lg">
                <h3 class="text-lg font-semibold text-teal-300 mb-4 border-b border-slate-700 pb-2">Detaljne komponente</h3>
                <div id="raw-metrics" class="grid grid-cols-2 gap-3 text-sm text-slate-300">
                    <!-- Dinamički popunjeno -->
                </div>
            </div>
        </div>
    </div>

    <script>
    async function uploadFile(file) {
        if (!file) return;
        const formData = new FormData();
        formData.append("file", file);

        document.getElementById("loader").classList.remove("hidden");
        document.getElementById("results").classList.add("hidden");

        try {
            const res = await fetch("/api/parse", { method: "POST", body: formData });
            const data = await res.json();
            
            if(!res.ok) { alert(data.detail || "Greška pri obradi."); return; }

            // Popuni derived
            document.getElementById("res-tbr").textContent = data.derived_metrics.TBR !== null ? data.derived_metrics.TBR + "%" : "N/A";
            document.getElementById("res-tir").textContent = data.derived_metrics.TIR !== null ? data.derived_metrics.TIR + "%" : "N/A";
            document.getElementById("res-tar").textContent = data.derived_metrics.TAR !== null ? data.derived_metrics.TAR + "%" : "N/A";

            // Popuni ostale raw metrike
            const rawContainer = document.getElementById("raw-metrics");
            rawContainer.innerHTML = Object.entries(data.actual_components).map(([key, val]) => `
                <div class="flex justify-between bg-slate-900/40 px-3 py-2 rounded border border-slate-700/30">
                    <span class="text-slate-400">${key}:</span>
                    <span class="font-bold text-slate-100">${val !== null ? val : 'Nije pronađeno'}</span>
                </div>
            `).join('');

            document.getElementById("results").classList.remove("hidden");
        } catch (err) {
            alert("Došlo je do greške sa serverom.");
        } finally {
            document.getElementById("loader").classList.add("hidden");
        }
    }
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
