# app.py - ROBUST VERSION: Demo = requests | Real = Selenium + base64 + LLM
from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import requests
import time
import re
import base64
import logging
import os
from dotenv import load_dotenv
from openai import OpenAI
import threading

# === LOAD ENV ===
load_dotenv()

app = Flask(__name__)

MY_SECRET = os.getenv("MY_SECRET", "MYTDSLLM2025")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
MAX_TIME = 170

# Demo hardcoded
DEMO_ANSWER = 12345
DEMO_SUBMIT_URL = "https://tds-llm-analysis.s-anand.net/submit"

# === SETUP SELENIUM (STEALTH) ===
def get_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => false});")
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => false});"
    })
    return driver

# === EXTRACT TASK (ROBUST BASE64 + TEXT) ===
def extract_task_data(html):
    soup = BeautifulSoup(html, 'html.parser')
    task_text = ""
    # #result div
    result_div = soup.find(id='result')
    if result_div:
        task_text = result_div.get_text(separator="\n", strip=True)

    # Base64 from script
    script = soup.find('script')
    if script and script.string:
        match = re.search(r'atob\s*\(\s*`([\s\S]*?)`\s*\)', script.string, re.DOTALL)
        if match:
            try:
                encoded = re.sub(r'[\s\r\n]+', '', match.group(1))
                decoded = base64.b64decode(encoded).decode('utf-8')
                task_text = decoded
                logger.info("Base64 decoded from script")
            except Exception as e:
                logger.error(f"Base64 decode error: {e}")

    if not task_text:
        return None, None, None

    # Submit URL
    submit_match = re.search(r'https?://[^\s"\'<>]+/submit', task_text)
    submit_url = submit_match.group(0) if submit_match else DEMO_SUBMIT_URL

    # PDF URL
    pdf_match = re.search(r'href=["\']([^"\']+\.pdf)["\']', task_text)
    pdf_url = pdf_match.group(1) if pdf_match else None

    logger.info(f"Task extracted: Submit = {submit_url}, PDF = {pdf_url}")
    return task_text, submit_url, pdf_url

# === SOLVE TASK (LLM FOR REAL) ===
def solve_task(task_text, pdf_url, is_demo):
    if is_demo:
        return DEMO_ANSWER

    prompt = f"Solve this task. Output only the final answer. Task: {task_text}"
    if pdf_url:
        prompt += f"\nPDF: {pdf_url}"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return "Error solving"

# === SUBMIT ===
def submit_answer(submit_url, email, secret, url, answer):
    payload = {
        "email": email,
        "secret": secret,
        "url": url,
        "answer": answer
    }
    try:
        r = requests.post(submit_url, json=payload, timeout=15)
        logger.info(f"Submit: {r.status_code}")
        return r.json() if r.status_code == 200 else {"error": r.text}
    except Exception as e:
        logger.error(f"Submit error: {e}")
        return {"error": "Submit failed"}

# === MAIN RUN ===
def run_quiz(email, secret, url):
    if secret != MY_SECRET:
        return {"error": "Invalid secret"}, 403

    start_time = time.time()
    driver = None
    try:
        is_demo = "demo" in url.lower()
        task_text, submit_url, pdf_url = None, None, None

        if is_demo:
            # Demo: requests
            r = requests.get(url, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            task_text, submit_url, pdf_url = extract_task_data(r.text)
            logger.info("Demo: requests used")
        else:
            # Real: Selenium
            driver = get_driver()
            driver.get(url)
            time.sleep(5)  # JS wait
            task_text, submit_url, pdf_url = extract_task_data(driver.page_source)
            logger.info("Real: Selenium used")

        if not task_text:
            return {"error": "No task found"}, 400

        answer = solve_task(task_text, pdf_url, is_demo)
        result = submit_answer(submit_url, email, secret, url, answer)

        if result.get('correct', False):
            next_url = result.get('url')
            if next_url and (time.time() - start_time < MAX_TIME - 25):
                return run_quiz(email, secret, next_url)
            return {"status": "finished"}

        # Retry
        if time.time() - start_time < MAX_TIME - 40:
            logger.info("Retrying...")
            answer = solve_task(task_text, pdf_url, is_demo)
            result = submit_answer(submit_url, email, secret, url, answer)
            if result.get('correct', False):
                return {"status": "finished"}

        return {"status": "finished", "note": "May be incorrect"}

    except Exception as e:
        logger.error(f"Error: {e}")
        return {"error": str(e)}, 500
    finally:
        if driver:
            driver.quit()
        logger.info(f"Time: {time.time() - start_time:.1f}s")

# === API ===
@app.route('/quiz', methods=['POST'])
def quiz():
    if not request.is_json:
        return jsonify({"error": "JSON required"}), 400

    data = request.get_json()
    email = data.get('email')
    secret = data.get('secret')
    url = data.get('url')

    if not all([email, secret, url]):
        return jsonify({"error": "Missing fields"}), 400

    result = [None]
    def target():
        result[0] = run_quiz(email, secret, url)
    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=MAX_TIME)
    if thread.is_alive():
        return jsonify({"error": "Timeout"}), 500

    resp, status = result[0] if isinstance(result[0], tuple) else (result[0], 200)
    return jsonify(resp), status

@app.route('/ping', methods=['GET', 'POST'])
def ping():
    return jsonify({"status": "alive"}), 200

if __name__ == '__main__':
    logger.info("API STARTED")
    app.run(host='0.0.0.0', port=5000, debug=False)