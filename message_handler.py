import logging
import random
import threading
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


class MessageHandler:
    def __init__(self, token, config):
        self.bot = telebot.TeleBot(token, parse_mode="HTML")
        self.config = config
        self.logger = logging.getLogger("telegram_bot")

        # регистрируем обработчики и запускаем polling в фоне
        self._register_handlers()
        self._start_polling_thread()

    # ---------- helpers ----------
    def _register_handlers(self):
        @self.bot.callback_query_handler(func=lambda c: c.data.startswith("answer:"))
        def on_answer(call):
            try:
                _, key, decision = call.data.split(":", 2)

                # убираем кнопки под исходным сообщением (если получится)
                try:
                    self.bot.edit_message_reply_markup(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=None,
                    )
                except Exception:
                    pass

                # короткий фидбек
                if decision == "yes":
                    reply = "✅ Отлично! Так держать 💪"
                else:
                    reply = "👌 Спасибо за честность. Попробуй сегодня — это просто!"

                self.bot.answer_callback_query(call.id, "Ответ записан")
                self.bot.send_message(call.message.chat.id, reply)

            except Exception as e:
                self.logger.error(f"Failed to process answer: {e}")

    def _start_polling_thread(self):
        def _poll():
            while True:
                try:
                    self.bot.infinity_polling(timeout=60, long_polling_timeout=60)
                except Exception as e:
                    self.logger.error(f"Polling crashed: {e}")

        t = threading.Thread(target=_poll, daemon=True)
        t.start()

    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

def _inline_keyboard(self, key: str, labels: list | None) -> InlineKeyboardMarkup:
    # страховка: если кнопок нет/мало — подставим дефолт
    labels = labels or ["👍 Да", "🤔 Пока нет"]

    kb = InlineKeyboardMarkup(row_width=2)
    buttons = []

    # первая кнопка (yes)
    if len(labels) >= 1 and labels[0]:
        buttons.append(
            InlineKeyboardButton(
                labels[0], callback_data=f"answer:{key}:yes"
            )
        )

    # вторая кнопка (no)
    if len(labels) >= 2 and labels[1]:
        buttons.append(
            InlineKeyboardButton(
                labels[1], callback_data=f"answer:{key}:no"
            )
        )

    # если всё же одна кнопка — добавим заглушку «Пока нет»
    if len(buttons) == 1:
        buttons.append(
            InlineKeyboardButton(
                "🤔 Пока нет", callback_data=f"answer:{key}:no"
            )
        )

    kb.add(*buttons)
    return kb

    # ----------- scheduler job -----------
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
        kb = self._inline_keyboard(key, message.get("buttons", []))

        for group in self.config.get("groups", []):
            try:
                self.bot.send_message(group["id"], text, reply_markup=kb)
                self.logger.info(f"Sent to {group['name']}")
            except Exception as e:
                self.logger.error(f"Failed for {group['name']}: {e}")
