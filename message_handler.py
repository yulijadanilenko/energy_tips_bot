import telebot
import logging
import random

class MessageHandler:
    def __init__(self, token, config):
        self.bot = telebot.TeleBot(token)
        self.config = config
        self.logger = logging.getLogger("telegram_bot")

    def send_daily_message(self):
        message = random.choice(self.config.get("messages", []))
        text = message.get("content", "")
        for group in self.config.get("groups", []):
            try:
                self.bot.send_message(group["id"], text)
                self.logger.info(f"Sent to {group['name']}")
            except Exception as e:
                self.logger.error(f"Failed for {group['name']}: {e}")
