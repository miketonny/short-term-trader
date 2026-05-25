import requests, os

TOKEN = os.environ.get('TG_TOKEN', '')
CHAT_ID = os.environ.get('TG_CHAT_ID', '6849175810')

def send(text):
    if not TOKEN:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TOKEN}/sendMessage',
            json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
            timeout=5
        )
    except:
        pass  # silent fail - trading is priority
