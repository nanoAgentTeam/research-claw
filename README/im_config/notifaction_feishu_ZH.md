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
