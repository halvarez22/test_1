import os
import io
import pytesseract
import logging
import tempfile
import shutil
import json
from fastapi import FastAPI, UploadFile, File, HTTPException
from pdf2image import convert_from_path
from PIL import Image
from fastapi.responses import StreamingResponse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ocr-service")

app = FastAPI(title="LicitAI OCR Service")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "ocr"}

@app.post("/ocr/process")
async def process_ocr(file: UploadFile = File(...)):
    logger.info(f"Processing request: {file.filename}")
    temp_dir = tempfile.mkdtemp()
    temp_pdf_path = os.path.join(temp_dir, "input.pdf")
    
    async def generate_progress():
        try:
            results = []
            if file.content_type == "application/pdf" or file.filename.lower().endswith(".pdf"):
                with open(temp_pdf_path, "wb") as tmp:
                    content = await file.read()
                    tmp.write(content)
                
                yield json.dumps({"status": "info", "msg": "Archivo recibido. Iniciando análisis..."}) + "\n"
                
                from pypdf import PdfReader
                reader = PdfReader(temp_pdf_path)
                num_pages = len(reader.pages)
                
                needs_ocr = False
                for i in range(num_pages):
                    page = reader.pages[i]
                    digital_text = page.extract_text() or ""
                    
                    if len(digital_text.strip()) > 50:
                        yield json.dumps({"status": "info", "msg": f"Página {i+1}: Texto digital detectado."}) + "\n"
                        results.append({"page": i + 1, "text": digital_text})
                    else:
                        needs_ocr = True
                        results.append({"page": i + 1, "text": None})

                if needs_ocr:
                    yield json.dumps({"status": "warning", "msg": "⚠️ OCR Necesario: Se detectaron imágenes escaneadas. Iniciando extracción visual. Esto puede tomar tiempo..."}) + "\n"
                    
                    output_folder = os.path.join(temp_dir, "pages")
                    os.makedirs(output_folder, exist_ok=True)
                    
                    # Optimized DPI for speed/quality balance
                    convert_from_path(temp_pdf_path, dpi=130, output_folder=output_folder, fmt="jpeg", thread_count=2)
                    page_files = sorted(os.listdir(output_folder))
                    
                    for i in range(num_pages):
                        if results[i]["text"] is None:
                            page_img_path = os.path.join(output_folder, page_files[i])
                            try:
                                with Image.open(page_img_path) as img:
                                    text = pytesseract.image_to_string(img, lang="spa")
                                    results[i]["text"] = text
                            except Exception as e:
                                results[i]["text"] = f"[Error OCR: {e}]"
                            
                            progress = int((i + 1) / num_pages * 100)
                            yield json.dumps({"status": "progress", "val": progress, "msg": f"Procesando página {i+1} de {num_pages}..."}) + "\n"
            else:
                content = await file.read()
                image = Image.open(io.BytesIO(content))
                text = pytesseract.image_to_string(image, lang="spa")
                results.append({"page": 1, "text": text})
                yield json.dumps({"status": "progress", "val": 100, "msg": "Imagen procesada."}) + "\n"

            yield json.dumps({"status": "complete", "data": results}) + "\n"
        except Exception as e:
            logger.error(f"Error: {e}")
            yield json.dumps({"status": "error", "msg": str(e)}) + "\n"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return StreamingResponse(generate_progress(), media_type="application/x-ndjson")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8082)
