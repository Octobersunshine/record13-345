import schedule
import time
import json
import os
from datetime import datetime

REMINDERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reminders.json")


def load_reminders():
    if not os.path.exists(REMINDERS_FILE):
        return []
    with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_reminders(reminders):
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, ensure_ascii=False, indent=2)


def notify(message):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"  ⏰ 提醒时间: {now_str}")
    print(f"  📌 提醒内容: {message}")
    print(f"{'='*50}\n")


def add_reminder(schedule_type, time_str, message):
    reminders = load_reminders()
    reminder = {
        "type": schedule_type,
        "time": time_str,
        "message": message,
    }
    reminders.append(reminder)
    save_reminders(reminders)
    _register_reminder(reminder)
    print(f"✅ 已添加提醒: [{schedule_type}] {time_str} - {message}")


def _register_reminder(reminder):
    schedule_type = reminder["type"]
    time_str = reminder["time"]
    message = reminder["message"]

    if schedule_type == "每天":
        schedule.every().day.at(time_str).do(notify, message=message)
    elif schedule_type == "每小时":
        schedule.every().hour.at(time_str).do(notify, message=message)
    elif schedule_type == "每分钟":
        schedule.every().minute.at(time_str).do(notify, message=message)
    elif schedule_type == "每周一":
        schedule.every().monday.at(time_str).do(notify, message=message)
    elif schedule_type == "每周二":
        schedule.every().tuesday.at(time_str).do(notify, message=message)
    elif schedule_type == "每周三":
        schedule.every().wednesday.at(time_str).do(notify, message=message)
    elif schedule_type == "每周四":
        schedule.every().thursday.at(time_str).do(notify, message=message)
    elif schedule_type == "每周五":
        schedule.every().friday.at(time_str).do(notify, message=message)
    elif schedule_type == "每周六":
        schedule.every().saturday.at(time_str).do(notify, message=message)
    elif schedule_type == "每周日":
        schedule.every().sunday.at(time_str).do(notify, message=message)
    else:
        print(f"⚠️ 不支持的调度类型: {schedule_type}")


def restore_reminders():
    reminders = load_reminders()
    for reminder in reminders:
        _register_reminder(reminder)
    if reminders:
        print(f"📋 已从文件恢复 {len(reminders)} 条提醒")


def list_reminders():
    reminders = load_reminders()
    if not reminders:
        print("📭 暂无提醒")
        return
    print(f"\n📋 当前提醒列表 (共 {len(reminders)} 条):")
    print("-" * 50)
    for i, r in enumerate(reminders, 1):
        print(f"  {i}. [{r['type']}] {r['time']} - {r['message']}")
    print("-" * 50)


def delete_reminder(index):
    reminders = load_reminders()
    if index < 1 or index > len(reminders):
        print(f"⚠️ 无效序号: {index}，当前共 {len(reminders)} 条提醒")
        return
    removed = reminders.pop(index - 1)
    save_reminders(reminders)
    print(f"🗑️ 已删除提醒: [{removed['type']}] {removed['time']} - {removed['message']}")
    print("💡 提示: 删除后需重启服务才能生效")


def show_help():
    print("""
╔══════════════════════════════════════════════════╗
║              定时提醒服务 - 帮助                  ║
╠══════════════════════════════════════════════════╣
║  add    添加提醒                                  ║
║         用法: add <类型> <时间> <内容>             ║
║         类型: 每天/每周一~每周日/每小时/每分钟      ║
║         示例: add 每天 09:00 晨会                  ║
║               add 每周一 10:00 周例会              ║
║                                                  ║
║  list   列出所有提醒                              ║
║  del    删除提醒                                  ║
║         用法: del <序号>                           ║
║  help   显示帮助                                  ║
║  quit   退出服务                                  ║
╚══════════════════════════════════════════════════╝
""")


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
        elif action == "add":
            if len(parts) < 4:
                print("⚠️ 用法: add <类型> <时间> <内容>")
                print("   类型: 每天/每周一~每周日/每小时/每分钟")
                print("   示例: add 每天 09:00 晨会")
                continue
            schedule_type = parts[1]
            time_str = parts[2]
            message = parts[3]
            add_reminder(schedule_type, time_str, message)
        else:
            print(f"⚠️ 未知命令: {action}，输入 help 查看帮助")

        schedule.run_pending()


if __name__ == "__main__":
    run_service()
