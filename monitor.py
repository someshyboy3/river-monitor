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
WARNING_LEVELS_URL = "https://opendata.wra.gov.tw/api/v2/39ad439a-f7aa-4fd4-b1a7-e4622852cc69?sort=_importdate%20asc&format=JSON"

# --- 1. 自動同步 API 測站清單至 Google Sheet ---
def sync_stations_to_sheet(stations_data):
    if not GOOGLE_WEBAPP_URL:
        print("⚠️ 提示：未設定 GOOGLE_WEBAPP_URL，跳過同步測站字典至 Google Sheet。")
        return
    
    dict_list = []
    for st in stations_data:
        name = str(st.get("Name", "")).strip()
        basin = str(st.get("BasinName", "未知")).strip()
        if name:
            dict_list.append([name, basin])
    
    print(f"🔄 準備推送 {len(dict_list)} 筆測站至 Google Sheet...")
    try:
        res = requests.post(GOOGLE_WEBAPP_URL, json=dict_list, timeout=15)
        print(f"📡 Google WebApp 回應狀態碼：{res.status_code}")
        print(f"📡 Google WebApp 回應內容：{res.text}")
        if res.status_code == 200:
            print(f"✅ 成功將 {len(dict_list)} 個測站同步至 Google 試算表字典！")
    except Exception as e:
        print(f"❌ 同步測站字典至 Google Sheet 失敗: {e}")

# --- 2. 讀取 Google Sheet 控制台設定 ---
def get_user_targets():
    targets = {}
    if not GOOGLE_SHEET_ID:
        print("⚠️ 未設定 GOOGLE_SHEET_ID！")
        return targets

    # 嘗試兩種網址解析模式（gviz CSV 與 pub CSV）
    urls = [
        f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=控制台",
        f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/export?format=csv&sheet=控制台"
    ]

    for idx, url in enumerate(urls, 1):
        try:
            print(f"🔍 嘗試讀取 Google Sheet [方式 {idx}]...")
            res = requests.get(url, timeout=10)
            print(f"📄 HTTP 狀態碼: {res.status_code}")
            
            if res.status_code == 200:
                res.encoding = 'utf-8'
                csv_text = res.text.strip()
                print(f"📝 抓取到的前 100 個字元內容：\n{csv_text[:100]}\n---")
                
                # 檢查是否下載到 HTML 登入頁面（代表權限沒開）
                if "<html" in csv_text.lower() or "google.com/accounts" in csv_text.lower():
                    print("❌ 錯誤：存取被拒絕！請確認 Google 試算表存取權已改為『知道連結的人皆可檢視』。")
                    continue

                csv_reader = csv.reader(io.StringIO(csv_text))
                header = next(csv_reader, None)
                print(f"📋 解析出的表格標題列: {header}")

                for row in csv_reader:
                    if len(row) >= 2:
                        st_name = row[0].strip()
                        is_active = row[1].strip().upper()
                        if st_name and is_active == "YES":
                            c_l1 = float(row[2]) if len(row) > 2 and row[2].strip() else None
                            c_l2 = float(row[3]) if len(row) > 3 and row[3].strip() else None
                            c_l3 = float(row[4]) if len(row) > 4 and row[4].strip() else None
                            targets[st_name] = {"l1": c_l1, "l2": c_l2, "l3": c_l3}
                
                if targets:
                    print(f"✅ 成功載入 {len(targets)} 個監控目標: {list(targets.keys())}")
                    break
        except Exception as e:
            print(f"⚠️ 讀取方式 {idx} 發生異常: {e}")

    # 保底防護機制
    if not targets:
        print("💡 線上控制台未能成功啟用任何測站，自動啟動【保底監控】：美濃橋、旗山橋")
        targets = {"美濃橋": {}, "旗山橋": {}}

    return targets

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ 發送 Telegram 出錯: {e}")

def get_wra_token():
    try:
        payload = {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
        res = requests.post(IOT_TOKEN_URL, data=payload, timeout=15, verify=False)
        if res.status_code == 200:
            return res.json().get("access_token")
    except Exception:
        pass
    return None

def main():
    print("🚀 開始執行河川水位檢測...")
    
    token = get_wra_token()
    if not token:
        print("❌ 無法取得水利署 API Token")
        return

    # 1. 抓取 API 即時資料
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        res = requests.get(IOT_STATIONS_URL, headers=headers, timeout=20, verify=False)
        stations_data = res.json() if res.status_code == 200 else []
    except Exception:
        stations_data = []

    print(f"🌐 成功從水利署 API 取得 {len(stations_data)} 個即時測站資料。")

    # 2. 自動同步測站清單至 Google Sheet 字典
    sync_stations_to_sheet(stations_data)

    # 3. 讀取 Google Sheet 控制台
    target_config = get_user_targets()

    # 4. 執行監控與水位列印
    realtime_map = {str(item.get("Name", "")).strip(): item for item in stations_data}

    for st_name, custom_cfg in target_config.items():
        st_data = realtime_map.get(st_name)
        if not st_data:
            print(f"❓ API 資料中找不到測站 [{st_name}]")
            continue

        water_level, record_time = None, "未知"
        for m in st_data.get("Measurements", []):
            if "水位" in str(m.get("Name", "")) or "water" in str(m.get("FullName", "")).lower():
                val = m.get("Value")
                if val is not None and float(val) > -900:
                    water_level = float(val)
                    record_time = m.get("TimeStamp", "未知")
                    break

        if water_level is not None:
            print(f"📊 [{st_name}] 當前水位：{water_level:.3f} m (時間: {record_time})")

if __name__ == "__main__":
    main()
