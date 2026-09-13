name: River Water Level Monitor

on:
  schedule:
    # Cron 語法（UTC 時間）：每 15 分鐘自動執行一次
    # 註：GitHub 免費版 cron 執行時間可能會有幾分鐘延遲，屬正常現象
    - cron: '*/15 * * * *'
  workflow_dispatch: # 支援在 GitHub 網頁上手動點擊按鈕立即測試執行

jobs:
  run-monitor:
    runs-on: ubuntu-latest

    steps:
      - name: 檢出專案程式碼
        uses: actions/checkout@v4

      - name: 設定 Python 環境
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: 安裝依賴套件
        run: |
          python -m pip install --upgrade pip
          pip install requests urllib3

      - name: 執行水位監控腳本
        env:
          CLIENT_ID: ${{ secrets.CLIENT_ID }}
          CLIENT_SECRET: ${{ secrets.CLIENT_SECRET }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          python monitor.py
