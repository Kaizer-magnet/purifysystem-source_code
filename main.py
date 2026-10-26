# main.py -- AuraFlow OS Core Firmware for ESP32
import time
import json
import machine
from machine import Pin, ADC, SoftI2C, UART, WDT
from umqtt.simple import MQTTClient
import aht10
from ssd1306 import SSD1306_I2C

CONFIG_FILE = "sys_config.json"

# ================= 1. โหลดการตั้งค่า =================
def load_sys_config():
    default_cfg = {
        "mqtt_host": "o1c1f1d1.ala.asia-southeast1.emqxsl.com",
        "mqtt_port": 8883,
        "mqtt_user": "purifier_admin",
        "mqtt_pass": "Pass1234!",
        "client_id": "esp32_air_purifier",
        "topic_data": "air_purifier/data",
        "topic_cmd": "air_purifier/cmd",
        "filter_seconds": 0,
        "p_m": 35,   # PM2.5 Med threshold
        "p_h": 50,   # PM2.5 High threshold
        "m_m": 400,  # MQ135 Med threshold
        "m_h": 600,  # MQ135 High threshold
        "n_s": 22,   # Night Mode start hour (22:00)
        "n_e": 6     # Night Mode end hour (06:00)
    }
    try:
        with open(CONFIG_FILE, "r") as f:
            saved = json.load(f)
            for k, v in saved.items():
                default_cfg[k] = v
    except Exception as e:
        print("Config load notice:", e)
    return default_cfg

def save_sys_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
        print("Saved system config.")
    except Exception as e:
        print("Config save error:", e)

sys_cfg = load_sys_config()

# ================= 2. ฮาร์ดแวร์ พิน และ I2C =================
# I2C & OLED (SCL=22, SDA=21)
has_oled = False
has_aht10 = False
has_dht = False
i2c = None
oled = None
aht_sensor = None
dht_sensor = None

try:
    i2c = SoftI2C(scl=Pin(22), sda=Pin(21), freq=100000)
    i2c_devs = i2c.scan()
    print("I2C Bus scan (Pins 21/22):", [hex(d) for d in i2c_devs])

    if 0x3c in i2c_devs:
        try:
            oled = SSD1306_I2C(128, 64, i2c)
            oled.fill(0)
            oled.text("AuraFlow OS", 18, 16)
            oled.text("Starting...", 24, 34)
            oled.show()
            has_oled = True
            print("OLED (0x3C) ready.")
        except Exception as e:
            print("OLED init error:", e)
    else:
        print("Notice: OLED (0x3C) not detected on I2C bus.")

    if 0x38 in i2c_devs:
        try:
            aht_sensor = aht10.AHT10(i2c)
            has_aht10 = True
            print("AHT10 (0x38) Temp/Hum ready.")
        except Exception as e:
            print("AHT10 init error:", e)
    else:
        print("Notice: AHT10 (0x38) not detected on I2C bus.")
except Exception as e:
    print("I2C Bus error:", e)

# Fallback: ตรวจสอบเซ็นเซอร์ DHT11 บน Pin 4 (เผื่อต่อ DHT11 ไว้)
if not has_aht10:
    try:
        import dht
        dht_sensor = dht.DHT11(Pin(4))
        dht_sensor.measure()
        has_dht = True
        print("DHT11 detected on Pin 4 (using as Temp/Hum sensor).")
    except Exception:
        pass

# รีเลย์พัดลม 3 สปีด
# หากเป็นโมดูล Active LOW ให้ตั้ง RELAY_ACTIVE_LOW = True
RELAY_ACTIVE_LOW = True

RELAY_FAN_1 = Pin(27, Pin.OUT)  # LOW
RELAY_FAN_2 = Pin(26, Pin.OUT)  # MED
RELAY_FAN_3 = Pin(25, Pin.OUT)  # HIGH / TURBO

# Initialize relays to a safe OFF state at startup
def init_relays():
    # Ensure all relays are OFF when the system starts.
    # For active‑low hardware the OFF state is a HIGH output, otherwise LOW.
    if RELAY_ACTIVE_LOW:
        RELAY_FAN_1.value(1)
        RELAY_FAN_2.value(1)
        RELAY_FAN_3.value(1)
    else:
        RELAY_FAN_1.value(0)
        RELAY_FAN_2.value(0)
        RELAY_FAN_3.value(0)

# Call the initializer immediately after defining the pins
init_relays()

