# main.py
import logging
import os
import json
import gspread
from datetime import datetime
import urllib.parse

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from scrapingbee import ScrapingBeeClient
from bs4 import BeautifulSoup

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
    logging.info(f"CORRECTED SCRAPINGBEE SCRAPER STARTED for query: '{search_query}'")
    try:
        # Get secrets from Render's environment variables
        SHEET_NAME = os.environ["SHEET_NAME"]
        SCRAPINGBEE_API_KEY = os.environ["SCRAPINGBEE_API_KEY"]
        google_creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
        google_creds_dict = json.loads(google_creds_json)
    except KeyError as e:
        logging.error(f"FATAL: Missing Environment Variable on Render: {e}")
        return

    try:
        # Connect to Google Sheets
        gc = gspread.service_account_from_dict(google_creds_dict)
        spreadsheet = gc.open(SHEET_NAME)
        worksheet = spreadsheet.worksheet("Data")
        existing_data = set(worksheet.col_values(1)[1:])
        logging.info(f"Connected to G-Sheet. Found {len(existing_data)} existing entries.")
    except Exception as e:
        logging.error(f"G-Sheets connection failed: {e}")
        return

    scraped_data = []
    try:
        client = ScrapingBeeClient(api_key=SCRAPINGBEE_API_KEY)
        
        # --- Create a valid, full Google Maps URL ---
        encoded_query = urllib.parse.quote_plus(search_query)
        url = f"https://www.google.com/maps/search/{encoded_query}"
        logging.info(f"Requesting URL: {url}")

        # --- Make the API call to ScrapingBee ---
        response = client.get(url, params={'render_js': True, 'country_code': 'in'})
        
        if response.status_code != 200:
            logging.error(f"ScrapingBee failed with status {response.status_code}")
            return

        # --- Parse the HTML response ---
        soup = BeautifulSoup(response.content, "lxml")
        
        # Find all link elements that are business results
        results = soup.select('a[href^="https://www.google.com/maps/place/"]')
        logging.info(f"Found {len(results)} potential listings in the HTML.")

        for result in results:
            name = result.get('aria-label')
            if not name or name in existing_data:
                continue

            # Find the parent container for the result's text details
            parent_div = result.find_parent('div', class_=lambda x: x and x.startswith('Nv2PK'))
            if not parent_div:
                continue

            # Extract and clean up the text block
            details_text = parent_div.text
            parts = [p.strip() for p in details_text.split('·') if p.strip()]
            rating, reviews, category = "N/A", "0", "N/A"

            if len(parts) > 0 and parts[0] and parts[0][0].isdigit():
                rating_info = parts[0].split()
                rating = rating_info[0]
                if len(rating_info) > 1:
                    reviews = rating_info[1].replace('(', '').replace(')', '')
            
            if len(parts) > 1:
                category = parts[1]

            business_data = [
                name, category, "N/A", str(rating), str(reviews),
                "N/A", "N/A", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ]
            scraped_data.append(business_data)
            existing_data.add(name)
            logging.info(f"Extracted: {name}")

    except Exception as e:
        logging.error(f"An error occurred during API scraping: {e}", exc_info=True)

    if scraped_data:
        worksheet.append_rows(scraped_data, value_input_option='USER_ENTERED')
        logging.info(f"SUCCESS: Appended {len(scraped_data)} new rows to G-Sheet.")
    else:
        logging.info("No new data was found to append.")

@app.post("/scrape")
async def scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_scraper, request.query)
    return {"message": "ScrapingBee job started. Check your Google Sheet for results."}

@app.get("/")
def read_root():
    return {"status": "Backend is running!"}