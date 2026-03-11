import os
import json
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from dotenv import load_dotenv
from datetime import datetime
import sys
load_dotenv()

# Config
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

creds = Credentials.from_service_account_file(os.getenv('GOOGLE_SHEETS_KEY'), scopes=SCOPES)
client = gspread.authorize(creds)
DETAILS_GC = client.open_by_key(os.getenv('DETAILS_SHEET_ID')).sheet1
COSTS_GC = client.open_by_key(os.getenv('COSTS_SHEET_ID')).sheet1
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

import json
import re

def safe_json_parse(text):
    """Clean + parse Gemini JSON responses."""
    if not text:
        return {"error": "empty_response"}
    
    # 1. Strip whitespace + newlines
    cleaned = text.strip()
    
    # 2. Remove markdown wrappers (if any)
    cleaned = re.sub(r'```json\s*|\s*```', '', cleaned, flags=re.IGNORECASE).strip()
    
    # 3. Fix common Gemini issues: extra braces, unescaped chars
    cleaned = re.sub(r'([\{\}\[\]])', r' \1 ', cleaned)  # Space around braces
    cleaned = cleaned.replace('\\n', '').replace('\\t', '')  # Remove literal escapes
    
    # 4. Parse with error handling
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"JSON Error: {e}")
        print(f"Raw (first 100): {repr(text[:100])}")
        print(f"Cleaned: {repr(cleaned[:100])}")
        return {"error": "parse_failed", "raw": cleaned}

def llm_extract(message):
    """LLM extracts structured data from message using Gemini."""
    prompt = """
    Analyze this WhatsApp message for a sales lead. The service name may be mentioned along with a number, use your best intelligence to judge if it is the quantity of the service sold. If yes, populate the "Quantity" field, otherwise default to 1
    Respond ONLY with valid JSON in the following format (no extra text, no explanations, no unnecessary special characters):
    {"Service": "task or ''", "Quantity": "number or '1'", "Date": "number or ''", "Time": "number or ''", "Guest": "name or ''", "Room": "name or ''", "Asignee": "name or ''", "Amount": number or 0, "confidence": "high/medium/low"}
    Message: """ + message

    response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents={'text': prompt},
    config={
        'temperature': 0,
        'response_mime_type': 'application/json'
        # 'top_p': 0.95,
        # 'top_k': 20,
    },
    )   
    try:
        extracted = safe_json_parse(response.text)
        client.close()
        return extracted
    except json.JSONDecodeError:
        # Fallback: retry with stricter instruction
        prompt += "\nCRITICAL: Pure JSON, no extra text."
        response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents={'text': prompt},
        config={
        'temperature': 0,
        'response_mime_type': 'application/json'
        # 'top_p': 0.95,
        # 'top_k': 20,
        },
        ) 
        client.close()
        return {"error": "Parse failed", "raw": response}

def update_costs(service, Quantity):
    """Read costs sheet, find matching cost, log."""
    costs_data = COSTS_GC.get_all_records()  # Headers: Task, BaseCost, PerUnit
    for row in costs_data:
        if row['Service'].lower() in service.lower():
            base_cost = float(row['Cost'])
            per_unit = float(Quantity)
            total_cost = per_unit * base_cost
            return total_cost
    return 0

def process_message(message):
    extracted = llm_extract(message)
    # extracted = {"Service": "airport to riad", "Quantity": 1, "Date": "04/03/2026", "Time": "12:00", "Guest": "Roli", "Room": "The Noida Room", "Asignee": "Mohammad", "Amount":  0, "confidence": "high"}
    if 'error' in extracted:
        print("Extraction failed:", extracted)
        return
    
    timestamp = datetime.now().isoformat()
    cost = update_costs(extracted['Service'], extracted['Quantity'])
    
    # Append to details (cols: Timestamp, Customer, Phone, Service, Amount, Confidence, Cost)

    DETAILS_GC.append_row([
        extracted['Service'], extracted['Quantity'],
        extracted['Date'], extracted['Time'], extracted['Guest'], extracted['Room'], extracted['Asignee'], cost
    ])
    print(f"Logged: {extracted} | Cost: Rs{cost}")

# Test
if __name__ == "__main__":
    test_msg1 = "Service: I sold 1 Transfer from Airport to Riad\nDate : 02/03/2026 \nGuest:2px \nTime:3:00pm \nRoom:The Casablanca Room \nMohammad Rizwan"
    test_msg2 = "Service: 2 Hammame\nDate : 04/03/2026 \nGuest:2px \nTime:6:00pm \nRoom:The Sahara Room \nArjun Rampal"
    process_message(test_msg2)

