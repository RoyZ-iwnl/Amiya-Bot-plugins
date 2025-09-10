import re
import os
import time
import json
import uuid
import asyncio
import aiohttp
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime

from PIL import Image

from amiyabot.network.download import download_async
from amiyabot.network.httpRequests import http_requests
from core.util import remove_xml_tag, char_seat, create_dir

from amiyabot.builtin.lib.browserService import basic_browser_service
from core import send_to_console_channel, Chain

# CeobeAPI配置
SERVER_BASE = 'https://server.ceobecanteen.top/api/v1/'
SERVER_CDN_BASE = 'https://server-cdn.ceobecanteen.top/api/v1/'
CDN_BASE = 'https://cdn.ceobecanteen.top/'

# 调试开关 - 可在配置文件中设置
DEBUG_CEOBE_API = True  # 可通过setting.debugCeobeAPI控制

# 保持原有的headers.json路径用于兼容性
#HEADERS_PATH = os.path.join(os.path.dirname(__file__), "headers.json")

def debug_log(message: str, force: bool = False):
    """调试日志输出 - 只在开启调试时输出"""
    if DEBUG_CEOBE_API or force:
        print(f"[CeobeAPI Debug] {message}")

def get_ceobe_headers(client_id: str = None) -> dict:
    """获取CeobeAPI请求头"""
    if not client_id:
        client_id = str(uuid.uuid4())
    
    return {
        'Content-Type': 'application/json',
        'User-Agent': 'Ceobe-Canteen-Browser-Extension/4.0.5',
        'x-ceobe-client-id': client_id,
        'x-ceobe-client-type': 'browser-extension',
        'x-ceobe-client-platform': 'chrome',
        'x-ceobe-client-version': '4.0.5'
    }

# CeobeAPI辅助函数
async def make_ceobe_request(url: str, method: str = 'GET', data: Optional[Dict] = None, 
                            headers: Optional[Dict] = None, timeout: int = 10) -> Optional[Dict]:
    """发送CeobeAPI请求 - 使用aiohttp直接请求避免amiyabot框架兼容性问题"""
    try:
        if not headers:
            headers = get_ceobe_headers()
        else:
            headers = headers.copy()
        
        debug_log(f"发送请求: {method} {url}")
        if data:
            debug_log(f"请求数据: {json.dumps(data, ensure_ascii=False)}")
        
        # 使用aiohttp直接发送请求，避免amiyabot的http_requests兼容性问题
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            if method.upper() == 'GET':
                async with session.get(url, headers=headers) as response:
                    debug_log(f"GET响应状态码: {response.status}")
                    if response.status == 200:
                        result = await response.json()
                        debug_log(f"GET响应成功, 数据长度: {len(str(result))}")
                        return result
                    else:
                        error_text = await response.text()
                        debug_log(f"GET请求失败: HTTP {response.status}, 错误: {error_text[:200]}")
                        return None
                        
            elif method.upper() == 'POST':
                if data:
                    async with session.post(url, headers=headers, json=data) as response:
                        debug_log(f"POST响应状态码: {response.status}")
                        if response.status == 200:
                            result = await response.json()
                            debug_log(f"POST响应成功, 数据长度: {len(str(result))}")
                            return result
                        else:
                            error_text = await response.text()
                            debug_log(f"POST请求失败: HTTP {response.status}, 错误: {error_text[:200]}")
                            if response.status == 404:
                                debug_log(f"404错误详情 - URL: {url}")
                                debug_log(f"404错误详情 - Headers: {headers}")
                                debug_log(f"404错误详情 - Data: {data}")
                            return None
                else:
                    async with session.post(url, headers=headers) as response:
                        debug_log(f"POST(无数据)响应状态码: {response.status}")
                        if response.status == 200:
                            result = await response.json()
                            debug_log(f"POST(无数据)响应成功, 数据长度: {len(str(result))}")
                            return result
                        else:
                            error_text = await response.text()
                            debug_log(f"POST(无数据)请求失败: HTTP {response.status}, 错误: {error_text[:200]}")
                            return None
            else:
                debug_log(f"不支持的HTTP方法: {method}")
                return None
            
    except asyncio.TimeoutError:
        debug_log(f"请求超时: {url}", force=True)
        return None
    except Exception as e:
        debug_log(f"网络请求异常: {e}", force=True)
        import traceback
        debug_log(f"异常详情: {traceback.format_exc()}")
        return None

