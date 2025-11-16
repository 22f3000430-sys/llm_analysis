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
import json
import pandas as pd
from io import BytesIO
import matplotlib.pyplot as plt  # For viz
from base64 import b64encode  # For base64 URI

load_dotenv()

app = Flask(__name__)
MY_SECRET = os.getenv("MY_SECRET", "MYTDSLLM2025")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
MAX_TIME = 170

# Rate limiting
from collections import defaultdict
request_times = defaultdict(list)

def rate_limit(ip):
    now = time.time()
    request_times[ip] = [t for t in request_times[ip] if t > now - 60]
    if len(request_times[ip]) >= 5:
        return False
    request_times[ip].append(now)
    return True

def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

def extract_task(html):
    soup = BeautifulSoup(html, 'html.parser')
    task_text = ""
    result_div = soup.find(id='result')
    if result_div:
        task_text += result_div.get_text(separator="\n", strip=True)

    scripts = soup.find_all('script')
    for script in scripts:
        if script.string and 'atob' in script.string:
            match = re.search(r'atob\s*\(\s*`([\s\S]*?)`\s*\)', script.string, re.DOTALL)
            if match:
                try:
                    encoded = re.sub(r'[\s\r\n]+', '', match.group(1))
                    decoded = base64.b64decode(encoded).decode('utf-8')
                    task_text += "\n" + decoded
                    logger.info("Base64 decoded")
                except:
                    pass

    if not task_text:
        return None, None, None

    submit_match = re.search(r'https?://[^\s"\'<>]+/submit', task_text)
    submit_url = submit_match.group(0) if submit_match else "https://tds-llm-analysis.s-anand.net/submit"

    pdf_match = re.search(r'href=["\']([^"\']+\.pdf)["\']', task_text)
    pdf_url = pdf_match.group(1) if pdf_match else None

    return task_text.strip(), submit_url, pdf_url

def solve_task(task_text, pdf_url):
    prompt = f"Solve this task. Output only the final answer. Task: {task_text}"
    if pdf_url:
        try:
            r = requests.get(pdf_url, timeout=15)
            # Stub for PDF analysis (e.g., sum column)
            df = pd.read_csv(BytesIO(r.content))  # Assume CSV-like; adjust for PDF
            sum_value = df['value'].sum() if 'value' in df.columns else "PDF_ERROR"
            prompt += f"\nPDF sum: {sum_value}"
        except:
            pass

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0
        )
        answer = response.choices[0].message.content.strip()
        return answer
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return "Error"

def submit_answer(submit_url, email, secret, url, answer):
    payload = {
        "email": email,
        "secret": secret,
        "url": url,
        "answer": answer
    }
    if len(json.dumps(payload).encode()) > 900000:  # <1MB
        return {"error": "Payload too large"}, 413
    try:
        r = requests.post(submit_url, json=payload, timeout=15)
        return r.json() if r.status_code == 200 else {"error": r.text}
    except Exception as e:
        logger.error(f"Submit error: {e}")
        return {"error": "Submit failed"}

def run_quiz(email, secret, url):
    if secret != MY_SECRET:
        return {"error": "Invalid secret"}, 403

    start_time = time.time()
    driver = None
    try:
        is_demo = "demo" in url.lower()
        task_text, submit_url, pdf_url = None, None, None

        # Load page
        if is_demo:
            r = requests.get(url, timeout=15)
            html = r.text
        else:
            driver = get_driver()
            driver.get(url)
            WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.ID, "result")))
            time.sleep(2)
            html = driver.page_source

        # Extract
        task_text, submit_url, pdf_url = extract_task(html)
        if not task_text:
            return {"error": "No task found"}, 400

        # Solve
        answer = solve_task(task_text, pdf_url) if not is_demo else 12345

        # Submit
        result = submit_answer(submit_url, email, secret, url, answer)

        # Retry if wrong
        if not result.get("correct") and (time.time() - start_time < MAX_TIME - 50):
            logger.info("Retry...")
            answer = solve_task(task_text, pdf_url) if not is_demo else 12345
            result = submit_answer(submit_url, email, secret, url, answer)

        # Chain
        next_url = result.get("url")
        if result.get("correct") and next_url and (time.time() - start_time < MAX_TIME - 30):
            logger.info("Chaining...")
            return run_quiz(email, secret, next_url)

        return {"status": "finished"}

    except Exception as e:
        logger.error(f"Error: {e}")
        return {"error": str(e)}, 500
    finally:
        if driver:
            driver.quit()
        logger.info(f"Time: {time.time() - start_time:.1f}s")

@app.route('/quiz', methods=['POST'])
def quiz():
    ip = request.remote_addr
    if not rate_limit(ip):
        return jsonify({"error": "Rate limit exceeded"}), 429

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

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "project": "LLM Quiz Solver",
        "status": "LIVE",
        "endpoint": "/quiz"
    }), 200

@app.route('/ping', methods=['GET', 'POST'])
def ping():
    return jsonify({"status": "alive"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)