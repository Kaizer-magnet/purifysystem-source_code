# boot.py -- Wi-Fi Management & Auto-Popup Captive Portal
import network
import socket
import time
import json
import machine
import ntptime

CONFIG_FILE = "sys_config.json"

DEFAULT_CONFIG = {
    "wifi_ssid": "",
    "wifi_pass": "",
    "mqtt_host": "o1c1f1d1.ala.asia-southeast1.emqxsl.com",
    "mqtt_port": 8883,
    "mqtt_user": "purifier_admin",
    "mqtt_pass": "Pass1234!",
    "client_id": "esp32_air_purifier",
    "topic_data": "air_purifier/data",
    "topic_cmd": "air_purifier/cmd",
    "filter_seconds": 0,
    "p_m": 35,
    "p_h": 50,
    "m_m": 400,
    "m_h": 600,
    "n_s": 22,
    "n_e": 6
}

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    except:
        return DEFAULT_CONFIG.copy()

def save_wifi_config(ssid, password):
    cfg = load_config()
    cfg["wifi_ssid"] = ssid
    cfg["wifi_pass"] = password
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
        print("Wi-Fi configuration saved to", CONFIG_FILE)
    except Exception as e:
        print("Failed to save config:", e)

def scan_networks(wlan):
    networks = []
    try:
        raw_list = wlan.scan()
        seen = set()
        for item in raw_list:
            ssid = item[0].decode('utf-8', 'ignore').strip()
            rssi = item[3]
            if ssid and ssid not in seen:
                seen.add(ssid)
                networks.append({"ssid": ssid, "rssi": rssi})
    except Exception as e:
        print("Scan error:", e)
    return networks