# 微博ID映射表 - 将原有的UID映射到CeobeAPI的数据源ID
WEIBO_UID_TO_DATASOURCE = {
    '6279793937': None,   # 明日方舟 - 需要通过API查询具体的数据源ID
    '6441489862': None,   # 明日方舟朝陇山 - 需要通过API查询
    '7745672941': None,   # 明日方舟终末地 - 需要通过API查询  
    '7697896274': None,   # 来自星尘 - 需要通过API查询
    '7751894824': None,   # 森空岛 - 需要通过API查询
}

async def get_available_datasources() -> Optional[List[Dict]]:
    """获取可用的微博数据源列表"""
    debug_log("开始获取可用数据源列表")
    url = f'{SERVER_BASE}canteen/config/datasource/list'
    
    response = await make_ceobe_request(url)
    if response and response.get('code') == '00000':
        datasources = response.get('data', [])
        debug_log(f"找到 {len(datasources)} 个数据源")
        # 筛选微博数据源
        weibo_sources = [ds for ds in datasources if ds.get('datasource') == 'weibo:dynamic-by-uid']
        debug_log(f"其中微博数据源 {len(weibo_sources)} 个")
        
        # 显示可用的微博数据源
        for i, source in enumerate(weibo_sources[:10]):  # 只显示前10个
            debug_log(f"  数据源 {i+1}: {source.get('nickname', '未知')} (UID: {source.get('db_unique_key', '未知')}, ID: {source.get('unique_id', '未知')})")
        
        return weibo_sources
    else:
        debug_log(f"获取数据源列表失败: {response}", force=True)
        return None

async def get_datasource_combo_id(datasource_ids: List[str]) -> Optional[str]:
    """获取数据源组合ID"""
    debug_log(f"开始获取数据源组合ID，数据源ID列表: {datasource_ids}")
    url = f'{SERVER_BASE}canteen/user/getDatasourceComb'
    data = {'datasource_push': datasource_ids}
    
    response = await make_ceobe_request(url, 'POST', data)
    if response and response.get('code') == '00000':
        combo_id = response['data']['datasource_comb_id']
        debug_log(f"成功获取组合ID: {combo_id}")
        return combo_id
    else:
        error_msg = response.get('message', '未知错误') if response else '请求失败'
        debug_log(f"获取组合ID失败: {error_msg}", force=True)
        return None

async def get_cookie_info(combo_id: str, max_retries: int = 3) -> Optional[Dict[str, str]]:
    """获取cookie信息"""
    debug_log(f"开始获取Cookie信息，组合ID: {combo_id}")
    
    # 检查组合ID是否有效 - 如果是单个点或过短，跳过
    if not combo_id or combo_id.strip() == '.' or len(combo_id.strip()) < 2:
        debug_log(f"组合ID无效或为空: '{combo_id}'，跳过获取Cookie", force=True)
        return None
    
    url = f'{CDN_BASE}datasource-comb/{combo_id}'
    
    for attempt in range(max_retries):
        if attempt > 0:
            debug_log(f"第 {attempt + 1} 次重试获取Cookie信息")
            await asyncio.sleep(2 ** attempt)  # 指数退避
        
        response = await make_ceobe_request(url)
        if response:
            cookie_id = response.get('cookie_id')
            update_cookie_id = response.get('update_cookie_id')
            
            debug_log(f"Cookie响应: cookie_id={cookie_id}, update_cookie_id={update_cookie_id}")
            
            if cookie_id:
                debug_log(f"成功获取Cookie ID: {cookie_id}")
                return {
                    'cookie_id': cookie_id,
                    'update_cookie_id': update_cookie_id
                }
            else:
                debug_log(f"尝试 {attempt + 1}: Cookie ID 为空，可能数据源暂无数据")
        else:
            debug_log(f"尝试 {attempt + 1}: 请求失败")
    
    debug_log("多次尝试后仍无法获取有效的Cookie ID（这通常是正常情况，表示没有新数据）")
    return None

# 保留兼容性函数（暂时不用）
async def fetch_weibo_cookies() -> list:
    return []

def cookies_to_str(cookies: list) -> str:
    return ''

def to_lowercase_keys(d: dict) -> dict:
    return {k.lower(): v for k, v in d.items() if not k.startswith(":")}

async def load_headers_from_json_or_fallback(weibo_id: str) -> dict:
    return get_ceobe_headers()

