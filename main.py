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

    # --- Ежедневная рассылка ---
    cron_expr = config.get("schedule_interval", "0 9 * * *").split()
    if len(cron_expr) != 5:
        logger.error("Неверное cron-выражение schedule_interval в config.json")
        sys.exit(1)

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

    # --- Субботнее/видео-сообщение ---
    watch_cron = config.get("watch_schedule")
    if watch_cron:
        watch_parts = watch_cron.split()
        if len(watch_parts) != 5:
            logger.error("Неверное cron-выражение watch_schedule в config.json")
            sys.exit(1)

        w_minute, w_hour, w_day, w_month, w_dow = watch_parts
        scheduler.add_job(
            handler.send_watch_message,
            trigger="cron",
            minute=w_minute,
            hour=w_hour,
            day=w_day,
            month=w_month,
            day_of_week=w_dow
        )

    logger.info("Бот запущен и ждет запусков по расписанию...")
    scheduler.start()

    # слушаем кнопки и апдейты бесконечно
    handler.start()


if __name__ == "__main__":
    main()
