import logging
import sys
from message_handler import MessageHandler
from config_manager import ConfigManager

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    logger = logging.getLogger("telegram_bot")

    config = ConfigManager().load_config()
    bot_token = config.get("bot_token", "")
    if not bot_token:
        logger.error("Bot token not found.")
        sys.exit(1)

    handler = MessageHandler(bot_token, config)
    handler.send_daily_message()

if __name__ == "__main__":
    main()