async def refresh_headers_and_json(weibo_id: str, info: str = ''):
    pass

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
        self.weibo_id = str(weibo_id).strip() if weibo_id else ''
        self.setting = setting
        self.user_name = ''
        self.images_cache_dir = self.setting.imagesCache
        self.client_id = str(uuid.uuid4())
        self.headers = get_ceobe_headers(self.client_id)
        
        # 验证weibo_id有效性
        if not self.weibo_id or self.weibo_id == '' or self.weibo_id == 'None':
            debug_log(f"⚠️  无效的微博ID: '{weibo_id}', 已设置为空字符串", force=True)
            self.weibo_id = ''
        
        # 设置调试开关
        global DEBUG_CEOBE_API
        if hasattr(setting, 'debugCeobeAPI'):
            DEBUG_CEOBE_API = setting.debugCeobeAPI
        
        # CeobeAPI相关 - 改为使用所有微博数据源的组合
        self.target_weibo_id = self.weibo_id  # 保存目标微博ID用于过滤
        self.datasource_id = None
        self.combo_id = None
        self.current_cookies_cache = []
        self.all_weibo_datasources = []  # 存储所有微博数据源
        
        # 错误状态追踪，避免重复的console输出和实现重试机制
        self.last_error_time = 0
        self.error_count = 0
        self.consecutive_failures = 0  # 连续失败次数
        self.max_retry_count = 3      # 最大重试次数
        self.retry_intervals = [30, 120, 300]  # 重试间隔（秒）: 30秒, 2分钟, 5分钟
        self.is_disabled = False      # 是否因连续失败而禁用
        self.last_success_time = time.time()  # 上次成功时间
        self.disable_until_manual = False  # 是否需要手动重新启用
        
        debug_log(f"创建WeiboUser实例，目标微博ID: '{self.weibo_id}', 客户端ID: {self.client_id}")
        
        # 初始化数据源映射
        self._initialize_datasource_mapping()

    def _initialize_datasource_mapping(self):
        """初始化数据源映射 - 不再只查找单个数据源"""
        # 保持兼容性，但实际上我们会使用所有微博数据源的组合
        self.datasource_id = WEIBO_UID_TO_DATASOURCE.get(self.weibo_id)
        
    def reset_error_state(self):
        """重置错误状态（用于手动触发时重新启用）"""
        self.consecutive_failures = 0
        self.is_disabled = False
        self.disable_until_manual = False
        self.last_success_time = time.time()
        debug_log(f"重置错误状态 (UID: {self.weibo_id})")
    
    def should_retry(self) -> bool:
        """判断是否应该重试"""
        if self.disable_until_manual:
            debug_log(f"数据源已禁用，需要手动重新启用 (UID: {self.weibo_id})")
            return False
        
        if self.consecutive_failures >= self.max_retry_count:
            debug_log(f"超过最大重试次数，禁用数据源 (UID: {self.weibo_id})")
            return False
        
        # 检查是否到了重试时间
        if self.consecutive_failures > 0:
            retry_interval = self.retry_intervals[min(self.consecutive_failures - 1, len(self.retry_intervals) - 1)]
            if time.time() - self.last_error_time < retry_interval:
                debug_log(f"尚未到重试时间，剩余 {retry_interval - (time.time() - self.last_error_time):.1f} 秒 (UID: {self.weibo_id})")
                return False
        
        return True
    
    async def ensure_datasource_id(self):
        """确保获取到所有微博数据源ID（带重试机制）"""
        # 如果微博ID为空或无效，直接返回False
        if not self.weibo_id or self.weibo_id == '':
            debug_log(f"微博ID为空或无效，跳过数据源查找", force=True)
            return False
        
        # 检查是否应该重试
        if not self.should_retry():
            return False
        
        # 获取所有可用的微博数据源
        datasources = await get_available_datasources()
        if not datasources:
            self.consecutive_failures += 1
            current_time = time.time()
            self.last_error_time = current_time
            
            # 根据失败次数决定处理方式
            if self.consecutive_failures >= self.max_retry_count:
                self.disable_until_manual = True
                await send_to_console_channel(Chain().text(
                    f'微博数据源连续获取失败 {self.max_retry_count} 次，已禁用自动推送\n'
                    f'UID: {self.weibo_id}\n'
                    f'请手动发送"微博"关键词重新启用'
                ))
            else:
                next_retry = self.retry_intervals[min(self.consecutive_failures - 1, len(self.retry_intervals) - 1)]
                await send_to_console_channel(Chain().text(
                    f'无法获取微博数据源列表 (第{self.consecutive_failures}次失败)\n'
                    f'UID: {self.weibo_id}\n'
                    f'{next_retry//60}分钟后重试'
                ))
            return False
        
        # 成功获取数据源，重置失败计数
        if self.consecutive_failures > 0:
            debug_log(f"数据源恢复正常，重置失败计数 (UID: {self.weibo_id})")
            self.consecutive_failures = 0
            self.last_success_time = time.time()
        
        # 存储所有微博数据源，用于组合查询
        self.all_weibo_datasources = datasources
        debug_log(f"找到 {len(datasources)} 个微博数据源")
        
        # 查找目标用户名
        for ds in datasources:
            if ds.get('db_unique_key') == self.weibo_id:
                self.user_name = ds.get('nickname', f'微博用户{self.weibo_id}')
                debug_log(f"找到目标用户: {self.user_name} (UID: {self.weibo_id})")
                break
        
        if not self.user_name:
            self.user_name = f'微博用户{self.weibo_id}'
            debug_log(f"未找到匹配的用户名，使用默认: {self.user_name}")
            
        return True
        
    async def ensure_combo_id(self):
        """确保获取到组合ID - 使用所有微博数据源的组合"""
        if self.combo_id:
            return True
            
        if not await self.ensure_datasource_id():
            return False
        
        # 使用所有微博数据源的ID来获取组合ID
        all_datasource_ids = [ds.get('unique_id') for ds in self.all_weibo_datasources if ds.get('unique_id')]
        debug_log(f"使用 {len(all_datasource_ids)} 个微博数据源获取组合ID")
        
        self.combo_id = await get_datasource_combo_id(all_datasource_ids)
        if not self.combo_id:
            debug_log(f"无法获取数据源组合ID", force=True)
            return False
        
        # 验证combo_id的有效性
        if self.combo_id.strip() == '.' or len(self.combo_id.strip()) < 2:
            debug_log(f"获得无效的组合ID: '{self.combo_id}'，但这不应该发生在多数据源组合中", force=True)
            return False
            
        return True

    async def get_ceobe_weibo_data(self) -> Optional[Dict]:
        """从CeobeAPI获取微博数据"""
        if not await self.ensure_combo_id():
            return None
            
        # 获取Cookie信息
        cookie_info = await get_cookie_info(self.combo_id)
        if not cookie_info or not cookie_info.get('cookie_id'):
            # 没有新数据是正常情况
            return None
            
        # 获取微博数据
        url = f'{SERVER_CDN_BASE}cdn/cookie/mainList/cookieList'
        params = [f'datasource_comb_id={self.combo_id}', f'cookie_id={cookie_info["cookie_id"]}']  
        
        if cookie_info.get('update_cookie_id'):
            params.append(f'update_cookie_id={cookie_info["update_cookie_id"]}')
            
        full_url = url + '?' + '&'.join(params)
        
        response = await make_ceobe_request(full_url)
        if response and response.get('code') == '00000':
            data = response.get('data', {})
            cookies = data.get('cookies', [])
            self.current_cookies_cache = cookies
            return data
        return None

    def format_ceobe_weibo_item(self, cookie_item: Dict) -> Dict[str, Any]:
        """格式化CeobeAPI返回的微博数据"""
        try:
            item = cookie_item.get('item', {})
            default_cookie = cookie_item.get('default_cookie', {})
            timestamp = cookie_item.get('timestamp', {})
            
            # 处理可能为None的item
            if item is None:
                item = {}
            
            # 格式化时间
            platform_time = timestamp.get('platform', 0)
            fetcher_time = timestamp.get('fetcher', 0)
            
            formatted_time = None
            if platform_time:
                formatted_time = datetime.fromtimestamp(platform_time / 1000)
            elif fetcher_time:
                formatted_time = datetime.fromtimestamp(fetcher_time / 1000)
            
            # 时间过滤 - 处理最近7天的微博，避免推送历史内容但不过度严格
            if formatted_time:
                now = datetime.now()
                # 允许最近7天的微博，给CeobeAPI一些容错空间
                time_diff = now - formatted_time
                if time_diff.days > 7:  # 超过7天的微博跳过
                    debug_log(f"跳过旧微博 ID: {item.get('id', '未知')}, 时间: {formatted_time}")
                    return {}
                else:
                    debug_log(f"保留微博 ID: {item.get('id', '未知')}, 时间: {formatted_time}, 距现在: {time_diff.days}天")
            
            # 处理图片
            images = default_cookie.get('images', []) if default_cookie else []
            image_urls = []
            if images:
                for img in images:
                    if img and img.get('origin_url'):
                        image_urls.append(img['origin_url'])
            
            return {
                'id': item.get('id', ''),
                'text': default_cookie.get('text', '') if default_cookie else '',
                'url': item.get('url', ''),
                'time': formatted_time,
                'images': image_urls,
                'is_retweeted': item.get('is_retweeted', False),
                'retweeted_info': item.get('retweeted', {}) if item.get('is_retweeted') else None
            }
        except Exception as e:
            debug_log(f"格式化微博数据时出错: {e}")
            return {}

    async def get_user_name(self, result=None):
        if self.user_name:
            return self.user_name
            
        # 通过数据源获取用户名
        if not await self.ensure_datasource_id():
            self.user_name = f'微博用户{self.weibo_id}'
            
        return self.user_name

    async def get_cards_list(self):
        """从CeobeAPI获取微博列表（兼容原有接口）"""
        data = await self.get_ceobe_weibo_data()
        if not data:
            return []
            
        cookies = data.get('cookies', [])
        debug_log(f"从CeobeAPI获取到 {len(cookies)} 条微博数据")
        
        # 转换为类似原有格式的数据结构，并只保留目标用户的微博
        cards = []
        for cookie in cookies:
            # 检查是否是目标用户的微博
            source_data = cookie.get('source', {}).get('data', '')
            if source_data != self.target_weibo_id:
                debug_log(f"跳过非目标用户微博: {source_data} (目标: {self.target_weibo_id})")
                continue
            
            formatted = self.format_ceobe_weibo_item(cookie)
            # 跳过空的格式化结果（比如被时间过滤掉的旧微博）
            if not formatted or not formatted.get('id'):
                continue
                
            # 构造类似原有cards格式的数据
            card = {
                'itemid': formatted['id'],
                'scheme': formatted['url'],
                'mblog': {
                    'id': formatted['id'],
                    'bid': formatted['id'],
                    'text': formatted['text'],
                    'created_at': formatted['time'].strftime('%a %b %d %H:%M:%S +0800 %Y') if formatted['time'] else '',
                    'pics': self._format_pics_for_compatibility(formatted['images']),
                    'retweeted_status': formatted['retweeted_info'] if formatted['is_retweeted'] else None
                }
            }
            cards.append(card)
            
        debug_log(f"过滤后的目标用户({self.target_weibo_id})微博卡片数量: {len(cards)}")
        return cards
        
    def _format_pics_for_compatibility(self, image_urls: List[str]) -> List[Dict]:
        """将图片URL格式化为兼容原有格式"""
        pics = []
        for url in image_urls:
            pics.append({
                'large': {'url': url},
                'url': url
            })
        return pics

    async def get_blog_list(self):
        """获取微博列表（兼容原有接口）"""
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
            
            # 处理时间格式
            try:
                if item['mblog']['created_at']:
                    date = time.strptime(item['mblog']['created_at'], '%a %b %d %H:%M:%S +0800 %Y')
                    date = time.strftime('%Y-%m-%d %H:%M:%S', date)
                else:
                    date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            except:
                date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
            blog_list.append({'index': index + 1, 'date': date, 'content': content})
        return blog_list

    async def get_weibo_id(self, index: int):
        """获取指定索引的微博ID"""
        cards = await self.get_cards_list()
        if cards and index < len(cards):
            return cards[index]['itemid']
        return None

    async def get_weibo_content(self, index: int):
        """获取指定索引的微博内容"""
        cards = await self.get_cards_list()
        if not cards:
            return None
        if index >= len(cards):
            index = len(cards) - 1
            
        target_blog = cards[index]
        blog = target_blog['mblog']
        
        content = WeiboContent(await self.get_user_name())
        
        # 使用CeobeAPI已经处理好的文本内容
        text = blog['text']
        text = re.sub(r'<br\s*/?>', '\n', text)
        text = remove_xml_tag(text)
        content.html_text = text.strip('\n')
        content.detail_url = target_blog['scheme']

        # 处理图片
        pics = blog.get('pics', [])
        if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
            debug_log(f"开始处理图片，pics数量: {len(pics)}")
        
        for i, pic in enumerate(pics):
            pic_url = pic['url'] if 'url' in pic else pic.get('large', {}).get('url', '')
            if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                debug_log(f"处理第{i+1}张图片: {pic_url}")
            
            if not pic_url:
                if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                    debug_log(f"跳过第{i+1}张图片：没有URL")
                continue
                
            name = pic_url.split('/')[-1]
            if '?' in name:
                name = name.split('?')[0]
            suffix = name.split('.')[-1] if '.' in name else 'jpg'
            
            if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                debug_log(f"图片文件名: {name}, 后缀: {suffix}")
            
            if suffix.lower() == 'gif':
                if not self.setting.sendGIF:
                    if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                        debug_log(f"跳过GIF: sendGIF配置为{self.setting.sendGIF}")
                    continue
                path = os.path.join(self.images_cache_dir, name)
                create_dir(path, is_file=True)
                if not os.path.exists(path):
                    if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                        debug_log(f"开始下载GIF: {pic_url}")
                    # 使用普通的下载headers，不需要特殊的微博cookie
                    try:
                        stream = await download_async(pic_url)
                        if stream:
                            with open(path, 'wb') as f:
                                f.write(stream)
                            if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                                debug_log(f"GIF下载成功: {path}, 大小: {len(stream)} 字节")
                        else:
                            if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                                debug_log(f"GIF下载失败: download_async返回None - {pic_url}")
                            continue
                    except Exception as e:
                        if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                            debug_log(f"GIF下载异常: {e} - {pic_url}")
                            import traceback
                            debug_log(f"异常详情: {traceback.format_exc()}")
                        continue
                else:
                    if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                        debug_log(f"GIF已存在: {path}")
                content.gif_list.append(path)
                content.gif_urls.append(pic_url)
            else:
                path = os.path.join(self.images_cache_dir, name)
                create_dir(path, is_file=True)
                if not os.path.exists(path):
                    if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                        debug_log(f"开始下载图片: {pic_url}")
                    # 使用普通的下载headers，不需要特殊的微博cookie
                    try:
                        stream = await download_async(pic_url)
                        if stream:
                            with open(path, 'wb') as f:
                                f.write(stream)
                            if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                                debug_log(f"图片下载成功: {path}, 大小: {len(stream)} 字节")
                        else:
                            if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                                debug_log(f"图片下载失败: download_async返回None - {pic_url}")
                            continue
                    except Exception as e:
                        if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                            debug_log(f"图片下载异常: {e} - {pic_url}")
                            import traceback
                            debug_log(f"异常详情: {traceback.format_exc()}")
                        continue
                else:
                    if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
                        debug_log(f"图片已存在: {path}")
                content.pics_list.append(path)
                content.pics_urls.append(pic_url)
                
        if hasattr(self.setting, 'debugCeobeAPI') and self.setting.debugCeobeAPI:
            debug_log(f"图片处理完成 - pics_list: {len(content.pics_list)}, pics_urls: {len(content.pics_urls)}, gif_list: {len(content.gif_list)}, gif_urls: {len(content.gif_urls)}")
                
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


