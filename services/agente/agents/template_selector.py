def classify_procedure(text: str):
    t = (text or "").lower()
    is_electronic = ("comprasmx" in t) or ("compranet" in t) or ("proposiciones electrónicas" in t) or ("proposiciones electronicas" in t) or ("plataforma" in t and "electr" in t)
    is_municipal = ("ayuntamiento" in t) or ("alcalde municipal" in t) or ("casa de la cultura" in t)
    tipo_proc = "electrónico" if is_electronic else ("presencial" if is_municipal else "mixto")
    if tipo_proc == "electrónico":
        tipo_entidad = "federal_electronica"
    elif tipo_proc == "presencial":
        tipo_entidad = "local_presencial"
    else:
        tipo_entidad = "mixta"
    return {"tipo_procedimiento": tipo_proc, "tipo_entidad": tipo_entidad}

