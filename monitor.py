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

def sync_stations_to_sheet(stations_data):
    """自動將 API 全台測站清單寫入 Google Sheet 字典"""
    if not GOOGLE_WEBAPP_URL:
        return
    
    dict_list = []
    for st in stations_data:
        name = str(st.get("Name", "")).strip()
        basin = str(st.get("BasinName", "未知")).strip()
        if name:
            dict_list.append([name, basin])
    
    try:
        res = requests.post(GOOGLE_WEBAPP_URL, json=dict_list, timeout=15)
        if res.status_code == 200:
            print(f"🔄 已成功同步 {len(dict_list)} 個測站至試算表字典。")
    except Exception as e:
        print(f"⚠️ 同步測站字典失敗: {e}")

def get_user_targets():
    """
    從 Google Sheet 控制台讀取監控名單
    欄位結構：
    A欄: 流域 | B欄: 測站名稱 | C欄: 是否監控 (YES) | D欄: 一級門檻 | E欄: 二級門檻 | F欄: 三級門檻
    """
    targets = {}
    if not GOOGLE_SHEET_ID:
        return targets

    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=控制台"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            res.encoding = 'utf-8'
            csv_reader = csv.reader(io.StringIO(res.text))
            next(csv_reader, None)  # 跳過標題列
            
            for row in csv_reader:
                if len(row) >= 3:
                    st_name = row[1].strip()         # B 欄：測站名稱
                    is_active = row[2].strip().upper() # C 欄：是否監控
                    
                    if st_name and is_active == "YES":
                        c_l1 = float(row[3]) if len(row) > 3 and row[3].strip() else None
                        c_l2 = float(row[4]) if len(row) > 4 and row[4].strip() else None
                        c_l3 = float(row[5]) if len(row) > 5 and row[5].strip() else None
                        targets[st_name] = {"l1": c_l1, "l2": c_l2, "l3": c_l3}
    except Exception as e:
        print(f"⚠️ 讀取線上控制台失敗: {e}")

    return targets

def send_telegram_alert(message):
    """發送警報推播至 Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ 發送 Telegram 訊息失敗: {e}")

def get_wra_token():
    """取得水利署 API Token"""
    try:
        payload = {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
        res = requests.post(IOT_TOKEN_URL, data=payload, timeout=15, verify=False)
        if res.status_code == 200:
            return res.json().get("access_token")
    except Exception:
        pass
    return None

def main():
    print("🚀 開始執行河川水位巡檢...")
    
    token = get_wra_token()
    if not token:
        print("❌ 無法取得水利署 API Token")
        return

    # 1. 抓取 API 即時資料
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

    # 2. 自動更新線上測站字典
    sync_stations_to_sheet(stations_data)

    # 3. 讀取使用者設定的監控名單
    target_config = get_user_targets()
    if not target_config:
        print("ℹ️ 目前沒有開啟任何監控目標。")
        return

    print(f"📋 已載入 {len(target_config)} 個監控測站：{list(target_config.keys())}")

    # 4. 比對與水位監控
    realtime_map = {str(item.get("Name", "")).strip(): item for item in stations_data}

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

        if water_level is not None:
            print(f"📊 [{st_name}] 當前水位：{water_level:.3f} m (更新時間: {record_time})")

if __name__ == "__main__":
    main()
