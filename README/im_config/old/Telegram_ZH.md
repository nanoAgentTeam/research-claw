# Telegram 聊天配置

请先在web ui配置好模型参数

参考：https://zhuanlan.zhihu.com/p/2005005876503790436

## 获取Token

打开telegram，搜索BotFather

![1772613659710](image/Telegram/1772613659710.png)输入 /newbot，按提示设置机器人名称和用户名，完成后会收到一个 API Token，请务必保存好，后面配置时需要用到。

![1772613763006](image/Telegram/1772613763006.png)

搜索你刚创建的 Bot 用户名，进入聊天界面，点击 Start。

![1772614304599](image/Telegram/1772614304599.png)

现在输入 你好，还不会回复消息

## web ui配置token

启动gateway `python cli/main.py gateway`

填入telegram的token

![1772624563503](image/Telegram/1772624563503.png)

然后重启gateway

再发送消息就能回复了

![1772624660398](image/Telegram_ZH/1772624660398.png)


# telegram 推送消息

token，在上面步骤已经获取

还需要获取chat id

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
