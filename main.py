import logging
import os
import json
import gspread
from datetime import datetime

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from serpapi import GoogleSearch

# --- Configuration & App Initialization ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ScrapeRequest(BaseModel):
    query: str

def run_scraper(search_query: str):
    """
    Main function to run the scraper, designed as a background task.
    It fetches data from SerpApi and appends it to Google Sheets.
    """
    logging.info(f"SerpApi scraper started for query: '{search_query}'")
    try:
        # Load secrets from environment variables for secure deployment
        SHEET_NAME = os.environ["SHEET_NAME"]
        SERPAPI_KEY = os.environ["SERPAPI_KEY"]
        google_creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
        google_creds_dict = json.loads(google_creds_json)
    except KeyError as e:
        logging.error(f"FATAL: Missing Environment Variable: {e}. Please configure it in your hosting provider.")
        return

    # --- Google Sheets Integration ---
    try:
        gc = gspread.service_account_from_dict(google_creds_dict)
        spreadsheet = gc.open(SHEET_NAME)
        worksheet = spreadsheet.worksheet("Data")
        # Duplicate Detection: Fetch existing business names to avoid re-adding them
        existing_data = set(worksheet.col_values(1)[1:])
        logging.info(f"Connected to G-Sheet. Found {len(existing_data)} existing entries.")
    except Exception as e:
        logging.error(f"G-Sheets connection failed: {e}")
        return

    # --- SerpApi Scraping Logic ---
    scraped_data = []
    try:
        params = {
            "api_key": SERPAPI_KEY,
            "engine": "google_maps",
            "q": search_query,
            "ll": "@13.0827,80.2707,15z", # Latitude/Longitude for Chennai to improve accuracy
            "type": "search",
            "google_domain": "google.co.in",
            "hl": "en",
        }

        search = GoogleSearch(params)
        results = search.get_dict()
        
        local_results = results.get("local_results", [])
        logging.info(f"SerpApi returned {len(local_results)} results.")

        for result in local_results:
            name = result.get("title")
            # Skip if the name is missing or already exists in the sheet
            if not name or name in existing_data:
                continue

            # Data Validation & Extraction
            category = result.get("type", "N/A")
            address = result.get("address", "N/A")
            rating = result.get("rating", "N/A")
            reviews = result.get("reviews", 0)
            website = result.get("website", "N/A")
            phone = result.get("phone", "N/A")

            business_data = [
                name, category, address, str(rating), str(reviews),
                website, phone, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ]
            scraped_data.append(business_data)
            existing_data.add(name) # Add to set to avoid duplicates within the same run
            logging.info(f"Extracted: {name}")

    except Exception as e:
        logging.error(f"An error occurred during API scraping: {e}", exc_info=True)

    # --- Append Data to Google Sheet ---
    if scraped_data:
        worksheet.append_rows(scraped_data, value_input_option='USER_ENTERED')
        logging.info(f"SUCCESS: Appended {len(scraped_data)} new rows to G-Sheet.")
    else:
        logging.info("No new data was found to append.")

@app.post("/scrape")
async def scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    """API endpoint to trigger the scraper as a background job."""
    background_tasks.add_task(run_scraper, request.query)
    return {"message": "Scraping job started. This will be very fast. Check your Google Sheet for results."}

@app.get("/")
def read_root():
    """Root endpoint to confirm the backend is running."""
    return {"status": "Backend is running!"}