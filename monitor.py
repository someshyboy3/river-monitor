import os
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 1. 從環境變數 (Secrets) 讀取金鑰 ---
CLIENT_ID = os.environ.get("CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# 監控目標測站
TARGET_STATIONS = ["美濃橋", "旗山橋"]

IOT_TOKEN_URL = "https://iot.wra.gov.tw/Oauth2/token"
IOT_STATIONS_URL = "https://iot.wra.gov.tw/river/stations"
WARNING_LEVELS_URL = "https://opendata.wra.gov.tw/api/v2/39ad439a-f7aa-4fd4-b1a7-e4622852cc69?sort=_importdate%20asc&format=JSON"

# --- 2. Telegram 直接推播 ---
def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ 未設定 TELEGRAM 金鑰，跳過推播。")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("✅ Telegram 警報發送成功！")
        else:
            print(f"⚠️ Telegram 發送失敗，狀態碼：{res.status_code}")
    except Exception as e:
        print(f"❌ 發送 Telegram 出錯：{e}")

# --- 3. 水利署 API 連線與解析 ---
def get_wra_token():
    try:
        payload = {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET
        }
        res = requests.post(IOT_TOKEN_URL, data=payload, timeout=15, verify=False)
        if res.status_code == 200:
            return res.json().get("access_token")
    except Exception as e:
        print(f"取得 Token 失敗: {e}")
    return None

def fetch_warning_levels():
    try:
        res = requests.get(WARNING_LEVELS_URL, timeout=10, verify=False)
        if res.status_code == 200:
            data = res.json()
            raw_list = data if isinstance(data, list) else data.get("data", [])
            mapping = {}
            for item in raw_list:
                sname = item.get("StationName") or item.get("stationName")
                if sname:
                    mapping[sname] = {
                        "level1": float(item.get("WarningLevel1")) if item.get("WarningLevel1") else None,
                        "level2": float(item.get("WarningLevel2")) if item.get("WarningLevel2") else None,
                        "level3": float(item.get("WarningLevel3")) if item.get("WarningLevel3") else None,
                    }
            return mapping
    except Exception as e:
        print(f"取得動態警戒水位失敗: {e}")
    return {}

def fetch_stations_data(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        res = requests.get(IOT_STATIONS_URL, headers=headers, timeout=20, verify=False)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"取得測站資料失敗: {e}")
    return []

def parse_water_level(station_item):
    measurements = station_item.get("Measurements", [])
    if not isinstance(measurements, list):
        return None, "未知"
    for m in measurements:
        name = str(m.get("Name", ""))
        if "水位" in name or "water" in str(m.get("FullName", "")).lower():
            val = m.get("Value")
            t_stamp = m.get("TimeStamp", "未知")
            try:
                if val is not None and float(val) > -900:
                    return float(val), t_stamp
            except Exception:
                pass
    return None, "未知"

# --- 4. 主流程 ---
def main():
    print("🚀 開始執行河川水位檢測...")
    token = get_wra_token()
    if not token:
        print("❌ 無法取得水利署 API 授權 Token，結束程式。")
        return

    warning_levels_db = fetch_warning_levels()
    stations_data = fetch_stations_data(token)

    if not stations_data:
        print("⚠️ 未取得任何測站即時資料。")
        return

    realtime_map = {str(item.get("Name", "")).strip(): item for item in stations_data}

    for st_name in TARGET_STATIONS:
        st_data = realtime_map.get(st_name)
        if not st_data:
            print(f"❓ 找不到測站 [{st_name}] 的即時資料。")
            continue

        water_level, record_time = parse_water_level(st_data)
        if water_level is None:
            print(f"⚠️ 測站 [{st_name}] 水位資料無效。")
            continue

        levels = warning_levels_db.get(st_name, {})
        l1, l2, l3 = levels.get("level1"), levels.get("level2"), levels.get("level3")

        print(f"📊 [{st_name}] 目前水位：{water_level:.3f} m (時間: {record_time})")

        alert_msg = ""
        if l1 and water_level >= l1:
            alert_msg = f"🚨 *【一級警戒警報】*\n測站：{st_name}\n當前水位：`{water_level:.3f}` m (一級門檻: {l1}m)\n時間：{record_time}"
        elif l2 and water_level >= l2:
            alert_msg = f"⚠️ *【二級警戒警報】*\n測站：{st_name}\n當前水位：`{water_level:.3f}` m (二級門檻: {l2}m)\n時間：{record_time}"
        elif l3 and water_level >= l3:
            alert_msg = f"⚡ *【三級警戒警報】*\n測站：{st_name}\n當前水位：`{water_level:.3f}` m (三級門檻: {l3}m)\n時間：{record_time}"

        if alert_msg:
            print(f"📢 觸發警報，發送 Telegram 推播...")
            send_telegram_alert(alert_msg)

if __name__ == "__main__":
    main()
