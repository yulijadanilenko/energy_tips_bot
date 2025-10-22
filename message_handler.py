import os
import json
import telebot
from telebot import types
import random
import logging
from datetime import datetime

# gspread + google-auth для Google Sheets
import gspread
from google.oauth2.service_account import Credentials


class MessageHandler:
    def __init__(self, token, config):
        self.bot = telebot.TeleBot(token)
        self.config = config
        self.logger = logging.getLogger("telegram_bot")
        
        self._answered = set()  # (chat_id, message_id, user_id) — кто уже отвечал

        # ---------- Инициализация Google Sheets ----------
        self.gc = None
        self.sheet = None
        try:
            creds_json = os.getenv("GOOGLE_CREDENTIALS", "")
            spreadsheet_id = os.getenv("SPREADSHEET_ID", "")

            # 🔎 ОТЛАДКА: проверяем, что переменные окружения пришли
            self.logger.info(
                f"ENV check: creds_len={len(creds_json)}, sheet_id_head={spreadsheet_id[:8]}"
            )

            if creds_json and spreadsheet_id:
                info = json.loads(creds_json)

                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]
                creds = Credentials.from_service_account_info(info, scopes=scopes)
                self.gc = gspread.authorize(creds)

                # первая вкладка (или создаём “Responses”)
                sh = self.gc.open_by_key(spreadsheet_id)
                try:
                    self.sheet = sh.worksheet("Responses")
                except gspread.exceptions.WorksheetNotFound:
                    self.sheet = sh.add_worksheet(title="Responses", rows="1000", cols="20")
                    # шапка
                    self.sheet.append_row(
                        ["timestamp", "chat_title", "chat_id", "user", "user_id",
                         "question_key", "answer_value", "message_id"],
                        value_input_option="USER_ENTERED"
                    )

                # 🔎 ОТЛАДКА: кто мы для Google
                self.logger.info(f"Google Sheets OK as {info.get('client_email')}")
            else:
                # если чего-то не хватает — явно логируем
                self.logger.warning(
                    "GOOGLE_CREDENTIALS or SPREADSHEET_ID not set – answers won't be saved to Sheets."
                )
        except Exception:
            # полный traceback, чтобы сразу видеть причину
            self.logger.exception("Failed to init Google Sheets")

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

            # Записываем в Google Sheets (если подключено)
            try:
                if self.sheet:
                    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    chat_title = getattr(call.message.chat, "title", "") or call.message.chat.username or ""
                    user_name = (call.from_user.full_name or "").strip()
                    row = [
                        ts,
                        chat_title,
                        call.message.chat.id,
                        user_name,
                        call.from_user.id,
                        key,
                        val,
                        call.message.message_id,
                    ]
                    self.sheet.append_row(row, value_input_option="USER_ENTERED")
                    # 🔎 ОТЛАДКА: подтверждаем запись строки
                    self.logger.info(f"Sheet append OK: {row[:4]} ...")
                else:
                    self.logger.warning("Sheet is not initialized; skipping append.")
            except Exception:
                self.logger.exception("Failed to append to sheet")

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
