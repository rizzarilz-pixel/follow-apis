# app.py
import json
import requests
import urllib3
import gzip
import concurrent.futures
import os
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import blackboxprotobuf

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)

# Konstanta enkripsi
AeSkEy = b'Yg&tc%DEuh6%Zc^8'
AeSiV  = b'6oyZDr22E3ychjM%'

GAME_HEADERS = {
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; SM-S908E Build/TP1A.220624.014)",
    "X-GA": "v1 1",
    "X-Unity-Version": "2018.4.11f1",
    "ReleaseVersion": "OB54",
    "Content-Type": "application/octet-stream",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip",
}

TARGET_URL = "https://clientbp.ggpolarbear.com/Follow"
JWT_API = "https://freefiremax.pages.dev/GenJwt/token?key=raigenff&uid={uid}&password={password}"

def enc(d):
    return AES.new(AeSkEy, AES.MODE_CBC, AeSiV).encrypt(pad(d, 16))

def dec(d):
    try:
        return unpad(AES.new(AeSkEy, AES.MODE_CBC, AeSiV).decrypt(d), 16)
    except Exception:
        return d

def smart_encode(payload_dict):
    typedef = {}
    for key, value in payload_dict.items():
        if isinstance(value, int):
            typedef[str(key)] = {'type': 'int', 'name': ''}
        elif isinstance(value, str):
            typedef[str(key)] = {'type': 'bytes', 'name': ''}
        elif isinstance(value, dict):
            _, inner_typedef = blackboxprotobuf.decode_message(blackboxprotobuf.encode_message(value, {}))
            typedef[str(key)] = {'type': 'message', 'message_typedef': inner_typedef, 'name': ''}
        else:
            typedef[str(key)] = {'type': 'int', 'name': ''}
    return blackboxprotobuf.encode_message(payload_dict, typedef)

def get_jwt(uid, password):
    try:
        url = JWT_API.format(uid=uid, password=password)
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            return data.get("token")
    except:
        pass
    return None

def process_follow(account, target_uid):
    uid = str(account.get("uid", "")).strip()
    password = account.get("password", "").strip()

    if not uid or not password or uid == "0":
        return {"uid": uid, "success": False, "message": "Invalid account data"}

    jwt = get_jwt(uid, password)
    if not jwt:
        return {"uid": uid, "success": False, "message": "Failed to get JWT"}

    headers = GAME_HEADERS.copy()
    headers['Authorization'] = f"Bearer {jwt}"

    payload_dict = {"1": int(target_uid)}
    try:
        binary_payload = smart_encode(payload_dict)
        encrypted_req = enc(binary_payload)
        r = requests.post(TARGET_URL, headers=headers, data=encrypted_req, timeout=15, verify=False)

        if r.status_code == 200:
            is_success = True
            msg = ""
            try:
                decrypted = dec(r.content)
                if decrypted.startswith(b'\x1f\x8b'):
                    decrypted = gzip.decompress(decrypted)
                hex_length = len(decrypted.hex())
                if hex_length > 100:
                    is_success = True
                    msg = "Success (binary response)"
                else:
                    raw_text = decrypted.decode('utf-8', errors='ignore').strip()
                    filtered = ''.join(c for c in raw_text if c.isalpha())
                    if "BR_WORKSHOP_INSUFFICIENT_MAPS" in raw_text or "ACCOUNT_NOT_FOUND" in raw_text:
                        is_success = False
                        msg = raw_text[:200]
                    else:
                        is_success = True
                        msg = filtered if filtered and len(filtered) <= 100 else "Success"
            except Exception:
                is_success = True
                msg = "Success (decoding error ignored)"
            return {"uid": uid, "success": is_success, "message": msg}
        else:
            try:
                decrypted = dec(r.content)
                if decrypted.startswith(b'\x1f\x8b'):
                    decrypted = gzip.decompress(decrypted)
                err_text = decrypted.decode('utf-8', errors='ignore').strip()
                err_text = " ".join(err_text.split())
            except:
                err_text = "Unknown Error"
            return {"uid": uid, "success": False, "message": f"HTTP {r.status_code}: {err_text}"}
    except Exception as e:
        return {"uid": uid, "success": False, "message": f"Request exception: {str(e)}"}

def load_accounts_from_file():
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    ACCOUNTS_FILE = os.path.join(SCRIPT_DIR, "accounts.txt")

    if not os.path.exists(ACCOUNTS_FILE):
        return None

    accounts = []
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue
            uid = parts[0].strip()
            password = parts[1].strip()
            if uid and password and uid != "0":
                accounts.append({"uid": uid, "password": password})
    return accounts

# ─── ENDPOINT GET ───
@app.route('/api/cfollow', methods=['GET'])
def follow_endpoint():
    uid = request.args.get('uid')
    jumlahfollow_str = request.args.get('jumlahfollow')

    if not uid:
        return jsonify({"error": "Missing uid parameter"}), 400
    if not uid.isdigit():
        return jsonify({"error": "uid must be numeric"}), 400
    target_uid = int(uid)

    # Jumlah akun yang akan digunakan
    if jumlahfollow_str:
        if not jumlahfollow_str.isdigit():
            return jsonify({"error": "jumlahfollow must be numeric"}), 400
        jumlahfollow = int(jumlahfollow_str)
        if jumlahfollow <= 0:
            return jsonify({"error": "jumlahfollow must be positive"}), 400
    else:
        jumlahfollow = None

    accounts = load_accounts_from_file()
    if accounts is None:
        return jsonify({"error": "accounts.txt not found"}), 500
    if not accounts:
        return jsonify({"error": "No valid accounts in accounts.txt"}), 400

    # Batasi sesuai jumlahfollow
    if jumlahfollow is not None:
        if jumlahfollow > len(accounts):
            jumlahfollow = len(accounts)  # ambil semua jika lebih
        accounts = accounts[:jumlahfollow]

    total = len(accounts)
    max_workers = min(total, 20)  # batasi thread agar tidak overload

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_account = {
            executor.submit(process_follow, acc, target_uid): acc
            for acc in accounts
        }
        for future in concurrent.futures.as_completed(future_to_account):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                acc = future_to_account[future]
                results.append({
                    "uid": acc.get("uid"),
                    "success": False,
                    "message": f"Unexpected error: {str(e)}"
                })

    success_count = sum(1 for r in results if r.get("success"))
    failed_count = total - success_count

    return jsonify({
        "target_uid": target_uid,
        "total": total,
        "success": success_count,
        "failed": failed_count,
        "results": results
    })

@app.route('/', methods=['GET'])
def root():
    return jsonify({"status": "Free Fire Follow API is running", "endpoint": "/api/cfollow"})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)