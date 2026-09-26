import fitz  # PyMuPDF
import re
from datetime import datetime
from typing import Optional, Dict, Any, List

class UniversalCGMParser:
    """
    Čist parser dizajniran isključivo za mySugr / Accu-Chek AGP izveštaje.
    Podržava: Engleski, Srpski (Latinica), Srpski (Ćirilica).
    """
    
    def parse(self, pdf_bytes: bytes) -> Dict[str, Any]:
        # Čita PDF tako da vizuelno rekonsturiše redove (sprečava lomljenje kolona)
        raw_text = self._extract_clean_text(pdf_bytes)
        
        actuals = {
            "VERY_LOW": None, "LOW": None, "IN_RANGE": None, "HIGH": None, "VERY_HIGH": None,
            "GMI": None, "CV": None, "ACTIVE_TIME": None, "AVG_GLUCOSE": None
        }

        # ==========================================
        # OPSEZI (Procenat stoji ISPRED naziva, npr: "6% Low" ili "3% Веома ниско")
        # ==========================================
        
        m = re.search(r"([<>]?\s*\d+(?:\.\d+)?)\s*%\s*(?:very high|веома високо|veoma visoko|veoma visok|vrlo visoko)", raw_text)
        if m: actuals["VERY_HIGH"] = self._to_float(m.group(1))

        # Negativni lookbehind (?<!...) osigurava da ne nađe "high" unutar "very high"
        m = re.search(r"([<>]?\s*\d+(?:\.\d+)?)\s*%\s*(?<!very )(?<!veoma )(?<!веома )(?<!vrlo )(?:high|високо|visoko|visok)", raw_text)
        if m: actuals["HIGH"] = self._to_float(m.group(1))

        m = re.search(r"([<>]?\s*\d+(?:\.\d+)?)\s*%\s*(?:in range|u opsegu|у опсегу|normalno)", raw_text)
        if m: actuals["IN_RANGE"] = self._to_float(m.group(1))

        m = re.search(r"([<>]?\s*\d+(?:\.\d+)?)\s*%\s*(?:very low|vrlo nisko|веома ниско|veoma nizak|veoma nisko|vrlo nizak)", raw_text)
        if m: actuals["VERY_LOW"] = self._to_float(m.group(1))

        m = re.search(r"([<>]?\s*\d+(?:\.\d+)?)\s*%\s*(?<!very )(?<!veoma )(?<!веома )(?<!vrlo )(?:low|nisko|ниско|nizak)", raw_text)
        if m: actuals["LOW"] = self._to_float(m.group(1))


        # ==========================================
        # METRIKE (Procenat/Vrednost stoji IZA naziva, npr: "CV 21.2%")
        # Koristimo .{0,60}? da bi tražio strogo u blizini reči, a ne na drugom kraju strane
        # ==========================================
        
        m = re.search(r"(?:gmi|glucose management indicator|indikator upravljanja).{0,60}?([<>]?\s*\d+(?:\.\d+)?)\s*%", raw_text)
        if m: actuals["GMI"] = self._to_float(m.group(1))

        m = re.search(r"(?:cv|varijabilnost|variability|coefficient of variation|koeficijent|gv).{0,60}?([<>]?\s*\d+(?:\.\d+)?)\s*%", raw_text)
        if m: actuals["CV"] = self._to_float(m.group(1))

        m = re.search(r"(?:active time|aktivno vreme|sensor active|cgm active|time cgm|aktivno).{0,60}?([<>]?\s*\d+(?:\.\d+)?)\s*%", raw_text)
        if m: actuals["ACTIVE_TIME"] = self._to_float(m.group(1))

        m = re.search(r"(?:average glucose|prosečna glukoza|mean glucose|mbg|просечна глукоза|просечна вредност).{0,60}?([<>]?\s*\d+(?:\.\d+)?)\s*(?:mmol/l|mg/dl)", raw_text)
        if m: actuals["AVG_GLUCOSE"] = self._to_float(m.group(1))


        # ==========================================
        # FINALIZACIJA
        # ==========================================
        
        derived = self._derive_metrics(actuals)
        period = self._extract_period(raw_text)
        reasons = [k for k, v in actuals.items() if v is None]

        return {
            "status": "SUCCESS" if not reasons else "MANUAL_REVIEW",
            "reporting_period": period,
            "actual_components": actuals,
            "derived_metrics": derived,
            "validation_warnings": [],
            "review_reasons": [f"Nedostaje {r}" for r in reasons]
        }

    def _extract_clean_text(self, pdf_bytes: bytes) -> str:
        """Čita PDF uz očuvanje horizontalnih redova kako bi vrednosti ostale uz svoje nazive."""
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            raise ValueError(f"Neuspešno otvaranje PDF-a: {e}")

        lines = []
        for page in doc:
            words = page.get_text("words")
            # Sortiramo prvo po Y (visini), pa po X (širini)
            words.sort(key=lambda w: (w[1], w[0]))
            
            current_line = []
            current_y = None
            
            for w in words:
                y_center = (w[1] + w[3]) / 2
                if current_y is None:
                    current_line.append(w)
                    current_y = y_center
                elif abs(y_center - current_y) < 10.0:  # Spaja reči koje su u istoj vizuelnoj liniji
                    current_line.append(w)
                    current_y = sum((x[1] + x[3]) / 2 for x in current_line) / len(current_line)
                else:
                    current_line.sort(key=lambda x: x[0])
                    lines.append(" ".join(x[4] for x in current_line))
                    current_line = [w]
                    current_y = y_center
            if current_line:
                current_line.sort(key=lambda x: x[0])
                lines.append(" ".join(x[4] for x in current_line))
        doc.close()

        # Spajamo u jedan tekst i prebacujemo u mala slova
        text = " ".join(lines).lower().replace(",", ".")

        # HIRURŠKO BRISANJE "SMEĆA": Brišemo ciljeve i uputstva da ne uđu kao lažni rezultati
        text = re.sub(r"(?:target|cilj|goal|reference).*?\d+(?:\.\d+)?\s*%", "", text)
        text = re.sub(r"(?:svako povecanje|свако повећање|each 5%).*?\d+(?:\.\d+)?\s*%", "", text)
        
        return text

    def _to_float(self, val_str: str) -> Optional[float]:
        if not val_str: return None
        # Brišemo <, > i praznine
        clean = re.sub(r"[<>≤≥\s]", "", val_str)
        try: return float(clean)
        except: return None

    def _derive_metrics(self, actuals: Dict[str, Any]) -> Dict[str, Any]:
        vl, l = actuals.get("VERY_LOW"), actuals.get("LOW")
        h, vh = actuals.get("HIGH"), actuals.get("VERY_HIGH")
        
        tbr = round(vl + l, 2) if (vl is not None and l is not None) else None
        tar = round(h + vh, 2) if (h is not None and vh is not None) else None
        tir = actuals.get("IN_RANGE")
        
        return {"TBR": tbr, "TIR": tir, "TAR": tar}

    def _extract_period(self, text: str) -> Dict[str, Any]:
        """Pronalazi prvi i drugi datum u tekstu, ignorišući datum štampanja."""
        text = re.sub(r"(?:print|štampan|stampan|generisan).*?\d{1,2}\s+[a-zа-ш]+\s+\d{4}", "", text)
        
        months = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|maj|avg|okt|јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец"
        matches = re.findall(rf"(\d{{1,2}}\s+(?:{months})\.?\s+\d{{4}})", text, re.IGNORECASE)
        
        unique_dates = []
        for m in matches:
            if m not in unique_dates:
                unique_dates.append(m)
                
        if len(unique_dates) >= 2:
            return {
                "start": self._format_date(unique_dates[0]),
                "end": self._format_date(unique_dates[1])
            }
        return {"start": None, "end": None}

    def _format_date(self, date_str: str) -> str:
        srb_to_eng = {
            "јан": "Jan", "feb": "Feb", "мар": "Mar", "апр": "Apr", "мај": "May", "јун": "Jun",
            "јул": "Jul", "авг": "Aug", "сеп": "Sep", "окт": "Oct", "нов": "Nov", "дец": "Dec",
            "jan": "Jan", "mar": "Mar", "apr": "Apr", "maj": "May", "jun": "Jun",
            "jul": "Jul", "avg": "Aug", "sep": "Sep", "okt": "Oct", "nov": "Nov", "dec": "Dec"
        }
        clean = date_str.replace(".", "").strip().lower()
        for srb, eng in srb_to_eng.items():
            if srb in clean:
                clean = clean.replace(srb, eng.lower())
                break
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try: return datetime.strptime(clean, fmt.lower()).strftime("%d %b %Y")
            except ValueError: continue
        return date_str
