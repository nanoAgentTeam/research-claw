# 钉钉聊天配置

参考：https://catchadmin.com/post/2026-01/openclaw-dingding-install

## 创建应用

登录[钉钉开放平台](https://open-dev.dingtalk.com/)，点击「创建应用」

> 注意：创建钉钉应用需要你的钉钉账号有开发者权限。如果没有，可以联系组织管理员获取，或参考[获取开发者权限](https://open.dingtalk.com/document/orgapp/obtain-developer-permissions)
>
> 或者自己创建一个组织

在应用开发的左侧导航栏中，点击「钉钉应用」，然后点击右上角「创建应用」。

![1772534796825](image/DingTalk_ZH/1772534796825.png)

填写应用名称和应用描述，上传应用图标后保存。

![1772534812662](image/DingTalk_ZH/1772534812662.png)

### 添加机器人

在应用开发的左侧导航栏中，点击「添加应用能力」，然后点击添加「机器人」。

![1772534885351](image/DingTalk_ZH/1772534885351.png)

添加完机器人之后，就是配置一些基本信息之后，点击发布。最后得消息接受模式一定要是 `stream 模式`

![1772534895436](image/DingTalk_ZH/1772534895436.png)

### 发布版本

在发布完机器人之后，一定要发布版本。在应用开发的左侧导航栏中，点击「版本管理与发布」，然后点击右上角「创建新版本」。

![1772534939081](image/DingTalk_ZH/1772534939081.png)

### 获取凭证信息

发布版本成功之后，点击左侧菜单的「凭证与基础信息」，获取以下凭证信息

* Client ID (AppKey)
* Client Secret (AppSecret)
* Robot Code (与 Client ID 相同)
* Agent ID (应用 ID)

![1772534966606](image/DingTalk_ZH/1772534966606.png)

Corp ID (企业 ID)

![1772535009157](image/DingTalk_ZH/1772535009157.png)

## 填写key

启动gateway `python cli/main.py gateway`

![1772535080769](image/DingTalk_ZH/1772535080769.png)

然后重启gateway `python cli/main.py gateway`

## 对话

回到钉钉客户端软件，在顶部搜索栏目搜索机器人名称 `openclaw`

![1772535135703](image/DingTalk_ZH/1772535135703.png)
