# main.py
import logging
import os
import json
import gspread
import time
from datetime import datetime

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- Configuration & App Initialization ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = FastAPI()

# This middleware allows our frontend (on Vercel) to communicate with this backend (on Render)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allows all websites to call this API
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Defines the expected input format for our API
class ScrapeRequest(BaseModel):
    query: str

def run_scraper(search_query: str):
    """The main scraping logic, designed to be run in the background."""
    logging.info(f"BACKGROUND SCRAPER STARTED for query: '{search_query}'")
    try:
        # These values are read from Environment Variables set on the Render dashboard
        SHEET_NAME = os.environ["SHEET_NAME"]
        google_creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
        google_creds_dict = json.loads(google_creds_json)
    except KeyError as e:
        logging.error(f"FATAL: Missing Environment Variable on Render: {e}")
        return

    # --- Authenticate with Google Sheets ---
    try:
        gc = gspread.service_account_from_dict(google_creds_dict)
        spreadsheet = gc.open(SHEET_NAME)
        worksheet = spreadsheet.worksheet("Data")
        existing_data = set(worksheet.col_values(1)[1:])
        logging.info(f"Connected to G-Sheet. Found {len(existing_data)} existing entries.")
    except Exception as e:
        logging.error(f"G-Sheets connection failed: {e}")
        return

    # --- Selenium WebDriver Setup ---
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=chrome_options)

    url = f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"
    driver.get(url)

    scraped_data = []
    try:
        wait = WebDriverWait(driver, 20)
        feed_selector = '[role="feed"]'
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, feed_selector)))

        scrollable_div = driver.find_element(By.CSS_SELECTOR, feed_selector)
        for _ in range(5): # Scroll 5 times
            driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight', scrollable_div)
            time.sleep(2)

        results = driver.find_elements(By.CSS_SELECTOR, f'{feed_selector} > div > div > a')
        for result in results[:50]: # Limit to 50 results
            try:
                name = result.find_element(By.CSS_SELECTOR, 'div.font-medium').text
                if not name or name in existing_data:
                    continue

                details = result.text.split('\n')
                rating, reviews, category, address = 'N/A', '0', 'N/A', 'N/A'
                if len(details) > 1 and details[1] and details[1][0].isdigit():
                    parts = details[1].split(' ')
                    rating = parts[0]
                    if len(parts) > 1 and '(' in parts[1]:
                        reviews = parts[1].replace('(', '').replace(')', '').replace(',', '')
                for line in details[2:]:
                    if '·' in line:
                        parts = line.split('·'); category = parts[0].strip(); address = parts[1].strip() if len(parts) > 1 else 'N/A'
                        break

                business_data = [name, category, address, rating, reviews, "N/A", "N/A", datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
                scraped_data.append(business_data)
                existing_data.add(name)
                logging.info(f"Scraped: {name}")
            except Exception:
                continue
    finally:
        driver.quit()

    if scraped_data:
        worksheet.append_rows(scraped_data, value_input_option='USER_ENTERED')
        logging.info(f"SUCCESS: Appended {len(scraped_data)} new rows to G-Sheet.")
    else:
        logging.info("No new data was found to append.")

@app.post("/scrape")
async def scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    """API endpoint to trigger the scraper as a background job."""
    logging.info(f"API call received for query: '{request.query}'")
    background_tasks.add_task(run_scraper, request.query)
    return {"message": "Scraping job started. Check your Google Sheet for results in a few minutes."}

@app.get("/")
def read_root():
    """Root endpoint to check if the backend is running."""
    return {"status": "Backend is running!"}