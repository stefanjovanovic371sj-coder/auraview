class ParserNotImplementedError(Exception):
    pass

def get_parser_for_device(device_choice: str):
    # Provereni i aktivni parser za Accu-Chek SmartGuide i mySugr
    if device_choice in ["accu_check_mysugr", "accu_check", "mysugr"]:
        from parsers.mysugr_accucheck import UniversalCGMParser
        return UniversalCGMParser()
    
    # Lista uredjaja koji su u pripremi
    unsupported = {
        "dexcom": "Dexcom G6 / G7 / ONE",
        "freestyle_libre": "Abbott FreeStyle Libre 1 / 2 / 3",
        "medtronic": "Medtronic Guardian / Simplera",
        "medtrum": "Medtrum TouchCare / Nano",
        "anytime": "Anytime CGM"
    }
    
    if device_choice in unsupported:
        raise ParserNotImplementedError(
            f"Parser za {unsupported[device_choice]} je trenutno u fazi izrade."
        )
        
    # Podrazumevani univerzalni algoritam
    from parsers.mysugr_accucheck import UniversalCGMParser
    return UniversalCGMParser()
