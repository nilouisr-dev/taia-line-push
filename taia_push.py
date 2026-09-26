import os
import sys
import logging
import base64
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
from apscheduler.schedulers.blocking import BlockingScheduler
import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS")
WORKER_URL = os.environ.get("WORKER_URL")
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "12"))
RETRY_DELAY = int(os.environ.get("RETRY_DELAY", "300"))
BKK_TZ = timezone(timedelta(hours=7))

def create_logger():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger("TAIA")

def fetch_email_body(msg_id, service):
    msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    for part in msg.get('payload', {}).get('parts', []):
        if part.get('mimeType') == 'text/html':
            data = part.get('body', {}).get('data', '')
            return base64.urlsafe_b64decode(data).decode('utf-8')
    body = msg.get('payload', {}).get('body', {}).get('data', '')
    if body:
        return base64.urlsafe_b64decode(body).decode('utf-8')
    return ""

def connect_gmail():
    creds = Credentials(token=GMAIL_APP_PASS)
    return build('gmail', 'v1', credentials=creds)

def find_today_report(service):
    tz = BKK_TZ
    now = datetime.now(tz)
    search_after = (now - timedelta(hours=26)).strftime('%Y/%m/%d')
    search_before = (now + timedelta(hours=2)).strftime('%Y/%m/%d')
    query = f'subject:"[TAIA-REPORT]" after:{search_after} before:{search_before}'
    results = service.users().messages().list(userId='me', q=query, maxResults=5).execute()
    messages = results.get('messages', [])
    if messages:
        return messages[0]['id']
    return None

def send_alert(msg_id, service):
    now = datetime.now(BKK_TZ)
    thai_date = now.strftime('%d %b %Y').replace('Jan','ม.ค.').replace('Feb','ก.พ.').replace('Mar','มี.ค.').replace('Apr','เม.ย.').replace('May','พ.ค.').replace('Jun','มิ.ย.').replace('Jul','ก.ค.').replace('Aug','ส.ค.').replace('Sep','ก.ย.').replace('Oct','ต.ค.').replace('Nov','พ.ย.').replace('Dec','ธ.ค.')
    payload = {
        "messages": [{
            "type": "text",
            "text": f"⚠️ TAIA Alert: ไม่พบรายงานวันที่ {now.strftime('%Y-%m-%d')} ({thai_date})\nเวลาตรวจสอบ: {now.strftime('%H:%M น.')}\nอีเมลอาจยังไม่ถึงหรือมีปัญหา กรุณาตรวจสอบ Gmail"
        }]
    }
    headers = {"Content-Type": "application/json"}
    r = requests.post(WORKER_URL, json=payload, headers=headers, timeout=10)
    return r.status_code

def main():
    logger = create_logger()
    logger.info("TAIA Push Started")

    if not WORKER_URL:
        logger.error("WORKER_URL not configured")
        sys.exit(1)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            service = connect_gmail()
            msg_id = find_today_report(service)
            if msg_id:
                logger.info(f"Found report: {msg_id}")
                break
        except Exception as e:
            logger.warning(f"Attempt {attempt}/{MAX_RETRIES} failed: {e}")
        if attempt < MAX_RETRIES:
            logger.info(f"Retry in {RETRY_DELAY}s...")
            import time
            time.sleep(RETRY_DELAY)
    else:
        logger.error("Report not found after all retries")
        send_alert(msg_id, service)
        logger.info("Alert sent to LINE")
        sys.exit(0)

    try:
        html = fetch_email_body(msg_id, service)
        payload = {"messages": [{"type": "text", "text": html, "format": "html"}]}
        headers = {"Content-Type": "application/json"}
        r = requests.post(WORKER_URL, json=payload, headers=headers, timeout=10)
        logger.info(f"Sent to LINE: {r.status_code}")
    except Exception as e:
        logger.error(f"Send failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
