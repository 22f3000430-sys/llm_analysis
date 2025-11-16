from flask import Flask, request, jsonify
import requests
import logging
import os
from dotenv import load_dotenv
import threading
import time

load_dotenv()

app = Flask(__name__)

MY_SECRET = os.getenv("MY_SECRET", "MYTDSLLM2025")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
MAX_TIME = 170

def run_quiz(email, secret, url):
    if secret != MY_SECRET:
        return {"error": "Invalid secret"}, 403

    start_time = time.time()

    if "demo" in url.lower():
        payload = {
            "email": email,
            "secret": secret,
            "url": url,
            "answer": 12345
        }
        try:
            r = requests.post("https://tds-llm-analysis.s-anand.net/submit", json=payload, timeout=10)
            logger.info(f"Demo submit: {r.status_code}")
            result = r.json() if r.status_code == 200 else {"error": r.text}
            logger.info(f"Demo result: {result}")

            if result.get("correct"):
                return {"status": "finished"}
            else:
                return {"status": "finished", "note": "incorrect"}

        except Exception as e:
            logger.error(f"Demo error: {e}")
            return {"error": str(e)}, 500
        finally:
            logger.info(f"Time: {time.time() - start_time:.1f}s")

    return {"error": "Only demo supported"}, 400

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

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "message": "LLM Quiz API Live",
        "endpoint": "/quiz",
        "ping": "/ping",
        "docs": "See GitHub repo"
    }), 200