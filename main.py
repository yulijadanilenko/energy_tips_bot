import logging
import os

from config_manager import ConfigManager
from message_handler import MessageHandler


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logger = logging.getLogger("telegram_bot")

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        logger.error("Не задана переменная BOT_TOKEN.")
        raise SystemExit(1)

    config = ConfigManager().load_config()
    handler = MessageHandler(bot_token, config)

    logger.info("Запуск тренажёра. Начать тренировку: /train.")
    handler.start()


if name == "main":
    main()