# ========== 聚合推送系统增量功能 ==========

@dataclass
class UnifiedContent:
    """统一内容格式 - 用于聚合推送"""
    content_id: str      # 内容唯一ID
    platform: str        # 平台类型
    source_id: str       # 数据源ID
    source_name: str     # 数据源名称
    text: str            # 文本内容
    publish_time: Optional[datetime] = None  # 发布时间
    source_url: str = '' # 原文链接
    media_urls: List[str] = field(default_factory=list)  # 媒体URL列表

    def get_display_text(self, max_length: int = 200) -> str:
        """获取显示文本（限制长度）"""
        if len(self.text) <= max_length:
            return self.text
        return self.text[:max_length] + "..."


async def get_all_datasources() -> Optional[List[Dict]]:
    """获取所有平台的数据源列表"""
    debug_log("获取全平台数据源列表")
    url = f'{SERVER_BASE}canteen/config/datasource/list'
    
    response = await make_ceobe_request(url)
    if response and response.get('code') == '00000':
        datasources = response.get('data', [])
        debug_log(f"找到 {len(datasources)} 个数据源")
        
        # 按平台分组统计
        platform_counts = {}
        for ds in datasources:
            platform = ds.get('platform', 'unknown')
            platform_counts[platform] = platform_counts.get(platform, 0) + 1
        
        debug_log(f"平台分布: {platform_counts}")
        return datasources
    else:
        debug_log(f"获取数据源列表失败: {response}", force=True)
        return None


