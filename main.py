from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
import pymupdf as fitz

app = FastAPI()

@app.post("/upload")
async def diagnostic_upload(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    
    total_pages = len(doc)
    pages_diagnostic = []
    total_words_count = 0
    total_text_len = 0
    
    for page_num in range(total_pages):
        page = doc[page_num]
        words = page.get_text("words")
        page_text = page.get_text()
        
        total_words_count += len(words)
        total_text_len += len(page_text)
        
        if len(words) == 0:
            word_status = "NO TEXT LAYER — OCR REQUIRED"
        else:
            word_status = "OK"
            
        # Prvih 50 reči sa traženim detaljima (text, x0, y0, x1, y1, block, line, word number)
        first_50_words = []
        for idx, w in enumerate(words[:50]):
            first_50_words.append({
                "word_number": idx + 1,
                "text": w[4],
                "x0": round(w[0], 2),
                "y0": round(w[1], 2),
                "x1": round(w[2], 2),
                "y1": round(w[3], 2),
                "block": w[5],
                "line": w[6]
            })
            
        pages_diagnostic.append({
            "page_number": page_num + 1,
            "word_count": len(words),
            "status": word_status,
            "first_50_words": first_50_words,
            "full_text": page_text
        })
        
    diagnostic_result = {
        "total_pages": total_pages,
        "total_words": total_words_count,
        "total_text_length": total_text_len,
        "pages": pages_diagnostic
    }
    
    return JSONResponse(content=diagnostic_result)

@app.get("/", response_class=HTMLResponse)
def patient_form():
    return """
    <!DOCTYPE html>
    <html lang="sr">
    <head>
        <meta charset="UTF-8">
        <title>Diagnostic Mode</title>
        <style>
            body { font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .card { background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); width: 100%; max-width: 600px; border: 1px solid #334155; }
            h2 { color: #38bdf8; margin-top: 0; }
            input[type="file"] { margin: 15px 0; color: #cbd5e1; }
            button { background: #0d9488; color: white; border: none; width: 100%; padding: 12px; border-radius: 8px; font-weight: bold; cursor: pointer; }
            button:hover { background: #0f766e; }
            pre { margin-top: 15px; font-size: 11px; max-height: 300px; overflow: auto; background: #0f172a; padding: 10px; border-radius: 6px; border: 1px solid #334155; color: #34d399; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>PDF Diagnostic Mode</h2>
            <form id="f">
                <input type="file" id="fi" accept=".pdf" required><br>
                <button type="submit">Pokreni Dijagnostiku</button>
            </form>
            <pre id="s">Čekanje na fajl...</pre>
        </div>
        <script>
        document.getElementById('f').onsubmit = async (e) => {
            e.preventDefault();
            const fd = new FormData(); 
            fd.append('file', document.getElementById('fi').files[0]);
            document.getElementById('s').innerText = "Učitavanje i analiza u toku...";
            try {
                const res = await fetch('/upload', { method: 'POST', body: fd });
                const data = await res.json();
                document.getElementById('s').innerText = JSON.stringify(data, null, 2);
            } catch(err) {
                document.getElementById('s').innerText = "Greška: " + err;
            }
        };
        </script>
    </body>
    </html>
    """
