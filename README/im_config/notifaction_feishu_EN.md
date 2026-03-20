# Feishu Push Configuration

## Create a Push Bot

In a group chat, click here to view bots (if you don't have a group, you can create one yourself).
![1772800515610](image/feishu_ZH/1772800515610.png)
Click "Add Robot".
![1772800578609](image/feishu_ZH/1772800578609.png)
Click "Custom Robot".
![1772800652399](image/feishu_ZH/1772800652399.png)

Fill in the information in these two fields.
![1772800705375](image/feishu_ZH/1772800705375.png)

Copy the webhook URL and click "Finish".
![1772800753424](image/feishu_ZH/1772800753424.png)

Click "Skip for now".
![1772800826407](image/feishu_ZH/1772800826407.png)

## WebUI Configuration

Start the WebUI: `python cli/main.py gateway`

Visit the WebUI at `http://127.0.0.1:18790/ui/` and add the webhook.
![1772800986203](image/feishu_ZH/1772800986203.png)

Click "Test" — it will show "Sent".
![1772801050011](image/feishu_ZH/1772801050011.png)

Receiving a push notification in Feishu means the configuration is successful.
![1772801097326](image/feishu_ZH/1772801097326.png)
