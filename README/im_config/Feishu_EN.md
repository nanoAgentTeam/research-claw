# Feishu (Lark) IM Configuration

## Register a Feishu Bot

Feishu Open Platform: [https://open.feishu.cn](https://open.feishu.cn/)

> If you don't have a Feishu account, you need to register one first.

Click the top-right corner to enter the **Developer Console**:

![1772518210271](image/feishu_ZH/1772518210271.png)

Create an application:

![1772518307946](image/feishu_ZH/1772518307946.png)

Fill in the application name and description:

![1772518460233](image/feishu_ZH/1772518460233.png)

Save the **App ID** and **App Secret** — you will need them later.

![1772518539507](image/feishu_ZH/1772518539507.png)

Click **Create Bot**:

![1772518597653](image/feishu_ZH/1772518597653.png)

After adding the bot, a new bot entry will appear here:

![1772518837376](image/feishu_ZH/1772518837376.png)

Configure permissions for the application:

![1772518919041](image/feishu_ZH/1772518919041.png)

Search for **im**:

Click on **Messages & Groups** and enable all permissions:

![1772519280581](image/feishu_ZH/1772519280581.png)![1772519074124](image/feishu_ZH/1772519074124.png)

You also need to enable the file sending capability: **im:resource**

![1773396297487](image/feishu_ZH/1773396297487.png)

Create a version:

![1772519280581](image/feishu_ZH/1772519280581.png)

Add a version number and feature description, keep other settings as default, then save:

![1772519334998](image/feishu_ZH/1772519334998.png)

Confirm and publish:

![1772519385208](image/feishu_ZH/1772519385208.png)

Application published successfully:

![1772519441946](image/feishu_ZH/1772519441946.png)

If approval is required, go to **Feishu Approval**:

![1772519476451](image/feishu_ZH/1772519476451.png)

Next, configure the event callback. First, start the open-research-claw UI:

`python cli/main.py gateway`

Visit the web interface and add the Feishu **App ID** and **App Secret**.

(Location of App ID and App Secret in Feishu):

![1772518539507](image/feishu_ZH/1772518539507.png)

![1772519792135](image/feishu_ZH/1772519792135.png)

After adding, click **Activate**:

![1772521712822](image/feishu_ZH/1772521712822.png)

After adding, restart the gateway: `python cli/main.py gateway`

Then go back to the Feishu page, click **Events & Callbacks** → **Subscription Method**:

![1772520027117](image/feishu_ZH/1772520027117.png)

Select **Long Connection**:

![1772520074150](image/feishu_ZH/1772520074150.png)

After enabling long connection, the **Add Event** button becomes available. Click **Add Event**:

![1772520128522](image/feishu_ZH/1772520128522.png)

Add the following event: **Receive Message**
`im.message.receive_v1`

![1772521457997](image/feishu_ZH/1772521457997.png)

Then republish a new version:

![1772521501221](image/feishu_ZH/1772521501221.png)

![1772521543351](image/feishu_ZH/1772521543351.png)

Follow the same steps as before — publish as an online application.

Now you can chat with the AI assistant in Feishu!

## Chat in Feishu

Restart the gateway: `python cli/main.py gateway`

Then open the Feishu app and click **Search**:

![1772521808240](image/feishu_ZH/1772521808240.png)

Search for the bot name you set earlier. In this example, it's `overleaf-claw`. Click on it:

![1772521853687](image/feishu_ZH/1772521853687.png)

Send a message to start using it:

![1772523142205](image/feishu_ZH/1772523142205.png)