def start_captive_portal():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="AirPurifier_Setup", authmode=0)
    
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    networks = scan_networks(sta)
    
    print("\n=============================================")
    print("🚀 Auto-Popup Captive Portal Active!")
    print("เชื่อมต่อ Wi-Fi: AirPurifier_Setup")
    print("หน้าจอจะเด้งเปิดหน้าตั้งค่าขึ้นมาอัตโนมัติ")
    print("=============================================\n")
    
    # 1. DNS Server (พอร์ต 53 UDP) สำหรับหลอกอุปกรณ์ให้ชี้มาที่ 192.168.4.1
    dns_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns_sock.setblocking(False)
    dns_sock.bind(('', 53))

    # 2. Web Server (พอร์ต 80 TCP)
    web_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    web_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    web_sock.setblocking(False)
    web_sock.bind(('', 80))
    web_sock.listen(2)
    
    net_options = ""
    for net in networks:
        net_options += f'<option value="{net["ssid"]}">{net["ssid"]} ({net["rssi"]} dBm)</option>'

    html_template = """<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AuraFlow - Wi-Fi Setup</title>
<style>
* { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
body { background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
.card { background: #1e293b; padding: 26px; border-radius: 16px; width: 100%; max-width: 400px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; }
.logo { text-align: center; font-size: 36px; margin-bottom: 8px; }
h2 { text-align: center; margin: 0 0 6px; font-size: 20px; color: #38bdf8; }
p { text-align: center; color: #94a3b8; font-size: 13px; margin: 0 0 20px; }
label { display: block; font-size: 13px; color: #cbd5e1; margin-bottom: 6px; font-weight: 500; }
select, input[type=text], input[type=password] { width: 100%; padding: 12px; background: #0f172a; border: 1px solid #475569; border-radius: 8px; color: #fff; font-size: 15px; margin-bottom: 14px; outline: none; }
select:focus, input:focus { border-color: #38bdf8; }
.btn { width: 100%; padding: 14px; background: linear-gradient(135deg, #0284c7, #06b6d4); color: #fff; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; transition: 0.2s; margin-top: 6px; }
.btn:hover { opacity: 0.9; }
.hint { font-size: 12px; color: #64748b; text-align: center; margin-top: 14px; }
</style>
<script>
function selectSsid(val) {
    if(val) document.getElementById('ssid-input').value = val;
}
</script>
</head>
<body>
<div class="card">
<div class="logo">🍃</div>
<h2>AuraFlow Smart Air</h2>
<p>ตั้งค่าเชื่อมต่อ Wi-Fi สำหรับเครื่องฟอกอากาศ</p>
<form action="/save" method="GET">
"""
    if net_options:
        html_template += f"""<label>เลือก Wi-Fi ใกล้เคียง:</label>
<select onchange="selectSsid(this.value)">
<option value="">-- เลือก Wi-Fi หรือพิมพ์ด้านล่าง --</option>
{net_options}
</select>"""

    html_template += """<label>ชื่อ Wi-Fi บ้านของคุณ (SSID):</label>
<input type="text" id="ssid-input" name="ssid" placeholder="พิมพ์ชื่อ Wi-Fi 2.4GHz" required>
<label>รหัสผ่าน Wi-Fi:</label>
<input type="password" name="pass" placeholder="รหัสผ่าน (หากมี)">
<button type="submit" class="btn">บันทึกและเชื่อมต่อทันที</button>
</form>
<div class="hint">รองรับ Wi-Fi คลื่น 2.4GHz</div>
</div>
</body>
</html>"""

    while True:
        # A. จัดการ DNS Request (ส่ง IP 192.168.4.1 กลับไปทุกคำขอ)
        try:
            dns_data, dns_addr = dns_sock.recvfrom(1024)
            if dns_data:
                # สร้าง DNS ตอบกลับ
                reply = dns_data[:2] + b'\x81\x80' + dns_data[4:6] + dns_data[4:6] + b'\x00\x00\x00\x00'
                reply += dns_data[12:]
                reply += b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\xc0\xa8\x04\x01' # 192.168.4.1
                dns_sock.sendto(reply, dns_addr)
        except:
            pass

        # B. จัดการ Web Request (HTTP)
        try:
            conn, addr = web_sock.accept()
            conn.settimeout(1.5)
            req = conn.recv(1024).decode('utf-8', 'ignore')
            
            if '/save?' in req:
                params = req.split('/save?')[1].split(' HTTP')[0]
                pairs = params.split('&')
                ssid = ""
                password = ""
                for p in pairs:
                    if '=' in p:
                        k, v = p.split('=', 1)
                        if k == 'ssid': ssid = v.replace('+', ' ').replace('%20', ' ')
                        if k == 'pass': password = v.replace('+', ' ').replace('%20', ' ')
                
                # Unquote URL encoding
                for hex_val in ["21", "40", "23", "24", "25", "26", "2A", "2D", "5F"]:
                    ssid = ssid.replace("%" + hex_val, chr(int(hex_val, 16)))
                    password = password.replace("%" + hex_val, chr(int(hex_val, 16)))
                
                save_wifi_config(ssid, password)
                
                res = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
                res += f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
                <style>body{{background:#0f172a;color:#fff;font-family:sans-serif;text-align:center;padding:50px 20px;}} .box{{background:#1e293b;padding:30px;border-radius:16px;max-width:360px;margin:auto;}} h2{{color:#38bdf8;}}</style></head>
                <body><div class="box"><h2>✅ บันทึกสำเร็จ!</h2><p>เครื่องกำลังรีสตาร์ทเพื่อเชื่อมต่อกับ "{ssid}"...</p><p style="font-size:12px;color:#94a3b8;">กรุณาเชื่อมต่ออุปกรณ์ของคุณกลับสู่ Wi-Fi ปกติ</p></div></body></html>"""
                conn.send(res.encode('utf-8'))
                conn.close()
                time.sleep(2)
                machine.reset()
            elif "generate_204" in req or "gen_204" in req or "hotspot-detect" in req:
                # คำขอตรวจสอบเน็ตจาก Android และ Apple ให้ redirect เข้าหน้าตั้งค่าทันที
                res = "HTTP/1.1 302 Found\r\nLocation: http://192.168.4.1/\r\n\r\n"
                conn.send(res.encode('utf-8'))
                conn.close()
            else:
                res = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n" + html_template
                conn.send(res.encode('utf-8'))
                conn.close()
        except:
            pass

        time.sleep(0.01)

def sync_ntp_time():
    print("Synchronizing NTP time...")
    for _ in range(3):
        try:
            ntptime.host = "pool.ntp.org"
            ntptime.settime()
            print("NTP time synchronized!")
            return True
        except Exception as e:
            print("NTP sync attempt failed:", e)
            time.sleep(1)
    return False

def connect_wifi():
    cfg = load_config()
    ssid = cfg.get("wifi_ssid", "").strip()
    password = cfg.get("wifi_pass", "").strip()
    
    if not ssid:
        print("No Wi-Fi credentials found. Launching Captive Portal...")
        start_captive_portal()
        return

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if not wlan.isconnected():
        print(f"Connecting to Wi-Fi: '{ssid}'...")
        wlan.connect(ssid, password)
        timeout = 15
        while not wlan.isconnected() and timeout > 0:
            time.sleep(1)
            timeout -= 1
            print(".", end="")
        print("")
            
    if wlan.isconnected():
        print(f"Wi-Fi Connected successfully! IP: {wlan.ifconfig()[0]}")
        try:
            ap = network.WLAN(network.AP_IF)
            ap.active(False)
        except:
            pass
        sync_ntp_time()
    else:
        print("Failed to connect to Wi-Fi. Switching to Captive Portal...")
        start_captive_portal()

# เริ่มต้นระบบเชื่อมต่อ Wi-Fi
connect_wifi()