async def get_platform_datasources(platform: str) -> Optional[List[Dict]]:
    """获取指定平台的数据源列表"""
    debug_log(f"获取平台 {platform} 的数据源列表")
    all_datasources = await get_all_datasources()
    if all_datasources:
        platform_sources = [ds for ds in all_datasources if ds.get('platform') == platform]
        debug_log(f"平台 {platform} 有 {len(platform_sources)} 个数据源")
        return platform_sources
    return None


async def get_aggregated_content(datasource_ids: List[str]) -> Optional[List[Dict]]:
    """获取聚合内容数据"""
    if not datasource_ids:
        debug_log("数据源ID列表为空，跳过获取内容")
        return None
        
    debug_log(f"获取聚合内容，数据源数量: {len(datasource_ids)}")
    
    # 获取组合ID
    combo_id = await get_datasource_combo_id(datasource_ids)
    if not combo_id:
        debug_log("无法获取数据源组合ID")
        return None
    
    # 获取Cookie信息
    cookie_info = await get_cookie_info(combo_id)
    if not cookie_info:
        debug_log("无法获取Cookie信息，可能暂无新内容")
        return None
    
    # 获取内容数据
    url = f'{SERVER_CDN_BASE}cdn/cookie/mainList/cookieList'
    params = [f'datasource_comb_id={combo_id}', f'cookie_id={cookie_info["cookie_id"]}']
    if cookie_info.get('update_cookie_id'):
        params.append(f'update_cookie_id={cookie_info["update_cookie_id"]}')
    
    full_url = url + '?' + '&'.join(params)
    response = await make_ceobe_request(full_url)
    
    if response and response.get('code') == '00000':
        cookies = response.get('data', {}).get('cookies', [])
        debug_log(f"获取到 {len(cookies)} 条内容")
        return cookies
    else:
        debug_log(f"获取内容失败: {response}")
        return None


