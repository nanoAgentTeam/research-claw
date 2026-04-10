import os
import json
import base64
import hashlib
from typing import Any

try:
    import pymupdf4llm
    import fitz
except ImportError:
    pymupdf4llm = None
    fitz = None

from core.infra.config import Config
from core.utils.logger import Logger

class StorageService:
    """
    持久化存储服务

    负责系统所有非数据库数据的存储管理，包括：
    1. 浏览历史的 JSONL 归档
    2. 富媒体内容（截图、PDF、MHTML）的物理保存
    3. 内容转换（PDF 到 Markdown）

    该类所有方法均为静态方法，提供全局一致的存储访问。
    """

    @staticmethod
    def append_history(data: dict):
        """
        将一条浏览记录追加到 JSONL 历史文件中

        Args:
            data: 会话数据字典，将被序列化为 JSON 行
        """
        try:
            with open(Config.DATA_PATH, 'a', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
                f.write('\n')
        except Exception as e:
            Logger.error(f"写入历史记录失败: {e}")

    @staticmethod
    def save_content(timestamp: float, capture_mode: str, content_type: str, data: Any) -> str:
        """
        保存捕获的内容文件（截图/文本/PDF/归档）

        这是保存富媒体内容的统一入口。它根据 capture_mode 自动路由到不同的存储逻辑。

        Args:
            timestamp: 捕获的时间戳（秒），用于生成唯一文件名
            capture_mode: 捕获模式，定义了内容的来源和处理方式
                          可选值: FULL_SCREENSHOT, VISIBLE_SCREENSHOT, MARKDOWN,
                                 MARKDOWN_PLUS, MHTML, PDF
            content_type: 内容的 MIME 类型或格式标识（如 'pdf', 'image/png'）
            data: 原始数据
                  - 截图/二进制：Base64 编码字符串
                  - 文本/Markdown：纯文本字符串
                  - MARKDOWN_PLUS：包含 'text' 和 'images' 的字典

        Returns:
            str: 存储后的相对路径（如 "screenshots/123.png"），用于数据库记录
                 如果保存失败，返回 None
        """
        try:
            # 使用毫秒级时间戳作为文件名基准
            filename_base = str(int(timestamp * 1000))

            if not capture_mode:
                return None

            # 路由处理逻辑
            if capture_mode in ["FULL_SCREENSHOT", "VISIBLE_SCREENSHOT"]:
                return StorageService._save_screenshot(filename_base, data)

            elif capture_mode == "MARKDOWN":
                # 处理 PDF 转换请求
                if content_type and content_type.lower() == 'pdf':
                    data = StorageService.convert_pdf_to_markdown(data)
                return StorageService._save_text(filename_base, data)

            elif capture_mode == "MARKDOWN_PLUS":
                return StorageService._save_markdown_plus(filename_base, data)

            elif capture_mode == "MHTML":
                return StorageService._save_binary(filename_base, data, Config.ARCHIVE_DIR, "archives", ".mhtml")

            elif capture_mode == "PDF":
                return StorageService._save_binary(filename_base, data, Config.PDF_DIR, "pdfs", ".pdf")

            return None
        except Exception as e:
            Logger.error(f"保存内容失败: {e}")
            return None

    @staticmethod
    def _save_screenshot(filename_base, data):
        """保存截图"""
        header, encoded = data.split(",", 1)
        file_ext = ".jpg" if "jpeg" in header else ".png"
        binary_data = base64.b64decode(encoded)
        filename = f"{filename_base}{file_ext}"
        filepath = os.path.join(Config.SCREENSHOT_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(binary_data)
        return f"screenshots/{filename}"

    @staticmethod
    def _save_text(filename_base, data):
        """保存纯文本/Markdown"""
        filename = f"{filename_base}.md"
        filepath = os.path.join(Config.TEXT_DIR, filename)
        with open(filepath, "w", encoding='utf-8') as f:
            f.write(data)
        return f"page_texts/{filename}"

    @staticmethod
    def _save_markdown_plus(filename_base, data):
        """保存包含内嵌图片的 Markdown"""
        raw_text = data.get('text', '')
        image_map = data.get('images', {})

        final_text = raw_text

        # 处理内嵌图片
        for img_url, img_data in image_map.items():
            try:
                ext = ".jpg"
                if "png" in img_data[:20]: ext = ".png"
                if "gif" in img_data[:20]: ext = ".gif"

                if "," in img_data:
                    _, encoded = img_data.split(",", 1)
                else:
                    encoded = img_data

                binary = base64.b64decode(encoded)

                # 使用 URL 哈希作为文件名的一部分，避免重复
                url_hash = hashlib.md5(img_url.encode('utf-8')).hexdigest()[:10]
                img_filename = f"{filename_base}_{url_hash}{ext}"
                img_filepath = os.path.join(Config.IMAGES_DIR, img_filename)

                with open(img_filepath, "wb") as f:
                    f.write(binary)

                # 替换 Markdown 中的图片链接为相对路径
                rel_path = f"../page_images/{img_filename}"
                final_text = final_text.replace(img_url, rel_path)

            except Exception as e:
                Logger.error(f"保存内嵌图片失败 {img_url}: {e}")

        # 保存处理后的 Markdown
        filename = f"{filename_base}.md"
        filepath = os.path.join(Config.TEXT_DIR, filename)
        with open(filepath, "w", encoding='utf-8') as f:
            f.write(final_text)

        return f"page_texts/{filename}"

    @staticmethod
    def convert_pdf_to_markdown(data):
        """将 PDF 内容转换为 Markdown 保存"""
        try:
            if "," in data:
                _, encoded = data.split(",", 1)
            else:
                encoded = data
            binary_data = base64.b64decode(encoded)

            with fitz.open(stream=binary_data, filetype="pdf") as doc:
                md_text = pymupdf4llm.to_markdown(doc)

            return md_text
        except Exception as e:
            Logger.error(f"PDF 转 Markdown 失败: {e}")
            return f"Error converting PDF: {e}"

    @staticmethod
    def _save_binary(filename_base, data, target_dir, rel_dir_name, ext):
        """通用二进制文件保存"""
        if "," in data: _, encoded = data.split(",", 1)
        else: encoded = data
        binary_data = base64.b64decode(encoded)
        filename = f"{filename_base}{ext}"
        filepath = os.path.join(target_dir, filename)
        with open(filepath, "wb") as f:
            f.write(binary_data)
        return f"{rel_dir_name}/{filename}"
