import os
import sys
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from message_handler import MessageHandler
from config_manager import ConfigManager

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logger = logging.getLogger("telegram_bot")

    config = ConfigManager().load_config()
    bot_token = os.getenv("BOT_TOKEN", "").strip()

    if not bot_token:
        logger.error("Bot token not found.")
        sys.exit(1)

    handler = MessageHandler(bot_token, config)

    # Планировщик (фоновый, чтобы параллельно работал polling)
    scheduler = BackgroundScheduler()
    cron_expr = config.get("schedule_interval", "0 9 * * *").split()

    if len(cron_expr) == 5:
        minute, hour, day, month, day_of_week = cron_expr
        scheduler.add_job(
            handler.send_daily_message,
            trigger="cron",
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week
        )
    else:
        logger.error("Неверное cron-выражение в config.json")
        sys.exit(1)

    logger.info("Бот запущен и ждет следующего запуска...")
    scheduler.start()

    # слушаем кнопки и апдейты бесконечно
    handler.start()

if __name__ == "__main__":
    main()
