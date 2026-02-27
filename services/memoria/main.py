import os
import sqlite3
import json
import glob
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
import logging

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="LicitAI Memoria DB")

# Habilitar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DB_PATH = os.getenv("DB_PATH", "/app/data/db/licitai.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        razon_social TEXT NOT NULL,
        rfc TEXT UNIQUE NOT NULL,
        representante TEXT,
        cargo TEXT,
        domicilio TEXT
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bids (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        convocante TEXT,
        numero_licitacion TEXT UNIQUE,
        objeto TEXT,
        presupuesto_estimado TEXT,
        fianzas_requeridas TEXT,
        certificaciones TEXT,
        fecha_apertura DATE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS workspaces (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        logo_path TEXT,
        cif_data TEXT, -- JSON string
        acta_data TEXT, -- JSON string
        prices_data TEXT, -- JSON string
        sources TEXT, -- JSON list of sources state
        analysis TEXT, -- JSON string of the latest analysis
        status TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    # Migraciones manuales
    try:
        cursor.execute("ALTER TABLE workspaces ADD COLUMN acta_data TEXT")
        logging.info("Añadida columna acta_data a la tabla workspaces.")
    except sqlite3.OperationalError:
        pass # La columna ya existe
        
    conn.commit()
    conn.close()

@app.on_event("startup")
async def startup_event():
    init_db()

class Company(BaseModel):
    razon_social: str
    rfc: str
    representante: Optional[str] = None
    cargo: Optional[str] = None
    domicilio: Optional[str] = None

class Bid(BaseModel):
    convocante: str
    numero_licitacion: str
    objeto: str
    presupuesto_estimado: Optional[str] = None
    fianzas_requeridas: Optional[str] = None
    certificaciones: Optional[str] = None
    fecha_apertura: Optional[str] = None

class Workspace(BaseModel):
    id: str
    name: str
    logo_path: Optional[str] = None
    cif_data: Optional[str] = None
    acta_data: Optional[str] = None
    prices_data: Optional[str] = None
    sources: Optional[str] = None
    analysis: Optional[str] = None
    status: Optional[str] = None

@app.post("/db/bids")
async def create_bid(bid: Bid):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO bids (convocante, numero_licitacion, objeto, presupuesto_estimado, 
               fianzas_requeridas, certificaciones, fecha_apertura) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (bid.convocante, bid.numero_licitacion, bid.objeto, bid.presupuesto_estimado, 
             bid.fianzas_requeridas, bid.certificaciones, bid.fecha_apertura)
        )
        conn.commit()
        bid_id = cursor.lastrowid
        conn.close()
        return {"id": bid_id, "status": "success"}
    except sqlite3.IntegrityError:
        # Update if already exists
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE bids SET convocante=?, objeto=?, presupuesto_estimado=?, 
               fianzas_requeridas=?, certificaciones=?, fecha_apertura=?
               WHERE numero_licitacion=?""",
            (bid.convocante, bid.objeto, bid.presupuesto_estimado, 
             bid.fianzas_requeridas, bid.certificaciones, bid.fecha_apertura, bid.numero_licitacion)
        )
        conn.commit()
        conn.close()
        return {"status": "updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/db/companies")
async def create_company(company: Company):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO companies (razon_social, rfc, representante, cargo, domicilio) VALUES (?, ?, ?, ?, ?)",
            (company.razon_social, company.rfc, company.representante, company.cargo, company.domicilio)
        )
        conn.commit()
        comp_id = cursor.lastrowid
        conn.close()
        return {"id": comp_id, "status": "success"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="RFC already exists")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/db/companies/{rfc}")
async def get_company(rfc: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM companies WHERE rfc = ?", (rfc,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Company not found")
        
    return {
        "id": row[0],
        "razon_social": row[1],
        "rfc": row[2],
        "representante": row[3],
        "cargo": row[4],
        "domicilio": row[5]
    }

@app.post("/db/workspaces")
async def create_workspace(ws: Workspace):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO workspaces (id, name, logo_path, cif_data, acta_data, prices_data, sources, analysis, status) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ws.id, ws.name, ws.logo_path, ws.cif_data, ws.acta_data, ws.prices_data, ws.sources, ws.analysis, ws.status)
        )
        conn.commit()
        conn.close()
        return {"status": "success"}
    except sqlite3.IntegrityError:
        # Update — solo actualizar campos que vienen llenos
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        import re
        safe_name = ws.name if (ws.name and not re.match(r'^\d{10,}$', ws.name.strip())) else None
        
        # Filtro de seguridad: si el JSON viene vacío o es "{}", lo tratamos como None para que COALESCE no borre lo que ya hay
        def filter_empty(val):
            if not val or val in ["{}", "[]", "null"]: return None
            return val

        cursor.execute(
            """UPDATE workspaces SET 
               name=COALESCE(?, name), 
               logo_path=COALESCE(?, logo_path), 
               cif_data=COALESCE(?, cif_data), 
               acta_data=COALESCE(?, acta_data), 
               prices_data=COALESCE(?, prices_data), 
               sources=COALESCE(?, sources),
               analysis=COALESCE(?, analysis),
               status=COALESCE(?, status) 
               WHERE id=?""",
            (safe_name, ws.logo_path, filter_empty(ws.cif_data), filter_empty(ws.acta_data), 
             filter_empty(ws.prices_data), filter_empty(ws.sources), filter_empty(ws.analysis), ws.status, ws.id)
        )
        conn.commit()
        conn.close()
        return {"status": "updated"}

@app.get("/db/workspaces")
async def list_workspaces():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Acceso por nombre de columna
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, logo_path, cif_data, acta_data, prices_data, sources, analysis, status, created_at FROM workspaces ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [{
        "id": r["id"],
        "name": r["name"],
        "logo_path": r["logo_path"],
        "cif_data": r["cif_data"],
        "acta_data": r["acta_data"],
        "prices_data": r["prices_data"],
        "sources": r["sources"],
        "analysis": r["analysis"],
        "status": r["status"],
        "date": r["created_at"]
    } for r in rows]

@app.delete("/db/workspaces/{ws_id}")
async def delete_workspace(ws_id: str):
    import shutil
    import logging
    logging.info(f"🚨 DELETE request received for: {ws_id}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM workspaces WHERE id = ?", (ws_id,))
    conn.commit()
    conn.close()
    
    # Intento de borrar archivos
    workspaces_dir = "/app/data/workspaces"
    target_dir = os.path.join(workspaces_dir, ws_id)
    if os.path.exists(target_dir):
        try:
            shutil.rmtree(target_dir)
            logging.info(f"✅ Folder deleted: {target_dir}")
        except: pass
            
    return {"status": "deleted"}

@app.get("/db/workspaces/{ws_id}")
async def get_workspace(ws_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,))
    row = cursor.fetchone()
    conn.close()
    if not row: raise HTTPException(status_code=404)
    return {
        "id": row["id"], 
        "name": row["name"], 
        "logo_path": row["logo_path"], 
        "cif_data": row["cif_data"], 
        "acta_data": row["acta_data"], 
        "prices_data": row["prices_data"], 
        "sources": row["sources"],
        "analysis": row["analysis"],
        "status": row["status"]
    }

@app.get("/db/stats")
async def get_stats():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM companies")
    companies_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM bids")
    bids_count = cursor.fetchone()[0]
    
    conn.close()
    
    # Placeholder para VRAM (integrar con monitor de GPU después)
    vram_usage = "0.0 GB" 
    
    return {
        "propuestas": bids_count,
        "anexos": bids_count * 5, # Estimación
        "vram": vram_usage
    }

@app.get("/db/activity")
async def get_activity():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Retornar las últimas 5 licitaciones analizadas
    cursor.execute("SELECT convocante, numero_licitacion FROM bids ORDER BY id DESC LIMIT 5")
    rows = cursor.fetchall()
    conn.close()
    
    activities = []
    for row in rows:
        activities.append({
            "time": "Reciente",
            "message": f"Análisis completado: <strong>{row[0]} - {row[1]}</strong>"
        })
    
    if not activities:
        activities = [{"time": "-", "message": "No hay actividad registrada aún."}]
        
    return activities

@app.post("/db/reindex")
async def reindex_workspaces():
    """
    Reconstruye la tabla workspaces a partir de las carpetas existentes en data/workspaces.
    No toca archivos físicos; solo rellena la DB con lo que ya existe.
    """
    base_dir = os.path.dirname(DB_PATH)
    root_dir = os.path.dirname(base_dir)
    candidates = [
        os.path.join(root_dir, "workspaces"),
        os.path.join(base_dir, "workspaces"),
        "/app/data/workspaces"
    ]
    WORKSPACES_DIR = next((p for p in candidates if os.path.isdir(p)), None)
    if not WORKSPACES_DIR:
        # Devolver 200 con inserted=0 para evitar bloquear el flujo
        return {"status": "ok", "inserted": 0, "detail": "No workspaces folder found"}
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Borrar tabla actual (opcional; podría hacer UPSERT también)
    cursor.execute("DELETE FROM workspaces")
    conn.commit()

    inserted = 0
    for ws_folder in glob.glob(os.path.join(WORKSPACES_DIR, "*/")):
        ws_id = os.path.basename(os.path.dirname(ws_folder))
        # Nombre por defecto: extraer de analysis.json o usar el ID
        name = ws_id
        logo_path = None
        cif_data = None
        analysis = None
        sources = None

        # Leer analysis.json si existe
        analysis_file = os.path.join(ws_folder, "analysis.json")
        if os.path.isfile(analysis_file):
            with open(analysis_file, encoding="utf-8") as f:
                try:
                    analysis_obj = json.load(f)
                    # Usar objeto como nombre legible
                    name = analysis_obj.get("objeto", ws_id)[:80]  # truncar si muy largo
                    analysis = json.dumps(analysis_obj, ensure_ascii=False)
                except Exception:
                    pass

        # Buscar logo (primer .png/.jpg en la carpeta)
        for ext in ("*.png", "*.jpg", "*.jpeg"):
            logo_candidates = glob.glob(os.path.join(ws_folder, ext))
            if logo_candidates:
                logo_path = logo_candidates[0]
                break

        # Cargar CIF si existe (JSON)
        cif_candidates = glob.glob(os.path.join(ws_folder, "*.json"))
        for cf in cif_candidates:
            if "analysis" not in cf and os.path.basename(cf) != "sources.json":
                with open(cf, encoding="utf-8") as f:
                    try:
                        cif_obj = json.load(f)
                        if "rfc" in cif_obj or "razon_social" in cif_obj:
                            cif_data = json.dumps(cif_obj, ensure_ascii=False)
                            break
                    except Exception:
                        continue

        # Listar PDFs como sources
        pdfs = glob.glob(os.path.join(ws_folder, "*.pdf"))
        if pdfs:
            sources = json.dumps([{"filename": os.path.basename(p), "uploaded": True} for p in pdfs], ensure_ascii=False)

        # Insertar en DB
        cursor.execute(
            """INSERT INTO workspaces (id, name, logo_path, cif_data, sources, analysis, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ws_id, name, logo_path, cif_data, sources, analysis, "ready")
        )
        inserted += 1

    conn.commit()
    conn.close()
    return {"status": "ok", "inserted": inserted}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "memoria-db"}

if __name__ == "__main__":
    init_db()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8083)
