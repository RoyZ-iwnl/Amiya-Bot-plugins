import re
import os
import time
import json
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

from PIL import Image

from amiyabot.network.download import download_async
from amiyabot.network.httpRequests import http_requests
from core.util import remove_xml_tag, char_seat, create_dir

from amiyabot.builtin.lib.browserService import basic_browser_service
from core import send_to_console_channel, Chain

HEADERS_PATH = os.path.join(os.path.dirname(__file__), "headers.json")

def get_default_headers(weibo_id: str) -> dict:
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36',
        'Content-Type': 'application/json; charset=utf-8',
        'Referer': f'https://m.weibo.cn/u/{weibo_id}',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Cookie': ''
    }

async def fetch_weibo_cookies() -> list:
    browser = basic_browser_service.browser
    context = await browser.new_context()
    page = await context.new_page()
    try:
        await page.goto("https://weibo.com")
        await page.wait_for_timeout(5000)
        cookies = await context.cookies()
        weibo_cookies = [cookie for cookie in cookies if cookie.get('domain') in ('.weibo.com', 'weibo.com')]
        return weibo_cookies
    finally:
        await page.close()
        await context.close()

def cookies_to_str(cookies: list) -> str:
    if not cookies: return ''
    return '; '.join([f"{cookie['name']}={cookie['value']}" for cookie in cookies])

def to_lowercase_keys(d: dict) -> dict:
    # helper小工具
    return {k.lower(): v for k, v in d.items() if not k.startswith(":")}

async def load_headers_from_json_or_fallback(weibo_id: str) -> dict:
    if os.path.exists(HEADERS_PATH):
        try:
            with open(HEADERS_PATH, "r", encoding="utf-8") as f:
                headers = json.load(f)
            clean_headers = to_lowercase_keys(headers)
            if not clean_headers.get('cookie'):
                cookies = await fetch_weibo_cookies()
                clean_headers['cookie'] = cookies_to_str(cookies)
                with open(HEADERS_PATH, "w", encoding="utf-8") as f:
                    json.dump(clean_headers, f, ensure_ascii=False, indent=2)
                await send_to_console_channel(Chain().text('微博 header.json 无Cookie，已自动刷新并写入cookie。'))
            return clean_headers
        except Exception as e:
            await send_to_console_channel(Chain().text(f'微博 header.json 读取失败，自动降级获取cookie: {e}'))
    # fallback
    headers = get_default_headers(weibo_id)
    cookies = await fetch_weibo_cookies()
    headers['Cookie'] = cookies_to_str(cookies)
    with open(HEADERS_PATH, "w", encoding="utf-8") as f:
        json.dump(headers, f, ensure_ascii=False, indent=2)
    await send_to_console_channel(Chain().text('微博 header.json 不存在，已自动生成并写入cookie。'))
    return headers

async def refresh_headers_and_json(weibo_id: str, info: str = ''):
    headers = get_default_headers(weibo_id)
    cookies = await fetch_weibo_cookies()
    headers['Cookie'] = cookies_to_str(cookies)
    with open(HEADERS_PATH, "w", encoding="utf-8") as f:
        json.dump(headers, f, ensure_ascii=False, indent=2)
    await send_to_console_channel(Chain().text(f'微博已自动刷新Cookie并写入header.json。原因: {info}'))

@dataclass
class WeiboContent:
    user_name: str
    html_text: str = ''
    detail_url: str = ''
    pics_list: list = field(default_factory=list)
    pics_urls: list = field(default_factory=list)
    gif_list: list = field(default_factory=list)
    gif_urls: list = field(default_factory=list)

