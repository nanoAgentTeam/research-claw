# telegram 推送配置

## 获取Token

如果要单独推送，可以创建一个新的bot专门用于推送

打开telegram，搜索BotFather

![1772613659710](image/Telegram/1772613659710.png)输入 /newbot，按提示设置机器人名称和用户名，完成后会收到一个 API Token，请务必保存好，后面配置时需要用到。

![1772613763006](image/Telegram/1772613763006.png)

## 获取chat id

添加userinfobot 获取你的ID，在任意聊天窗口发送 `@userinfobot`

然后点击这条消息即可打开与**userinfobot**的聊天，

发送任意消息给**userinfobot**它会返回你的信息

其中包含一个ID，这就是我们需要的**chat_id**

![1772626437699](image/Telegram_ZH/1772626437699.png)

## 把token 和chat_id填入 web ui

启动gateway `python cli/main.py gateway`

填入Token 和 Chat id,保存即可

![1772626630859](image/Telegram_ZH/1772626630859.png)

点击测试 test，会显示已经发送消息

在telegram收到信息，表明配置成功

![1772626676933](image/Telegram_ZH/1772626676933.png)
