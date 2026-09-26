#!/usr/bin/env python3
"""
TAIA LINE Push - GitHub Actions Version (with retry)
"""
import os
import sys
import json
import re
import email
import time
import logging
import imaplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: Missing dependencies")
    sys.exit(1)

GMAIL_USER = os.environ.get("GMAIL_USER", "nilouis.r@gmail.com")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")
WORKER_URL = os.environ.get("WORKER_URL", "https://line-push-bot.farmer-line-bot.workers.dev")
EMAIL_SUBJECT_PREFIX = "[TAIA-REPORT]"
THAI_TZ = timezone(timedelta(hours=7))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "12"))
RETRY_DELAY = int(os.environ.get("RETRY_DELAY", "300"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("taia_push")

def get_today_str():
    return datetime.now(THAI_TZ).strftime("%Y-%m-%d")

def connect_gmail():
    logger.info(f"Connecting to Gmail IMAP as {GMAIL_USER}...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(GMAIL_USER, GMAIL_APP_PASS)
    logger.info("Gmail login OK")
    return mail

def find_today_report(mail):
    today = get_today_str()
    search_subject = f'{EMAIL_SUBJECT_PREFIX} {today}'
    logger.info(f'Searching for email: subject="{search_subject}"')
    mail.select('INBOX', readonly=True)
    date_str = datetime.now(THAI_TZ).strftime("%d-%b-%Y")
    status, data = mail.search(None, f'(SUBJECT "{search_subject}" SINCE {date_str})')
    if status != 'OK' or not data[0]:
        status, data = mail.search(None, f'SUBJECT "{search_subject}"')
    if status != 'OK' or not data[0]:
        logger.info("No report email found")
        return None, None
    email_ids = data[0].split()
    latest_id = email_ids[-1]
    logger.info(f"Found report email (ID: {latest_id.decode()})")
    status, msg_data = mail.fetch(latest_id, '(RFC822)')
    if status != 'OK':
        return None, None
    raw_email = msg_data[0][1]
    msg = email.message_from_bytes(raw_email)
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ('text/plain', 'text/html'):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    body = payload.decode(charset, errors='replace')
                    if ctype == 'text/plain':
                        break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            body = payload.decode(charset, errors='replace')
    url = None
    for pattern in [r'(https://www\.coze\.cn/s/[^\s<>"\']+)',
                    r'(https://www\.coze\.cn/[^\s<>"\']+)']:
        match = re.search(pattern, body)
        if match:
            url = match.group(1).rstrip('.'\'" \n\r')
            break
    summary = None
    summary_match = re.search(r'\[TAIA-SUMMARY\]\s*\n(.*?)(?:\n\[TAIA-URL\]|\n---|\nhttp)',
                              body, re.DOTALL)
    if summary_match:
        summary = summary_match.group(1).strip()
    logger.info(f"URL found: {url is not None}")
    logger.info(f"Summary found: {summary is not None}")
    return url, summary

def push_to_line(text):
    if not text:
        return False
    try:
        MAX_LEN = 4900
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= MAX_LEN:
                chunks.append(remaining)
                break
            idx = remaining.rfind('\n', 0, MAX_LEN)
            if idx < MAX_LEN * 0.5:
                idx = MAX_LEN
            chunks.append(remaining[:idx])
            remaining = remaining[idx:]
        for i, chunk in enumerate(chunks):
            resp = requests.post(
                WORKER_URL,
                headers={"Content-Type": "application/json"},
                json={"message": chunk},
                timeout=30,
            )
            if resp.status_code == 200:
                logger.info(f"LINE push chunk {i+1}/{len(chunks)} OK")
            else:
                logger.error(
                    f"Worker chunk {i+1} returned {resp.status_code}: {resp.text}"
                )
                return False
        return True
    except Exception as e:
        logger.error(f"Worker push failed: {e}")
        return False

def build_line_message(today, summary, url):
    days_th = [
        'จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์'
    ]
    months_th = [
        'มกราคม', 'กุมภาพันธ์', 'มีนาคม', 'เมษายน', 'พฤษภาคม', 'มิถุนายน',
        'กรกฎาคม', 'สิงหาคม', 'กันยายน', 'ตุลาคม', 'พฤศจิกายน', 'ธันวาคม'
    ]
    try:
        dt = datetime.strptime(today, '%Y-%m-%d')
        day_th = days_th[dt.weekday()]
        month_th = months_th[dt.month - 1]
        year_th = dt.year + 543
        date_th = f"วัน{day_th}ที่ {dt.day} {month_th} {year_th}"
    except Exception:
        date_th = today
    lines = []
    lines.append("🌴 TAIA รายงานประจำวัน")
    lines.append(f" {date_th}")
    lines.append("")
    if summary:
        lines.append(summary)
        lines.append("")
    if url:
        lines.append("📖 อ่านฉบับเต็ม:")
        lines.append(url)
        lines.append("")
    lines.append("🤖 โดย TAIA v8.2 (via GitHub Actions)")
    return '\n'.join(lines)

def main():
    logger.info("=" * 60)
    logger.info(" TAIA LINE Push (GitHub Actions)")
    logger.info(f"📅 Date (TH): {get_today_str()}")
    logger.info(f"🔄 Max retries: {MAX_RETRIES}, delay: {RETRY_DELAY}s")
    logger.info("=" * 60)

    if not GMAIL_APP_PASS:
        logger.error("GMAIL_APP_PASS not set!")
        sys.exit(1)

    url = None
    summary = None

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"--- Attempt {attempt}/{MAX_RETRIES} ---")
        try:
            mail = connect_gmail()
            url, summary = find_today_report(mail)
            mail.logout()
        except Exception as e:
            logger.error(f"Gmail attempt {attempt} failed: {e}")
            url = None

        if url:
            logger.info(f"✅ Report found on attempt {attempt}")
            break

        if attempt < MAX_RETRIES:
            logger.info(f"No email found yet. Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
        else:
            logger.warning("All retries exhausted. No report email found.")

    if not url:
        alert = (
            "⚠️ TAIA แจ้งเตือน\n"
            f"วันที่ {get_today_str()}: ไม่พบรายงานประจำวันในอีเมล\n"
            f"ลองแล้ว {MAX_RETRIES} ครั้ง (ทุก {RETRY_DELAY} วินาที)\n"
            "กรุณาตรวจสอบด้วยตนเอง"
        )
        push_to_line(alert)
        sys.exit(0)

    today = get_today_str()
    message = build_line_message(today, summary, url)
    ok = push_to_line(message)
    if ok:
        logger.info("✅ TAIA daily report pushed to LINE successfully")
    else:
        logger.error("Failed to push to LINE")
        sys.exit(1)

if __name__ == "__main__":
    main()
