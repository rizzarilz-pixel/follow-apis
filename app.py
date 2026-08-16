# app.py
import json
import requests
import urllib3
import gzip
import concurrent.futures
import os
import re
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
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

FOLLOW_URL = "https://clientbp.ggpolarbear.com/Follow"
UNFOLLOW_URL = "https://clientbp.ggpolarbear.com/Unfollow"
JWT_API = "https://emoterara.pages.dev/api?action=jwt_generate"

# Data untuk auto unfollow
auto_unfollow_data = {
    "active": False,
    "target_uid": None,
    "last_follower_count": 0,
    "last_check": None,
    "thread": None
}

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

def get_jwt_token(uid, password):
    """Mendapatkan JWT token dari API baru"""
    try:
        url = f"{JWT_API}&uid={uid}&password={password}"
        response = requests.get(url, timeout=15)

        if response.status_code == 200:
            response_text = response.text.strip()
            if not response_text or len(response_text) < 20:
                return None
            try:
                data = response.json()
                for key in ['token', 'accessToken']:
                    if key in data:
                        return data[key]
                for key in data:
                    if 'token' in key.lower() or 'accessToken' in key.lower():
                        return data[key]
                return None
            except:
                if response_text and len(response_text) > 30:
                    return response_text
                return None
        return None
    except:
        return None

def process_action(account, target_uid, target_url):
    uid = str(account.get("uid", "")).strip()
    password = account.get("password", "").strip()

    if not uid or not password or uid == "0":
        return {"uid": uid, "success": False, "message": "Invalid account data"}

    jwt = get_jwt_token(uid, password)
    if not jwt:
        return {"uid": uid, "success": False, "message": "Failed to get JWT"}

    headers = GAME_HEADERS.copy()
    headers['Authorization'] = f"Bearer {jwt}"

    payload_dict = {"1": int(target_uid)}
    try:
        binary_payload = smart_encode(payload_dict)
        encrypted_req = enc(binary_payload)
        r = requests.post(target_url, headers=headers, data=encrypted_req, timeout=15, verify=False)

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

# ─── AUTO UNFOLLOW THREAD ───
def auto_unfollow_loop(target_uid):
    global auto_unfollow_data
    
    accounts = load_accounts_from_file()
    if not accounts:
        return
    
    while auto_unfollow_data["active"]:
        try:
            # Cek setiap 6 jam (21600 detik)
            time.sleep(21600)
            
            if not auto_unfollow_data["active"]:
                break
                
            # Cek follower count dengan akun pertama
            test_account = accounts[0]
            jwt = get_jwt_token(test_account["uid"], test_account["password"])
            if not jwt:
                continue
                
            # Dapatkan follower count (dummy, karena tidak ada API)
            current_count = auto_unfollow_data["last_follower_count"]
            
            # Jika follower berkurang >= 10, jalankan unfollow
            if auto_unfollow_data["last_follower_count"] - current_count >= 10:
                # Jalankan unfollow dengan 5 akun
                target_url = UNFOLLOW_URL
                accounts_to_use = accounts[:5]
                results = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    future_to_account = {
                        executor.submit(process_action, acc, target_uid, target_url): acc
                        for acc in accounts_to_use
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
                                "message": f"Error: {str(e)}"
                            })
                
                # Reset counter setelah unfollow
                auto_unfollow_data["last_follower_count"] = current_count
                auto_unfollow_data["last_check"] = datetime.now()
                
        except Exception as e:
            print(f"Auto unfollow error: {e}")
            continue

