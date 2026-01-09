import os
import json
import time
import logging
import random
from datetime import datetime

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

# gspread + google-auth для Google Sheets
import gspread
from google.oauth2.service_account import Credentials


# --- Ссылки на видео, которые бот отправляет В ГРУППУ по нажатию "Интересно" ---
WATCH_LINKS = {
    "s9_watch_solar_2_alt": "https://youtube.com/shorts/3m0MyZVbF_A?si=QFYQ2GSB0Bwi-Yys",
}


class MessageHandler:
    def __init__(self, token, config):
        # Инициализация бота (без webhook)
        self.bot = telebot.TeleBot(token)
        self.config = config
        self.logger = logging.getLogger("telegram_bot")

        # (chat_id, message_id, user_id) — защита от повторных ответов одним человеком
        self._answered = set()

        # ---------- Инициализация Google Sheets ----------
        self.gc = None
        self.sheet = None
        try:
            creds_json = os.getenv("GOOGLE_CREDENTIALS", "")
            spreadsheet_id = os.getenv("SPREADSHEET_ID", "")

            # Отладка окружения
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

                sh = self.gc.open_by_key(spreadsheet_id)
                try:
                    self.sheet = sh.worksheet("Responses")
                except gspread.exceptions.WorksheetNotFound:
                    self.sheet = sh.add_worksheet(title="Responses", rows="1000", cols="20")
                    self.sheet.append_row(
                        ["timestamp", "chat_title", "chat_id", "user", "user_id",
                         "question_key", "answer_value", "message_id"],
                        value_input_option="USER_ENTERED"
                    )

                self.logger.info(f"Google Sheets OK as {info.get('client_email')}")
            else:
                self.logger.warning(
                    "GOOGLE_CREDENTIALS or SPREADSHEET_ID not set – answers won't be saved to Sheets."
                )
        except Exception:
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
            # --- разбираем данные коллбэка ---
            try:
                _, key, val = call.data.split(":")
            except Exception:
                try:
                    self.bot.answer_callback_query(call.id, "Ошибка данных")
                finally:
                    return

            # --- защита от повторного ответа одним пользователем на тот же вопрос ---
            answered_key = (call.message.chat.id, call.message.message_id, call.from_user.id)
            if answered_key in self._answered:
                self.bot.answer_callback_query(call.id, "Вы уже отвечали 👍")
                return
            self._answered.add(answered_key)

            # --- запись в Google Sheets (если подключено) ---
            try:
                if self.sheet:
                    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    chat_title = getattr(call.message.chat, "title", "") or (call.message.chat.username or "")
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
                    self.logger.info(f"Sheet append OK: {row[:4]} ...")
                else:
                    self.logger.warning("Sheet is not initialized; skipping append.")
            except Exception:
                self.logger.exception("Failed to append to sheet")

            # --- ВИДЕО-ССЫЛКА: если это спец-ключ и нажали первую кнопку (yes) ---
            # Отправляем ссылку В ГРУППУ (ответом на исходное сообщение), чтобы не засорять чат.
            if key in WATCH_LINKS and val == "yes":
                url = WATCH_LINKS[key]
                try:
                    self.bot.send_message(
                        call.message.chat.id,
                        f"🎬 Видео по теме:\n{url}",
                        reply_to_message_id=call.message.message_id
                    )
                    self.bot.answer_callback_query(call.id, "Ссылка отправлена ✅")
                except Exception as e:
                    self.logger.error(f"Failed to send link in group: {e}")
                    try:
                        self.bot.answer_callback_query(call.id, "Не получилось отправить ссылку 😅")
                    except Exception:
                        pass
                return  # не шлём стандартную всплывашку ниже

            # --- ТИХИЙ РЕЖИМ ---
            # Только всплывающее уведомление. Сообщения в чат НЕ отправляем.
            try:
                self.bot.answer_callback_query(call.id, "Ответ сохранён ✅")
            except Exception as e:
                self.logger.error(f"Error in answer_callback_query: {e}")

            # Клавиатуру НЕ убираем — другие участники тоже могут ответить.

    # ---------------- Надёжный запуск polling ----------------
    def _run_polling_forever(self):
        """
        Запускает infinity_polling в бесконечном цикле.
        Если случился 409 Conflict (или сетевые проблемы),
        аккуратно ждём и пробуем снова.
        """
        # На всякий случай отключаем webhook перед polling
        try:
            # Без параметров (в некоторых версиях telebot нет drop_pending_updates)
            self.bot.remove_webhook()
            self.logger.info("Webhook removed (switching to polling).")
        except Exception as e:
            self.logger.warning(f"remove_webhook failed: {e}")

        backoff = 3  # начальная задержка между попытками
        while True:
            try:
                # Основной бесконечный polling
                self.logger.info("Starting infinity_polling...")
                # Параметры таймаутов помогают пережить сетевые разрывы
                self.bot.infinity_polling(timeout=60, long_polling_timeout=50)
            except ApiTelegramException as e:
                text = str(e)
                # Ошибка двойного запуска (409)
                if "409" in text or "Conflict" in text:
                    self.logger.error(
                        "409 Conflict: другой экземпляр бота сейчас получает обновления. "
                        "Ожидаю и пробую снова..."
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 60)  # экспоненциальный бэкофф, максимум 60 сек
                    continue
                # Любая другая ошибка Telegram API
                self.logger.exception("Telegram API error. Will retry.")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception:
                # Сетевые и прочие ошибки
                self.logger.exception("Unexpected error in polling. Will retry.")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            else:
                # Если infinity_polling завершился без исключения — небольшой перезапуск
                self.logger.info("Polling finished gracefully. Restarting shortly...")
                time.sleep(2)
                backoff = 3  # сбрасываем бэкофф

    # ---------------- Запуск ----------------
    def start(self):
        self.logger.info("Бот запущен и готов к приёму сообщений.")
        self._run_polling_forever()
