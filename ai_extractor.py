# ai_extractor.py
import os
import json
import base64
import fitz  # PyMuPDF
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Initialize the new official Google Client
# It automatically finds GEMINI_API_KEY from your .env file!
client = genai.Client()

def get_image_bytes_from_file(file_bytes: bytes) -> bytes:
    """Converts PDF first page to a clean PNG image."""
    if file_bytes[:4] == b'%PDF':
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        return pix.tobytes("png")
    return file_bytes

def extract_with_google_gemini(image_bytes: bytes) -> dict:
    """Uses the new Google Interactions API to extract JSON."""
    
    # Convert bytes to base64 string
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    
    prompt = """Extract the vendor name, total amount, and date from this receipt. 
    Return ONLY valid JSON in this exact format, no other text:
    {"vendor": "...", "total_amount": "...", "date": "..."}"""

    # Using the exact syntax from the Google Docs you provided
    interaction = client.interactions.create(
        model="gemini-2.5-flash",  # Fast, free, and excellent at vision
        input=[
            {"type": "text", "text": prompt},
            {
                "type": "image",
                "data": b64_image,
                "mime_type": "image/png"
            }
        ]
    )
    
    # The SDK gives us the text directly via .output_text!
    result_text = interaction.output_text
    
    # Clean up markdown code blocks just in case
    if "```json" in result_text:
        result_text = result_text.split("```json")[1].split("```")[0]
    elif "```" in result_text:
        result_text = result_text.split("```")[1].split("```")[0]
        
    return json.loads(result_text)

def run_ai_extraction(supabase_url: str) -> dict:
    """Main pipeline"""
    import requests
    
    # 1. Download the file from Supabase
    response = requests.get(supabase_url)
    file_bytes = response.content
    
    # 2. Convert to image if it's a PDF
    image_bytes = get_image_bytes_from_file(file_bytes)
    
    # 3. Send to Google
    print("🧠 Sending image to Google Gemini 2.5 Flash...")
    try:
        result = extract_with_google_gemini(image_bytes)
        print("✅ Google Gemini extraction succeeded!")
        return {"data": result, "source": "google_gemini_2.5_flash", "confidence": 0.98}
    except Exception as e:
        print(f"❌ Google Gemini failed: {e}")
        raise Exception("Extraction failed")