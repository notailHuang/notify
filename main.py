from fastapi import FastAPI, Request, Header, HTTPException
from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime
import sqlite3
import threading
import time
import os


print("DEBUG TOKEN =", os.getenv("CHANNEL_ACCESS_TOKEN"))
print("DEBUG SECRET =", os.getenv("CHANNEL_SECRET"))
# ========= LINE 設定（用環境變數） =========
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)

# ========= FastAPI =========
app = FastAPI()

# ========= DB =========
conn = sqlite3.connect("reminder.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS reminder (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT,
    remind_time TEXT,
    message TEXT,
    sent INTEGER DEFAULT 0
)
""")
conn.commit()

# ========= Webhook =========
@app.post("/webhook")
async def webhook(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    try:
        events = parser.parse(body.decode(), x_line_signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            handle_message(event)

    return "OK"

# ========= 訊息處理 =========
def handle_message(event: MessageEvent):
    text = event.message.text.strip()

    if not text.startswith("提醒"):
        return

    # 只處理群組
    if event.source.type != "group":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⚠️ 請在群組中使用提醒功能")
        )
        return

    try:
        # 指令格式：提醒 2026-02-10 14:30 事項
        _, date_str, time_str, *msg = text.split(" ")
        remind_time = datetime.strptime(
            f"{date_str} {time_str}", "%Y-%m-%d %H:%M"
        )
        message = " ".join(msg)

        group_id = event.source.group_id

        cursor.execute(
            "INSERT INTO reminder (group_id, remind_time, message) VALUES (?, ?, ?)",
            (group_id, remind_time.isoformat(), message)
        )
        conn.commit()

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text=f"✅ 已設定提醒\n時間：{date_str} {time_str}\n事項：{message}"
            )
        )

    except Exception:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="❌ 指令格式錯誤\n範例：提醒 2026-02-10 14:30 開會"
            )
        )

# ========= 排程提醒 =========
def reminder_loop():
    while True:
        now = datetime.now().isoformat()
        cursor.execute(
            "SELECT id, group_id, message FROM reminder WHERE sent=0 AND remind_time<=?",
            (now,)
        )
        rows = cursor.fetchall()

        for rid, group_id, message in rows:
            try:
                line_bot_api.push_message(
                    group_id,
                    TextSendMessage(text=f"⏰ 提醒\n{message}")
                )
                cursor.execute(
                    "UPDATE reminder SET sent=1 WHERE id=?", (rid,)
                )
                conn.commit()
            except Exception as e:
                print("Push error:", e)

        time.sleep(30)

threading.Thread(target=reminder_loop, daemon=True).start()
