import logging
import random
import telebot
from telebot import types


class MessageHandler:
    def __init__(self, token, config):
        self.bot = telebot.TeleBot(token, parse_mode="HTML")
        self.config = config
        self.logger = logging.getLogger("telegram_bot")

    def _build_markup(self, msg: dict):
        buttons = msg.get("buttons")
        if not buttons:
            return None
        markup = types.InlineKeyboardMarkup()
        key = msg.get("key", "btn")
        row = [types.InlineKeyboardButton(text=b, callback_data=f"{key}:{i}")
               for i, b in enumerate(buttons)]
        markup.row(*row)
        return markup

    def send_daily_message(self):
        messages = self.config.get("messages", [])
        if not messages:
            self.logger.error("No messages in config.")
            return

        msg = random.choice(messages)

        # поддерживаем оба поля
        text = (msg.get("text") or msg.get("content") or "").strip()
        if not text:
            self.logger.warning("Skipped empty message: %s", msg)
            return

        markup = self._build_markup(msg)

        for group in self.config.get("groups", []):
            try:
                self.bot.send_message(
                    chat_id=group["id"],
                    text=text,
                    reply_markup=markup,
                    disable_web_page_preview=True
                )
                self.logger.info(f"Sent to {group['name']}")
            except Exception as e:
                self.logger.error(f"Failed for {group['name']}: {e}")