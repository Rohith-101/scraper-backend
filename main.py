# main.py
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
    allow_origins=["*"], # Allows your frontend to communicate with this backend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ScrapeRequest(BaseModel):
    query: str

def run_scraper(search_query: str):
    logging.info(f"STATELESS Scraper Started for query: '{search_query}'")
    try:
        # Load secrets securely from environment variables
        SHEET_NAME = os.environ["SHEET_NAME"]
        SERPAPI_KEY = os.environ["SERPAPI_KEY"]
        google_creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
        google_creds_dict = json.loads(google_creds_json)
    except KeyError as e:
        logging.error(f"FATAL: Missing Environment Variable on Render: {e}")
        return

    try:
        # Connect to your Google Sheet
        gc = gspread.service_account_from_dict(google_creds_dict)
        spreadsheet = gc.open(SHEET_NAME)
        worksheet = spreadsheet.worksheet("Data")
        # Duplicate Detection: Read all existing business names
        existing_data = set(worksheet.col_values(1)[1:])
        start_index = len(existing_data) # Calculate starting point
        logging.info(f"Connected to G-Sheet. Found {start_index} existing entries. Will start search from this index.")
    except Exception as e:
        logging.error(f"G-Sheets connection failed: {e}")
        return

    # --- SerpApi Scraping Logic with Pagination ---
    scraped_data = []
    
    params = {
        "api_key": SERPAPI_KEY,
        "engine": "google_maps",
        "q": search_query,
        "ll": "@13.0827,80.2707,15z", # Latitude/Longitude for Chennai
        "type": "search",
        "google_domain": "google.co.in",
        "hl": "en",
        "start": start_index # Tell the API where to start the search
    }
    
    search = GoogleSearch(params)
    
    page_num = 0
    while True:
        # Stop if we have successfully scraped 10 pages in this run
        if page_num >= 10:
            logging.info("Reached the 10-page limit for this run.")
            break
            
        page_num += 1
        logging.info(f"Scraping page batch #{page_num}...")
        results = search.get_dict()
        
        local_results = results.get("local_results", [])
        if not local_results:
            logging.info("No more results found on this page.")
            break

        logging.info(f"Found {len(local_results)} results on this page.")

        for result in local_results:
            name = result.get("title")
            if not name or name in existing_data:
                continue

            # Extract all the detailed columns
            category = result.get("type", "N/A"); address = result.get("address", "N/A")
            rating = result.get("rating", "N/A"); reviews = result.get("reviews", 0)
            website = result.get("website", "N/A"); phone = result.get("phone", "N/A")
            price_level = result.get("price", "N/A"); operating_hours = result.get("operating_hours", {}).get("wednesday", "N/A")
            service_options_list = [k for k, v in result.get("service_options", {}).items() if v]
            service_options = ", ".join(service_options_list) if service_options_list else "N/A"
            gps_coordinates = result.get("gps_coordinates", {}); latitude = gps_coordinates.get("latitude", "N/A"); longitude = gps_coordinates.get("longitude", "N/A")

            business_data = [
                name, category, address, str(rating), str(reviews),
                website, phone, price_level, operating_hours, service_options,
                str(latitude), str(longitude), datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ]
            scraped_data.append(business_data)
            existing_data.add(name)

        # Check if SerpApi has provided a link to the next page of results
        if "next" not in results.get("serpapi_pagination", {}):
            logging.info("Reached the last page of results.")
            break
        
        # Prepare the search for the next page
        search.params_dict.update(results.get("serpapi_pagination"))

    if scraped_data:
        logging.info(f"Total new businesses to add in this run: {len(scraped_data)}")
        worksheet.append_rows(scraped_data, value_input_option='RAW')
        logging.info(f"SUCCESS: Appended {len(scraped_data)} new detailed rows to G-Sheet.")
    else:
        logging.info("No new data was found to append in this run.")

@app.post("/scrape")
async def scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_scraper, request.query)
    return {"message": "Stateless scraping job started. Check your Google Sheet for results."}

@app.get("/")
def read_root():
    return {"status": "Backend is running!"}