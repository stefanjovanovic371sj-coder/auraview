import fitz  # PyMuPDF
import re
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple


class UniversalCGMParser:
    """
    Robust parser za mySugr / Accu-Chek AGP izveštaje.

    PRINCIP:
    - Parser je NAMENSKI za mySugr / Accu-Chek AGP format.
    - Ne pokušava da bude univerzalni parser za sve proizvođače.
    - Goal/Target vrednosti se nikada ne tretiraju kao actual vrednosti.
    - Actual glucose ranges se čitaju kao strukturisana grupa.
    - Metrike se traže u lokalnom kontekstu oko njihovog naziva.
    - Ako parser nije siguran -> MANUAL_REVIEW.
    - Nikada ne izmišlja vrednost.

    Standardni izlaz:
        VERY_LOW
        LOW
        IN_RANGE
        HIGH
        VERY_HIGH
        TBR
        TIR
        TAR
        GMI
        CV
        ACTIVE_TIME
        AVG_GLUCOSE
        REPORTING_PERIOD
    """

    FORMAT_NAME = "mysugr_accu_chek_agp"

    # ---------------------------------------------------------
    # GLAVNI PARSER
    # ---------------------------------------------------------

    def parse(self, pdf_bytes: bytes) -> Dict[str, Any]:

        lines = self._extract_lines(pdf_bytes)

        if not lines:
            return self._manual_review(
                reason="PDF nema čitljiv tekst."
            )

        normalized_lines = [
            self._normalize_text(line["text"])
            for line in lines
        ]

        # -----------------------------------------------------
        # 1. IDENTIFIKACIJA REPORTA
        # -----------------------------------------------------

        report_check = self._check_report_identity(normalized_lines)

        if not report_check["recognized"]:
            return self._manual_review(
                reason="PDF ne izgleda kao prepoznat mySugr / Accu-Chek AGP izveštaj.",
                extra={
                    "format_detected": False
                }
            )

        # -----------------------------------------------------
        # 2. ACTUAL GLUCOSE RANGES
        # -----------------------------------------------------

        ranges, range_warnings = self._extract_actual_ranges(
            normalized_lines
        )

        # -----------------------------------------------------
        # 3. METRIKE
        # -----------------------------------------------------

        metrics, metric_warnings = self._extract_metrics(
            normalized_lines
        )

        # -----------------------------------------------------
        # 4. REPORTING PERIOD
        # -----------------------------------------------------

        reporting_period, period_warning = self._extract_reporting_period(
            normalized_lines
        )

        # -----------------------------------------------------
        # 5. VALIDACIJA
        # -----------------------------------------------------

        actuals = {
            **ranges,
            **metrics
        }

        validation_warnings = []

        validation_warnings.extend(range_warnings)
        validation_warnings.extend(metric_warnings)

        if period_warning:
            validation_warnings.append(period_warning)

        validation_warnings.extend(
            self._validate_ranges(actuals)
        )

        validation_warnings.extend(
            self._validate_metrics(actuals)
        )

        # -----------------------------------------------------
        # 6. DERIVACIJA
        # -----------------------------------------------------

        derived = self._derive_metrics(actuals)

        # -----------------------------------------------------
        # 7. REVIEW REASONS
        # -----------------------------------------------------

        review_reasons = self._build_review_reasons(
            actuals=actuals,
            reporting_period=reporting_period,
            validation_warnings=validation_warnings
        )

        status = (
            "SUCCESS"
            if len(review_reasons) == 0
            else "MANUAL_REVIEW"
        )

        return {
            "status": status,
            "parser": self.FORMAT_NAME,

            "reporting_period": reporting_period,

            "actual_components": {
                "VERY_LOW": actuals.get("VERY_LOW"),
                "LOW": actuals.get("LOW"),
                "IN_RANGE": actuals.get("IN_RANGE"),
                "HIGH": actuals.get("HIGH"),
                "VERY_HIGH": actuals.get("VERY_HIGH"),

                "GMI": actuals.get("GMI"),
                "CV": actuals.get("CV"),
                "ACTIVE_TIME": actuals.get("ACTIVE_TIME"),
                "AVG_GLUCOSE": actuals.get("AVG_GLUCOSE")
            },

            "derived_metrics": derived,

            "validation_warnings": validation_warnings,

            "review_reasons": review_reasons
        }

    # =========================================================
    # PDF -> LINIJE
    # =========================================================

    def _extract_lines(self, pdf_bytes: bytes) -> List[Dict[str, Any]]:

        try:
            doc = fitz.open(
                stream=pdf_bytes,
                filetype="pdf"
            )
        except Exception as e:
            raise ValueError(
                f"Neuspešno otvaranje PDF-a: {e}"
            )

        all_lines = []

        for page_number, page in enumerate(doc):

            words = page.get_text("words")

            if not words:
                continue

            # word:
            # x0, y0, x1, y1, text, block_no, line_no, word_no

            words.sort(
                key=lambda w: (
                    w[1],   # y
                    w[0]    # x
                )
            )

            current_words = []
            current_y = None

            for word in words:

                x0, y0, x1, y1, text = word[:5]

                center_y = (y0 + y1) / 2

                if current_y is None:
                    current_words = [word]
                    current_y = center_y
                    continue

                # Adaptivni tolerance za isti vizuelni red.
                tolerance = max(
                    3.0,
                    min(
                        10.0,
                        abs(y1 - y0) * 0.55
                    )
                )

                if abs(center_y - current_y) <= tolerance:

                    current_words.append(word)

                    current_y = (
                        sum(
                            (w[1] + w[3]) / 2
                            for w in current_words
                        )
                        / len(current_words)
                    )

                else:

                    line = self._make_line(
                        current_words,
                        page_number
                    )

                    if line:
                        all_lines.append(line)

                    current_words = [word]
                    current_y = center_y

            if current_words:

                line = self._make_line(
                    current_words,
                    page_number
                )

                if line:
                    all_lines.append(line)

        doc.close()

        # Globalno sortiranje po stranici i vertikalnoj poziciji.
        all_lines.sort(
            key=lambda x: (
                x["page"],
                x["y"]
            )
        )

        return all_lines

    def _make_line(
        self,
        words: List[Tuple],
        page_number: int
    ) -> Optional[Dict[str, Any]]:

        if not words:
            return None

        words = sorted(
            words,
            key=lambda w: w[0]
        )

        text = " ".join(
            str(w[4]).strip()
            for w in words
            if str(w[4]).strip()
        )

        if not text:
            return None

        return {
            "page": page_number,
            "x0": min(w[0] for w in words),
            "y": min(w[1] for w in words),
            "x1": max(w[2] for w in words),
            "y1": max(w[3] for w in words),
            "text": text
        }

    # =========================================================
    # NORMALIZACIJA
    # =========================================================

    def _normalize_text(self, text: str) -> str:

        text = text.lower()

        # Decimalni zarez -> tačka
        text = text.replace(",", ".")

        # Razni Unicode crtica karakteri
        text = (
            text
            .replace("–", "-")
            .replace("—", "-")
            .replace("−", "-")
        )

        # Višestruki whitespace
        text = re.sub(
            r"\s+",
            " ",
            text
        ).strip()

        return text

    # =========================================================
    # IDENTIFIKACIJA
    # =========================================================

    def _check_report_identity(
        self,
        lines: List[str]
    ) -> Dict[str, Any]:

        text = " ".join(lines)

        signals = 0

        if "ambulatory glucose profile" in text:
            signals += 1

        if "agp" in text:
            signals += 1

        if "time in ranges" in text:
            signals += 1

        if "glucose metrics" in text:
            signals += 1

        if "glucose management indicator" in text:
            signals += 1

        if "glucose variability" in text:
            signals += 1

        # Za ovaj format zahtevamo minimum dva jaka signala.
        recognized = signals >= 2

        return {
            "recognized": recognized,
            "signals": signals
        }

    # =========================================================
    # ACTUAL RANGE EXTRACTION
    # =========================================================

    def _extract_actual_ranges(
        self,
        lines: List[str]
    ) -> Tuple[Dict[str, Optional[float]], List[str]]:

        result = {
            "VERY_LOW": None,
            "LOW": None,
            "IN_RANGE": None,
            "HIGH": None,
            "VERY_HIGH": None
        }

        warnings = []

        # -----------------------------------------------------
        # Prvo tražimo strukturu:
        #
        # 2% Very low
        # 5% Low
        # 93% In range
        # 0% High
        # 0% Very high
        #
        # -----------------------------------------------------

        label_patterns = {
            "VERY_LOW": [
                r"\bvery low\b",
                r"\bvrlo nisko\b",
                r"\bveoma nisko\b",
                r"\bveoma nizak\b",
                r"\bvrlo nizak\b"
            ],

            "LOW": [
                r"\blow\b",
                r"\bnisko\b",
                r"\bnizak\b"
            ],

            "IN_RANGE": [
                r"\bin range\b",
                r"\bu opsegu\b",
                r"\bnormalno\b"
            ],

            "HIGH": [
                r"\bhigh\b",
                r"\bvisoko\b",
                r"\bvisok\b"
            ],

            "VERY_HIGH": [
                r"\bvery high\b",
                r"\bveoma visoko\b",
                r"\bveoma visok\b",
                r"\bvrlo visoko\b"
            ]
        }

        # -----------------------------------------------------
        # Pokušaj 1:
        # Vrednost i label u ISTOJ liniji.
        # -----------------------------------------------------

        for line in lines:

            # Goal linije ne smeju u actual.
            if self._is_goal_line(line):
                continue

            for key, patterns in label_patterns.items():

                for pattern in patterns:

                    regex = (
                        r"([<>≤≥]?\s*\d+(?:\.\d+)?)"
                        r"\s*%"
                        r"\s*"
                        + pattern
                    )

                    match = re.search(
                        regex,
                        line,
                        re.IGNORECASE
                    )

                    if match:

                        value = self._to_float(
                            match.group(1)
                        )

                        if value is not None:

                            if result[key] is None:
                                result[key] = value

                            elif result[key] != value:
                                warnings.append(
                                    f"Konfliktna vrednost za {key}: "
                                    f"{result[key]} i {value}."
                                )

                            break

        # -----------------------------------------------------
        # Pokušaj 2:
        # Ako PDF razbije procenat i label u posebne linije,
        # tražimo lokalni blok.
        # -----------------------------------------------------

        missing_keys = [
            key
            for key, value in result.items()
            if value is None
        ]

        if missing_keys:

            for i, line in enumerate(lines):

                if self._is_goal_line(line):
                    continue

                for key in list(missing_keys):

                    if result[key] is not None:
                        continue

                    patterns = label_patterns[key]

                    if not any(
                        re.search(
                            p,
                            line,
                            re.IGNORECASE
                        )
                        for p in patterns
                    ):
                        continue

                    # Proveri samo neposredni lokalni kontekst.
                    nearby = lines[
                        max(0, i - 2):
                        min(len(lines), i + 3)
                    ]

                    candidates = []

                    for candidate_line in nearby:

                        if self._is_goal_line(
                            candidate_line
                        ):
                            continue

                        matches = re.findall(
                            r"([<>≤≥]?\s*\d+(?:\.\d+)?)\s*%",
                            candidate_line
                        )

                        for raw in matches:

                            value = self._to_float(raw)

                            if value is not None:
                                candidates.append(value)

                    candidates = list(
                        dict.fromkeys(candidates)
                    )

                    if len(candidates) == 1:

                        result[key] = candidates[0]

                    elif len(candidates) > 1:

                        warnings.append(
                            f"{key}: više mogućih vrednosti "
                            f"u lokalnom kontekstu."
                        )

        return result, warnings

    # =========================================================
    # METRIKE
    # =========================================================

    def _extract_metrics(
        self,
        lines: List[str]
    ) -> Tuple[Dict[str, Optional[float]], List[str]]:

        result = {
            "GMI": None,
            "CV": None,
            "ACTIVE_TIME": None,
            "AVG_GLUCOSE": None
        }

        warnings = []

        # -----------------------------------------------------
        # GMI
        # -----------------------------------------------------

        result["GMI"], warning = self._extract_metric_after_label(
            lines,
            labels=[
                "glucose management indicator",
                "gmi",
                "indikator upravljanja"
            ],
            unit="percent",
            key="GMI"
        )

        if warning:
            warnings.append(warning)

        # -----------------------------------------------------
        # CV
        # -----------------------------------------------------

        result["CV"], warning = self._extract_metric_after_label(
            lines,
            labels=[
                "glucose variability",
                "coefficient of variation",
                "variability",
                "varijabilnost",
                "koeficijent varijacije",
                "cv"
            ],
            unit="percent",
            key="CV"
        )

        if warning:
            warnings.append(warning)

        # -----------------------------------------------------
        # ACTIVE TIME
        # -----------------------------------------------------

        result["ACTIVE_TIME"], warning = self._extract_metric_after_label(
            lines,
            labels=[
                "time cgm active",
                "time cgm",
                "cgm active",
                "time sensor active",
                "sensor active",
                "active time",
                "aktivno vreme"
            ],
            unit="percent",
            key="ACTIVE_TIME"
        )

        if warning:
            warnings.append(warning)

        # -----------------------------------------------------
        # AVERAGE GLUCOSE
        # -----------------------------------------------------

        result["AVG_GLUCOSE"], warning = self._extract_average_glucose(
            lines
        )

        if warning:
            warnings.append(warning)

        return result, warnings

    # =========================================================
    # GENERIČKI METRIC HELPER
    # =========================================================

    def _extract_metric_after_label(
        self,
        lines: List[str],
        labels: List[str],
        unit: str,
        key: str
    ) -> Tuple[Optional[float], Optional[str]]:

        label_indices = []

        for i, line in enumerate(lines):

            if self._is_goal_line(line):
                continue

            if any(
                self._contains_label(line, label)
                for label in labels
            ):
                label_indices.append(i)

        if not label_indices:
            return (
                None,
                f"Nedostaje {key}."
            )

        candidates = []

        for index in label_indices:

            # Nikada ne gledamo 150 karaktera kroz ceo dokument.
            # Gledamo samo narednih nekoliko fizičkih linija.
            nearby = lines[
                index:
                min(len(lines), index + 5)
            ]

            for j, line in enumerate(nearby):

                if self._is_goal_line(line):
                    continue

                # GMI/CV/Active Time -> %
                if unit == "percent":

                    matches = re.findall(
                        r"([<>≤≥]?\s*\d+(?:\.\d+)?)\s*%",
                        line
                    )

                    for raw in matches:

                        value = self._to_float(raw)

                        if value is not None:
                            candidates.append(
                                (index + j, value)
                            )

        # Ukloni duplikate po vrednosti.
        unique_values = []

        for _, value in candidates:

            if value not in unique_values:
                unique_values.append(value)

        if len(unique_values) == 1:
            return unique_values[0], None

        if len(unique_values) == 0:
            return (
                None,
                f"{key}: naziv pronađen, ali vrednost nije pouzdano pronađena."
            )

        return (
            None,
            f"{key}: pronađeno više mogućih vrednosti {unique_values}."
        )

    # =========================================================
    # AVERAGE GLUCOSE
    # =========================================================

    def _extract_average_glucose(
        self,
        lines: List[str]
    ) -> Tuple[Optional[float], Optional[str]]:

        labels = [
            "average glucose",
            "mean glucose",
            "prosečna glukoza",
            "prosecna glukoza",
            "mbg"
        ]

        candidates = []

        for i, line in enumerate(lines):

            if self._is_goal_line(line):
                continue

            if not any(
                self._contains_label(line, label)
                for label in labels
            ):
                continue

            nearby = lines[
                i:
                min(len(lines), i + 5)
            ]

            for j, candidate_line in enumerate(nearby):

                if self._is_goal_line(candidate_line):
                    continue

                # Tražimo broj koji je eksplicitno vezan
                # za mmol/L ili mg/dL.
                matches = re.findall(
                    r"([<>≤≥]?\s*\d+(?:\.\d+)?)"
                    r"\s*(mmol\s*/?\s*l|mg\s*/?\s*dl)",
                    candidate_line,
                    re.IGNORECASE
                )

                for raw_value, unit in matches:

                    value = self._to_float(raw_value)

                    if value is None:
                        continue

                    unit_normalized = (
                        unit.lower()
                        .replace(" ", "")
                    )

                    # Standardni output platforme je mmol/L.
                    if unit_normalized in (
                        "mg/dl",
                        "mgdl"
                    ):
                        value = value / 18.0

                    candidates.append(
                        round(value, 2)
                    )

        candidates = list(
            dict.fromkeys(candidates)
        )

        if len(candidates) == 1:
            return candidates[0], None

        if len(candidates) == 0:
            return (
                None,
                "AVG_GLUCOSE: vrednost nije pouzdano pronađena."
            )

        return (
            None,
            f"AVG_GLUCOSE: više mogućih vrednosti {candidates}."
        )

    # =========================================================
    # GOAL / TARGET DETECTION
    # =========================================================

    def _is_goal_line(self, line: str) -> bool:

        goal_terms = [
            "goal:",
            "goal ",
            "target:",
            "target ",
            "cilj:",
            "cilj ",
            "reference:",
            "reference ",
            "recommended target"
        ]

        return any(
            term in line
            for term in goal_terms
        )

    # =========================================================
    # LABEL MATCH
    # =========================================================

    def _contains_label(
        self,
        line: str,
        label: str
    ) -> bool:

        label = self._normalize_text(label)

        if label == "cv":
            return bool(
                re.search(
                    r"\bcv\b",
                    line
                )
            )

        if label == "mbg":
            return bool(
                re.search(
                    r"\bmbg\b",
                    line
                )
            )

        return label in line

    # =========================================================
    # REPORTING PERIOD
    # =========================================================

    def _extract_reporting_period(
        self,
        lines: List[str]
    ) -> Tuple[Dict[str, Any], Optional[str]]:

        # Prvo tražimo baš liniju koja govori
        # Reporting period.
        for i, line in enumerate(lines):

            if (
                "reporting period" not in line
                and "period izveštaja" not in line
                and "period izvestaja" not in line
            ):
                continue

            # Prvo sama linija
            period = self._parse_two_dates(line)

            if period["start"] and period["end"]:
                return period, None

            # Onda naredne 2 linije.
            nearby = lines[
                i:
                min(len(lines), i + 3)
            ]

            combined = " ".join(nearby)

            period = self._parse_two_dates(
                combined
            )

            if period["start"] and period["end"]:
                return period, None

        # Fallback: tražimo 2 datuma,
        # ali IGNORIŠEMO Printing date.
        dates = []

        for line in lines:

            if (
                "printing date" in line
                or "print date" in line
                or "datum štampanja" in line
                or "datum stampanja" in line
            ):
                continue

            found = self._find_dates(line)

            for date in found:

                if date not in dates:
                    dates.append(date)

        if len(dates) >= 2:

            return {
                "start": self._format_date(dates[0]),
                "end": self._format_date(dates[1])
            }, None

        return (
            {
                "start": None,
                "end": None
            },
            "Reporting period nije pouzdano pronađen."
        )

    def _parse_two_dates(
        self,
        text: str
    ) -> Dict[str, Optional[str]]:

        dates = self._find_dates(text)

        if len(dates) >= 2:

            return {
                "start": self._format_date(dates[0]),
                "end": self._format_date(dates[1])
            }

        return {
            "start": None,
            "end": None
        }

    def _find_dates(
        self,
        text: str
    ) -> List[str]:

        months = (
            r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
            r"maj|avg|okt|nov|"
            r"јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец"
        )

        pattern = (
            rf"\b"
            rf"\d{{1,2}}"
            rf"\s+"
            rf"(?:{months})"
            rf"\.?"
            rf"\s+"
            rf"\d{{4}}"
            rf"\b"
        )

        return re.findall(
            pattern,
            text,
            re.IGNORECASE
        )

    # =========================================================
    # DATE FORMAT
    # =========================================================

    def _format_date(
        self,
        date_str: str
    ) -> Optional[str]:

        month_map = {

            "jan": "jan",
            "feb": "feb",
            "mar": "mar",
            "apr": "apr",
            "may": "may",
            "jun": "jun",
            "jul": "jul",
            "aug": "aug",
            "sep": "sep",
            "oct": "oct",
            "nov": "nov",
            "dec": "dec",

            "maj": "may",
            "avg": "aug",
            "okt": "oct",

            "јан": "jan",
            "феб": "feb",
            "мар": "mar",
            "апр": "apr",
            "мај": "may",
            "јун": "jun",
            "јул": "jul",
            "авг": "aug",
            "сеп": "sep",
            "окт": "oct",
            "нов": "nov",
            "дец": "dec"
        }

        clean = (
            date_str
            .replace(".", "")
            .strip()
            .lower()
        )

        parts = clean.split()

        if len(parts) != 3:
            return None

        day, month, year = parts

        month = month_map.get(
            month,
            month
        )

        normalized = (
            f"{day} {month} {year}"
        )

        try:

            date_obj = datetime.strptime(
                normalized,
                "%d %b %Y"
            )

            # ISO je mnogo bolji za bazu.
            return date_obj.strftime(
                "%Y-%m-%d"
            )

        except ValueError:

            return None

    # =========================================================
    # NUMBER
    # =========================================================

    def _to_float(
        self,
        value: str
    ) -> Optional[float]:

        if not value:
            return None

        clean = re.sub(
            r"[<>≤≥\s]",
            "",
            value
        )

        clean = clean.replace(
            ",",
            "."
        )

        try:
            return float(clean)
        except (ValueError, TypeError):
            return None

    # =========================================================
    # DERIVACIJA
    # =========================================================

    def _derive_metrics(
        self,
        actuals: Dict[str, Any]
    ) -> Dict[str, Optional[float]]:

        vl = actuals.get("VERY_LOW")
        low = actuals.get("LOW")
        in_range = actuals.get("IN_RANGE")
        high = actuals.get("HIGH")
        very_high = actuals.get("VERY_HIGH")

        tbr = None
        tir = None
        tar = None

        if vl is not None and low is not None:
            tbr = round(
                vl + low,
                2
            )

        if in_range is not None:
            tir = in_range

        if high is not None and very_high is not None:
            tar = round(
                high + very_high,
                2
            )

        return {
            "TBR": tbr,
            "TIR": tir,
            "TAR": tar
        }

    # =========================================================
    # VALIDACIJA RANGE-OVA
    # =========================================================

    def _validate_ranges(
        self,
        actuals: Dict[str, Any]
    ) -> List[str]:

        warnings = []

        keys = [
            "VERY_LOW",
            "LOW",
            "IN_RANGE",
            "HIGH",
            "VERY_HIGH"
        ]

        values = [
            actuals.get(key)
            for key in keys
        ]

        present = [
            value
            for value in values
            if value is not None
        ]

        # -----------------------------------------------------
        # Svaki procenat mora biti 0-100.
        # -----------------------------------------------------

        for key in keys:

            value = actuals.get(key)

            if value is None:
                continue

            if value < 0 or value > 100:

                warnings.append(
                    f"{key} ima nemoguć procenat: {value}%."
                )

        # -----------------------------------------------------
        # Ako su svi prisutni, zbir mora biti približno 100.
        # -----------------------------------------------------

        if len(present) == 5:

            total = round(
                sum(present),
                2
            )

            if abs(total - 100) > 2:

                warnings.append(
                    f"Glucose range procenti daju zbir "
                    f"{total}%, očekivano približno 100%."
                )

        return warnings

    # =========================================================
    # VALIDACIJA METRIKA
    # =========================================================

    def _validate_metrics(
        self,
        actuals: Dict[str, Any]
    ) -> List[str]:

        warnings = []

        percent_metrics = [
            "GMI",
            "CV",
            "ACTIVE_TIME"
        ]

        for key in percent_metrics:

            value = actuals.get(key)

            if value is None:
                continue

            if value < 0 or value > 100:

                warnings.append(
                    f"{key} ima nemoguć procenat: {value}%."
                )

        avg = actuals.get(
            "AVG_GLUCOSE"
        )

        if avg is not None:

            if avg <= 0:

                warnings.append(
                    "AVG_GLUCOSE mora biti veći od 0."
                )

        return warnings

    # =========================================================
    # REVIEW REASONS
    # =========================================================

    def _build_review_reasons(
        self,
        actuals: Dict[str, Any],
        reporting_period: Dict[str, Any],
        validation_warnings: List[str]
    ) -> List[str]:

        reasons = []

        required = [
            "VERY_LOW",
            "LOW",
            "IN_RANGE",
            "HIGH",
            "VERY_HIGH",
            "GMI",
            "CV",
            "ACTIVE_TIME",
            "AVG_GLUCOSE"
        ]

        for key in required:

            if actuals.get(key) is None:

                reasons.append(
                    f"Nedostaje {key}."
                )

        if (
            not reporting_period.get("start")
            or not reporting_period.get("end")
        ):
            reasons.append(
                "Nedostaje reporting period."
            )

        # Svaki warning koji ukazuje na
        # moguću pogrešnu ekstrakciju -> review.
        for warning in validation_warnings:

            if warning not in reasons:
                reasons.append(
                    warning
                )

        return reasons

    # =========================================================
    # MANUAL REVIEW RESPONSE
    # =========================================================

    def _manual_review(
        self,
        reason: str,
        extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:

        result = {
            "status": "MANUAL_REVIEW",
            "parser": self.FORMAT_NAME,
            "reporting_period": {
                "start": None,
                "end": None
            },
            "actual_components": {
                "VERY_LOW": None,
                "LOW": None,
                "IN_RANGE": None,
                "HIGH": None,
                "VERY_HIGH": None,
                "GMI": None,
                "CV": None,
                "ACTIVE_TIME": None,
                "AVG_GLUCOSE": None
            },
            "derived_metrics": {
                "TBR": None,
                "TIR": None,
                "TAR": None
            },
            "validation_warnings": [],
            "review_reasons": [
                reason
            ]
        }

        if extra:
            result.update(extra)

        return result


# =============================================================
# TEST
# =============================================================

if __name__ == "__main__":

    print(
        "UniversalCGMParser je spreman."
    )

    print(
        "Koristi:"
    )

    print(
        "parser = UniversalCGMParser()"
    )

    print(
        "result = parser.parse(pdf_bytes)"
    )
