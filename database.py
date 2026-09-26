import requests
from typing import List, Dict, Any

SUPABASE_URL = "https://tuhgurlibsaqqxrmhgdr.supabase.co"
SUPABASE_KEY = "sb_publishable_Dgu75wMHYMifHkuVTGgmpg_-czgDx39"

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

def save_report_to_db(report_data: dict) -> bool:
    """Upisuje obradjeni CGM izvestaj u bazu podataka."""
    try:
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/cgm_reports", 
            headers=SUPABASE_HEADERS, 
            json=report_data, 
            timeout=10
        )
        print("STATUS BAZE:", response.status_code)
        print("ODGOVOR BAZE:", response.text)
        return response.status_code in (200, 201)
    except Exception as e:
        print("GRESKA PRI UPISU U BAZU:", str(e))
        return False

def fetch_reports_from_db() -> List[Dict[str, Any]]:
    """Dohvata izvestaje hronoloski sortirane za timeline prikaz."""
    try:
        # Primarno sortira po kraju medicinskog perioda (najnoviji prvi), sekundarno po vremenu upisa
        url = f"{SUPABASE_URL}/rest/v1/cgm_reports?select=*&order=reporting_period_end.desc.nullslast,created_at.desc"
        res = requests.get(
            url, 
            headers=SUPABASE_HEADERS, 
            timeout=10
        )
        if res.status_code == 200:
            return res.json()
        print(f"GRESKA REST API ({res.status_code}):", res.text)
        return []
    except Exception as e:
        print("GRESKA PRI CITANJU IZ BAZE:", str(e))
        return []
