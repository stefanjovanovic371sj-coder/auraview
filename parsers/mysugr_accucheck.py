import fitz  # PyMuPDF
import re
from datetime import datetime
from typing import Optional, Dict, Any, List


class UniversalCGMParser:
    """
    Namenski parser za mySugr / Accu-Chek AGP izveštaje zasnovan na zoniranju stranice.
    Podržava: Engleski, Srpski (latinica) i Srpski (ćirilica).
    """

    FORMAT_NAME = "mysugr_accu_chek_agp"

    def parse(self, pdf_bytes: bytes) -> Dict[str, Any]:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            raise ValueError(f"Neuspešno otvaranje PDF-a: {e}")

        first_page = doc[0]
        page_width = first_page.rect.width
        page_height = first_page.rect.height

        # ZONIRANJE STRANICE:
        # Leva polovina (opsezi) i Desna gornja polovina (metrike)
        rect_ranges = fitz.Rect(0, 0, page_width * 0.55, page_height * 0.55)
        rect_metrics = fitz.Rect(page_width * 0.45, 0, page_width, page_height * 0.55)

        text_ranges = self._clean_extracted_text(first_page.get_text("text", clip=rect_ranges))
        text_metrics = self._clean_extracted_text(first_page.get_text("text", clip=rect_metrics))
        full_text = self._clean_extracted_text(first_page.get_text("text"))

        doc.close()

        # 1. Ekstrakcija opsega (Leva zona)
        actuals = self._extract_ranges(text_ranges)

        # 2. Ekstrakcija metrika (Desna zona)
        metrics = self._extract_metrics(text_metrics)
        actuals.update(metrics)

        # 3. Izvođenje izvedenih vrednosti (TBR, TIR, TAR)
        derived = self._derive_metrics(actuals)

        # 4. Period izveštaja
        reporting_period = self._extract_reporting_period(full_text)

        # Provera nedostajućih podataka
        required_keys = ["VERY_LOW", "LOW", "IN_RANGE", "HIGH", "VERY_HIGH", "GMI", "CV", "ACTIVE_TIME", "AVG_GLUCOSE"]
        missing = [k for k in required_keys if actuals.get(k) is None]

        status = "SUCCESS" if len(missing) == 0 else "MANUAL_REVIEW"

        return {
            "status": status,
            "parser": self.FORMAT_NAME,
            "reporting_period": reporting_period,
            "actual_components": actuals,
            "derived_metrics": derived,
            "validation_warnings": [],
            "review_reasons": [f"Nedostaje {m}." for m in missing]
        }

    # =========================================================
    # POMOĆNE METODE ZA OBRADU TEKSTA
    # =========================================================

    def _clean_extracted_text(self, text: str) -> str:
        text = text.lower().replace(",", ".")
        text = text.replace("–", "-").replace("—", "-").replace("−", "-")
        return re.sub(r"\s+", " ", text).strip()

    def _to_float(self, val_str: str) -> Optional[float]:
        if not val_str:
            return None
        clean = re.sub(r"[<>≤≥\s]", "", val_str)
        try:
            return float(clean)
        except (ValueError, TypeError):
            return None

    # =========================================================
    # EKSTRAKCIJA OPSEGA (LEVA STRANA)
    # =========================================================

    def _extract_ranges(self, text: str) -> Dict[str, Optional[float]]:
        # Uklanjamo objašnjenja i ciljeve
        clean = re.sub(r"(?:each 5%|svako povecanje|свако повећање).*?\d+%", "", text)

        patterns = {
            "VERY_HIGH": r"([<>≤≥]?\s*\d+(?:\.\d+)?)\s*%\s*(?:very high|веома високо|veoma visoko|veoma visok|vrlo visoko)",
            "HIGH": r"([<>≤≥]?\s*\d+(?:\.\d+)?)\s*%\s*(?<!very )(?<!veoma )(?<!веома )(?<!vrlo )(?:high|високо|visoko|visok)",
            "IN_RANGE": r"([<>≤≥]?\s*\d+(?:\.\d+)?)\s*%\s*(?:in range|u opsegu|у опсегу|normalno)",
            "LOW": r"([<>≤≥]?\s*\d+(?:\.\d+)?)\s*%\s*(?<!very )(?<!veoma )(?<!веома )(?<!vrlo )(?:low|nisko|ниско|nizak)",
            "VERY_LOW": r"([<>≤≥]?\s*\d+(?:\.\d+)?)\s*%\s*(?:very low|vrlo nisko|веома ниско|veoma nizak|veoma nisko|vrlo nizak)"
        }

        res: Dict[str, Optional[float]] = {}
        for key, pat in patterns.items():
            m = re.search(pat, clean)
            res[key] = self._to_float(m.group(1)) if m else None

        return res

    # =========================================================
    # EKSTRAKCIJA METRIKA (DESNA STRANA)
    # =========================================================

    def _extract_metrics(self, text: str) -> Dict[str, Optional[float]]:
        # Uklanjamo referentne ciljeve (Goal / Target) da ne kupe njihove vrednosti
        clean = re.sub(r"(?:goal|target|cilj|reference)\s*[:<>=]?\s*[<>]?\s*\d+(?:\.\d+)?\s*(?:%|mmol/l|mg/dl)?", "", text)

        res: Dict[str, Optional[float]] = {
            "ACTIVE_TIME": None,
            "AVG_GLUCOSE": None,
            "GMI": None,
            "CV": None
        }

        # 1. Aktivno vreme senzora
        m_act = re.search(r"(?:time cgm active|cgm active|active time|aktivno vreme).*?([<>]?\s*\d+(?:\.\d+)?)\s*%", clean)
        if m_act:
            res["ACTIVE_TIME"] = self._to_float(m_act.group(1))

        # 2. Prosečna glukoza
        m_avg = re.search(r"(?:average glucose|mean glucose|prosečna glukoza|prosecna glukoza|mbg).*?([<>]?\s*\d+(?:\.\d+)?)\s*(?:mmol/l|mg/dl)", clean)
        if m_avg:
            val = self._to_float(m_avg.group(1))
            if "mg/dl" in m_avg.group(0):
                val = round(val / 18.0, 2) if val else None
            res["AVG_GLUCOSE"] = val

        # 3. GMI
        m_gmi = re.search(r"(?:glucose management indicator|gmi|indikator upravljanja).*?([<>]?\s*\d+(?:\.\d+)?)\s*%", clean)
        if m_gmi:
            res["GMI"] = self._to_float(m_gmi.group(1))

        # 4. CV (Koeficijent varijacije)
        m_cv = re.search(r"(?:glucose variability|coefficient of variation|varijabilnost|variability|koeficijent varijacije|\bcv\b).*?([<>]?\s*\d+(?:\.\d+)?)\s*%", clean)
        if m_cv:
            res["CV"] = self._to_float(m_cv.group(1))

        return res

    # =========================================================
    # DERIVACIJA I DATUMI
    # =========================================================

    def _derive_metrics(self, actuals: Dict[str, Any]) -> Dict[str, Optional[float]]:
        vl, low = actuals.get("VERY_LOW"), actuals.get("LOW")
        h, vh = actuals.get("HIGH"), actuals.get("VERY_HIGH")

        tbr = round(vl + low, 2) if (vl is not None and low is not None) else None
        tar = round(h + vh, 2) if (h is not None and vh is not None) else None
        tir = actuals.get("IN_RANGE")

        return {"TBR": tbr, "TIR": tir, "TAR": tar}

    def _extract_reporting_period(self, text: str) -> Dict[str, Optional[str]]:
        # Ignorišemo datum štampanja
        clean = re.sub(r"(?:printing date|print date|datum štampanja|datum stampanja).*?\d{1,2}\s+[a-zа-ш]+\s+\d{4}", "", text)

        months = (
            r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
            r"maj|avg|okt|nov|"
            r"јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец"
        )
        pattern = rf"\b(\d{{1,2}}\s+(?:{months})\.?\s+\d{{4}})\b"
        found = re.findall(pattern, clean, re.IGNORECASE)

        dates = []
        for d in found:
            if d not in dates:
                dates.append(d)

        if len(dates) >= 2:
            return {
                "start": self._format_date(dates[0]),
                "end": self._format_date(dates[1])
            }

        return {"start": None, "end": None}

    def _format_date(self, date_str: str) -> Optional[str]:
        month_map = {
            "jan": "jan", "feb": "feb", "mar": "mar", "apr": "apr", "may": "may", "jun": "jun",
            "jul": "jul", "aug": "aug", "sep": "sep", "oct": "oct", "nov": "nov", "dec": "dec",
            "maj": "may", "avg": "aug", "okt": "oct",
            "јан": "jan", "феб": "feb", "мар": "mar", "апр": "apr", "мај": "may", "јун": "jun",
            "јул": "jul", "авг": "aug", "сеп": "sep", "окт": "oct", "нов": "nov", "дец": "dec"
        }
        clean = date_str.replace(".", "").strip().lower()
        parts = clean.split()
        if len(parts) != 3:
            return None

        day, month, year = parts
        month = month_map.get(month, month)
        normalized = f"{day} {month} {year}"

        try:
            return datetime.strptime(normalized, "%d %b %Y").strftime("%Y-%m-%d")
        except ValueError:
            return None


if __name__ == "__main__":
    print("UniversalCGMParser sa zonskom ekstrakcijom je spreman.")
