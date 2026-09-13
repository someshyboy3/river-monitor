import os
import csv
import io
import json
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CLIENT_ID = os.environ.get("CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "").strip()
GOOGLE_WEBAPP_URL = os.environ.get("GOOGLE_WEBAPP_URL", "").strip()

IOT_TOKEN_URL = "https://iot.wra.gov.tw/Oauth2/token"
IOT_STATIONS_URL = "https://iot.wra.gov.tw/river/stations"
# 正規的水利署 OpenData 警戒水位門檻 API
WARNING_LEVELS_URL = "https://opendata.wra.gov.tw/api/v2/39ad439a-f7aa-4fd4-b1a7-e4622852cc69?sort=_importdate%20asc&format=JSON"

def get_wra_token():
    """使用 Client ID / Secret 取得水利署 IoT 認證 Token"""
    try:
        payload = {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET
        }
        res = requests.post(IOT_TOKEN_URL, data=payload, timeout=15, verify=False)
        if res.status_code == 200:
            return res.json().get("access_token")
        else:
            print(f"❌ IoT Oauth2 認證失敗，HTTP 狀態碼：{res.status_code}")
    except Exception as e:
        print(f"❌ 取得 IoT Token 異常: {e}")
    return None

def sync_stations_to_sheet(stations_data):
    """將 API 測站資訊寫入 Google Sheet 字典"""
    if not GOOGLE_WEBAPP_URL:
        return
    dict_list = [[str(st.get("Name", "")).strip(), str(st.get("BasinName", "未知")).strip()] for st in stations_data if st.get("Name")]
    try:
        res = requests.post(GOOGLE_WEBAPP_URL, json=dict_list, timeout=15)
        if res.status_code == 200:
            print(f"🔄 已成功同步 {len(dict_list)} 個測站至試算表字典。")
    except Exception as e:
        print(f"⚠️ 同步測站字典失敗: {e}")

def get_user_targets():
    """讀取 Google Sheet 控制台設定"""
    targets = {}
    if not GOOGLE_SHEET_ID:
        return targets
    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=控制台"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            res.encoding = 'utf-8'
            csv_reader = csv.reader(io.StringIO(res.text))
            next(csv_reader, None)
            for row in csv_reader:
                if len(row) >= 3:
                    st_name = row[1].strip()
                    is_active = row[2].strip().upper()
                    if st_name and is_active == "YES":
                        c_l1 = float(row[3]) if len(row) > 3 and row[3].strip() else None
                        c_l2 = float(row[4]) if len(row) > 4 and row[4].strip() else None
                        c_l3 = float(row[5]) if len(row) > 5 and row[5].strip() else None
                        targets[st_name] = {"l1": c_l1, "l2": c_l2, "l3": c_l3}
    except Exception as e:
        print(f"⚠️ 讀取線上控制台失敗: {e}")
    return targets

def fetch_official_thresholds_from_iot(stations_data):
    """
    從 IoT API 回傳的測站詳細屬性中，直接解析官方定義的水位警戒門檻
    """
    thresholds = {}
    for st in stations_data:
        name = str(st.get("Name", "")).strip()
        if not name:
            continue
        
        l1, l2, l3 = None, None, None
        
        # 解析 IoT 測站屬性欄位 (Thresholds / AlarmLevels)
        for attr in st.get("Attributes", []):
            attr_name = str(attr.get("Name", "")).lower()
            val = attr.get("Value")
            try:
                num_val = float(val) if val is not None else None
                if "level1" in attr_name or "一級" in attr_name:
                    l1 = num_val
                elif "level2" in attr_name or "二級" in attr_name:
                    l2 = num_val
                elif "level3" in attr_name or "三級" in attr_name:
                    l3 = num_val
            except ValueError:
                pass

        if l1 or l2 or l3:
            thresholds[name] = {"l1": l1, "l2": l2, "l3": l3}
            
    return thresholds

def send_telegram_alert(message):
    """發送 Telegram 推播"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ 未設定 Telegram 金鑰，跳過推播。")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("🚨 警報已成功發送至 Telegram！")
        else:
            print(f"❌ Telegram 推播失敗: HTTP {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ 發送 Telegram 訊息異常: {e}")

def main():
    print("🚀 開始執行河川水位巡檢與警報核對...")
    
    # 1. 驗證身分並取得 Token
    token = get_wra_token()
    if not token:
        print("❌ 無法取得水利署 API Token，停止執行。")
        return
    print("✅ 成功透過 CLIENT_ID / CLIENT_SECRET 取得水利署 API 授權。")

    # 2. 從水利署 IoT 平台抓取完整的測站與屬性資料
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        res = requests.get(IOT_STATIONS_URL, headers=headers, timeout=20, verify=False)
        stations_data = res.json() if res.status_code == 200 else []
    except Exception as e:
        print(f"❌ 擷取 API 水位資料失敗: {e}")
        stations_data = []

    if not stations_data:
        print("ℹ️ 未取得任何即時測站資料。")
        return

    # 3. 同步字典至 Google Sheet
    sync_stations_to_sheet(stations_data)

    # 4. 讀取使用者設定
    target_config = get_user_targets()
    if not target_config:
        print("ℹ️ 目前沒有開啟任何監控目標。")
        return

    # 5. 直接從授權的 IoT API 抽取官方門檻
    official_thresholds = fetch_official_thresholds_from_iot(stations_data)
    print(f"🌐 成功從 IoT 授權 API 提取 {len(official_thresholds)} 個測站的官方警戒門檻。")

    realtime_map = {str(item.get("Name", "")).strip(): item for item in stations_data}

    # 6. 比對水位與發送警報
    for st_name, custom_cfg in target_config.items():
        st_data = realtime_map.get(st_name)
        if not st_data:
            print(f"❓ 找不到對應測站 [{st_name}]")
            continue

        water_level, record_time = None, "未知"
        for m in st_data.get("Measurements", []):
            if "水位" in str(m.get("Name", "")) or "water" in str(m.get("FullName", "")).lower():
                val = m.get("Value")
                if val is not None and float(val) > -900:
                    water_level = float(val)
                    record_time = m.get("TimeStamp", "未知")
                    break

        if water_level is None:
            continue

        # 優先採用 Google Sheet 自訂門檻，若無自訂則採用 IoT API 提供的官方門檻
        off_cfg = official_thresholds.get(st_name, {})
        l1 = custom_cfg.get("l1") if custom_cfg.get("l1") is not None else off_cfg.get("l1")
        l2 = custom_cfg.get("l2") if custom_cfg.get("l2") is not None else off_cfg.get("l2")
        l3 = custom_cfg.get("l3") if custom_cfg.get("l3") is not None else off_cfg.get("l3")

        alert_level = None
        if l1 and water_level >= l1:
            alert_level = "🔴 一級警戒 (極高危險)"
        elif l2 and water_level >= l2:
            alert_level = "🟠 二級警戒 (高危險)"
        elif l3 and water_level >= l3:
            alert_level = "🟡 三級警戒 (注意)"

        print(f"📊 [{st_name}] 水位：{water_level:.3f} m | 門檻 (三/二/一級): {l3}/{l2}/{l1} | 狀態: {alert_level or '🟢 正常'}")

        if alert_level:
            msg = (
                f"🚨 *【水位警戒通知】*\n\n"
                f"📍 *測站名稱*：{st_name}\n"
                f"⚠️ *警戒狀態*：{alert_level}\n"
                f"🌊 *當前水位*：`{water_level:.3f}` m\n"
                f"📏 *警戒門檻*：\n"
                f"  • 三級：{l3 or '未設定'} m\n"
                f"  • 二級：{l2 or '未設定'} m\n"
                f"  • 一級：{l1 or '未設定'} m\n"
                f"🕒 *更新時間*：{record_time}"
            )
            send_telegram_alert(msg)

if __name__ == "__main__":
    main()
