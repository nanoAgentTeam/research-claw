# Telegram Push Configuration

## Obtain a Token

If you want a dedicated push channel, you can create a new bot specifically for push notifications.

Open Telegram and search for **BotFather**.

![1772613659710](image/Telegram/1772613659710.png)

Send `/newbot`, follow the prompts to set the bot name and username. Once completed, you will receive an API Token — make sure to save it, as it will be needed for configuration.

![1772613763006](image/Telegram/1772613763006.png)

## Obtain Your Chat ID

Add **userinfobot** to get your ID. Send `@userinfobot` in any chat window.

Then click on the message to open a chat with **userinfobot**.

Send any message to **userinfobot** and it will return your information.

This includes an ID — that is the **chat_id** we need.

![1772626437699](image/Telegram_ZH/1772626437699.png)

## Enter the Token and Chat ID in WebUI

Start the gateway: `python cli/main.py gateway`

Enter the Token and Chat ID, then save.

![1772626630859](image/Telegram_ZH/1772626630859.png)

Click "Test" — it will show that the message has been sent.

Receiving a message in Telegram confirms the configuration is successful.

![1772626676933](image/Telegram_ZH/1772626676933.png)
