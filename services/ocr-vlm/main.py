import os
import io
import torch
import json
import logging
import tempfile
import shutil
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import StreamingResponse
from PIL import Image
from transformers import AutoProcessor, AutoModelForImageTextToText
from pypdf import PdfReader
from pdf2image import convert_from_path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ocr-vlm-service")

app = FastAPI(title="LicitAI GLM-OCR Local Service")

# Model configuration
MODEL_ID = "zai-org/GLM-OCR"
device = "cuda" if torch.cuda.is_available() else "cpu"

logger.info(f"Loading processor and model {MODEL_ID} on {device}...")
processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
model = AutoModelForImageTextToText.from_pretrained(
    MODEL_ID, 
    torch_dtype=torch.bfloat16, 
    trust_remote_code=True,
    device_map="auto"
).eval()
logger.info("Model loaded successfully.")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "ocr-vlm", "device": device}

@app.post("/ocr/process")
async def process_vlm(file: UploadFile = File(...)):
    logger.info(f"VLM Processing request: {file.filename}")
    temp_dir = tempfile.mkdtemp()
    temp_pdf_path = os.path.join(temp_dir, "input.pdf")
    
    async def generate_vlm_progress():
        try:
            results = []
            if file.content_type == "application/pdf" or file.filename.lower().endswith(".pdf"):
                with open(temp_pdf_path, "wb") as tmp:
                    content = await file.read()
                    tmp.write(content)
                
                yield json.dumps({"status": "info", "msg": "Archivo recibido. Iniciando análisis híbrido VLM..."}) + "\n"
                
                reader = PdfReader(temp_pdf_path)
                num_pages = len(reader.pages)
                
                for i in range(num_pages):
                    page = reader.pages[i]
                    # Attempt digital extraction first (Bypass)
                    digital_text = page.extract_text() or ""
                    
                    if len(digital_text.strip()) > 50:
                        yield json.dumps({"status": "info", "msg": f"Página {i+1}: Texto digital nativo detectado."}) + "\n"
                        results.append({"page": i + 1, "text": digital_text})
                    else:
                        # Fallback to GLM-OCR
                        yield json.dumps({"status": "progress", "val": int((i+1)/num_pages*100), "msg": f"Analizando Página {i+1} con GLM-OCR..."}) + "\n"
                        
                        # Convert specific page to image
                        images = convert_from_path(temp_pdf_path, first_page=i+1, last_page=i+1, dpi=130)
                        if images:
                            img = images[0]
                            # Inference with GLM-OCR (using processors for compatibility)
                            messages = [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "image"},
                                        {"type": "text", "text": "Analyze this document page and extract all text, tables and structure."}
                                    ]
                                }
                            ]
                            prompt = processor.apply_chat_template(
                                messages, 
                                tokenize=False, 
                                add_generation_prompt=True
                            )
                            inputs = processor(text=[prompt], images=[img], return_tensors="pt").to(device)
                            
                            generated_ids = model.generate(**inputs, max_new_tokens=4096)
                            response = processor.decode(generated_ids[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
                            results.append({"page": i + 1, "text": response})
                        else:
                            results.append({"page": i + 1, "text": "[Error: No se pudo convertir página a imagen]"})
            else:
                # Direct image processing
                content = await file.read()
                img = Image.open(io.BytesIO(content))
                yield json.dumps({"status": "progress", "val": 50, "msg": "Analizando imagen con GLM-OCR..."}) + "\n"
                with torch.no_grad():
                    messages = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image"},
                                {"type": "text", "text": "Extract all text and information from this image."}
                            ]
                        }
                    ]
                    prompt = processor.apply_chat_template(
                        messages, 
                        tokenize=False, 
                        add_generation_prompt=True
                    )
                    inputs = processor(text=[prompt], images=[img], return_tensors="pt").to(device)
                    
                    generated_ids = model.generate(**inputs, max_new_tokens=4096)
                    response = processor.decode(generated_ids[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
                    results.append({"page": 1, "text": response})
                yield json.dumps({"status": "progress", "val": 100, "msg": "Completado."}) + "\n"

            yield json.dumps({"status": "complete", "data": results}) + "\n"
        except Exception as e:
            logger.error(f"VLM Error: {e}")
            yield json.dumps({"status": "error", "msg": str(e)}) + "\n"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return StreamingResponse(generate_vlm_progress(), media_type="application/x-ndjson")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8082)
