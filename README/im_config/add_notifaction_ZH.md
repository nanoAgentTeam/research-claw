# 添加推送订阅

流程都是一样的，以Feishu为例

前提条件：

- 配置好Feishu Bot
- 配置好LLM Model

启动 python cli/main.py gateway

访问web ui：http://127.0.0.1:18790/ui/

在Feishu给Bot发送一条消息

在web ui 的推送订阅中就可以看到 Feishu的渠道，点击添加订阅

![1773419120492](image/add_notifaction_ZH/1773419120492.png)

就添加了im 推送订阅

![1773419222347](image/add_notifaction_ZH/1773419222347.png)

点击测试，可以在feishu里面收到推送消息，即代表成功

![1773419290496](image/add_notifaction_ZH/1773419290496.png)

在自动化任务中，选择好项目

（如果没有，可以在im中对话，让bot创建一个项目）

勾选上im_feishu，就可以进行消息推送了（自动化任务执行完成就会推送消息）

![1773419403229](image/add_notifaction_ZH/1773419403229.png)
