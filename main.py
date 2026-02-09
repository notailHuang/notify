from fastapi import FastAPI, Request, Header, HTTPException
from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.base import JobLookupError
from zoneinfo import ZoneInfo
import sqlite3
import os

# =========================
# 基本設定
# =========================
TZ = ZoneInfo("Asia/Taipei")

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise RuntimeError("LINE channel token / secret not set")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)

app = FastAPI()

# =========================
# DB
# =========================
conn = sqlite3.connect("reminder.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS reminder (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT,
    remind_time TEXT,
    message TEXT,
    job_id TEXT,
    notify_all INTEGER
)
""")
conn.commit()

# =========================
# Scheduler
# =========================
scheduler = BackgroundScheduler(timezone=TZ)
scheduler.start()

# =========================
# 發送提醒
# =========================
def send_reminder(group_id: str, message: str, notify_all: bool):
    try:
        prefix = "@all\n" if notify_all else ""
        line_bot_api.push_message(
            group_id,
            TextSendMessage(text=f"{prefix}⏰ 提醒\n{message}")
        )
    except Exception as e:
        print("Push failed:", e)

# =========================
# Webhook
# =========================
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

# =========================
# 指令處理
# =========================
def handle_message(event: MessageEvent):
    print(event.source.user_id)
    text = event.message.text.strip()

    if not text.startswith("HINOTIFY提醒"):
        return

    if event.source.type != "group":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⚠️ 請在群組中使用提醒功能")
        )
        return

    try:
        notify_all = "@All" in text or "@all" in text
        text = text.replace("@All", "").replace("@all", "").strip()

        # HINOTIFY提醒 2026-02-10 14:30 事項
        _, date_str, time_str, *msg = text.split(" ")
        remind_time = datetime.strptime(
            f"{date_str} {time_str}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=TZ)

        message = " ".join(msg)
        group_id = event.source.group_id

        job = scheduler.add_job(
            send_reminder,
            trigger=DateTrigger(run_date=remind_time),
            args=[group_id, message, notify_all]
        )

        cursor.execute(
            """
            INSERT INTO reminder (group_id, remind_time, message, job_id, notify_all)
            VALUES (?, ?, ?, ?, ?)
            """,
            (group_id, remind_time.isoformat(), message, job.id, int(notify_all))
        )
        conn.commit()

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text=(
                    "✅ 已設定提醒\n"
                    f"時間：{date_str} {time_str}\n"
                    f"事項：{message}\n"
                    f"@All：{'是' if notify_all else '否'}"
                )
            )
        )

    except Exception as e:
        print("Parse error:", e)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="❌ 指令格式錯誤\n範例：HINOTIFY提醒@All 2026-02-10 14:30 開會"
            )
        )

# =========================
# 啟動時恢復排程
# =========================
def restore_jobs():
    cursor.execute(
        "SELECT group_id, remind_time, message, job_id, notify_all FROM reminder"
    )
    rows = cursor.fetchall()

    for group_id, remind_time, message, job_id, notify_all in rows:
        run_date = datetime.fromisoformat(remind_time)
        if run_date > datetime.now(TZ):
            try:
                scheduler.add_job(
                    send_reminder,
                    trigger=DateTrigger(run_date=run_date),
                    args=[group_id, message, bool(notify_all)],
                    id=job_id,
                    replace_existing=True
                )
            except JobLookupError:
                pass

restore_jobs()
