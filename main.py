import os
import json
import time
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import google.generativeai as genai

def main():
    print("🚀 啟動會議紀錄自動化流程...")
    
    # 1. 載入環境變數與設定驗證
    gcp_sa_json = json.loads(os.environ['GCP_SERVICE_ACCOUNT'])
    drive_folder_id = os.environ['DRIVE_FOLDER_ID']
    sheet_id = os.environ['SHEET_ID']
    genai.configure(api_key=os.environ['GEMINI_API_KEY'])

    # 設定 GCP 的權限範圍 (Drive 唯讀, Sheets 讀寫)
    scopes = [
        'https://www.googleapis.com/auth/drive.readonly',
        'https://www.googleapis.com/auth/spreadsheets'
    ]
    creds = Credentials.from_service_account_info(gcp_sa_json, scopes=scopes)

    # 2. 連線 Google Drive 尋找最新錄影檔
    drive_service = build('drive', 'v3', credentials=creds)
    query = f"'{drive_folder_id}' in parents and mimeType='video/mp4' and trashed=false"
    results = drive_service.files().list(
        q=query, orderBy="createdTime desc", pageSize=1, fields="files(id, name, createdTime)"
    ).execute()
    files = results.get('files', [])

    if not files:
        print("❌ 在指定資料夾中找不到任何 mp4 影片檔案。")
        return

    file_id = files[0]['id']
    file_name = files[0]['name']
    file_date = files[0]['createdTime']
    print(f"📥 找到最新影片: {file_name}，準備下載...")

    # 下載影片到 GitHub Runner 的暫存空間
    request = drive_service.files().get_media(fileId=file_id)
    with open("temp_video.mp4", "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            print(f"下載進度: {int(status.progress() * 100)}%")

    # 3. 將影片上傳至 Gemini API
    print("📤 正在將影片上傳至 Gemini 進行分析...")
    video_file = genai.upload_file(path="temp_video.mp4")
    
    # 影片需要時間處理，建立等待迴圈
    while video_file.state.name == "PROCESSING":
        print(".", end="", flush=True)
        time.sleep(10)
        video_file = genai.get_file(video_file.name)
    print("\n✅ 影片處理完成！")

    if video_file.state.name == "FAILED":
        print("❌ Gemini 影片處理失敗。")
        return

    # 4. 呼叫 Gemini 模型生成會議紀錄
    print("🧠 正在生成摘要與待辦清單...")
    model = genai.GenerativeModel(model_name="gemini-1.5-flash")
    
    # 這裡的 Prompt 清楚定義了輸出的 JSON 結構
    prompt = """
    這是一段 Google Meet 會議錄影。請仔細觀看並聆聽內容，然後以 JSON 格式輸出以下資訊：
    {
      "summary": "請用大約 150-200 字總結會議的核心目的與主要討論內容。",
      "action_items": "請條列會議中決定的所有待辦事項（包含負責人與期限，若無則寫無）。",
      "transcript_highlights": "請列出 3 到 5 點會議中最關鍵的逐字重點或決議金句。"
    }
    """
    
    # 強制 Gemini 輸出 JSON 格式 (application/json)
    response = model.generate_content(
        [video_file, prompt],
        generation_config={"response_mime_type": "application/json"}
    )
    
    result_data = json.loads(response.text)

    # 5. 將結果寫入 Google Sheets
    print("📝 正在將紀錄寫入 Google 表格...")
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(sheet_id).sheet1  # 預設寫入第一個工作表
    
    # 假設你的表格欄位依序是：[會議日期, 會議主題, 摘要, 待辦清單, 重點紀錄]
    row_data = [
        file_date,
        file_name,
        result_data.get("summary", ""),
        result_data.get("action_items", ""),
        result_data.get("transcript_highlights", "")
    ]
    sheet.append_row(row_data)

    # 清理暫存檔案並從 Gemini 伺服器刪除影片 (保護隱私)
    os.remove("temp_video.mp4")
    genai.delete_file(video_file.name)
    
    print("🎉 自動化流程大功告成！")

if __name__ == "__main__":
    main()