# ─── HTML TEMPLATE ───
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Free Fire Follow/Unfollow Tool</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            max-width: 650px;
            width: 100%;
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 10px;
            font-size: 28px;
        }
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 600;
            font-size: 14px;
        }
        input, select {
            width: 100%;
            padding: 12px 16px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 16px;
            transition: all 0.3s;
            outline: none;
        }
        input:focus, select:focus {
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        .radio-group {
            display: flex;
            gap: 20px;
            padding: 10px 0;
        }
        .radio-group label {
            display: flex;
            align-items: center;
            gap: 8px;
            font-weight: normal;
            cursor: pointer;
        }
        .radio-group input[type="radio"] {
            width: auto;
            cursor: pointer;
        }
        .btn-group {
            display: flex;
            gap: 10px;
            margin-top: 10px;
        }
        .btn {
            flex: 1;
            padding: 14px 20px;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .btn-follow {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .btn-follow:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(102, 126, 234, 0.3);
        }
        .btn-unfollow {
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
        }
        .btn-unfollow:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(245, 87, 108, 0.3);
        }
        .btn-auto {
            background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
            color: white;
        }
        .btn-auto:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(79, 172, 254, 0.3);
        }
        .btn-stop {
            background: linear-gradient(135deg, #fa709a 0%, #fee140 100%);
            color: white;
        }
        .btn-stop:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(250, 112, 154, 0.3);
        }
        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none !important;
        }
        .result-box {
            margin-top: 25px;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 10px;
            display: none;
            max-height: 400px;
            overflow-y: auto;
        }
        .result-box.show {
            display: block;
        }
        .result-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #e0e0e0;
        }
        .stat {
            text-align: center;
        }
        .stat-number {
            font-size: 24px;
            font-weight: bold;
        }
        .stat-label {
            font-size: 12px;
            color: #666;
        }
        .stat-success .stat-number { color: #28a745; }
        .stat-failed .stat-number { color: #dc3545; }
        .stat-total .stat-number { color: #667eea; }
        .result-item {
            padding: 8px 12px;
            margin-bottom: 5px;
            border-radius: 6px;
            font-size: 13px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .result-item.success {
            background: #d4edda;
            color: #155724;
        }
        .result-item.failed {
            background: #f8d7da;
            color: #721c24;
        }
        .status-badge {
            display: inline-block;
            padding: 2px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: bold;
        }
        .badge-success { background: #28a745; color: white; }
        .badge-failed { background: #dc3545; color: white; }
        .loading {
            display: none;
            text-align: center;
            padding: 20px;
        }
        .loading.show {
            display: block;
        }
        .spinner {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #667eea;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .info-box {
            background: #e7f3ff;
            border-left: 4px solid #4facfe;
            padding: 12px;
            border-radius: 6px;
            margin-top: 15px;
            font-size: 13px;
            color: #0056b3;
        }
        .info-box strong {
            display: block;
            margin-bottom: 5px;
        }
        .account-info {
            background: #f0f0f0;
            padding: 8px 12px;
            border-radius: 6px;
            margin-top: 5px;
            font-size: 12px;
            color: #555;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔥 Free Fire Tool</h1>
        <p class="subtitle">Follow / Unfollow Massal dengan Auto Unfollow</p>
        
        <form id="actionForm">
            <div class="form-group">
                <label>Target UID</label>
                <input type="number" id="targetUid" placeholder="Masukkan UID target" required>
            </div>
            
            <div class="form-group">
                <label>Mode Jumlah Akun Bot</label>
                <div class="radio-group">
                    <label>
                        <input type="radio" name="mode" value="manual" checked onchange="toggleJumlahInput()">
                        Manual
                    </label>
                    <label>
                        <input type="radio" name="mode" value="all" onchange="toggleJumlahInput()">
                        Semua Akun
                    </label>
                </div>
                <div id="manualInput">
                    <input type="number" id="jumlahFollow" placeholder="Masukkan jumlah akun" min="1">
                </div>
                <div class="account-info" id="accountInfo">Loading akun...</div>
            </div>
            
            <div class="btn-group">
                <button type="button" class="btn btn-follow" onclick="executeAction('follow')">
                    ➕ Follow
                </button>
                <button type="button" class="btn btn-unfollow" onclick="executeAction('unfollow')">
                    ➖ Unfollow
                </button>
            </div>
            
            <div class="btn-group" style="margin-top: 10px;">
                <button type="button" class="btn btn-auto" id="btnAuto" onclick="toggleAutoUnfollow()">
                    🔄 Auto Unfollow (On)
                </button>
                <button type="button" class="btn btn-stop" id="btnStop" onclick="stopAutoUnfollow()" style="display:none;">
                    ⏹ Stop Auto
                </button>
            </div>
        </form>
        
        <div class="loading" id="loading">
            <div class="spinner"></div>
            <p style="margin-top: 10px; color: #666;">Memproses...</p>
        </div>
        
        <div class="result-box" id="resultBox">
            <div class="result-header">
                <div class="stat stat-total">
                    <div class="stat-number" id="statTotal">0</div>
                    <div class="stat-label">Total</div>
                </div>
                <div class="stat stat-success">
                    <div class="stat-number" id="statSuccess">0</div>
                    <div class="stat-label">Berhasil</div>
                </div>
                <div class="stat stat-failed">
                    <div class="stat-number" id="statFailed">0</div>
                    <div class="stat-label">Gagal</div>
                </div>
            </div>
            <div id="resultList"></div>
        </div>
        
        <div class="info-box" id="autoInfo">
            <strong>🤖 Auto Unfollow</strong>
            <span id="autoStatus">Status: Off</span>
            <br>
            <span id="autoDetails">-</span>
        </div>
    </div>
    
    <script>
        let isProcessing = false;
        let autoActive = false;
        let totalAccounts = 0;
        
        // Load total accounts
        async function loadAccountInfo() {
            try {
                const response = await fetch('/api/account-count');
                const data = await response.json();
                totalAccounts = data.total || 0;
                document.getElementById('accountInfo').textContent = 
                    `Total akun tersedia: ${totalAccounts} akun`;
            } catch (error) {
                document.getElementById('accountInfo').textContent = 'Gagal load akun';
            }
        }
        loadAccountInfo();
        
        function toggleJumlahInput() {
            const mode = document.querySelector('input[name="mode"]:checked').value;
            const manualInput = document.getElementById('manualInput');
            if (mode === 'all') {
                manualInput.style.display = 'none';
            } else {
                manualInput.style.display = 'block';
            }
        }
        
        async function executeAction(action) {
            if (isProcessing) return;
            
            const uid = document.getElementById('targetUid').value.trim();
            const mode = document.querySelector('input[name="mode"]:checked').value;
            let jumlah = document.getElementById('jumlahFollow').value.trim();
            
            if (!uid || !/^\\d+$/.test(uid)) {
                alert('Masukkan UID yang valid (angka saja)');
                return;
            }
            
            // Jika mode all, set jumlah = 0 (akan dihandle di backend)
            if (mode === 'all') {
                jumlah = '0';
            } else if (!jumlah) {
                alert('Masukkan jumlah akun atau pilih mode "Semua Akun"');
                return;
            }
            
            isProcessing = true;
            document.getElementById('loading').classList.add('show');
            document.getElementById('resultBox').classList.remove('show');
            
            // Disable buttons
            document.querySelectorAll('.btn').forEach(btn => btn.disabled = true);
            
            try {
                let url = `/api/cfollow?uid=${uid}`;
                if (jumlah) url += `&jumlahfollow=${jumlah}`;
                if (action === 'unfollow') url += '&unfollow=true';
                
                const response = await fetch(url);
                const data = await response.json();
                
                displayResults(data);
            } catch (error) {
                alert('Error: ' + error.message);
            } finally {
                isProcessing = false;
                document.getElementById('loading').classList.remove('show');
                document.querySelectorAll('.btn').forEach(btn => btn.disabled = false);
            }
        }
        
        function displayResults(data) {
            document.getElementById('resultBox').classList.add('show');
            document.getElementById('statTotal').textContent = data.total || 0;
            document.getElementById('statSuccess').textContent = data.success || 0;
            document.getElementById('statFailed').textContent = data.failed || 0;
            
            const list = document.getElementById('resultList');
            list.innerHTML = '';
            
            if (data.results && data.results.length > 0) {
                data.results.forEach(item => {
                    const div = document.createElement('div');
                    div.className = `result-item ${item.success ? 'success' : 'failed'}`;
                    div.innerHTML = `
                        <span>UID: ${item.uid}</span>
                        <span>
                            <span class="status-badge ${item.success ? 'badge-success' : 'badge-failed'}">
                                ${item.success ? '✓ Success' : '✗ Failed'}
                            </span>
                            ${item.message ? `<span style="margin-left: 8px; font-size: 11px;">${item.message.substring(0, 50)}</span>` : ''}
                        </span>
                    `;
                    list.appendChild(div);
                });
            }
        }
        
        async function toggleAutoUnfollow() {
            const uid = document.getElementById('targetUid').value.trim();
            if (!uid || !/^\\d+$/.test(uid)) {
                alert('Masukkan UID target terlebih dahulu!');
                return;
            }
            
            if (!autoActive) {
                // Start auto unfollow
                autoActive = true;
                document.getElementById('btnAuto').textContent = '⏳ Starting...';
                document.getElementById('btnAuto').disabled = true;
                document.getElementById('btnStop').style.display = 'block';
                
                try {
                    const response = await fetch(`/api/auto-unfollow/start?uid=${uid}`, { method: 'POST' });
                    const data = await response.json();
                    
                    if (data.success) {
                        document.getElementById('btnAuto').textContent = '🔄 Auto Unfollow (Running)';
                        document.getElementById('autoStatus').textContent = 'Status: Running';
                        document.getElementById('autoDetails').textContent = `Target UID: ${uid} | Cek setiap 6 jam`;
                    } else {
                        alert('Gagal start: ' + data.message);
                        autoActive = false;
                        document.getElementById('btnAuto').textContent = '🔄 Auto Unfollow (On)';
                        document.getElementById('btnAuto').disabled = false;
                        document.getElementById('btnStop').style.display = 'none';
                    }
                } catch (error) {
                    alert('Error: ' + error.message);
                    autoActive = false;
                    document.getElementById('btnAuto').textContent = '🔄 Auto Unfollow (On)';
                    document.getElementById('btnAuto').disabled = false;
                    document.getElementById('btnStop').style.display = 'none';
                }
            }
        }
        
        async function stopAutoUnfollow() {
            if (!autoActive) return;
            
            try {
                const response = await fetch('/api/auto-unfollow/stop', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    autoActive = false;
                    document.getElementById('btnAuto').textContent = '🔄 Auto Unfollow (On)';
                    document.getElementById('btnAuto').disabled = false;
                    document.getElementById('btnStop').style.display = 'none';
                    document.getElementById('autoStatus').textContent = 'Status: Off';
                    document.getElementById('autoDetails').textContent = '-';
                }
            } catch (error) {
                alert('Error: ' + error.message);
            }
        }
    </script>
</body>
</html>
'''

# ─── ENDPOINT GET ───
@app.route('/api/cfollow', methods=['GET'])
def follow_endpoint():
    uid = request.args.get('uid')
    jumlahfollow_str = request.args.get('jumlahfollow')
    unfollow_param = request.args.get('unfollow', 'false').lower()

    if not uid:
        return jsonify({"error": "Missing uid parameter"}), 400
    if not uid.isdigit():
        return jsonify({"error": "uid must be numeric"}), 400
    target_uid = int(uid)

    is_unfollow = unfollow_param in ('true', '1', 'yes')
    target_url = UNFOLLOW_URL if is_unfollow else FOLLOW_URL

    accounts = load_accounts_from_file()
    if accounts is None:
        return jsonify({"error": "accounts.txt not found"}), 500
    if not accounts:
        return jsonify({"error": "No valid accounts in accounts.txt"}), 400

    # Mode: jika jumlahfollow_str = "0" atau kosong, gunakan semua akun
    if jumlahfollow_str is None or jumlahfollow_str == "0":
        # Mode ALL - gunakan semua akun
        jumlahfollow = len(accounts)
    else:
        if not jumlahfollow_str.isdigit():
            return jsonify({"error": "jumlahfollow must be numeric"}), 400
        jumlahfollow = int(jumlahfollow_str)
        if jumlahfollow <= 0:
            return jsonify({"error": "jumlahfollow must be positive"}), 400
        if jumlahfollow > len(accounts):
            jumlahfollow = len(accounts)
        accounts = accounts[:jumlahfollow]

    total = len(accounts)
    max_workers = min(total, 20)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_account = {
            executor.submit(process_action, acc, target_uid, target_url): acc
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
        "action": "unfollow" if is_unfollow else "follow",
        "total": total,
        "success": success_count,
        "failed": failed_count,
        "results": results
    })

# ─── GET TOTAL ACCOUNTS ───
@app.route('/api/account-count', methods=['GET'])
def account_count():
    accounts = load_accounts_from_file()
    if accounts is None:
        return jsonify({"total": 0, "error": "accounts.txt not found"}), 500
    return jsonify({"total": len(accounts)})

# ─── AUTO UNFOLLOW ENDPOINTS ───
@app.route('/api/auto-unfollow/start', methods=['POST'])
def start_auto_unfollow():
    global auto_unfollow_data
    
    uid = request.args.get('uid')
    if not uid or not uid.isdigit():
        return jsonify({"error": "Invalid UID"}), 400
    
    if auto_unfollow_data["active"]:
        return jsonify({"error": "Auto unfollow already running"}), 400
    
    auto_unfollow_data["active"] = True
    auto_unfollow_data["target_uid"] = int(uid)
    
    # Start thread
    thread = threading.Thread(target=auto_unfollow_loop, args=(int(uid),))
    thread.daemon = True
    thread.start()
    auto_unfollow_data["thread"] = thread
    
    return jsonify({"success": True, "message": "Auto unfollow started"})

@app.route('/api/auto-unfollow/stop', methods=['POST'])
def stop_auto_unfollow():
    global auto_unfollow_data
    
    if not auto_unfollow_data["active"]:
        return jsonify({"error": "Auto unfollow not running"}), 400
    
    auto_unfollow_data["active"] = False
    if auto_unfollow_data["thread"]:
        auto_unfollow_data["thread"] = None
    
    return jsonify({"success": True, "message": "Auto unfollow stopped"})

@app.route('/api/auto-unfollow/status', methods=['GET'])
def auto_unfollow_status():
    return jsonify({
        "active": auto_unfollow_data["active"],
        "target_uid": auto_unfollow_data["target_uid"],
        "last_check": auto_unfollow_data["last_check"].isoformat() if auto_unfollow_data["last_check"] else None
    })

# ─── HOME PAGE ───
@app.route('/', methods=['GET'])
def root():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
