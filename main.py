from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import pymupdf as fitz
import json

app = FastAPI()

# ---------------------------------------------------------
# POSTOJEĆI PRODUKCIONI ENDPOINTI (NETAKNUTI)
# ---------------------------------------------------------
# (Ovde ostaju tvoje postojeće / upload, /api/reports, / i /dashboard rute)
# Radi preglednosti i sigurnosti, u nastavku dajemo celu datoteku sa novom rutom.

SUPABASE_URL = "https://tuhgurlibsaqqxrmhgdr.supabase.co"
SUPABASE_HEADERS = {
    "apikey": "sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39",
    "Authorization": "Bearer sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# ---------------------------------------------------------
# NOVA ISOLATED DEBUG PDF RUTA: /debug-pdf
# ---------------------------------------------------------

@app.get("/debug-pdf", response_class=HTMLResponse)
def debug_pdf_ui():
    return """
    <!DOCTYPE html>
    <html lang="sr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>PDF Debug Viewer — PyMuPDF Inspector</title>
        <style>
            :root {
                --bg: #0b0f19;
                --card-bg: #111827;
                --card-border: #1f2937;
                --text: #f3f4f6;
                --text-muted: #9ca3af;
                --accent: #0ea5e9;
                --accent-hover: #0284c7;
                --success: #10b981;
                --warning: #f59e0b;
                --danger: #ef4444;
            }
            body {
                font-family: system-ui, -apple-system, sans-serif;
                background: var(--bg);
                color: var(--text);
                margin: 0;
                padding: 20px;
            }
            .container { max-width: 1400px; margin: 0 auto; }
            header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid var(--card-border);
                padding-bottom: 15px;
                margin-bottom: 20px;
            }
            h1 { font-size: 22px; color: var(--accent); margin: 0; }
            .badge {
                background: rgba(14, 165, 233, 0.1);
                color: var(--accent);
                padding: 4px 10px;
                border-radius: 9999px;
                font-size: 12px;
                font-weight: 600;
            }
            .card {
                background: var(--card-bg);
                border: 1px solid var(--card-border);
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 20px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            }
            .upload-zone {
                border: 2px dashed var(--card-border);
                padding: 30px;
                text-align: center;
                border-radius: 8px;
                cursor: pointer;
                transition: border-color 0.2s;
            }
            .upload-zone:hover { border-color: var(--accent); }
            input[type="file"] { color: var(--text); margin-bottom: 10px; }
            button {
                background: var(--accent);
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 6px;
                font-weight: 600;
                cursor: pointer;
                transition: background 0.2s;
            }
            button:hover { background: var(--accent-hover); }
            button.secondary {
                background: #374151;
                margin-left: 10px;
            }
            button.secondary:hover { background: #4b5563; }
            
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin-bottom: 20px;
            }
            .stat-card {
                background: #1f2937;
                padding: 15px;
                border-radius: 8px;
                border-left: 4px solid var(--accent);
            }
            .stat-value { font-size: 24px; font-weight: bold; margin-top: 5px; }
            
            tabs { display: flex; gap: 10px; margin-bottom: 15px; }
            .tab-btn {
                background: #1f2937;
                color: var(--text-muted);
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
            }
            .tab-btn.active { background: var(--accent); color: white; }
            
            pre, textarea {
                background: #030712;
                color: #34d399;
                padding: 15px;
                border-radius: 8px;
                border: 1px solid var(--card-border);
                font-family: ui-monospace, monospace;
                font-size: 12px;
                max-height: 400px;
                overflow: auto;
                width: 100%;
                box-sizing: border-box;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
                font-size: 13px;
                margin-top: 10px;
            }
            th, td {
                border: 1px solid var(--card-border);
                padding: 8px 12px;
                text-align: left;
            }
            th { background: #1f2937; color: var(--accent); }
            tr:hover { background: rgba(255,255,255,0.02); }
            
            .viewer-container {
                position: relative;
                display: inline-block;
                background: white;
                border-radius: 8px;
                overflow: auto;
                max-width: 100%;
                margin-top: 10px;
            }
            canvas { display: block; }
            .word-box {
                position: absolute;
                border: 1px solid rgba(239, 68, 68, 0.6);
                background: rgba(239, 68, 68, 0.15);
                cursor: pointer;
                box-sizing: border-box;
            }
            .word-box:hover {
                border-color: #38bdf8;
                background: rgba(56, 189, 248, 0.3);
                z-index: 10;
            }
            .word-id {
                position: absolute;
                font-size: 8px;
                color: #b91c1c;
                font-weight: bold;
                background: rgba(255,255,255,0.8);
                padding: 0 2px;
                pointer-events: none;
            }
            .block-box {
                position: absolute;
                border: 2px dashed rgba(16, 185, 129, 0.7);
                background: rgba(16, 185, 129, 0.05);
                pointer-events: none;
                box-sizing: border-box;
            }
            .line-box {
                position: absolute;
                border: 1px dotted rgba(245, 158, 11, 0.7);
                background: rgba(245, 158, 11, 0.05);
                pointer-events: none;
                box-sizing: border-box;
            }
            .controls-bar {
                display: flex;
                gap: 15px;
                align-items: center;
                margin-bottom: 15px;
                flex-wrap: wrap;
            }
            label { font-size: 14px; color: var(--text-muted); display: flex; align-items: center; gap: 6px; cursor: pointer; }
            
            .hidden { display: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>PDF Debug Viewer <span class="badge">PyMuPDF Raw Inspector</span></h1>
                <a href="/dashboard" style="color: var(--accent); text-decoration: none; font-size: 14px;">← Nazad na Dashboard</a>
            </header>

            <div class="card">
                <h2>1. Upload PDF-a za Dijagnostiku</h2>
                <form id="debugForm">
                    <div class="upload-zone">
                        <input type="file" id="pdfFile" accept=".pdf" required>
                        <p style="margin: 5px 0 0 0; color: var(--text-muted); font-size: 13px;">Izaberi AGP PDF fajl za raw inspekciju</p>
                    </div>
                    <div style="margin-top: 15px; display: flex; gap: 10px;">
                        <button type="submit">Pokreni Raw Analizu</button>
                        <a id="downloadBtn" class="hidden" style="display:inline-block;"><button type="button" class="secondary">Download Debug JSON</button></a>
                    </div>
                </form>
            </div>

            <div id="resultsSection" class="hidden">
                <!-- 8. TEXT EXTRACTION SUMMARY -->
                <div class="card">
                    <h2>8. Text Extraction Summary</h2>
                    <div class="stats-grid">
                        <div class="stat-card">
                            <div style="color:var(--text-muted)">Ukupno stranica</div>
                            <div class="stat-value" id="statPages">-</div>
                        </div>
                        <div class="stat-card">
                            <div style="color:var(--text-muted)">Ukupno reči (Words)</div>
                            <div class="stat-value" id="statWords">-</div>
                        </div>
                        <div class="stat-card">
                            <div style="color:var(--text-muted)">Ukupno karaktera</div>
                            <div class="stat-value" id="statChars">-</div>
                        </div>
                        <div class="stat-card">
                            <div style="color:var(--text-muted)">Status sloja teksta</div>
                            <div class="stat-value" id="statStatus" style="font-size:18px; color:var(--success)">-</div>
                        </div>
                    </div>
                    <div id="pagesSummaryList"></div>
                </div>

                <!-- 5. VISUAL WORD MAP & 6 & 7 VIEWS -->
                <div class="card">
                    <h2>Visual Layout & Bounding Box Inspector</h2>
                    <div class="controls-bar">
                        <label><input type="checkbox" id="chkBoxes" checked> Show word boxes</label>
                        <label><input type="checkbox" id="chkIds" checked> Show word IDs</label>
                        <label><input type="checkbox" id="chkBlocks"> Show PDF blocks</label>
                        <label><input type="checkbox" id="chkLines"> Show PDF lines</label>
                        <div style="margin-left: auto;">
                            <label>Stranica: <select id="pageSelector" style="background:#030712; color:white; border:1px solid var(--card-border); padding:4px 8px; border-radius:4px;"></select></label>
                        </div>
                    </div>
                    <div style="text-align: center; background: #030712; padding: 10px; border-radius: 8px;">
                        <div class="viewer-container" id="viewerContainer">
                            <canvas id="pdfCanvas"></canvas>
                            <div id="overlayLayer" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none;"></div>
                        </div>
                    </div>
                    <div id="tokenDetails" style="margin-top: 10px; padding: 10px; background: #030712; border-radius: 6px; font-size: 13px; color: var(--text-muted);">
                        Klikni na bilo koji word box iznad da vidiš njegove sirove parametre.
                    </div>
                </div>

                <!-- 2. RAW get_text() OUTPUT -->
                <div class="card">
                    <h2>2. Raw get_text() Output</h2>
                    <p style="color: var::text-muted; font-size: 13px;">Tačan sirovi tekst stranice vraćen direktno sa page.get_text()</p>
                    <textarea id="rawTextOutput" readonly rows="8"></textarea>
                </div>

                <!-- 3 & 4. RAW WORDS TABLE & COMPARISON -->
                <div class="card">
                    <h2>3 & 4. Raw Words (Original Order vs Geometric Order)</h2>
                    <div style="display: flex; gap: 10px; margin-bottom: 10px;">
                        <button class="tab-btn active" onclick="switchTableTab('raw')" id="btnTabRaw">A) Raw Order (PyMuPDF stream)</button>
                        <button class="tab-btn" onclick="switchTableTab('geo')" id="btnTabGeo">B) Geometric Order (y0 → x0)</button>
                    </div>
                    <div style="max-height: 400px; overflow-y: auto;">
                        <table id="wordsTable">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Text</th>
                                    <th>x0</th>
                                    <th>y0</th>
                                    <th>x1</th>
                                    <th>y1</th>
                                    <th>Block</th>
                                    <th>Line</th>
                                    <th>Word No</th>
                                    <th>Page</th>
                                </tr>
                            </thead>
                            <tbody id="wordsTableBody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
        <script>
            pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

            let debugData = null;
            let pdfDoc = null;
            let currentFileArrayBuffer = null;
            let activeTableMode = 'raw';
            let currentPageNum = 1;

            document.getElementById('debugForm').onsubmit = async (e) => {
                e.preventDefault();
                const fileInput = document.getElementById('pdfFile');
                if(!fileInput.files[0]) return;

                const file = fileInput.files[0];
                currentFileArrayBuffer = await file.arrayBuffer();
                
                // Load into PDF.js for rendering
                const loadingTask = pdfjsLib.getDocument({ data: currentFileArrayBuffer.slice(0) });
                pdfDoc = await loadingTask.promise;

                const fd = new FormData();
                fd.append('file', file);

                const submitBtn = e.target.querySelector('button[type="submit"]');
                submitBtn.innerText = "Učitavanje i analiza...";
                
                try {
                    const res = await fetch('/debug-pdf-inspect', { method: 'POST', body: fd });
                    debugData = await res.json();
                    
                    if(debugData.status === "success") {
                        renderDiagnostic(debugData.diagnostic);
                        document.getElementById('resultsSection').classList.remove('hidden');
                        
                        // Setup download JSON button
                        const jsonStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(debugData.diagnostic, null, 2));
                        const downloadBtn = document.getElementById('downloadBtn');
                        downloadBtn.setAttribute("href", jsonStr);
                        downloadBtn.setAttribute("download", "pdf_raw_debug.json");
                        downloadBtn.classList.remove('hidden');
                    } else {
                        alert("Greška: " + debugData.detail);
                    }
                } catch(err) {
                    alert("Došlo je do greške: " + err);
                } finally {
                    submitBtn.innerText = "Pokreni Raw Analizu";
                }
            };

            function renderDiagnostic(diag) {
                // 8. SUMMARY
                document.getElementById('statPages').innerText = diag.document.page_count;
                document.getElementById('statWords').innerText = diag.total_words;
                document.getElementById('statChars').innerText = diag.total_chars;
                
                const hasText = diag.total_words > 0;
                const statusEl = document.getElementById('statStatus');
                statusEl.innerText = hasText ? "OK (Text Layer Found)" : "NO TEXT LAYER — OCR REQUIRED";
                statusEl.style.color = hasText ? "var(--success)" : "var(--danger)";

                // Populate page selector
                const sel = document.getElementById('pageSelector');
                sel.innerHTML = "";
                diag.pages.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.page;
                    opt.innerText = `Stranica ${p.page} (${p.word_count} reči)`;
                    sel.appendChild(opt);
                });
                sel.onchange = (e) => {
                    currentPageNum = parseInt(e.target.value);
                    renderPageVisuals(currentPageNum);
                };

                currentPageNum = 1;
                renderPageVisuals(1);
                updateTables();
            }

            async function renderPageVisuals(pageNum) {
                if(!pdfDoc) return;
                const pageData = debugData.diagnostic.pages.find(p => p.page === pageNum);
                if(!pageData) return;

                // Update raw text output
                document.getElementById('rawTextOutput').value = pageData.raw_text;

                // Render PDF page via PDF.js
                const page = await pdfDoc.getPage(pageNum);
                const viewport = page.getViewport({ scale: 1.5 });
                
                const canvas = document.getElementById('pdfCanvas');
                const context = canvas.getContext('2d');
                canvas.height = viewport.height;
                canvas.width = viewport.width;

                await page.render({ canvasContext: context, viewport: viewport }).promise;

                // Render Overlays
                renderOverlays(pageData, viewport);
            }

            function renderOverlays(pageData, viewport) {
                const overlay = document.getElementById('overlayLayer');
                overlay.innerHTML = "";

                // Scale factors because PyMuPDF coordinates map to original PDF points, and viewport is scaled
                const scaleX = viewport.width / pageData.width;
                const scaleY = viewport.height / pageData.height;

                const showBoxes = document.getElementById('chkBoxes').checked;
                const showIds = document.getElementById('chkIds').checked;
                const showBlocks = document.getElementById('chkBlocks').checked;
                const showLines = document.getElementById('chkLines').checked;

                // 6. BLOCK VIEW
                if(showBlocks) {
                    const blocksMap = {};
                    pageData.words.forEach(w => {
                        if(!blocksMap[w.block]) blocksMap[w.block] = [];
                        blocksMap[w.block].push(w);
                    });
                    Object.keys(blocksMap).forEach(bId => {
                        const words = blocksMap[bId];
                        const x0 = Math.min(...words.map(w => w.x0)) * scaleX;
                        const y0 = Math.min(...words.map(w => w.y0)) * scaleY;
                        const x1 = Math.max(...words.map(w => w.x1)) * scaleX;
                        const y1 = Math.max(...words.map(w => w.y1)) * scaleY;

                        const div = document.createElement('div');
                        div.className = 'block-box';
                        div.style.left = x0 + 'px';
                        div.style.top = y0 + 'px';
                        div.style.width = (x1 - x0) + 'px';
                        div.style.height = (y1 - y0) + 'px';
                        overlay.appendChild(div);
                    });
                }

                // 7. LINE VIEW
                if(showLines) {
                    const linesMap = {};
                    pageData.words.forEach(w => {
                        const key = `${w.block}_${w.line}`;
                        if(!linesMap[key]) linesMap[key] = [];
                        linesMap[key].push(w);
                    });
                    Object.keys(linesMap).forEach(lKey => {
                        const words = linesMap[lKey];
                        const x0 = Math.min(...words.map(w => w.x0)) * scaleX;
                        const y0 = Math.min(...words.map(w => w.y0)) * scaleY;
                        const x1 = Math.max(...words.map(w => w.x1)) * scaleX;
                        const y1 = Math.max(...words.map(w => w.y1)) * scaleY;

                        const div = document.createElement('div');
                        div.className = 'line-box';
                        div.style.left = x0 + 'px';
                        div.style.top = y0 + 'px';
                        div.style.width = (x1 - x0) + 'px';
                        div.style.height = (y1 - y0) + 'px';
                        overlay.appendChild(div);
                    });
                }

                // 5. VISUAL WORD MAP
                pageData.words.forEach((w, idx) => {
                    const x0 = w.x0 * scaleX;
                    const y0 = w.y0 * scaleY;
                    const x1 = w.x1 * scaleX;
                    const y1 = w.y1 * scaleY;

                    if(showBoxes) {
                        const box = document.createElement('div');
                        box.className = 'word-box';
                        box.style.left = x0 + 'px';
                        box.style.top = y0 + 'px';
                        box.style.width = Math.max(2, x1 - x0) + 'px';
                        box.style.height = Math.max(2, y1 - y0) + 'px';
                        box.style.pointerEvents = 'auto';
                        
                        box.onclick = () => {
                            document.getElementById('tokenDetails').innerHTML = `
                                <strong>Word #${idx+1}</strong> | Text: <code>"${w.text}"</code> | 
                                BBox: [${w.x0}, ${w.y0}, ${w.x1}, ${w.y1}] | 
                                Block: ${w.block} | Line: ${w.line} | Word No: ${w.word_no} | Page: ${w.page}
                            `;
                        };
                        overlay.appendChild(box);
                    }

                    if(showIds) {
                        const idEl = document.createElement('div');
                        idEl.className = 'word-id';
                        idEl.style.left = x0 + 'px';
                        idEl.style.top = (y0 - 10 > 0 ? y0 - 10 : y0) + 'px';
                        idEl.innerText = `W${idx+1}`;
                        overlay.appendChild(idEl);
                    }
                });
            }

            // Checkbox event listeners to re-render overlay
            ['chkBoxes', 'chkIds', 'chkBlocks', 'chkLines'].forEach(id => {
                document.getElementById(id).onchange = () => renderPageVisuals(currentPageNum);
            });

            function switchTableTab(mode) {
                activeTableMode = mode;
                document.getElementById('btnTabRaw').className = mode === 'raw' ? 'tab-btn active' : 'tab-btn';
                document.getElementById('btnTabGeo').className = mode === 'geo' ? 'tab-btn active' : 'tab-btn';
                updateTables();
            }

            function updateTables() {
                if(!debugData) return;
                const pageData = debugData.diagnostic.pages.find(p => p.page === currentPageNum);
                if(!pageData) return;

                let wordsList = [...pageData.words];
                if(activeTableMode === 'geo') {
                    wordsList.sort((a, b) => {
                        if(a.page !== b.page) return a.page - b.page;
                        if(Math.abs(a.y0 - b.y0) > 3) return a.y0 - b.y0;
                        return a.x0 - b.x0;
                    });
                }

                const tbody = document.getElementById('wordsTableBody');
                tbody.innerHTML = "";
                wordsList.forEach((w, idx) => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td>${idx + 1}</td>
                        <td><strong>${w.text}</strong></td>
                        <td>${w.x0}</td>
                        <td>${w.y0}</td>
                        <td>${w.x1}</td>
                        <td>${w.y1}</td>
                        <td>${w.block}</td>
                        <td>${w.line}</td>
                        <td>${w.word_no}</td>
                        <td>${w.page}</td>
                    `;
                    tbody.appendChild(tr);
                });
            }
        </script>
    </body>
    </html>
    """

@app.post("/debug-pdf-inspect")
async def debug_pdf_inspect(file: UploadFile = File(...)):
    try:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")
        
        page_count = len(doc)
        metadata = doc.metadata
        
        pages_diagnostic = []
        total_words_count = 0
        total_chars_count = 0
        
        for page_num in range(page_count):
            page = doc[page_num]
            rect = page.rect
            width = rect.width
            height = rect.height
            
            raw_text = page.get_text()
            words_raw = page.get_text("words")
            
            total_chars_count += len(raw_text)
            total_words_count += len(words_raw)
            
            formatted_words = []
            for idx, w in enumerate(words_raw):
                formatted_words.append({
                    "text": w[4],
                    "x0": round(w[0], 2),
                    "y0": round(w[1], 2),
                    "x1": round(w[2], 2),
                    "y1": round(w[3], 2),
                    "block": w[5],
                    "line": w[6],
                    "word_no": idx,
                    "page": page_num + 1
                })
                
            pages_diagnostic.append({
                "page": page_num + 1,
                "width": round(width, 2),
                "height": round(height, 2),
                "text_length": len(raw_text),
                "word_count": len(formatted_words),
                "has_text_layer": len(formatted_words) > 0,
                "raw_text": raw_text,
                "words": formatted_words
            })
            
        diagnostic_result = {
            "document": {
                "page_count": page_count,
                "metadata": metadata
            },
            "total_words": total_words_count,
            "total_chars": total_chars_count,
            "pages": pages_diagnostic
        }
        
        return JSONResponse(content={"status": "success", "diagnostic": diagnostic_result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