def set_relays(r1, r2, r3):
    if RELAY_ACTIVE_LOW:
        RELAY_FAN_1.value(0 if r1 else 1)
        RELAY_FAN_2.value(0 if r2 else 1)
        RELAY_FAN_3.value(0 if r3 else 1)
    else:
        RELAY_FAN_1.value(1 if r1 else 0)
        RELAY_FAN_2.value(1 if r2 else 0)
        RELAY_FAN_3.value(1 if r3 else 0)

# เซ็นเซอร์แก๊ส MQ135 (ADC Pin 34)
mq135_adc = ADC(Pin(34))
mq135_adc.atten(ADC.ATTN_11DB)

# เซ็นเซอร์ฝุ่น PM2.5 PMS7003 (UART2: TX=17, RX=16)
pms_uart = None
try:
    pms_uart = UART(2, baudrate=9600, tx=Pin(17), rx=Pin(16), timeout=300)
    print("PMS7003 UART ready on Pins 16/17.")
except Exception as e:
    print("PMS7003 UART init failed:", e)

# ================= 3. ตัวแปรสถานะระบบ =================
c_mode = "AUTO"       # โหมดที่ผู้ใช้สั่ง: AUTO, NIGHT, LOW, MED, HIGH, OFF
fan_state = "OFF"     # สถานะพัดลมจริงที่รีเลย์เปิด: OFF, LOW, MED, HIGH
timer_remaining_sec = 0  # ตัวนับเวลาถอยหลัง (วินาที)
MAX_FILTER_HOURS = 2500  # อายุไส้กรอง HEPA (2,500 ชม.)
filter_seconds = sys_cfg.get("filter_seconds", 0)

last_loop_time = time.time()
last_telemetry_time = 0
last_config_save_time = time.time()

# ================= 4. ฟังก์ชันควบคุมฮาร์ดแวร์ =================
def set_fan(speed):
    global fan_state
    fan_state = speed
    if speed == "LOW":
        set_relays(1, 0, 0)
    elif speed == "MED":
        set_relays(0, 1, 0)
    elif speed == "HIGH":
        set_relays(0, 0, 1)
    else: # OFF
        fan_state = "OFF"
        set_relays(0, 0, 0)

def read_pm25():
    """อ่านค่า PM2.5 จาก PMS7003 / PMSA003 ผ่าน UART"""
    if not pms_uart:
        return 15  # Fallback จำลองถ้าไม่มีเซ็นเซอร์
    try:
        if pms_uart.any():
            data = pms_uart.read()
            if data and len(data) >= 32:
                for i in range(len(data) - 31):
                    if data[i] == 0x42 and data[i + 1] == 0x4D:
                        frame = data[i:i + 32]
                        # Checksum
                        chk_calc = sum(frame[0:30])
                        chk_recv = (frame[30] << 8) | frame[31]
                        if chk_calc == chk_recv:
                            # Standard PM2.5 atmospheric concentration (ug/m3)
                            return (frame[12] << 8) | frame[13]
    except Exception as e:
        pass
    return None

last_pm25_cached = 15

def get_air_data():
    global last_pm25_cached
    # 1. PM2.5
    raw_pm = read_pm25()
    if raw_pm is not None:
        last_pm25_cached = raw_pm
    pm_val = last_pm25_cached

    # 2. MQ135 Gas
    try:
        mq_raw = mq135_adc.read()
    except:
        mq_raw = 180

    # 3. Temp & Hum (AHT10 หรือ DHT11)
    temp = 26.5
    hum = 55.0
    if has_aht10 and aht_sensor:
        try:
            temp = round(aht_sensor.temperature(), 1)
            hum = round(aht_sensor.humidity(), 1)
        except:
            pass
    elif has_dht and dht_sensor:
        try:
            dht_sensor.measure()
            temp = float(dht_sensor.temperature())
            hum = float(dht_sensor.humidity())
        except:
            pass

    return pm_val, mq_raw, temp, hum

def get_thai_time_str():
    # UTC + 7 hours = 25200 seconds
    t = time.localtime(time.time() + 25200)
    return "%02d:%02d:%02d" % t[3:6], t[3]

# ================= 5. MQTT PROTOCOL =================
TOPIC_DATA = sys_cfg["topic_data"].encode('utf-8')
TOPIC_CMD  = sys_cfg["topic_cmd"].encode('utf-8')

