import sys
import logging
from pathlib import Path

# 1. 确保能够导入项目根目录下的 auth 模块
# 路径推导：qq/qq/config.py -> qq/qq ->qq->im_api ->overleaf_sync/ -> testing_ground/ -> root
root_dir = Path(__file__).parent.parent.parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))

try:
    from auth.credential_manager import get_bot_config
except ImportError:
    logging.warning("未能从 auth.credential_manager 导入配置接口")
    get_bot_config = lambda x: None

def get_qq_config():
    """
    通过项目统一的 credential_manager 获取飞书配置。
    读取 auth/config.yaml 中的 BotConfig -> FeiShu 部分。
    """
    try:
        feishu_cfg = get_bot_config("QQ")
        if feishu_cfg:
            app_id = feishu_cfg.get("APP_ID")
            app_secret = feishu_cfg.get("APP_SECRET")
            if app_id and app_secret:
                return app_id, app_secret

        logging.warning("auth/config.yaml 中未找到有效的 FeiShu 配置 (APP_ID 或 APP_SECRET 缺失)")
    except Exception as e:
        logging.error(f"从 credential_manager 获取飞书配置时出错: {e}")

    return None, None

if __name__ == "__main__":
    # 简单测试读取配置
    app_id, app_secret = get_qq_config()
    if app_id and app_secret:
        print("成功读取 QQ 配置:")
        print(f"APP_ID: {app_id}")
        print(f"APP_SECRET: {app_secret}")
    else:
        print("未能读取到有效的 QQ 配置，请检查 auth/config.yaml 中的 BotConfig -> QQ 部分。")