class WeiboUser:
    def __init__(self, weibo_id, setting):
        self.url = 'https://m.weibo.cn/api/container/getIndex'
        self.weibo_id = weibo_id
        self.setting = setting
        self.user_name = ''
        self.images_cache_dir = self.setting.imagesCache
        self.headers = None

    async def ensure_headers(self):
        if self.headers is None:
            self.headers = await load_headers_from_json_or_fallback(self.weibo_id)

    async def try_request(self, url):
        await self.ensure_headers()
        for _ in range(2):  # 最多重试1次
            res = await http_requests.get(url, headers=self.headers)
            try:
                if res and res.response.status == 200:
                    respjson = res.json
                    if isinstance(respjson, dict) and 'data' in respjson:
                        return respjson
                    else:
                        await refresh_headers_and_json(self.weibo_id, "接口无data字段，判定cookie失效，已自动刷新。")
                        self.headers = await load_headers_from_json_or_fallback(self.weibo_id)
                        continue  # retry
            except Exception as e:
                await send_to_console_channel(Chain().text(f'微博接口访问异常: {e}'))
            return None
        await send_to_console_channel(Chain().text("微博接口重试多次仍然异常，请检查网络和header配置。"))
        return None

    def __url(self, container_id=None):
        c_id = f'&containerid={container_id}' if container_id else ''
        return f'{self.url}?type=uid&uid={self.weibo_id}&value={self.weibo_id}{c_id}'

    async def get_user_name(self, result=None):
        if self.user_name:
            return self.user_name
        if not result:
            result = await self.try_request(self.__url())
        if not result:
            return self.user_name
        if 'userInfo' not in result['data']:
            return self.user_name
        self.user_name = result['data']['userInfo']['screen_name']
        return self.user_name

    async def get_cards_list(self):
        cards = []
        result = await self.try_request(self.__url())
        if not result or 'tabsInfo' not in result['data']:
            return cards
        await self.get_user_name(result)
        tabs = result['data']['tabsInfo']['tabs']
        container_id = ''
        for tab in tabs:
            if tab['tabKey'] == 'weibo':
                container_id = tab['containerid']
        if not container_id:
            return cards
        result = await self.try_request(self.__url(container_id))
        if not result or 'cards' not in result['data']:
            return cards
        for item in result['data']['cards']:
            if item['card_type'] == 9 and 'isTop' not in item['mblog'] and item['mblog']['mblogtype'] == 0:
                cards.append(item)
        return cards

    async def get_blog_list(self):
        cards = await self.get_cards_list()
        blog_list = []
        for index, item in enumerate(cards):
            detail = remove_xml_tag(item['mblog']['text']).replace('\n', ' ').strip()
            length = 0
            content = ''
            for char in detail:
                content += char
                length += char_seat(char)
                if length >= 32:
                    content += '...'
                    break
            date = item['mblog']['created_at']
            date = time.strptime(date, '%a %b %d %H:%M:%S +0800 %Y')
            date = time.strftime('%Y-%m-%d %H:%M:%S', date)
            blog_list.append({'index': index + 1, 'date': date, 'content': content})
        return blog_list

    async def get_weibo_id(self, index: int):
        cards = await self.get_cards_list()
        if cards:
            return cards[index]['itemid']

    async def get_weibo_content(self, index: int):
        cards = await self.get_cards_list()
        if not cards:
            return None
        if index >= len(cards):
            index = len(cards) - 1
        target_blog = cards[index]
        blog = target_blog['mblog']
        result = await self.try_request('https://m.weibo.cn/statuses/extend?id=' + blog['id'])
        if not result:
            return None
        content = WeiboContent(self.user_name)
        text = result['data']['longTextContent']
        text = re.sub(r'<br\s*/?>', '\n', text)
        text = remove_xml_tag(text)
        content.html_text = text.strip('\n')
        content.detail_url = target_blog['scheme']

        pics = blog['pics'] if 'pics' in blog else []
        for pic in pics:
            pic_url = pic['large']['url']
            name = pic_url.split('/')[-1]
            suffix = name.split('.')[-1]
            if suffix.lower() == 'gif':
                if not self.setting.sendGIF:
                    continue
                path = os.path.join(self.images_cache_dir, name)
                create_dir(path, is_file=True)
                if not os.path.exists(path):
                    stream = await download_async(pic_url, headers=self.headers)
                    if stream:
                        with open(path, 'wb') as f:
                            f.write(stream)
                content.gif_list.append(path)
                content.gif_urls.append(pic_url)
            else:
                path = os.path.join(self.images_cache_dir, name)
                create_dir(path, is_file=True)
                if not os.path.exists(path):
                    stream = await download_async(pic_url, headers=self.headers)
                    if stream:
                        with open(path, 'wb') as f:
                            f.write(stream)
                content.pics_list.append(path)
                content.pics_urls.append(pic_url)
        # 图片拼接支持
        if content.pics_list:
            if hasattr(self, '_process_and_merge_images'):
                content.pics_list = await self._process_and_merge_images(content.pics_list)
        return content

    # 下面保留拼图相关原函数（不变）
    async def _process_and_merge_images(self, pics_list: List[str]) -> List[str]:
        # 处理图片拼接，支持3图横排、6宫格、9宫格
        if len(pics_list) < 3:
            return pics_list

        def check_dimensions_consistent(image_paths: List[str]) -> Optional[Tuple[int, int]]:
            try:
                first_image = Image.open(image_paths[0])
                base_size = first_image.size
                first_image.close()
                for img_path in image_paths[1:]:
                    img = Image.open(img_path)
                    if img.size != base_size:
                        img.close()
                        return None
                    img.close()
                return base_size
            except Exception:
                return None

        def merge_images(images_to_merge: List[Image.Image], grid_size: Tuple[int, int], base_size: Tuple[int, int]) -> str:
            cols, rows = grid_size
            merged_width = base_size[0] * cols
            merged_height = base_size[1] * rows
            merged_image = Image.new('RGB', (merged_width, merged_height), (255, 255, 255))
            for index, img in enumerate(images_to_merge):
                row = index // cols
                col = index % cols
                x = col * base_size[0]
                y = row * base_size[1]
                merged_image.paste(img, (x, y))
                img.close()
            merged_image_name = f"merged_{self.weibo_id}_{int(time.time())}.png"
            merged_image_path = os.path.join(self.images_cache_dir, merged_image_name)
            merged_image.save(merged_image_path, 'PNG')
            return merged_image_path

        # 9宫格
        if len(pics_list) >= 8:
            base_size = check_dimensions_consistent(pics_list[:8])
            if base_size:
                images_to_process = pics_list[:9]
                original_9th_image_path = pics_list[8] if len(pics_list) >= 9 else None
                long_image_in_grid = None
                try:
                    pil_images = [Image.open(p) for p in pics_list[:8]]
                    if original_9th_image_path:
                        img9 = Image.open(original_9th_image_path)
                        if img9.size == base_size:
                            pil_images.append(img9)
                        elif img9.size[0] == base_size[0] and img9.size[1] > base_size[1]:
                            cropped_img9 = img9.crop((0, 0, base_size[0], base_size[1]))
                            pil_images.append(cropped_img9)
                            long_image_in_grid = original_9th_image_path
                        img9.close()
                    else:
                        for img in pil_images: img.close()
                        images_to_process = pics_list[:8]
                        pil_images = [Image.open(p) for p in images_to_process]
                    if len(pil_images) >= 8:
                        grid_cols = 3
                        grid_rows = 3
                        merged_path = merge_images(pil_images, (grid_cols, grid_rows), base_size)
                        new_pics_list = [merged_path]
                        if long_image_in_grid:
                            new_pics_list.append(long_image_in_grid)
                        new_pics_list.extend(pics_list[len(images_to_process):])
                        print(f"[微博插件] 已成功拼接 {len(pil_images)} 张图片为9宫格模式。")
                        return new_pics_list
                except Exception as e:
                    print(f"[微博插件] 拼接9宫格图片时发生错误: {e}")
                    return pics_list
        # 6宫格
        if len(pics_list) >= 5:
            base_size = check_dimensions_consistent(pics_list[:5])
            if base_size:
                images_to_process = pics_list[:6]
                original_6th_image_path = pics_list[5] if len(pics_list) >= 6 else None
                long_image_in_grid = None
                try:
                    pil_images = [Image.open(p) for p in pics_list[:5]]
                    if original_6th_image_path:
                        img6 = Image.open(original_6th_image_path)
                        if img6.size == base_size:
                            pil_images.append(img6)
                        elif img6.size[0] == base_size[0] and img6.size[1] > base_size[1]:
                            cropped_img6 = img6.crop((0, 0, base_size[0], base_size[1]))
                            pil_images.append(cropped_img6)
                            long_image_in_grid = original_6th_image_path
                        img6.close()
                    else:
                        for img in pil_images: img.close()
                        images_to_process = pics_list[:5]
                        pil_images = [Image.open(p) for p in images_to_process]
                    if len(pil_images) >= 5:
                        grid_cols = 3
                        grid_rows = 2
                        merged_path = merge_images(pil_images, (grid_cols, grid_rows), base_size)
                        new_pics_list = [merged_path]
                        if long_image_in_grid:
                            new_pics_list.append(long_image_in_grid)
                        new_pics_list.extend(pics_list[len(images_to_process):])
                        print(f"[微博插件] 已成功拼接 {len(pil_images)} 张图片为6宫格模式。")
                        return new_pics_list
                except Exception as e:
                    print(f"[微博插件] 拼接6宫格图片时发生错误: {e}")
                    return pics_list
        # 3图横排
        if len(pics_list) >= 3:
            base_size = check_dimensions_consistent(pics_list[:3])
            if base_size:
                try:
                    pil_images = [Image.open(p) for p in pics_list[:3]]
                    grid_cols = 3
                    grid_rows = 1
                    merged_path = merge_images(pil_images, (grid_cols, grid_rows), base_size)
                    new_pics_list = [merged_path]
                    new_pics_list.extend(pics_list[3:])
                    print(f"[微博插件] 已成功拼接前3张图片为横排模式。")
                    return new_pics_list
                except Exception as e:
                    print(f"[微博插件] 拼接前3张图片时发生错误: {e}")
                    return pics_list
        return pics_list
