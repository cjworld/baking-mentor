from dotenv import load_dotenv

import logging
import os
import json
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage
)
from openai import OpenAI

load_dotenv()

# 初始化 Flask app
app = Flask(__name__)

# 環境變數 (上線請用 os.getenv)
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

client = OpenAI(api_key=OPENAI_API_KEY)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# 假設每個user有自己的歷史對話，這裡用簡單的 dict 模擬
chat_histories = {}

def get_chat_history(user_id):
    return chat_histories.get(user_id, [
        {"role": "system", "content": "你是專業的烘焙師, 我會問你烘焙的問題, 你會用what's app的對話方式回答問題, 而且一次不會回答超過5則訊息"}
    ])

def ask_openai(user_id, user_message):
    history = get_chat_history(user_id)
    history.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(model="gpt-4o", messages=history)
    ai_reply = response.choices[0].message.content
    history.append({"role": "assistant", "content": ai_reply})

    chat_histories[user_id] = history

    logging.info(f'Updating chat history for user {user_id}:')
    logging.info(json.dumps(chat_histories[user_id], ensure_ascii=False, indent=2))

    ai_replies = ai_reply.split('\n\n')
    return ai_replies

# 接收 LINE Webhook 訊息的 endpoint
@app.route("/api/linewebhook", methods=['POST'])
def linewebhook():
    signature = request.headers.get('X-Line-Signature')

    body = request.get_data(as_text=True)
    logging.info(f"Received a request.")
    logging.info(f"Request body: {body}")

    if signature is None:
        logging.error("Missing X-Line-Signature header.")
        abort(400, "Missing signature")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logging.error("Invalid signature.")
        abort(400, "Invalid signature")
    except Exception as e:
        logging.error(f"Error: {e}")
        abort(500, f"Error: {e}")

    return 'OK', 200

# 處理文字訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    user_text = event.message.text
    logging.info(f'Message from Line user {user_id}: {user_text}')
    ai_replies = ask_openai(user_id, user_text)
    logging.info(f'Reply from OpenAI: {ai_replies}')
    text_replies = [TextSendMessage(text=reply) for reply in ai_replies]
    line_bot_api.reply_message(
        event.reply_token,
        text_replies
    )

# 處理圖片訊息
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    reply = "已收到圖片，我們現在還不會處理圖片，請給我們一點時間！"
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8000)
