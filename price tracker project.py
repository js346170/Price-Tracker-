# Import required modules
import os
import time
import random
import requests
import logging
from bs4 import BeautifulSoup
import csv
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
import socket
import sys
from email.message import EmailMessage
import smtplib

# Configure application paths
DATA_DIR = "/path/to/your/data_directory"  # Replace with actual path
os.makedirs(DATA_DIR, exist_ok=True)

# Email configuration (replace with your values)
EMAIL_ENABLED = True
SMTP_SERVER = "smtp.provider.com"
SMTP_PORT = 465
EMAIL_FROM = "your_email@provider.com"
EMAIL_TO = "recipient@provider.com"
APP_PASSWORD = "your_app_specific_password"

# Prevent multiple instances from running
def prevent_multiple_instances():
    """Ensures only one instance of the script runs at a time"""
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind(("127.0.0.1", 65432))
    except socket.error:
        print("Application already running!")
        sys.exit(1)

prevent_multiple_instances()

# Configure logging system
logging.basicConfig(
    filename=os.path.join(DATA_DIR, 'price_tracker.log'),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def get_random_headers():
    """Generate random browser headers for request rotation"""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/...",
        "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/116.0"
    ]
    return {
        "User-Agent": random.choice(user_agents),
        "Accept-Language": "en-US,en;q=0.9"
    }

def send_price_alert(product_title, old_price, new_price, url):
    """Send email notification for significant price changes"""
    if not EMAIL_ENABLED:
        return False
    
    try:
        # Create email message
        msg = EmailMessage()
        msg['Subject'] = f"Price Alert: {product_title}"
        msg['From'] = EMAIL_FROM
        msg['To'] = EMAIL_TO
        
        # HTML email content
        html_content = f"""
        <h3>Price Change Detected!</h3>
        <p><strong>Product:</strong> {product_title}</p>
        <p><strong>Previous Price:</strong> {old_price}</p>
        <p><strong>New Price:</strong> {new_price}</p>
        <p><a href="{url}">View Product</a></p>
        """
        msg.set_content(html_content, subtype='html')
        
        # Connect to SMTP server and send
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_FROM, APP_PASSWORD)
            server.send_message(msg)
        
        logging.info(f"Sent price alert for {product_title}")
        return True
    except Exception as e:
        logging.error(f"Email failed: {str(e)}")
        return False

def validate_price(price_str):
    """Clean and validate price formatting"""
    try:
        if price_str == "N/A":
            return False
        cleaned = ''.join([c for c in price_str if c in '0123456789.'])
        return float(cleaned)
    except ValueError:
        return False

def scrape_product(url, csv_filename='product_prices.csv'):
    """Main scraping function with data persistence"""
    try:
        # Fetch and parse product page
        response = requests.get(url, headers=get_random_headers(), timeout=20)
        response.raise_for_status()
        
        if "captcha" in response.text.lower():
            logging.warning(f"CAPTCHA detected for {url}")
            return False, None

        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract product details
        title_element = soup.find('span', {'id': 'productTitle'})
        title = title_element.get_text(strip=True) if title_element else "N/A"
        
        # Price extraction logic
        price_selectors = [
            'span.a-price span.a-offscreen',
            'span.aok-offscreen',
            '.priceToPay',
            'span#priceblock_ourprice'
        ]
        price_element = None
        for selector in price_selectors:
            price_element = soup.select_one(selector)
            if price_element:
                break
        
        raw_price = price_element.get_text(strip=True) if price_element else "N/A"
        cleaned_price = ''.join([c for c in raw_price if c in '0123456789.,']) if raw_price != "N/A" else "N/A"
        
        # Check price history
        previous_price = None
        clean_url = url.split('?')[0]
        csv_path = os.path.join(DATA_DIR, csv_filename)
        
        if os.path.exists(csv_path):
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['URL'] == clean_url:
                        previous_price = row.get('Price', "N/A")
        
        # Prepare data record
        price_change = "No Change"
        if previous_price and previous_price != "N/A":
            old_val = validate_price(previous_price)
            new_val = validate_price(cleaned_price)
            
            if old_val and new_val and abs(new_val - old_val) >= 0.01:
                price_change = f"{previous_price} → {cleaned_price}"
        
        data = {
            'Title': title,
            'Price': cleaned_price,
            'Previous_Price': previous_price or "N/A",
            'Price_Change': price_change,
            'URL': clean_url,
            'Timestamp': datetime.now().isoformat()
        }
        
        # Save to CSV
        file_exists = os.path.isfile(csv_path)
        with open(csv_path, 'a+', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=data.keys())
            if not file_exists or f.tell() == 0:
                writer.writeheader()
            writer.writerow(data)
        
        # Send alert if price changed
        if price_change != "No Change":
            send_price_alert(title, previous_price, cleaned_price, clean_url)
        
        return True, csv_path
    
    except Exception as e:
        logging.error(f"Scraping error: {str(e)}")
        return False, None

def daily_scrape_job():
    """Scheduled task to check all products"""
    urls_file = os.path.join(DATA_DIR, 'tracked_urls.txt')
    
    try:
        # Load and validate URLs
        with open(urls_file, 'r') as f:
            urls = [line.strip() for line in f if line.strip()]
        
        valid_urls = [url for url in urls if url.startswith('https://')]
        
        if not valid_urls:
            logging.error("No valid URLs found")
            return
        
        logging.info(f"Starting scrape of {len(valid_urls)} products")
        print(f"Processing {len(valid_urls)} items...")
        
        # Process each URL with randomized delays
        for idx, url in enumerate(valid_urls, 1):
            print(f"Item {idx}/{len(valid_urls)}: {url[:60]}...")
            success, path = scrape_product(url)
            
            if success:
                logging.info(f"Success: {url}")
                print(f"Saved to {path}")
            else:
                logging.warning(f"Failed: {url}")
                print("Processing failed")
            
            delay = random.uniform(15, 45)
            print(f"Next request in {delay:.1f}s")
            time.sleep(delay)
        
        logging.info("Daily scrape completed")
    
    except FileNotFoundError:
        logging.error("Missing URLs file")
        print("Error: URL list not found")

if __name__ == "__main__":
    print("Price Tracking System")
    print(f"Data directory: {DATA_DIR}")
    
    # Immediate first run
    daily_scrape_job()
    
    # Configure scheduled runs
    scheduler = BlockingScheduler()
    scheduler.add_job(
        daily_scrape_job,
        'cron',
        hour=3,
        minute=0,
        timezone='America/New_York'
    )
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nService stopped")
        logging.info("Application terminated by user")