def on_message(topic, msg):
    global c_mode, timer_remaining_sec, filter_seconds, sys_cfg
    try:
        payload = json.loads(msg.decode('utf-8'))
        print("MQTT Received:", payload)

        # 1. เปลี่ยนโหมดการทำงาน
        if "mode" in payload:
            cmd = payload["mode"]
            if cmd == "RESET_FILTER":
                filter_seconds = 0
                sys_cfg["filter_seconds"] = 0
                save_sys_config(sys_cfg)
                print("Filter counter reset to 0.")
            elif cmd in ["AUTO", "NIGHT", "LOW", "MED", "HIGH", "OFF"]:
                c_mode = cmd
                print("Mode set to:", c_mode)

        # 2. ตั้งเวลาปิดเครื่อง (ชั่วโมง)
        if "timer" in payload:
            hrs = float(payload["timer"])
            timer_remaining_sec = int(hrs * 3600)
            print(f"Timer set: {hrs} hours ({timer_remaining_sec}s)")

        # 3. อัปเดตเกณฑ์ความไว (sys_config)
        if "sys_config" in payload:
            sc = payload["sys_config"]
            if "pm_med" in sc: sys_cfg["p_m"] = int(sc["pm_med"])
            if "pm_high" in sc: sys_cfg["p_h"] = int(sc["pm_high"])
            if "mq_med" in sc: sys_cfg["m_m"] = int(sc["mq_med"])
            if "mq_high" in sc: sys_cfg["m_h"] = int(sc["mq_high"])
            if "night_start" in sc: sys_cfg["n_s"] = int(sc["night_start"])
            if "night_end" in sc: sys_cfg["n_e"] = int(sc["night_end"])
            save_sys_config(sys_cfg)
            print("System threshold config updated.")

    except Exception as e:
        print("Command parse error:", e)

client = None

def init_mqtt():
    global client
    try:
        print("Connecting to MQTT Broker:", sys_cfg["mqtt_host"])
        c = MQTTClient(
            client_id=sys_cfg["client_id"],
            server=sys_cfg["mqtt_host"],
            port=sys_cfg["mqtt_port"],
            user=sys_cfg["mqtt_user"],
            password=sys_cfg["mqtt_pass"],
            ssl=True
        )
        c.set_callback(on_message)
        c.connect()
        c.subscribe(TOPIC_CMD)
        print("MQTT Connected & Subscribed to", sys_cfg["topic_cmd"])
        return c
    except Exception as e:
        print("MQTT Connect failed:", e)
        return None

client = init_mqtt()
wdt = WDT(timeout=30000) # Watchdog 30s

# เริ่มต้นระบบปิดพัดลม
set_fan("OFF")

print("AuraFlow OS Engine Running...")

