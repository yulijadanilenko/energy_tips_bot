import logging
import os
from message_handler import MessageHandler

def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    token = os.getenv('BOT_TOKEN','').strip()
    if not token:
        raise RuntimeError('Не задан BOT_TOKEN')
    MessageHandler(token).start()

if __name__ == '__main__':
    main()
