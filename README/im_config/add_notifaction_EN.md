# Method1: Add Push Notification Subscription

The process is the same for all platforms. Here we use Feishu as an example.

Prerequisites:

- Feishu Bot is configured
- LLM is configured

Start the gateway: `python cli/main.py gateway`

Visit the Web UI: http://127.0.0.1:18790/ui/

Send a message to the Bot in Feishu.

In the Web UI's **Push Subscription** section, you will see the Feishu channel. Click **Add Subscription**.

![1773419120492](image/add_notifaction_ZH/1773419120492.png)

The IM push subscription has been added.

![1773419222347](image/add_notifaction_ZH/1773419222347.png)

Click **Test** — if you receive a push message in Feishu, it means the setup was successful.

![1773419290496](image/add_notifaction_ZH/1773419290496.png)

In **Automation Tasks**, select a project.

(If you don't have one, you can chat with the bot in IM and ask it to create a project.)

Check **im_feishu** to enable message push notifications (a push message will be sent when an automation task is completed).

![1773419403229](image/add_notifaction_ZH/1773419403229.png)

## Method 2: Push Subscription – For Push Notifications Only

This method is similar to email push and is  **only used for sending notifications** ; no further communication can be made based on the pushed messages.

Compared to Method 1, this approach requires additional configuration of other bots or email services and involves more steps. It is suitable for users who only want to receive push notifications.

* [Feishu Push Configuration](notifaction_feishu_EN.md)
* [Telegram Push Configuration](notifaction_Telegram_EN.md)
* [QQ Push Configuration](notifaction_QQBot_EN.md)
* [DingTalk Push Configuration](notifaction_DingTalk_EN.md)
* [Email Push Configuration](notifaction_email_EN.md)
