import schedule
import time
import sqlite3
import os
import json
import traceback
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reminders.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                config TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_type TEXT NOT NULL,
                time_str TEXT NOT NULL,
                message TEXT NOT NULL,
                channel_ids TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            )
        """)
        try:
            conn.execute("ALTER TABLE reminders ADD COLUMN channel_ids TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def insert_channel(name, channel_type, config):
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO channels (name, channel_type, config, created_at) VALUES (?, ?, ?, ?)",
            (name, channel_type, json.dumps(config, ensure_ascii=False), datetime.now().isoformat()),
        )
        conn.commit()
        return cursor.lastrowid


def load_channels():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, channel_type, config FROM channels ORDER BY id"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "type": row["channel_type"],
                "config": json.loads(row["config"]),
            }
            for row in rows
        ]


def get_channel_by_id(channel_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, channel_type, config FROM channels WHERE id = ?",
            (channel_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "type": row["channel_type"],
            "config": json.loads(row["config"]),
        }


def delete_channel_by_id(channel_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
        conn.commit()


def load_reminders():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, schedule_type, time_str, message, channel_ids FROM reminders ORDER BY id"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "type": row["schedule_type"],
                "time": row["time_str"],
                "message": row["message"],
                "channel_ids": json.loads(row["channel_ids"] or "[]"),
            }
            for row in rows
        ]


def insert_reminder(schedule_type, time_str, message, channel_ids=None):
    if channel_ids is None:
        channel_ids = []
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO reminders (schedule_type, time_str, message, channel_ids, created_at) VALUES (?, ?, ?, ?, ?)",
            (schedule_type, time_str, message, json.dumps(channel_ids), datetime.now().isoformat()),
        )
        conn.commit()
        return cursor.lastrowid


def delete_reminder_by_id(reminder_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()


def send_email(config, message):
    try:
        required = ["smtp_host", "smtp_port", "sender", "password", "receivers"]
        for key in required:
            if key not in config:
                return False, f"邮件配置缺少字段: {key}"

        msg = MIMEText(message, "plain", "utf-8")
        msg["From"] = Header(config.get("sender_name", "提醒服务"), "utf-8")
        msg["To"] = Header(", ".join(config["receivers"]), "utf-8")
        msg["Subject"] = Header(config.get("subject", "定时提醒"), "utf-8")

        use_ssl = config.get("use_ssl", True)
        if use_ssl:
            server = smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"], timeout=10)
        else:
            server = smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=10)
            server.starttls()

        server.login(config["sender"], config["password"])
        server.sendmail(config["sender"], config["receivers"], msg.as_string())
        server.quit()
        return True, "邮件发送成功"
    except Exception as e:
        return False, f"邮件发送失败: {str(e)}"


def send_webhook(config, message):
    if requests is None:
        return False, "缺少 requests 库，请先安装: pip install requests"
    try:
        if "url" not in config:
            return False, "WebHook 配置缺少 url 字段"

        url = config["url"]
        method = config.get("method", "POST").upper()
        headers = config.get("headers", {"Content-Type": "application/json"})
        body_template = config.get("body", {"content": "{message}"})

        def _render(obj):
            if isinstance(obj, str):
                return obj.replace("{message}", message)
            elif isinstance(obj, dict):
                return {k: _render(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [_render(item) for item in obj]
            else:
                return obj

        body = _render(body_template)

        if method == "POST":
            resp = requests.post(url, json=body, headers=headers, timeout=10)
        elif method == "GET":
            resp = requests.get(url, params=body, headers=headers, timeout=10)
        else:
            return False, f"不支持的 HTTP 方法: {method}"

        if 200 <= resp.status_code < 300:
            return True, f"WebHook 发送成功 (HTTP {resp.status_code})"
        else:
            return False, f"WebHook 发送失败 (HTTP {resp.status_code}): {resp.text[:200]}"
    except Exception as e:
        return False, f"WebHook 发送失败: {str(e)}"


def send_sms(config, message):
    if requests is None:
        return False, "缺少 requests 库，请先安装: pip install requests"
    try:
        provider = config.get("provider", "").lower()
        if provider == "twilio":
            required = ["account_sid", "auth_token", "from_number", "to_numbers"]
            for key in required:
                if key not in config:
                    return False, f"Twilio 配置缺少字段: {key}"
            url = f"https://api.twilio.com/2010-04-01/Accounts/{config['account_sid']}/Messages.json"
            results = []
            for to_num in config["to_numbers"]:
                data = {"From": config["from_number"], "To": to_num, "Body": message}
                resp = requests.post(url, data=data, auth=(config["account_sid"], config["auth_token"]), timeout=10)
                results.append(f"{to_num}: {'成功' if 200 <= resp.status_code < 300 else '失败'}")
            return True, f"Twilio 短信发送: {', '.join(results)}"
        elif provider == "aliyun":
            required = ["access_key", "access_secret", "sign_name", "template_code", "phone_numbers"]
            for key in required:
                if key not in config:
                    return False, f"阿里云短信配置缺少字段: {key}"
            return False, "阿里云短信需要签名算法，此处为占位符，请根据阿里云文档实现"
        else:
            if "url" not in config:
                return False, "短信配置缺少 provider 或 url 字段"
            return send_webhook(config, message)
    except Exception as e:
        return False, f"短信发送失败: {str(e)}"


def _send_via_channel(channel, message):
    channel_type = channel["type"]
    config = channel["config"]
    if channel_type == "邮件":
        return send_email(config, message)
    elif channel_type == "WebHook":
        return send_webhook(config, message)
    elif channel_type == "短信":
        return send_sms(config, message)
    else:
        return False, f"不支持的渠道类型: {channel_type}"


def notify(message, channel_ids=None):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  ⏰ 提醒时间: {now_str}")
    print(f"  📌 提醒内容: {message}")

    if not channel_ids:
        print(f"  📢 通知渠道: 终端 (默认)")
        print(f"{'='*60}\n")
        return

    channels_map = {}
    for c in load_channels():
        channels_map[c["id"]] = c

    valid_channels = []
    for cid in channel_ids:
        if cid in channels_map:
            valid_channels.append(channels_map[cid])
        else:
            print(f"  ⚠️  渠道 ID {cid} 不存在，已跳过")

    if not valid_channels:
        print(f"  📢 通知渠道: 终端 (无有效渠道)")
        print(f"{'='*60}\n")
        return

    channel_names = ", ".join([f"{c['name']}({c['type']})" for c in valid_channels])
    print(f"  📢 通知渠道: {channel_names}")
    print("-" * 60)

    for c in valid_channels:
        success, msg = _send_via_channel(c, message)
        icon = "✅" if success else "❌"
        print(f"  {icon} [{c['name']}] {msg}")

    print(f"{'='*60}\n")


def add_reminder(schedule_type, time_str, message, channel_ids=None):
    if channel_ids is None:
        channel_ids = []
    reminder = {
        "type": schedule_type,
        "time": time_str,
        "message": message,
        "channel_ids": channel_ids,
    }
    reminder_id = insert_reminder(schedule_type, time_str, message, channel_ids)
    reminder["id"] = reminder_id
    _register_reminder(reminder)
    if channel_ids:
        channels_map = {c["id"]: c["name"] for c in load_channels()}
        chan_names = ", ".join([channels_map.get(cid, str(cid)) for cid in channel_ids])
        print(f"✅ 已添加提醒 (ID: {reminder_id}): [{schedule_type}] {time_str} - {message} (渠道: {chan_names})")
    else:
        print(f"✅ 已添加提醒 (ID: {reminder_id}): [{schedule_type}] {time_str} - {message} (渠道: 终端)")


def _register_reminder(reminder):
    schedule_type = reminder["type"]
    time_str = reminder["time"]
    message = reminder["message"]
    channel_ids = reminder.get("channel_ids", [])

    if schedule_type == "每天":
        schedule.every().day.at(time_str).do(notify, message=message, channel_ids=channel_ids)
    elif schedule_type == "每小时":
        schedule.every().hour.at(time_str).do(notify, message=message, channel_ids=channel_ids)
    elif schedule_type == "每分钟":
        schedule.every().minute.at(time_str).do(notify, message=message, channel_ids=channel_ids)
    elif schedule_type == "每周一":
        schedule.every().monday.at(time_str).do(notify, message=message, channel_ids=channel_ids)
    elif schedule_type == "每周二":
        schedule.every().tuesday.at(time_str).do(notify, message=message, channel_ids=channel_ids)
    elif schedule_type == "每周三":
        schedule.every().wednesday.at(time_str).do(notify, message=message, channel_ids=channel_ids)
    elif schedule_type == "每周四":
        schedule.every().thursday.at(time_str).do(notify, message=message, channel_ids=channel_ids)
    elif schedule_type == "每周五":
        schedule.every().friday.at(time_str).do(notify, message=message, channel_ids=channel_ids)
    elif schedule_type == "每周六":
        schedule.every().saturday.at(time_str).do(notify, message=message, channel_ids=channel_ids)
    elif schedule_type == "每周日":
        schedule.every().sunday.at(time_str).do(notify, message=message, channel_ids=channel_ids)
    else:
        print(f"⚠️ 不支持的调度类型: {schedule_type}")


def restore_reminders():
    init_db()
    reminders = load_reminders()
    for reminder in reminders:
        _register_reminder(reminder)
    if reminders:
        print(f"📋 已从数据库恢复 {len(reminders)} 条提醒")


def list_reminders():
    reminders = load_reminders()
    if not reminders:
        print("📭 暂无提醒")
        return
    channels_map = {c["id"]: c["name"] for c in load_channels()}
    print(f"\n📋 当前提醒列表 (共 {len(reminders)} 条):")
    print("-" * 70)
    print(f"  {'序号':<4} {'ID':<4} {'类型':<8} {'时间':<8} {'渠道':<12} 内容")
    print("-" * 70)
    for i, r in enumerate(reminders, 1):
        if r["channel_ids"]:
            chan_names = ",".join([channels_map.get(cid, str(cid)) for cid in r["channel_ids"]])
        else:
            chan_names = "终端"
        print(f"  {i:<4} {r['id']:<4} {r['type']:<8} {r['time']:<8} {chan_names:<12} {r['message']}")
    print("-" * 70)


def list_channels():
    channels = load_channels()
    if not channels:
        print("📭 暂无渠道配置")
        return
    print(f"\n📡 渠道配置列表 (共 {len(channels)} 条):")
    print("-" * 65)
    print(f"  {'序号':<4} {'ID':<4} {'名称':<16} {'类型':<10} 配置")
    print("-" * 65)
    for i, c in enumerate(channels, 1):
        config_preview = json.dumps(c["config"], ensure_ascii=False)
        if len(config_preview) > 35:
            config_preview = config_preview[:32] + "..."
        print(f"  {i:<4} {c['id']:<4} {c['name']:<16} {c['type']:<10} {config_preview}")
    print("-" * 65)


def delete_channel(index):
    channels = load_channels()
    if index < 1 or index > len(channels):
        print(f"⚠️ 无效序号: {index}，当前共 {len(channels)} 条渠道")
        return
    removed = channels[index - 1]
    delete_channel_by_id(removed["id"])
    print(f"🗑️ 已删除渠道 (ID: {removed['id']}): {removed['name']} ({removed['type']})")


def add_channel_interactive():
    print("\n📡 新增渠道配置")
    print("  支持类型: 邮件 / WebHook / 短信")
    print("  输入 q 取消\n")

    name = input("  渠道名称> ").strip()
    if not name or name.lower() == "q":
        print("⚠️ 已取消")
        return
    if len(name) > 16:
        print("⚠️ 名称过长（最多 16 字符）")
        return

    channel_type = input("  渠道类型 (邮件/WebHook/短信)> ").strip()
    if channel_type.lower() == "q":
        print("⚠️ 已取消")
        return
    if channel_type not in ["邮件", "WebHook", "短信"]:
        print(f"⚠️ 不支持的渠道类型: {channel_type}")
        return

    config = {}
    if channel_type == "邮件":
        print("\n  邮件配置:")
        config["smtp_host"] = input("    SMTP 服务器 (如 smtp.qq.com)> ").strip()
        config["smtp_port"] = int(input("    SMTP 端口 (如 465)> ").strip())
        config["sender"] = input("    发件人邮箱> ").strip()
        config["password"] = input("    发件人密码/授权码> ").strip()
        config["sender_name"] = input("    发件人名称 (可选，默认: 提醒服务)> ").strip() or "提醒服务"
        config["subject"] = input("    邮件主题 (可选，默认: 定时提醒)> ").strip() or "定时提醒"
        receivers_raw = input("    收件人邮箱 (多个用逗号分隔)> ").strip()
        config["receivers"] = [r.strip() for r in receivers_raw.split(",") if r.strip()]
        config["use_ssl"] = input("    使用 SSL (y/n，默认 y)> ").strip().lower() != "n"
    elif channel_type == "WebHook":
        print("\n  WebHook 配置:")
        config["url"] = input("    WebHook URL> ").strip()
        method = input("    HTTP 方法 (POST/GET，默认 POST)> ").strip().upper() or "POST"
        config["method"] = method
        content_type = input("    Content-Type (默认 application/json)> ").strip() or "application/json"
        config["headers"] = {"Content-Type": content_type}
        body_raw = input('    请求体模板 (可用 {message} 占位，默认 {"content":"{message}"})> ').strip()
        if body_raw:
            try:
                config["body"] = json.loads(body_raw)
            except json.JSONDecodeError:
                print("⚠️ JSON 格式错误，使用默认模板")
                config["body"] = {"content": "{message}"}
        else:
            config["body"] = {"content": "{message}"}
    elif channel_type == "短信":
        print("\n  短信配置:")
        print("  支持的服务商: twilio / aliyun / 自定义")
        provider = input("    服务商 (twilio/aliyun/自定义)> ").strip().lower()
        if provider == "twilio":
            config["provider"] = "twilio"
            config["account_sid"] = input("    Account SID> ").strip()
            config["auth_token"] = input("    Auth Token> ").strip()
            config["from_number"] = input("    发件号码 (如 +1234567890)> ").strip()
            to_raw = input("    收件号码 (多个用逗号分隔)> ").strip()
            config["to_numbers"] = [n.strip() for n in to_raw.split(",") if n.strip()]
        elif provider == "aliyun":
            config["provider"] = "aliyun"
            config["access_key"] = input("    AccessKey> ").strip()
            config["access_secret"] = input("    AccessSecret> ").strip()
            config["sign_name"] = input("    签名名称> ").strip()
            config["template_code"] = input("    模板CODE> ").strip()
            phone_raw = input("    手机号码 (多个用逗号分隔)> ").strip()
            config["phone_numbers"] = [n.strip() for n in phone_raw.split(",") if n.strip()]
        else:
            print("    使用自定义 WebHook 方式发送短信")
            config["url"] = input("    短信 API URL> ").strip()
            method = input("    HTTP 方法 (POST/GET，默认 POST)> ").strip().upper() or "POST"
            config["method"] = method
            body_raw = input('    请求体模板 (可用 {message} 占位)> ').strip()
            if body_raw:
                try:
                    config["body"] = json.loads(body_raw)
                except json.JSONDecodeError:
                    print("⚠️ JSON 格式错误，使用默认模板")
                    config["body"] = {"content": "{message}"}
            else:
                config["body"] = {"content": "{message}"}

    required_valid = True
    for k, v in config.items():
        if v in [None, "", [], {}]:
            print(f"⚠️ 配置项 {k} 不能为空")
            required_valid = False
    if not required_valid:
        return

    channel_id = insert_channel(name, channel_type, config)
    print(f"\n✅ 渠道配置已添加 (ID: {channel_id}): {name} ({channel_type})")


def delete_reminder(index):
    reminders = load_reminders()
    if index < 1 or index > len(reminders):
        print(f"⚠️ 无效序号: {index}，当前共 {len(reminders)} 条提醒")
        return
    removed = reminders[index - 1]
    delete_reminder_by_id(removed["id"])
    print(f"🗑️ 已删除提醒 (ID: {removed['id']}): [{removed['type']}] {removed['time']} - {removed['message']}")
    print("💡 提示: 删除后需重启服务才能生效")


def show_help():
    print("""
