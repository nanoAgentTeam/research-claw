# QQ Push Configuration

## Register a Push Account

Visit https://qmsg.zendee.cn/ and click "Get Started".

![1772608746513](image/QQ/1772608746513.png)

Log in with your QQ account.

![1772608770544](image/QQ/1772608770544.png)

After logging in, click "Get Started" again.

![1772609454734](image/QQ/1772609454734.png)

Click to select a bot, and add the bot as a friend on QQ.

If you can't add one, try another.

![1772609515945](image/QQ/1772609515945.png)

Copy the key — you will need it later for the gateway configuration.

![1772609690178](image/QQ/1772609690178.png)

Click "QQ Number" and enter your own QQ number.

![1772609772192](image/QQ/1772609772192.png)

## Configure in WebUI

Start the gateway: `python cli/main.py gateway`

Visit `http://127.0.0.1:18790/ui/`

Enter the copied Key and your QQ number (this QQ number must have the bot added as a friend and be included in the QQ list above).

![1772610743199](image/QQ/1772610743199.png)

After adding, click "Test".

Restart the gateway, and you will start receiving subscriptions via QQ.
