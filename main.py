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
from selenium.common.exceptions import TimeoutException, NoSuchElementException

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

def get_detail(driver, selector):
    """Safely gets text from an element, returning 'N/A' if not found."""
    try:
        element = driver.find_element(By.CSS_SELECTOR, selector)
        return element.text
    except NoSuchElementException:
        return "N/A"

def run_scraper(search_query: str):
    logging.info(f"COMPREHENSIVE SCRAPER STARTED for query: '{search_query}'")
    try:
        SHEET_NAME = os.environ["SHEET_NAME"]
        google_creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
        google_creds_dict = json.loads(google_creds_json)
    except KeyError as e:
        logging.error(f"FATAL: Missing Environment Variable on Render: {e}")
        return

    try:
        gc = gspread.service_account_from_dict(google_creds_dict)
        spreadsheet = gc.open(SHEET_NAME)
        worksheet = spreadsheet.worksheet("Data")
        existing_data = set(worksheet.col_values(1)[1:])
        logging.info(f"Connected to G-Sheet. Found {len(existing_data)} existing entries.")
    except Exception as e:
        logging.error(f"G-Sheets connection failed: {e}")
        return

    # --- Stable Selenium Options ---
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/5.37.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/5.36")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_page_load_timeout(60) # Increased page load timeout
    
    scraped_data = []
    try:
        url = f"http://googleusercontent.com/maps/google.com/0{search_query.replace(' ', '+')}"
        logging.info(f"Navigating to: {url}")
        driver.get(url)

        wait = WebDriverWait(driver, 30)
        
        feed_selector = '[role="feed"]'
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, feed_selector)))
            logging.info("Main results container found.")
        except TimeoutException:
            logging.error("Timeout: Results container not found. Page might be a CAPTCHA.")
            driver.quit()
            return

        # --- Scroll aggressively to load all results ---
        scrollable_div = driver.find_element(By.CSS_SELECTOR, feed_selector)
        # Keep track of scroll height to know when to stop
        last_height = driver.execute_script("return arguments[0].scrollHeight", scrollable_div)
        while True:
            driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight', scrollable_div)
            time.sleep(4) # Wait longer for new results to load
            new_height = driver.execute_script("return arguments[0].scrollHeight", scrollable_div)
            if new_height == last_height:
                logging.info("Reached the end of the search results.")
                break
            last_height = new_height


        results_links = driver.find_elements(By.CSS_SELECTOR, f'{feed_selector} > div > div > a')
        listing_urls = [link.get_attribute('href') for link in results_links if link.get_attribute('href')]
        logging.info(f"Found {len(listing_urls)} total business listings to process.")

        #
        # ===== PROCESS EVERY SINGLE URL FOUND =====
        #
        for i, listing_url in enumerate(listing_urls):
            try:
                driver.get(listing_url)
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'h1')))
            except TimeoutException:
                logging.warning(f"Skipping listing, page took too long to load: {listing_url}")
                continue # Skip to the next URL if this one fails

            name = get_detail(driver, 'h1')
            if not name or name in existing_data:
                logging.info(f"Skipping duplicate or invalid name: {name}")
                continue
            
            address = get_detail(driver, '[data-item-id="address"]')
            website = get_detail(driver, '[data-item-id="authority"]')
            phone = get_detail(driver, '[data-item-id^="phone"]')
            
            category, reviews_text, rating_text = "N/A", "0", "N/A"
            try:
                category = driver.find_element(By.CSS_SELECTOR, '[jsaction="pane.rating.category"]').text
                rating_container = driver.find_element(By.CSS_SELECTOR, '[jsaction="pane.rating.moreReviews"]')
                rating_label = rating_container.get_attribute('aria-label')
                if rating_label and "stars" in rating_label:
                    parts = rating_label.replace('stars', '').strip().split(' ')
                    rating_text = parts[0]
                    if len(parts) > 1:
                        reviews_text = parts[-1].replace('Reviews', '').strip()
            except Exception:
                pass

            business_data = [
                name, category, address, rating_text, reviews_text,
                website, phone, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ]
            scraped_data.append(business_data)
            existing_data.add(name)
            logging.info(f"({i+1}/{len(listing_urls)}) Scraped: {name}")
            
            time.sleep(1)

    except Exception as e:
        logging.error(f"An unexpected error occurred during scraping: {e}", exc_info=True)
    finally:
        driver.quit()

    if scraped_data:
        worksheet.append_rows(scraped_data, value_input_option='USER_ENTERED')
        logging.info(f"SUCCESS: Appended {len(scraped_data)} new rows to G-Sheet.")
    else:
        logging.info("No new data was found to append.")

@app.post("/scrape")
async def scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_scraper, request.query)
    return {"message": "Comprehensive scraping job started. This will take a long time. Check your Google Sheet for results."}

@app.get("/")
def read_root():
    return {"status": "Backend is running!"}