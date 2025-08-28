# main.py
import logging
import os
import json
import gspread
from datetime import datetime

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
    logging.info(f"SCRAPINGBEE SCRAPER STARTED for query: '{search_query}'")
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
        # We construct the Google Maps URL to be scraped
        url = f"http://googleusercontent.com/maps/google.com/0{search_query.replace(' ', '+')}"

        # Make the API call to ScrapingBee to get the page HTML
        response = client.get(url, params={'render_js': True, 'country_code': 'in'})

        if response.status_code != 200:
            logging.error(f"ScrapingBee failed with status {response.status_code}: {response.text}")
            return

        # Use BeautifulSoup to parse the HTML content
        soup = BeautifulSoup(response.content, "lxml")

        # Find all the link tags that represent a business listing
        results = soup.select('a[href^="https://www.google.com/maps/place/"]')
        logging.info(f"Found {len(results)} potential listings in the HTML.")

        for result in results:
            name = result.get('aria-label')
            if not name or name in existing_data:
                continue

            # The details are inside the text content of the link
            details = result.text.split('·')
            rating, reviews, category = "N/A", "0", "N/A"

            if len(details) > 0 and details[0]:
                rating_parts = details[0].split()
                if rating_parts:
                    rating = rating_parts[0]
                    if len(rating_parts) > 1:
                        reviews = rating_parts[1].replace('(', '').replace(')', '')

            if len(details) > 1 and details[1]:
                category = details[1].strip()

            business_data = [
                name, category, "N/A", str(rating), str(reviews),
                "N/A", "N/A", datetime.now().strftime("%Y-m-%d %H:%M:%S")
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