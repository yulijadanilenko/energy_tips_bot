# message_handler.py
import logging
import random
import threading
import telebot
from telebot import types


class MessageHandler:
    def __init__(self, token: str, config: dict):
        self.bot = telebot.TeleBot(token, parse_mode="HTML")
        self.config = config
        self.logger = logging.getLogger("telegram_bot")
        self._register_handlers()
        # запускаем обработку коллбеков в фоне
        threading.Thread(
            target=self.bot.infinity_polling,
            kwargs={"timeout": 60, "long_polling_timeout": 50, "skip_pending": True},
            daemon=True,
        ).start()

    # ---------- UI ----------
    def _inline_keyboard(self, key: str, labels: list | None) -> types.InlineKeyboardMarkup:
        kb = types.InlineKeyboardMarkup()
        labels = labels or ["👍 Да", "🤔 Пока нет"]
        yes = labels[0]
        no = labels[1] if len(labels) > 1 else "🤔 Пока нет"
        kb.add(
            types.InlineKeyboardButton(yes, callback_data=f"answer:{key}:yes"),
            types.InlineKeyboardButton(no,  callback_data=f"answer:{key}:no"),
        )
        return kb

    # ---------- callbacks ----------
    def _register_handlers(self):
        @self.bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("answer:"))
        def _on_answer(call: types.CallbackQuery):
            try:
                _, key, val = call.data.split(":")
            except Exception:
                self.bot.answer_callback_query(call.id, "OK")
                return

            msg = "👍 Принято! Спасибо." if val == "yes" else "✅ Ответ записан."
            try:
                self.bot.answer_callback_query(call.id, "Записал!")
            except Exception:
                pass
            try:
                self.bot.send_message(call.message.chat.id, f"Ответ по «{key}»: {msg}")
            except Exception as e:
                self.logger.error(f"Reply send failed: {e}")

    # ---------- job ----------
    def send_daily_message(self):
        messages = self.config.get("messages", [])
        if not messages:
            self.logger.warning("No messages in config")
            return

        message = random.choice(messages)
        text = (message.get("text") or "").strip()
        if not text:
            self.logger.warning("Empty message text")
            return

        key = message.get("key", "q")
        kb = self._inline_keyboard(key, message.get("buttons"))

        for group in self.config.get("groups", []):
            try:
                self.bot.send_message(group["id"], text, reply_markup=kb)
                self.logger.info(f"Sent to {group.get('name') or group.get('id')}")
            except Exception as e:
                self.logger.error(f"Failed for {group.get('name') or group.get('id')}: {e}")
