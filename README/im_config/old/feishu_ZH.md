# feishu im 配置

## 注册飞书机器人

飞书开放平台地址：[https://open.feishu.cn](https://open.feishu.cn/)

> 没有飞书账号的，需要自己注册账号

点击右上角进入 开发者后台：

![1772518210271](image/feishu_ZH/1772518210271.png)

创建应用

![1772518307946](image/feishu_ZH/1772518307946.png)

填写应用名称和应用描述

![1772518460233](image/feishu_ZH/1772518460233.png)

AppID和APPSecret保存好，后面需要用到

![1772518539507](image/feishu_ZH/1772518539507.png)

点击创建机器人

![1772518597653](image/feishu_ZH/1772518597653.png)

添加机器人后，这里多了个机器人

![1772518837376](image/feishu_ZH/1772518837376.png)

给应用配置权限

![1772518919041](image/feishu_ZH/1772518919041.png)

搜索im:

点击消息与群组，把权限全都开通

![1772519280581](image/feishu_ZH/1772519280581.png)![1772519074124](image/feishu_ZH/1772519074124.png)

还要开通发送文件功能 im:resource

![1773396297487](image/feishu_ZH/1773396297487.png)

创建版本

![1772519280581](image/feishu_ZH/1772519280581.png)

添加版本号与功能说明，其他默认，然后保存

![1772519334998](image/feishu_ZH/1772519334998.png)

确认发布

![1772519385208](image/feishu_ZH/1772519385208.png)

应用成功发布

![1772519441946](image/feishu_ZH/1772519441946.png)

如果需要审核，则需要来到飞书审批

![1772519476451](image/feishu_ZH/1772519476451.png)

接下来配置事件回调，首先启动open-overleaf-claw的UI

`python cli/main.py gateway`

访问web界面，添加飞书的AppID和AppSecret

(飞书的AppID和AppSecre位置)

![1772518539507](image/feishu_ZH/1772518539507.png)

![1772519792135](image/feishu_ZH/1772519792135.png)

添加之后，点击激活

![1772521712822](image/feishu_ZH/1772521712822.png)

添加后重新启动gateway `python cli/main.py gateway`

然后回到飞书页面，点击事件与回调-订阅方式

![1772520027117](image/feishu_ZH/1772520027117.png)

选择使用长连接

![1772520074150](image/feishu_ZH/1772520074150.png)

开启长连接后，添加事件按钮变得可用，点击添加事件

![1772520128522](image/feishu_ZH/1772520128522.png)

添加以下事件：接收消息
im.message.receive_v1

![1772521457997](image/feishu_ZH/1772521457997.png)

然后重新发布版本

![1772521501221](image/feishu_ZH/1772521501221.png)

![1772521543351](image/feishu_ZH/1772521543351.png)

跟前面的步骤一样，发布为在线应用即可。

现在可以在 飞书中与 AI 助手对话了！

## 飞书中对话

现在重新启动gateway  `python cli/main.py gateway`

然后打开飞书应用，点击搜索

![1772521808240](image/feishu_ZH/1772521808240.png)

搜索填的机器人名字，我这里是overleaf-claw，然后点击

![1772521853687](image/feishu_ZH/1772521853687.png)

发送消息即可使用

![1772523142205](image/feishu_ZH/1772523142205.png)

# feishu推送配置

## 创建推送机器人

在群组里点击这里查看机器人（如果没有群，可以自己建一个）
![1772800515610](image/feishu_ZH/1772800515610.png)
点击添加机器人
![1772800578609](image/feishu_ZH/1772800578609.png)
点击自定义机器人
![1772800652399](image/feishu_ZH/1772800652399.png)

把信息填入这两处
![1772800705375](image/feishu_ZH/1772800705375.png)

复制webhook地址，点击完成
![1772800753424](image/feishu_ZH/1772800753424.png)

点击暂不配置
![1772800826407](image/feishu_ZH/1772800826407.png)

## webui添加配置

启动webui `python cli/main.py gateway`

访问webui：`http://127.0.0.1:18790/ui/`,添加webhook
![1772800986203](image/feishu_ZH/1772800986203.png)

点击测试，会显示已经发送
![1772801050011](image/feishu_ZH/1772801050011.png)

在飞书收到推送，意味着配置成功
![1772801097326](image/feishu_ZH/1772801097326.png)
