import telebot
from telebot import types
import random
import logging


class MessageHandler:
    def __init__(self, token, config):
        self.bot = telebot.TeleBot(token)
        self.config = config
        self.logger = logging.getLogger("telegram_bot")
        self._register_handlers()

    # ---------------- Создание кнопок ----------------
    def _inline_keyboard(self, key, buttons):
        kb = types.InlineKeyboardMarkup(row_width=2)

        if not buttons:
            return kb

        inline_buttons = []
        for i, label in enumerate(buttons):
            data = f"answer:{key}:{'yes' if i == 0 else 'no'}"
            inline_buttons.append(types.InlineKeyboardButton(label, callback_data=data))

        # если только одна кнопка, добавим вторую "Пока нет"
        if len(inline_buttons) == 1:
            inline_buttons.append(
                types.InlineKeyboardButton("🤔 Пока нет", callback_data=f"answer:{key}:no")
            )

        kb.add(*inline_buttons)
        return kb

    # ---------------- Отправка сообщений ----------------
    def send_daily_message(self):
        messages = self.config.get("messages", [])
        if not messages:
            self.logger.warning("No messages found in config.")
            return

        message = random.choice(messages)
        text = (message.get("text") or "").strip()
        if not text:
            self.logger.warning("Empty message text, skipping.")
            return

        key = message.get("key", "q")
        buttons = message.get("buttons", [])
        kb = self._inline_keyboard(key, buttons)

        for group in self.config.get("groups", []):
            try:
                self.bot.send_message(group["id"], text, reply_markup=kb)
                self.logger.info(f"Sent to {group['name']}")
            except Exception as e:
                self.logger.error(f"Failed for {group['name']}: {e}")

    # ---------------- Обработка ответов ----------------
    def _register_handlers(self):
        @self.bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("answer:"))
        def _on_answer(call: types.CallbackQuery):
            try:
                _, key, val = call.data.split(":")
            except Exception:
                self.bot.answer_callback_query(call.id)
                return

            # одно благодарственное сообщение без дублей
            msg = "👍 Принято! Спасибо." if val == "yes" else "✅ Ответ записан."

            try:
                self.bot.answer_callback_query(call.id, "Ответ сохранён ✅")
                self.bot.send_message(call.message.chat.id, msg)
            except Exception as e:
                self.logger.error(f"Error sending response: {e}")

            # кнопки убираем, чтобы не жали повторно
            try:
                self.bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=None
                )
            except Exception:
                pass

    # ---------------- Запуск ----------------
    def start(self):
        self.logger.info("Бот запущен и готов к приёму сообщений.")
        self.bot.infinity_polling()
