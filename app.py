from flask import Flask, request, jsonify
import requests
import json
import threading
from byte import Encrypt_ID, encrypt_api
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import xH
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

app = Flask(__name__)

API_KEY = "FADAI"
MAX_WORKERS = 50
REFRESH_INTERVAL = 8 * 3600

REGION_FILES = {
    "IND": "spam_ind.json",
    "BR": "spam_br.json",
    "US": "spam_br.json",
    "SAC": "spam_br.json",
    "NA": "spam_br.json",
    "EU": "spam_eu.json",
    "VN": "spam_vn.json",
    "ME": "spam_me.json",
    "BD": "spam_bd.json"
}

# ملفات البيانات
TARGET_FILE = "xTaRgEt"
SAFE_FILE = "safe_uids.json"

# جلسة HTTP واحدة مع إعادة المحاولة
session = requests.Session()
retry = Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry, pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS*2)
session.mount('https://', adapter)
session.mount('http://', adapter)

tokens_cache = {}
cache_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# إدارة السبام المستمر
spam_jobs = {}          # uid -> threading.Event (للإيقاف)
spam_jobs_lock = threading.Lock()

# الحماية من السبام
protected_uids = set()
protected_uids_lock = threading.Lock()

def load_accounts(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def generate_jwt(account):
    uid = account.get("uid", "")
    password = account.get("password", "")
    if not uid or not password:
        return None
    try:
        token = xH.gJwt(uid, password)
        return token.strip() if token else None
    except Exception:
        return None

def preload_all_tokens():
    global tokens_cache
    new_cache = {}
    for region, file_path in REGION_FILES.items():
        accounts = load_accounts(file_path)
        if not accounts:
            new_cache[region] = []
            continue
        tokens = []
        futures = [executor.submit(generate_jwt, acc) for acc in accounts]
        for future in as_completed(futures):
            jwt = future.result()
            if jwt:
                tokens.append(jwt)
        new_cache[region] = tokens
    with cache_lock:
        tokens_cache = new_cache

def refresh_tokens_loop():
    while True:
        time.sleep(REFRESH_INTERVAL)
        preload_all_tokens()

def send_friend_request(target_uid, jwt_token, results, lock):
    try:
        encrypted_id = Encrypt_ID(target_uid)
        payload = f"08a7c4839f1e10{encrypted_id}1801"
        encrypted_payload = encrypt_api(payload)
        url = "https://clientbp.ggpolarbear.com/RequestAddingFriend"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "X-Unity-Version": "2018.4.11f1",
            "X-GA": "v1 1",
            "ReleaseVersion": "OB53",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SM-N975F Build/PI)",
            "Connection": "keep-alive"
        }
        response = session.post(
            url,
            headers=headers,
            data=bytes.fromhex(encrypted_payload),
            timeout=5
        )
        with lock:
            if response.status_code == 200:
                results["success"] += 1
            else:
                results["failed"] += 1
    except Exception:
        with lock:
            results["failed"] += 1

def spam_loop(uid, region, stop_event):
    """يرسل طلبات صداقة باستمرار حتى يتم إيقافه"""
    while not stop_event.is_set():
        with cache_lock:
            tokens = tokens_cache.get(region, [])
        if not tokens:
            time.sleep(1)
            continue

        # إرسال دفعة واحدة متوازية باستخدام جميع التوكنات
        lock = threading.Lock()
        results = {"success": 0, "failed": 0}
        futures = [executor.submit(send_friend_request, uid, t, results, lock) for t in tokens]
        for _ in as_completed(futures):
            pass

        # مهلة قصيرة بين الدورات لتجنب الضغط الزائد
        time.sleep(2)