def adapt_content_to_unified(raw_data: Dict[str, Any]) -> Optional[UnifiedContent]:
    """将原始内容数据转换为统一格式"""
    try:
        source = raw_data.get('source', {})
        item = raw_data.get('item', {})
        default_cookie = raw_data.get('default_cookie', {})
        
        # datasource 是字符串，需要从中提取信息
        datasource_name = raw_data.get('datasource', '')
        
        # 从URL或datasource名称推断平台信息
        platform = 'unknown'
        item_url = item.get('url', '')
        
        if 'bilibili.com' in item_url or 'B站' in datasource_name:
            platform = 'bilibili'
        elif 'weibo.com' in item_url or '微博' in datasource_name:
            platform = 'weibo'
        elif 'music.163.com' in item_url or '网易云音乐' in datasource_name:
            platform = 'netease-cloud-music'
        elif 'arknights' in datasource_name.lower() or '明日方舟' in datasource_name:
            if '游戏' in datasource_name:
                platform = 'arknights-game'
            elif '官网' in datasource_name:
                platform = 'arknights-website'
            else:
                platform = 'arknights-game'  # 默认为游戏平台
        
        # 提取基本信息
        content_id = item.get('id', '')
        
        # 优先从default_cookie中获取文本内容，如果没有再从item中获取
        text = default_cookie.get('text', '') or item.get('text', '')
        
        # 处理时间戳 - 从timestamp字段获取
        timestamp_info = raw_data.get('timestamp', {})
        publish_time = None
        
        # 尝试从多个时间戳字段获取时间
        platform_time = timestamp_info.get('platform')
        fetcher_time = timestamp_info.get('fetcher')
        
        if platform_time:
            try:
                # platform时间通常是毫秒时间戳
                publish_time = datetime.fromtimestamp(platform_time / 1000)
            except:
                pass
        elif fetcher_time:
            try:
                # fetcher时间通常也是毫秒时间戳
                publish_time = datetime.fromtimestamp(fetcher_time / 1000)
            except:
                pass
        
        # 处理媒体URL - 优先从default_cookie获取
        media_urls = []
        images = default_cookie.get('images', []) or item.get('images', [])
        if isinstance(images, list):
            for img in images:
                if isinstance(img, dict):
                    # 尝试多个可能的URL字段
                    url = img.get('origin_url') or img.get('url') or img.get('large', {}).get('url', '')
                    if url:
                        media_urls.append(url)
                elif isinstance(img, str):
                    media_urls.append(img)
        
        return UnifiedContent(
            content_id=content_id,
            platform=platform,
            source_id=source.get('data', ''),  # 使用 source.data 作为来源ID
            source_name=datasource_name,       # 使用 datasource 字符串作为来源名称
            text=text,
            publish_time=publish_time,
            source_url=item.get('url', ''),
            media_urls=media_urls
        )
        
    except Exception as e:
        debug_log(f"内容适配失败: {e}", force=True)
        import traceback
        debug_log(f"异常详情: {traceback.format_exc()}")
        return None


