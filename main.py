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
    logging.info(f"BACKGROUND SCRAPER STARTED for query: '{search_query}'")
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

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36")
    
    driver = webdriver.Chrome(options=chrome_options)
    
    scraped_data = []
    try:
        logging.info("Navigating to google.com to handle consent screen...")
        driver.get("https://www.google.com")
        time.sleep(2)

        try:
            accept_button_xpath = "//button[.//span[contains(text(), 'Accept all')]] | //button[contains(text(), 'Accept all')] | //div[contains(text(), 'Accept all')]"
            accept_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, accept_button_xpath))
            )
            accept_button.click()
            logging.info("Clicked the 'Accept all' button.")
            time.sleep(2)
        except Exception:
            logging.info("No 'Accept all' button was found. Proceeding assuming no consent screen.")

        logging.info(f"Navigating to Google Maps search for: '{search_query}'")
        url = f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"
        driver.get(url)

        wait = WebDriverWait(driver, 20)
        feed_selector = '[role="feed"]'
        
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, feed_selector)))
        logging.info("Main results container '[role=\"feed\"]' found.")
        
        scrollable_div = driver.find_element(By.CSS_SELECTOR, feed_selector)
        for _ in range(5):
            driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight', scrollable_div)
            time.sleep(2)

        results = driver.find_elements(By.CSS_SELECTOR, f'{feed_selector} > div > div > a')
        logging.info(f"Found {len(results)} potential business listings on the page.")

        #
        # ===== UPDATED EXTRACTION LOGIC =====
        #
        for result in results[:50]:
            try:
                # Use the aria-label of the link, which is a more reliable way to get the name
                name = result.get_attribute('aria-label')
                
                if not name or name in existing_data:
                    continue
                
                # The rest of the details are still parsed from the text block
                details = result.text.split('\n')
                rating, reviews, category, address = 'N/A', '0', 'N/A', 'N/A'
                
                if len(details) > 1 and details[1] and details[1][0].isdigit():
                    parts = details[1].split(' ')
                    rating = parts[0]
                    if len(parts) > 1 and '(' in parts[1]:
                        reviews = parts[1].replace('(', '').replace(')', '').replace(',', '')
                
                for line in details[2:]:
                    if '·' in line:
                        parts = line.split('·')
                        category = parts[0].strip()
                        if len(parts) > 1:
                            address = parts[1].strip()
                        break

                business_data = [name, category, address, rating, reviews, "N/A", "N/A", datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
                scraped_data.append(business_data)
                existing_data.add(name)
                logging.info(f"Successfully extracted: {name}")
            except Exception as e:
                logging.warning(f"Could not parse a business listing. Error: {e}. Text was: {result.text}")
                continue
        #
        # ===== END OF UPDATED LOGIC =====
        #
    
    except Exception as e:
        logging.error(f"An unexpected error occurred during scraping: {e}", exc_info=True)
    finally:
        driver.quit()

    if scraped_data:
        worksheet.append_rows(scraped_data, value_input_option='USER_ENTERED')
        logging.info(f"SUCCESS: Appended {len(scraped_data)} new rows to G-Sheet.")
    else:
        logging.info("No new data was found to append. This could be due to a CAPTCHA, a change in Google's page layout, or no results for the query.")

@app.post("/scrape")
async def scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    logging.info(f"API call received for query: '{request.query}'")
    background_tasks.add_task(run_scraper, request.query)
    return {"message": "Scraping job started. Check your Google Sheet for results in a few minutes."}

@app.get("/")
def read_root():
    return {"status": "Backend is running!"}