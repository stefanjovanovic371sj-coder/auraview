import fitz  # PyMuPDF
import re
from datetime import datetime
from typing import Optional, Dict, Any, List

def safe_float(val_str: str) -> Optional[float]:
    if not val_str: return None
    # Uklanjamo operatore (<, >) da bismo dobili čist broj
    clean = re.sub(r"[<>≤≥\s]", "", val_str).replace(",", ".")
    try:
        return float(clean)
    except:
        return None

def parse_date_flexible(date_str: str) -> Optional[str]:
    # Normalizacija na engleski za lakše parsiranje
    srb_to_eng = {
        "јан": "Jan", "feb": "Feb", "мар": "Mar", "апр": "Apr", "мај": "May", "јун": "Jun",
        "јул": "Jul", "авг": "Aug", "сеп": "Sep", "окт": "Oct", "нов": "Nov", "дец": "Dec",
        "jan": "Jan", "mar": "Mar", "apr": "Apr", "maj": "May", "jun": "Jun",
        "jul": "Jul", "avg": "Aug", "sep": "Sep", "okt": "Oct", "nov": "Nov", "dec": "Dec"
    }
    
    clean_str = date_str.replace(".", "").strip().lower()
    for srb, eng in srb_to_eng.items():
        if srb in clean_str:
            clean_str = clean_str.replace(srb, eng.lower())
            break
            
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            dt = datetime.strptime(clean_str, fmt.lower())
            return dt.strftime("%d %b %Y")
        except ValueError:
            continue
    return date_str

