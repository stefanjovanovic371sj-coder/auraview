from pydantic import BaseModel
from typing import Optional, Dict, Any

class CGMReportCreate(BaseModel):
    patient_id: str
    device_name: Optional[str] = "Accu-Check SmartGuide Predict"
    manufacturer: Optional[str] = "Accu-Check / Universal"
    tir: Optional[float] = None
    tbr: Optional[float] = None
    tar: Optional[float] = None
    gmi_percent: Optional[float] = None
    cv: Optional[float] = None
    active_time: Optional[str] = None