╔══════════════════════════════════════════════════╗
║              定时提醒服务 - 帮助                  ║
╠══════════════════════════════════════════════════╣
║  提醒管理:                                        ║
║  add    添加提醒                                  ║
║         用法: add <类型> <时间> <内容> [--chan <ID列表>] ║
║         类型: 每天/每周一~每周日/每小时/每分钟      ║
║         示例: add 每天 09:00 晨会                  ║
║               add 每周一 10:00 周例会 --chan 1,2   ║
║                                                  ║
║  list   列出所有提醒                              ║
║  del    删除提醒                                  ║
║         用法: del <序号>                           ║
║                                                  ║
║  渠道管理:                                        ║
║  chan add    新增渠道（交互式）                    ║
║  chan list   列出所有渠道                          ║
║  chan del    删除渠道                              ║
║         用法: chan del <序号>                      ║
║                                                  ║
║  支持的渠道类型: 邮件 / WebHook / 短信             ║
║                                                  ║
║  help   显示帮助                                  ║
║  quit   退出服务                                  ║
╚══════════════════════════════════════════════════╝
""")


def _parse_chan_arg(message_part):
    channel_ids = []
    actual_message = message_part
    if "--chan" in message_part:
        parts = message_part.split("--chan", 1)
        actual_message = parts[0].strip()
        chan_part = parts[1].strip()
        chan_str = chan_part.split()[0] if chan_part.split() else ""
        try:
            channel_ids = [int(x.strip()) for x in chan_str.split(",") if x.strip().isdigit()]
        except ValueError:
            channel_ids = []
        remaining = " ".join(chan_part.split()[1:])
        if remaining:
            actual_message = (actual_message + " " + remaining).strip()
    return actual_message, channel_ids


def run_service():
    print("🚀 定时提醒服务已启动")
    restore_reminders()
    show_help()

    while True:
        try:
            cmd = input("提醒服务> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not cmd:
            continue

        parts = cmd.split(maxsplit=3)
        action = parts[0].lower()

        if action == "quit" or action == "exit":
            print("👋 再见！")
            break
        elif action == "help":
            show_help()
        elif action == "list":
            list_reminders()
        elif action == "del":
            if len(parts) < 2:
                print("⚠️ 用法: del <序号>")
                continue
            try:
                idx = int(parts[1])
                delete_reminder(idx)
            except ValueError:
                print("⚠️ 序号必须是数字")
        elif action == "chan":
            if len(parts) < 2:
                print("⚠️ 用法: chan <add|list|del> [参数]")
                continue
            sub_action = parts[1].lower()
            if sub_action == "add":
                add_channel_interactive()
            elif sub_action == "list":
                list_channels()
            elif sub_action == "del":
                if len(parts) < 3:
                    print("⚠️ 用法: chan del <序号>")
                    continue
                try:
                    idx = int(parts[2])
                    delete_channel(idx)
                except ValueError:
                    print("⚠️ 序号必须是数字")
            else:
                print(f"⚠️ 未知子命令: {sub_action}，支持 add/list/del")
        elif action == "add":
            if len(parts) < 4:
                print("⚠️ 用法: add <类型> <时间> <内容> [--chan <ID列表>]")
                print("   类型: 每天/每周一~每周日/每小时/每分钟")
                print("   示例: add 每天 09:00 晨会")
                print("         add 每周一 10:00 周例会 --chan 1,2")
                continue
            schedule_type = parts[1]
            time_str = parts[2]
            message_part = parts[3]
            actual_message, channel_ids = _parse_chan_arg(message_part)
            if not actual_message:
                print("⚠️ 提醒内容不能为空")
                continue
            add_reminder(schedule_type, time_str, actual_message, channel_ids)
        else:
            print(f"⚠️ 未知命令: {action}，输入 help 查看帮助")

        schedule.run_pending()


if __name__ == "__main__":
    run_service()