class UniversalCGMParser:
    
    def parse(self, pdf_bytes: bytes) -> Dict[str, Any]:
        lines = self._extract_lines_from_pdf(pdf_bytes)
        
        actuals = {
            "VERY_LOW": None, "LOW": None, "IN_RANGE": None, "HIGH": None, "VERY_HIGH": None,
            "GMI": None, "CV": None, "ACTIVE_TIME": None, "AVG_GLUCOSE": None
        }

        for line in lines:
            # 1. Preskačemo informativne linije i datume štampanja (sprečava greške u podacima)
            if any(ignore in line for ignore in ["target", "cilj", "each 5%", "svako povecanje", "свако повећање", "goal", "1% of time", "print", "štamp", "stamp", "generisano"]):
                continue

            # ==========================================
            # OPSEZI (Striktno gleda broj TIK ISPRED naziva, dozvoljava < i >)
            # ==========================================
            if actuals["VERY_HIGH"] is None and any(w in line for w in ["very high", "веома високо", "veoma visoko", "veoma visok", "vrlo visoko"]):
                m = re.search(r"([<>]?\s*\d+(?:\.\d+)?)\s*%\s*(?:very high|веома високо|veoma visoko|veoma visok|vrlo visoko)", line)
                if m: actuals["VERY_HIGH"] = safe_float(m.group(1))
                
            if actuals["HIGH"] is None and any(w in line for w in ["high", "високо", "visoko", "visok"]):
                # Negativni lookbehind sprečava da pomeša HIGH sa VERY HIGH
                m = re.search(r"([<>]?\s*\d+(?:\.\d+)?)\s*%\s*(?<!very )(?<!veoma )(?<!веома )(?<!vrlo )(?:high|високо|visoko|visok)", line)
                if m: actuals["HIGH"] = safe_float(m.group(1))

            if actuals["VERY_LOW"] is None and any(w in line for w in ["very low", "vrlo nisko", "веома ниско", "veoma nizak", "veoma nisko"]):
                m = re.search(r"([<>]?\s*\d+(?:\.\d+)?)\s*%\s*(?:very low|vrlo nisko|веома ниско|veoma nizak|veoma nisko)", line)
                if m: actuals["VERY_LOW"] = safe_float(m.group(1))

            if actuals["LOW"] is None and any(w in line for w in ["low", "nisko", "ниско", "nizak"]):
                m = re.search(r"([<>]?\s*\d+(?:\.\d+)?)\s*%\s*(?<!very )(?<!veoma )(?<!веома )(?<!vrlo )(?:low|nisko|ниско|nizak)", line)
                if m: actuals["LOW"] = safe_float(m.group(1))

            if actuals["IN_RANGE"] is None and any(w in line for w in ["in range", "u opsegu", "у опсегу", "normalno"]):
                m = re.search(r"([<>]?\s*\d+(?:\.\d+)?)\s*%\s*(?:in range|u opsegu|у опсегу|normalno)", line)
                if m: actuals["IN_RANGE"] = safe_float(m.group(1))

            # ==========================================
            # STANDARDNE METRIKE
            # (Uzimamo UVEK poslednji broj u redu da izbegnemo ciljeve poput "Target < 36%")
            # ==========================================
            if actuals["GMI"] is None and any(w in line for w in ["gmi", "glucose management", "indikator upravljanja"]):
                matches = re.findall(r"([<>]?\s*\d+(?:\.\d+)?)\s*%", line)
                if matches: actuals["GMI"] = safe_float(matches[-1])

            if actuals["CV"] is None and any(w in line for w in ["cv", "varijabilnost", "variability", "coefficient", "koeficijent", "gv"]):
                matches = re.findall(r"([<>]?\s*\d+(?:\.\d+)?)\s*%", line)
                if matches: actuals["CV"] = safe_float(matches[-1])

            if actuals["ACTIVE_TIME"] is None and any(w in line for w in ["active time", "aktivno vreme", "sensor active", "cgm active", "coverage", "time cgm"]):
                matches = re.findall(r"([<>]?\s*\d+(?:\.\d+)?)\s*%", line)
                if matches: actuals["ACTIVE_TIME"] = safe_float(matches[-1])

            if actuals["AVG_GLUCOSE"] is None and any(w in line for w in ["average glucose", "prosečna", "mean glucose", "mbg", "просечна"]):
                matches = re.findall(r"([<>]?\s*\d+(?:\.\d+)?)\s*(?:mmol/l|mg/dl)", line)
                if matches: actuals["AVG_GLUCOSE"] = safe_float(matches[-1])

        derived = self._derive_metrics(actuals)
        period = self._extract_period(lines)
        reasons = [k for k, v in actuals.items() if v is None]

        return {
            "status": "SUCCESS" if not reasons else "MANUAL_REVIEW",
            "reporting_period": period,
            "actual_components": actuals,
            "derived_metrics": derived,
            "validation_warnings": [],
            "review_reasons": [f"Missing {r}" for r in reasons]
        }

    def _extract_lines_from_pdf(self, pdf_bytes: bytes) -> List[str]:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            raise ValueError(f"Neuspešno otvaranje PDF-a: {e}")

        lines = []
        for page in doc:
            words = page.get_text("words")
            # Prvo sortiramo po visini, pa po horizontali
            words.sort(key=lambda w: (w[1], w[0]))
            
            current_line = []
            current_y = None
            
            # Povećana tolerancija na 12px da bez greške spoji CV i procente u isti red
            for w in words:
                y_center = (w[1] + w[3]) / 2
                if current_y is None:
                    current_line.append(w)
                    current_y = y_center
                elif abs(y_center - current_y) < 12.0:  
                    current_line.append(w)
                    current_y = sum((x[1] + x[3]) / 2 for x in current_line) / len(current_line)
                else:
                    current_line.sort(key=lambda x: x[0])
                    text = " ".join(x[4] for x in current_line).lower().replace(",", ".")
                    lines.append(text)
                    current_line = [w]
                    current_y = y_center
                    
            if current_line:
                current_line.sort(key=lambda x: x[0])
                lines.append(" ".join(x[4] for x in current_line).lower().replace(",", "."))
                
        doc.close()
        return lines

    def _derive_metrics(self, actuals: Dict[str, Any]) -> Dict[str, Any]:
        vl, l = actuals.get("VERY_LOW"), actuals.get("LOW")
        h, vh = actuals.get("HIGH"), actuals.get("VERY_HIGH")
        
        tbr = round(vl + l, 2) if (vl is not None and l is not None) else None
        tar = round(h + vh, 2) if (h is not None and vh is not None) else None
        tir = actuals.get("IN_RANGE")
        
        return {"TBR": tbr, "TIR": tir, "TAR": tar}

    def _extract_period(self, lines: List[str]) -> Dict[str, Any]:
        # Čistimo datume kada je PDF samo odštampan/generisan
        safe_lines = [l for l in lines if not any(x in l for x in ["print", "štamp", "stamp", "generisano"])]
        text = " ".join(safe_lines)
        months = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|maj|avg|okt|јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец"
        matches = re.findall(rf"(\d{{1,2}}\s+(?:{months})\.?\s+\d{{4}})", text, re.IGNORECASE)
        
        unique_dates = []
        for m in matches:
            if m not in unique_dates:
                unique_dates.append(m)
                
        if len(unique_dates) >= 2:
            return {
                "start": parse_date_flexible(unique_dates[0]),
                "end": parse_date_flexible(unique_dates[-1])
            }
        return {"start": None, "end": None}
