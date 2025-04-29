from dotenv import load_dotenv

import imghdr
import json
import logging
import os

from azure.storage.blob import BlobServiceClient, ContentSettings
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage
)
from openai import OpenAI

logging.basicConfig(level=logging.INFO)  # 設定全局 logging level
logger = logging.getLogger('gunicorn.error')  # 抓 gunicorn logger

load_dotenv()

# 初始化 Flask app
app = Flask(__name__)
app.logger.handlers = logger.handlers
app.logger.setLevel(logging.INFO)

# 設定Line Bot API
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
line_webhook_handler = WebhookHandler(CHANNEL_SECRET)

# 設定OpenAI API
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# 設定Azure Storage Account API
AZURE_STORAGE_ACCOUNT_CONNECTION_KEY = os.getenv('AZURE_STORAGE_ACCOUNT_CONNECTION_KEY')
blob_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_ACCOUNT_CONNECTION_KEY)

# 假設每個user有自己的歷史對話，這裡用簡單的 dict 模擬
chat_histories = {}


def get_chat_history(user_id):
    return chat_histories.get(user_id, [
        {"role": "system", "content": "你是專業的烘焙師, 我會問你烘焙的問題, 你會用what's app的對話方式回答問題, 而且一次不會回答超過5則訊息"}
    ])


def add_user_text(user_id, user_text):
    history = get_chat_history(user_id)
    history.append({"role": "user", "content": user_text})
    chat_histories[user_id] = history


def add_user_image(user_id, user_image_url):
    history = get_chat_history(user_id)
    content = [{"type": "image_url", "image_url": {"url": user_image_url}}]
    history.append({"role": "user", "content": content})
    chat_histories[user_id] = history


def ask_openai(user_id):
    history = get_chat_history(user_id)
    response = openai_client.chat.completions.create(model="gpt-4o", messages=history)
    ai_reply = response.choices[0].message.content

    # 更新對話紀錄
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
        line_webhook_handler.handle(body, signature)
    except InvalidSignatureError:
        logging.error("Invalid signature.")
        abort(400, "Invalid signature")
    except Exception as e:
        logging.error(f"Error: {e}")
        abort(500, f"Error: {e}")

    return 'OK', 200

# 處理文字訊息
@line_webhook_handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    user_text = event.message.text
    logging.info(f'Message from Line user {user_id}: {user_text}')

    add_user_text(user_id, user_text)
    ai_replies = ask_openai(user_id)
    logging.info(f'Replies from OpenAI: {ai_replies}')

    reply_messages = [TextSendMessage(text=reply) for reply in ai_replies]
    line_bot_api.reply_message(
        event.reply_token,
        reply_messages
    )

# 處理圖片訊息
@line_webhook_handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    logging.info(f'Image from Line user {user_id} for message {message_id}')

    # Read the image and identify the extension. JPEG as default extension.
    content = line_bot_api.get_message_content(message_id)
    binary = b''.join(chunk for chunk in content.iter_content())
    ext = imghdr.what(None, h=binary) or 'jpg'

    # Cache the image locally
    filename = f"image_message_{message_id}_user_{user_id}.{ext}"
    with open(filename, 'wb') as f:
        f.write(binary)
    logging.info(f'Image {filename} has been cached locally')

    # container client
    storage_account_name = 'bakingmentor'
    container_name = 'userimages'
    container_client = blob_client.get_container_client(container_name)

    # Upload the image as a blob
    with open(filename, "rb") as data:
        container_client.upload_blob(
            name=filename,
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type='image/jpeg')
        )
    logging.info(f'Image {filename} has been uploaded to Azure storage account {storage_account_name} container {container_name}')

    blob_url = f"https://{storage_account_name}.blob.core.windows.net/{container_name}/{filename}"
    logging.info(f'Blob URL: {blob_url}')

    add_user_image(user_id, blob_url)
    ai_replies = ask_openai(user_id)
    logging.info(f'Replies from OpenAI: {ai_replies}')

    reply_messages = [TextSendMessage(text=reply) for reply in ai_replies]
    line_bot_api.reply_message(
        event.reply_token,
        reply_messages
    )

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8000)
