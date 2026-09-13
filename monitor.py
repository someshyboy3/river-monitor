import os
import csv
import io
import json
import time
import requests
import urllib3

# 關閉不安全 HTTP 請求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 從環境變數讀取金鑰與基本設定
CLIENT_ID = os.environ.get("CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()  # 備用 Chat ID
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "").strip()
GOOGLE_WEBAPP_URL = os.environ.get("GOOGLE_WEBAPP_URL", "").strip()

IOT_TOKEN_URL = "https://iot.wra.gov.tw/Oauth2/token"
IOT_STATIONS_URL = "https://iot.wra.gov.tw/river/stations"
CACHE_FILE = "alert_cache.json"

# 同一等級警報重複推播的冷卻時間（單位：秒，預設 6 小時）
ALERT_COOLDOWN_SECONDS = 6 * 3600

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}

def load_alert_cache():
    """載入歷史警報快取記錄"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_alert_cache(cache_data):
    """儲存警報快取記錄至 alert_cache.json"""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 寫入警報快取失敗: {e}")

def sync_stations_to_sheet(stations_data):
    """將 API 抓到的全台測站字典同步至 Google 試算表"""
    if not GOOGLE_WEBAPP_URL:
        return
    dict_list = [[str(st.get("Name", "")).strip(), str(st.get("BasinName", "未知")).strip()] for st in stations_data if st.get("Name")]
    try:
        requests.post(GOOGLE_WEBAPP_URL, json=dict_list, headers=HTTP_HEADERS, timeout=15)
        print(f"🔄 已成功同步 {len(dict_list)} 個測站至試算表字典。")
    except Exception as e:
        print(f"⚠️ 同步測站字典失敗: {e}")

def get_user_targets():
    """從 Google 試算表『控制台』分頁讀取使用者自訂監控目標與門檻 (B, C, D, E, F 欄)"""
    targets = {}
    if not GOOGLE_SHEET_ID:
        return targets
    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=控制台"
    try:
        res = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if res.status_code == 200:
            res.encoding = 'utf-8'
            csv_reader = csv.reader(io.StringIO(res.text))
            next(csv_reader, None)  # 略過標頭列
            for row in csv_reader:
                if len(row) >= 3:
                    st_name = row[1].strip()  # B 欄
                    is_active = row[2].strip().upper()  # C 欄
                    if st_name and is_active == "YES":
                        c_l1 = float(row[3]) if len(row) > 3 and row[3].strip() else None  # D 欄
                        c_l2 = float(row[4]) if len(row) > 4 and row[4].strip() else None  # E 欄
                        c_l3 = float(row[5]) if len(row) > 5 and row[5].strip() else None  # F 欄
                        targets[st_name] = {"l1": c_l1, "l2": c_l2, "l3": c_l3}
    except Exception as e:
        print(f"⚠️ 讀取線上控制台監控目標失敗: {e}")
    return targets

def get_telegram_chat_ids():
    """從 Google 試算表『控制台』讀取 H, I, J 欄授權接收通知的 Telegram Chat ID 名單"""
    chat_ids = []
    if not GOOGLE_SHEET_ID:
        if TELEGRAM_CHAT_ID:
            chat_ids.append(TELEGRAM_CHAT_ID)
        return chat_ids

    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=控制台"
    try:
        res = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if res.status_code == 200:
            res.encoding = 'utf-8'
            csv_reader = csv.reader(io.StringIO(res.text))
            next(csv_reader, None)  # 略過標頭列
            for row in csv_reader:
                # H欄 = Index 7 (Chat ID)
                # I欄 = Index 8 (備註/姓名)
                # J欄 = Index 9 (啟用狀態 YES/NO)
                if len(row) >= 10:
                    cid = row[7].strip()
                    is_active = row[9].strip().upper()
                    if cid and is_active == "YES":
                        chat_ids.append(cid)
    except Exception as e:
        print(f"⚠️ 讀取 Telegram 通知名單失敗: {e}")

    # 如果試算表沒設定或讀取失敗，預設回退使用環境變數的 TELEGRAM_CHAT_ID
    if not chat_ids and TELEGRAM_CHAT_ID:
        chat_ids.append(TELEGRAM_CHAT_ID)

    return list(set(chat_ids))  # 去除重複 ID

def get_sheet_official_thresholds():
    """直接從 Google 試算表『官方警戒線』分頁讀取門檻資料"""
    thresholds = {}
    if not GOOGLE_SHEET_ID:
        return thresholds

    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=官方警戒線"
    try:
        res = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if res.status_code == 200:
            res.encoding = 'utf-8'
            csv_reader = csv.reader(io.StringIO(res.text))
            next(csv_reader, None)  # 略過第一列標頭
            
            for row in csv_reader:
                if len(row) >= 4:
                    st_name = row[0].strip()
                    if not st_name:
                        continue
                    
                    def parse_float(val):
                        try:
                            return float(val.strip()) if val and val.strip() else None
                        except ValueError:
                            return None

                    l3 = parse_float(row[1])
                    l2 = parse_float(row[2])
                    l1 = parse_float(row[3])
                    thresholds[st_name] = {"l1": l1, "l2": l2, "l3": l3}
            print(f"🌐 成功從試算表讀取 {len(thresholds)} 個官方警戒線資料。")
    except Exception as e:
        print(f"⚠️ 讀取試算表官方警戒線異常: {e}")
    return thresholds

def send_telegram_alert(message):
    """將警報訊息逐一推播給控制台 H/I/J 欄中開啟 YES 的所有用戶"""
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ 未設定 TELEGRAM_BOT_TOKEN，無法發送訊息。")
        return

    target_chat_ids = get_telegram_chat_ids()
    if not target_chat_ids:
        print("ℹ️ 目前控制台沒有啟用 (YES) 的 Telegram 接收者。")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    for cid in target_chat_ids:
        payload = {"chat_id": cid, "text": message, "parse_mode": "Markdown"}
        try:
            res = requests.post(url, json=payload, headers=HTTP_HEADERS, timeout=10)
            if res.status_code == 200:
                print(f"🚨 警報已成功發送至 Telegram Chat ID: [{cid}]")
            else:
                print(f"⚠️ 發送至 [{cid}] 失敗，HTTP 狀態碼: {res.status_code}")
        except Exception as e:
            print(f"❌ 發送至 [{cid}] 發生異常: {e}")

def get_wra_token():
    """取得水利署 API OAuth2 Bearer Token"""
    try:
        payload = {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
        res = requests.post(IOT_TOKEN_URL, data=payload, headers=HTTP_HEADERS, timeout=15, verify=False)
        if res.status_code == 200:
            return res.json().get("access_token")
    except Exception:
        pass
    return None

def main():
    print("🚀 開始執行河川水位巡檢與警報核對...")
    
    token = get_wra_token()
    if not token:
        print("❌ 無法取得水利署 API Token")
        return

    req_headers = HTTP_HEADERS.copy()
    req_headers["Authorization"] = f"Bearer {token}"

    try:
        res = requests.get(IOT_STATIONS_URL, headers=req_headers, timeout=20, verify=False)
        stations_data = res.json() if res.status_code == 200 else []
    except Exception as e:
        print(f"❌ 擷取 API 水位資料失敗: {e}")
        stations_data = []

    if not stations_data:
        print("ℹ️ 未取得任何即時測站資料。")
        return

    # 1. 同步測站字典至試算表
    sync_stations_to_sheet(stations_data)
    
    # 2. 讀取使用者監控目標與官方門檻
    target_config = get_user_targets()
    if not target_config:
        print("ℹ️ 目前控制台沒有開啟任何 YES 監控目標。")
        return

    official_thresholds = get_sheet_official_thresholds()
    realtime_map = {str(item.get("Name", "")).strip(): item for item in stations_data}
    
    # 3. 載入歷史警報快取記錄
    alert_cache = load_alert_cache()
    current_time = time.time()

    # 4. 逐一巡檢與核對狀態
    for st_name, custom_cfg in target_config.items():
        st_data = realtime_map.get(st_name)
        if not st_data:
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

        # 控制台自訂 > 官方門檻
        off_cfg = official_thresholds.get(st_name, {})
        l1 = custom_cfg.get("l1") if custom_cfg.get("l1") is not None else off_cfg.get("l1")
        l2 = custom_cfg.get("l2") if custom_cfg.get("l2") is not None else off_cfg.get("l2")
        l3 = custom_cfg.get("l3") if custom_cfg.get("l3") is not None else off_cfg.get("l3")

        # 計算警戒等級 (0: 正常, 1: 三級, 2: 二級, 3: 一級)
        alert_level, level_rank = None, 0
        if l1 is not None and water_level >= l1:
            alert_level = "🔴 一級警戒 (極高危險)"
            level_rank = 3
        elif l2 is not None and water_level >= l2:
            alert_level = "🟠 二級警戒 (高危險)"
            level_rank = 2
        elif l3 is not None and water_level >= l3:
            alert_level = "🟡 三級警戒 (注意)"
            level_rank = 1

        print(f"📊 [{st_name}] 水位：{water_level:.3f} m | 警戒線 (三/二/一級): {l3}/{l2}/{l1} | 狀態: {alert_level or '🟢 正常'}")

        # 讀取該測站先前的歷史記錄
        st_cache = alert_cache.get(st_name, {"rank": 0, "last_notify_time": 0})
        prev_rank = st_cache.get("rank", 0)
        last_time = st_cache.get("last_notify_time", 0)

        should_notify = False
        is_recovery = False

        if level_rank > 0:
            # 情況 A：剛進入警戒，或警戒等級升高
            if level_rank > prev_rank:
                should_notify = True
            # 情況 B：警戒等級相同，但已超過冷卻時間 (6 小時)
            elif level_rank == prev_rank and (current_time - last_time) >= ALERT_COOLDOWN_SECONDS:
                should_notify = True
        else:
            # 情況 C：由警戒狀態回落至正常水位 -> 發送解除警戒通知
            if prev_rank > 0:
                should_notify = True
                is_recovery = True

        if should_notify:
            if is_recovery:
                msg = (
                    f"🟢 *【水位警戒解除通知】*\n\n"
                    f"📍 *測站名稱*：{st_name}\n"
                    f"✅ *當前狀態*：水位已降至警戒線以下\n"
                    f"🌊 *當前水位*：`{water_level:.3f}` m\n"
                    f"🕒 *更新時間*：{record_time}"
                )
            else:
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
            # 更新狀態與發送時間
            alert_cache[st_name] = {
                "rank": level_rank,
                "last_notify_time": current_time
            }
        else:
            # 未觸發通知時，保持原狀態
            alert_cache[st_name] = {
                "rank": level_rank,
                "last_notify_time": last_time
            }

    # 5. 寫入快取檔案
    save_alert_cache(alert_cache)

if __name__ == "__main__":
    main()