# ================= 6. MAIN LOOP =================
while True:
    wdt.feed()
    now = time.time()
    dt = now - last_loop_time
    last_loop_time = now

    # A. ตรวจสอบการนับเวลาถอยหลัง (Timer Countdown)
    if timer_remaining_sec > 0:
        timer_remaining_sec -= int(dt)
        if timer_remaining_sec <= 0:
            timer_remaining_sec = 0
            c_mode = "OFF"
            set_fan("OFF")
            print("Timer completed. Switched to OFF mode.")

    # B. นับเวลาอายุไส้กรอง (นับเฉพาะตอนพัดลมทำงาน)
    if fan_state != "OFF":
        filter_seconds += dt
        # เซฟลง flash ทุกๆ 30 นาที
        if now - last_config_save_time > 1800:
            sys_cfg["filter_seconds"] = int(filter_seconds)
            save_sys_config(sys_cfg)
            last_config_save_time = now

    # C. เช็กคำสั่ง MQTT เข้า
    if client:
        try:
            client.check_msg()
        except Exception as e:
            print("MQTT check_msg error, attempting reconnect:", e)
            try:
                client.disconnect()
            except:
                pass
            client = init_mqtt()
    else:
        # พยายามต่อ MQTT ใหม่ทุก 10 วินาที
        if now - last_telemetry_time > 10:
            client = init_mqtt()

    # D. อ่านค่าเซ็นเซอร์
    pm25, mq_val, temp, hum = get_air_data()
    time_str, current_hour = get_thai_time_str()

    # E. ตรรกะควบคุมความเร็วพัดลม (Fan Logic Engine)
    target_speed = "OFF"
    is_quiet_active = False

    if c_mode == "OFF":
        target_speed = "OFF"
    elif c_mode == "LOW":
        target_speed = "LOW"
    elif c_mode == "MED":
        target_speed = "MED"
    elif c_mode == "HIGH":
        target_speed = "HIGH"
    elif c_mode == "NIGHT":
        # โหมดกลางคืน: ล็อกพัดลมระดับเงียบสุด (LOW)
        target_speed = "LOW"
        is_quiet_active = True
    elif c_mode == "AUTO":
        # ตรวจสอบช่วงเวลา Night Schedule อัตโนมัติ
        ns = sys_cfg.get("n_s", 22)
        ne = sys_cfg.get("n_e", 6)
        in_night_time = (current_hour >= ns or current_hour < ne) if ns > ne else (ns <= current_hour < ne)

        if in_night_time:
            target_speed = "LOW"
            is_quiet_active = True
        else:
            # ปรับตามค่า PM2.5 และก๊าซพิษ
            if pm25 >= sys_cfg["p_h"] or mq_val >= sys_cfg["m_h"]:
                target_speed = "HIGH"
            elif pm25 >= sys_cfg["p_m"] or mq_val >= sys_cfg["m_m"]:
                target_speed = "MED"
            else:
                target_speed = "LOW"

    set_fan(target_speed)

    # F. คำนวณอายุไส้กรองคงเหลือ (%)
    used_hours = filter_seconds / 3600.0
    filter_pct = max(0, min(100, int(100 - (used_hours / MAX_FILTER_HOURS * 100))))

    # ฟอร์แมตเวลา Timer ส่งเว็บ
    if timer_remaining_sec > 3600:
        timer_str = f"{round(timer_remaining_sec / 3600.0, 1)}h"
    elif timer_remaining_sec > 0:
        timer_str = f"{timer_remaining_sec // 60}m"
    else:
        timer_str = "OFF"

    # G. ส่ง Telemetry ข้อมูลขึ้น Web Dashboard (ทุก 2.5 วินาที)
    if now - last_telemetry_time >= 2.5:
        last_telemetry_time = now

        # ประเมินคุณภาพอากาศแบบเข้าใจง่าย
        if pm25 <= 15:
            st_text = "EXCELLENT"
            st_color = "#10b981" # เขียว
        elif pm25 <= 35:
            st_text = "GOOD"
            st_color = "#06b6d4" # ฟ้า
        elif pm25 <= 50:
            st_text = "MODERATE"
            st_color = "#f59e0b" # ส้ม
        else:
            st_text = "UNHEALTHY"
            st_color = "#ef4444" # แดง

        telemetry = {
            "time": time_str,
            "pm25": pm25,
            "temp": temp,
            "hum": hum,
            "mq135": mq_val,
            "f_mode": fan_state,
            "c_mode": c_mode,
            "st_text": st_text,
            "st_color": st_color,
            "filter_pct": filter_pct,
            "o_timer": timer_str,
            "q_timer": "RUNNING" if is_quiet_active else "OFF",
            "cfg": {
                "p_m": sys_cfg["p_m"],
                "p_h": sys_cfg["p_h"],
                "m_m": sys_cfg["m_m"],
                "m_h": sys_cfg["m_h"],
                "n_s": sys_cfg["n_s"],
                "n_e": sys_cfg["n_e"]
            }
        }

        if client:
            try:
                client.publish(TOPIC_DATA, json.dumps(telemetry))
            except Exception as e:
                print("Publish telemetry error:", e)

    # H. แสดงผลบนจอ OLED SSD1306
    if has_oled:
        try:
            if c_mode == "NIGHT" or (c_mode == "OFF"):
                # ในโหมดนอนหลับ หรือ ปิดเครื่อง ให้ดับไฟหน้าจอเพื่อไม่รบกวนสายตา
                oled.fill(0)
                if c_mode == "OFF":
                    oled.text("STANDBY / OFF", 12, 28)
                    oled.show()
                    time.sleep(1)
                oled.poweroff()
            else:
                oled.poweron()
                oled.fill(0)
                oled.text(f"PM2.5: {pm25} ug", 0, 0)
                oled.text(f"Fan: {fan_state} [{c_mode}]", 0, 16)
                oled.text(f"T:{temp}C  H:{hum}%", 0, 32)
                oled.text(f"Filter:{filter_pct}% {time_str[0:5]}", 0, 48)
                oled.show()
        except:
            pass

    time.sleep(0.5)