def load_protected_uids():
    """تحميل قائمة الآيدي المحمية من الملف"""
    global protected_uids
    try:
        with open(SAFE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                protected_uids = set(data)
    except FileNotFoundError:
        protected_uids = set()
    except Exception:
        protected_uids = set()

def save_protected_uids():
    """حفظ قائمة الآيدي المحمية إلى الملف"""
    with protected_uids_lock:
        with open(SAFE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(protected_uids), f)

def write_target_uid(uid):
    """تخزين الآيدي المستهدف في ملف xTaRgEt"""
    with open(TARGET_FILE, "a", encoding="utf-8") as f:
        f.write(uid + "\n")

def remove_target_uid(uid):
    """إزالة الآيدي من ملف xTaRgEt عند إيقاف السبام (اختياري)"""
    try:
        with open(TARGET_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(TARGET_FILE, "w", encoding="utf-8") as f:
            for line in lines:
                if line.strip() != uid:
                    f.write(line)
    except FileNotFoundError:
        pass

@app.route("/send_requests", methods=["GET"])
def send_requests():
    uid = request.args.get("uid")
    region = request.args.get("region")
    api_key = request.args.get("key")

    if not api_key or api_key != API_KEY:
        return jsonify({"error": "مفتاح API غير صحيح"}), 403
    if not uid or not region:
        return jsonify({"error": "يجب إدخال uid و region"}), 400

    region = region.upper()

    # التحقق من الحماية
    with protected_uids_lock:
        if uid in protected_uids:
            return jsonify({"error": "هذا الآيدي محمي من السبام"}), 403

    # التحقق من وجود توكنات للمنطقة
    with cache_lock:
        if region not in REGION_FILES:
            return jsonify({"error": f"المنطقة {region} غير معروفة"}), 400
        tokens = tokens_cache.get(region, [])
        if not tokens:
            return jsonify({"error": f"لا توجد توكنات للمنطقة {region}"}), 500

    # إدارة مهمة السبام المستمر
    with spam_jobs_lock:
        if uid in spam_jobs and not spam_jobs[uid].is_set():
            return jsonify({"message": "السبام قيد التشغيل بالفعل لهذا الآيدي"}), 200
        # إنشاء حدث إيقاف وبدء مهمة جديدة
        stop_event = threading.Event()
        spam_jobs[uid] = stop_event

    # تخزين الآيدي في الملف
    write_target_uid(uid)

    # تشغيل حلقة السبام في خيط منفصل
    thread = threading.Thread(target=spam_loop, args=(uid, region, stop_event), daemon=True)
    thread.start()

    return jsonify({
        "message": "تم بدء الإرسال المستمر للآيدي",
        "uid": uid,
        "region": region
    })

@app.route("/stop", methods=["GET"])
def stop_spam():
    api_key = request.args.get("key")
    uid = request.args.get("uid")  # اختياري: إيقاف آيدي محدد فقط

    if not api_key or api_key != API_KEY:
        return jsonify({"error": "مفتاح API غير صحيح"}), 403

    with spam_jobs_lock:
        if uid:
            # إيقاف سبام آيدي محدد
            if uid in spam_jobs:
                spam_jobs[uid].set()        # إرسال إشارة الإيقاف
                del spam_jobs[uid]
                remove_target_uid(uid)       # تنظيف الملف
                return jsonify({"message": f"تم إيقاف السبام للآيدي {uid}"})
            else:
                return jsonify({"error": "لا يوجد سبام نشط لهذا الآيدي"}), 404
        else:
            # إيقاف جميع مهام السبام
            if not spam_jobs:
                return jsonify({"message": "لا توجد مهام سبام نشطة"})
            for job_uid, event in list(spam_jobs.items()):
                event.set()
                remove_target_uid(job_uid)
            spam_jobs.clear()
            return jsonify({"message": "تم إيقاف جميع مهام السبام"})

@app.route("/safe", methods=["GET"])
def add_safe():
    api_key = request.args.get("key")
    uid = request.args.get("uid")

    if not api_key or api_key != API_KEY:
        return jsonify({"error": "مفتاح API غير صحيح"}), 403
    if not uid:
        return jsonify({"error": "يجب إدخال uid"}), 400

    # إضافة الآيدي إلى قائمة الحماية
    with protected_uids_lock:
        if uid in protected_uids:
            return jsonify({"message": "الآيدي محمي بالفعل"})
        protected_uids.add(uid)
        save_protected_uids()

    # إيقاف أي سبام نشط على هذا الآيدي تلقائياً
    with spam_jobs_lock:
        if uid in spam_jobs:
            spam_jobs[uid].set()
            del spam_jobs[uid]
            remove_target_uid(uid)

    return jsonify({"message": f"تمت إضافة الحماية للآيدي {uid}"})

@app.route("/")
def index():
    return jsonify({
        "service": "FF Friend Sender",
        "endpoints": {
            "/send_requests": "?uid=UID&region=REGION&key=FADAI (بدء سبام مستمر)",
            "/stop": "?key=CTX-TEAM [&uid=UID] (إيقاف السبام)",
            "/safe": "?uid=UID&key=FADAI (حماية آيدي من السبام)"
        },
        "regions": list(REGION_FILES.keys())
    })

if __name__ == "__main__":
    load_protected_uids()
    preload_all_tokens()
    threading.Thread(target=refresh_tokens_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=26080, threaded=True)