class AggregatorSubscriptionManager:
    """聚合推送订阅管理器 - 基于JSON文件"""
    
    def __init__(self, config_file: str = 'aggregator_subscriptions.json'):
        # 确保配置文件在插件目录下
        plugin_dir = os.path.dirname(__file__)
        self.config_file = os.path.join(plugin_dir, config_file)
        self.subscriptions = {}  # group_id_bot_id -> {datasource_ids, enabled, last_update}
        self.datasources = {}    # datasource_id -> datasource_info
        self._load_subscriptions()
    
    def _get_group_key(self, group_id: str, bot_id: str) -> str:
        """获取群组键"""
        return f"{group_id}_{bot_id}"
    
    def _load_subscriptions(self):
        """从JSON文件加载订阅信息"""
        if not os.path.exists(self.config_file):
            self._save_subscriptions()
            return
        
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.subscriptions = data.get('subscriptions', {})
            self.datasources = data.get('datasources', {})
            debug_log(f"加载了 {len(self.subscriptions)} 个订阅配置")
            
        except Exception as e:
            debug_log(f"加载订阅配置失败: {e}", force=True)
            self.subscriptions = {}
            self.datasources = {}
    
    def _save_subscriptions(self):
        """保存订阅信息到JSON文件"""
        try:
            data = {
                'subscriptions': self.subscriptions,
                'datasources': self.datasources,
                'last_update': time.time(),
                'version': '1.0'
            }
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                
            debug_log(f"订阅配置已保存到 {self.config_file}")
            
        except Exception as e:
            debug_log(f"保存订阅配置失败: {e}", force=True)
    
    def add_subscription(self, group_id: str, bot_id: str, datasource_ids: List[str]) -> bool:
        """添加订阅"""
        try:
            group_key = self._get_group_key(group_id, bot_id)
            
            self.subscriptions[group_key] = {
                'group_id': group_id,
                'bot_id': bot_id,
                'datasource_ids': datasource_ids,
                'enabled': True,
                'added_time': time.time(),
                'last_update': time.time()
            }
            
            self._save_subscriptions()
            debug_log(f"成功添加订阅: {group_id} -> {len(datasource_ids)} 个数据源")
            return True
            
        except Exception as e:
            debug_log(f"添加订阅失败: {e}", force=True)
            return False
    
    def remove_subscription(self, group_id: str, bot_id: str) -> bool:
        """移除订阅"""
        try:
            group_key = self._get_group_key(group_id, bot_id)
            
            if group_key in self.subscriptions:
                self.subscriptions[group_key]['enabled'] = False
                self.subscriptions[group_key]['last_update'] = time.time()
                self._save_subscriptions()
                debug_log(f"成功禁用订阅: {group_id}")
                return True
            
            return False
            
        except Exception as e:
            debug_log(f"移除订阅失败: {e}", force=True)
            return False
    
    def get_enabled_subscriptions(self) -> List[Dict]:
        """获取所有启用的订阅"""
        enabled = []
        for key, sub in self.subscriptions.items():
            if sub.get('enabled', False):
                enabled.append(sub)
        return enabled
    
    def update_datasources(self, datasources: List[Dict]):
        """更新数据源信息"""
        for ds in datasources:
            unique_id = ds.get('unique_id')
            if unique_id:
                self.datasources[unique_id] = ds
        self._save_subscriptions()
        debug_log(f"更新了 {len(datasources)} 个数据源信息")
    
    def generate_datasource_menu(self, supported_platforms: List[str] = None) -> Tuple[str, Dict[int, str]]:
        """生成数据源选择菜单"""
        if not supported_platforms:
            supported_platforms = ['weibo', 'bilibili', 'netease-cloud-music', 'arknights-game', 'arknights-website']
        
        platform_names = {
            'weibo': '微博',
            'bilibili': 'B站',
            'netease-cloud-music': '网易云音乐',
            'arknights-game': '明日方舟游戏',
            'arknights-website': '明日方舟官网'
        }
        
        menu_text = "请选择要订阅的数据源（回复数字，多个用逗号分隔）：\n\n"
        index_map = {}  # index -> datasource_id
        current_index = 1
        
        for platform in supported_platforms:
            platform_datasources = [(ds_id, ds_info) for ds_id, ds_info in self.datasources.items() 
                                   if ds_info.get('platform') == platform]
            
            if platform_datasources:
                platform_name = platform_names.get(platform, platform)
                menu_text += f"**{platform_name}**\n"
                
                for ds_id, ds_info in platform_datasources:
                    nickname = ds_info.get('nickname', '未知')
                    menu_text += f"{current_index}. {nickname}\n"
                    index_map[current_index] = ds_id
                    current_index += 1
                
                menu_text += "\n"
        
        return menu_text, index_map


# 全局订阅管理器实例
aggregator_manager = AggregatorSubscriptionManager()
