from fastapi import FastAPI, Request, Header, HTTPException
from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.models import JoinEvent
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
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")
conn.commit()
cursor.execute("""
CREATE TABLE IF NOT EXISTS allowed_groups (
    group_id TEXT PRIMARY KEY
)
""")
conn.commit()

def get_setting(key: str, default: str | None = None) -> str | None:
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    cursor.execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value)
    )
    conn.commit()
def is_group_allowed(group_id: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM allowed_groups WHERE group_id = ?",
        (group_id,)
    )
    return cursor.fetchone() is not None


def allow_group(group_id: str) -> None:
    cursor.execute(
        "INSERT OR IGNORE INTO allowed_groups (group_id) VALUES (?)",
        (group_id,)
    )
    conn.commit()
def disallow_group(group_id: str) -> None:
    """
    取消群組授權（停用 HINOTIFY）
    """
    cursor.execute(
        "DELETE FROM allowed_groups WHERE group_id = ?",
        (group_id,)
    )
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
        # 1️⃣ Bot 被加進群
        if isinstance(event, JoinEvent):
            handle_event(event)

        # 2️⃣ 一般文字訊息
        elif isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            handle_message(event)

    return "OK"


# =========================
# 指令處理
# =========================
OWNER_USER_ID = "U1a3eb06a3dcddb2c55a976c8bfe48188"
def handle_event(event: JoinEvent):
    group_id = event.source.group_id

    # 群組尚未授權才提示
    if not is_group_allowed(group_id):
        line_bot_api.push_message(
            group_id,
            TextSendMessage(
                text=(
                    "⚠️ HINOTIFY 尚未啟用\n"
                    "請由管理者輸入：\n"
                    "HINOTIFY啟用"
                )
            )
        )
commandList = ["HINOTIFY提醒"]
def handle_message(event: MessageEvent):
    text = event.message.text.strip()
    user_id = event.source.user_id
    
    # ===== 群組授權檢查 =====
    if event.source.type == "group":
        group_id = event.source.group_id

        # 尚未授權，只允許 OWNER 啟用
        if not is_group_allowed(group_id):
            if text.strip().lower() == "hinotify啟用" and user_id == OWNER_USER_ID:
                allow_group(group_id)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="✅ 此群組已啟用 HINOTIFY")
                )
            elif text.strip().lower() == "hinotify停用" and user_id == OWNER_USER_ID:
                
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="⚠️ 此群組已停用 HINOTIFY"")
                )
            elif any(text.startswith(cmd) for cmd in commandList):
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="⚠️ 此群組尚未啟用 HINOTIFY")
                )
            return

    FREE = get_setting("FREE", "N")

    # 非擁有者且未開放 → 擋指令
    if FREE == "N" and user_id != OWNER_USER_ID:
        if text.startswith("HINOTIFY提醒"):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="⚠️ 無權限")
            )
        return

    # ===== 管理指令（只有你能用）=====
    if user_id == OWNER_USER_ID and text.startswith("UPDATE"):
        try:
            _, key, value = text.split(" ", 2)
            set_setting(key, value)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"✅ 設定已更新\n{key} = {value}")
            )
        except Exception:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 指令格式：UPDATE KEY VALUE")
            )
        return

    # ===== 提醒指令 =====
    if not text.startswith("HINOTIFY提醒"):
        return

    if event.source.type != "group":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⚠️ 請在群組中使用提醒功能")
        )
        return

    try:
        notify_all = "@all" in text.lower()
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
