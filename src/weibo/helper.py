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
import math

from PIL import Image, ImageDraw

from amiyabot.network.download import download_async
from amiyabot.network.httpRequests import http_requests
from core.util import remove_xml_tag, char_seat, create_dir

from amiyabot.builtin.lib.browserService import basic_browser_service
from core import send_to_console_channel, Chain

# CeobeAPI配置
SERVER_BASE = 'https://server.ceobecanteen.top/api/v1/'
SERVER_CDN_BASE = 'https://server-cdn.ceobecanteen.top/api/v1/'
CDN_BASE = 'https://cdn.ceobecanteen.top/'

# 调试开关 - 从配置文件读取
DEBUG_CEOBE_API = False  # 默认值，实际值从bot配置读取

# 保持原有的headers.json路径用于兼容性
#HEADERS_PATH = os.path.join(os.path.dirname(__file__), "headers.json")

def debug_log(message: str, force: bool = False, bot_instance=None):
    """调试日志输出 - 只在开启调试时输出"""
    # 尝试从bot实例获取调试开关
    debug_enabled = DEBUG_CEOBE_API
    if bot_instance:
        try:
            debug_enabled = bot_instance.get_config('setting', {}).get('debugCeobeAPI', False)
        except:
            pass
    
    if debug_enabled or force:
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

async def get_datasource_combo_id(datasource_ids: List[str]) -> Optional[str]:
    """获取数据源组合ID"""
    if not datasource_ids:
        debug_log("数据源ID列表为空")
        return None
    
    debug_log(f"获取数据源组合ID: {datasource_ids}")
    url = f'{SERVER_BASE}canteen/user/getDatasourceComb'
    data = {
        "datasource_push": datasource_ids
    }
    
    response = await make_ceobe_request(url, method='POST', data=data)
    if response and response.get('code') == '00000':
        combo_id = response.get('data', {}).get('datasource_comb_id')
        debug_log(f"获取到数据源组合ID: {combo_id}")
        return combo_id
    else:
        debug_log(f"获取数据源组合ID失败: {response}", force=True)
        return None

async def get_cookie_info(combo_id: str) -> Optional[Dict[str, Any]]:
    """获取Cookie信息"""
    if not combo_id:
        debug_log("数据源组合ID为空")
        return None
    
    # 检查组合ID是否有效 - 如果是单个点或过短，跳过
    if combo_id.strip() == '.' or len(combo_id.strip()) < 2:
        debug_log(f"组合ID无效或为空: '{combo_id}'，跳过获取Cookie", force=True)
        return None
    
    debug_log(f"获取Cookie信息: {combo_id}")
    url = f'{CDN_BASE}datasource-comb/{combo_id}'
    
    response = await make_ceobe_request(url, method='GET')
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
            debug_log("Cookie响应中没有cookie_id，可能暂无新内容")
            return None
    else:
        debug_log(f"获取Cookie信息失败")
        return None

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

    def get_display_text(self, max_length: int = 0) -> str:
        """获取显示文本（限制长度）"""
        if max_length <= 0 or len(self.text) <= max_length:
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
        
        # datasource 是字符串，直接用作数据源标识
        datasource_name = raw_data.get('datasource', '')
        
        # 直接使用datasource_name作为source_id，简单直接
        source_id = datasource_name
        
        debug_log(f"内容数据源: {datasource_name}", bot_instance=aggregator_manager.bot_instance if aggregator_manager else None)
        
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
            source_id=source_id,  # 使用正确提取的数据源ID
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


def is_gif_file(file_path: str) -> bool:
    """检测文件是否为GIF格式"""
    if not file_path:
        return False
    
    # 先从文件扩展名判断
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.gif':
        return True
    
    # 如果扩展名不明确，尝试读取文件头判断
    try:
        with open(file_path, 'rb') as f:
            header = f.read(6)
            return header.startswith(b'GIF87a') or header.startswith(b'GIF89a')
    except:
        return False


class AggregatorSubscriptionManager:
    """聚合推送订阅管理器 - 基于JSON文件"""
    
    def __init__(self, config_file: str = 'aggregator_subscriptions.json', bot_instance=None):
        # 确保配置文件在插件目录下
        plugin_dir = os.path.dirname(__file__)
        self.config_file = os.path.join(plugin_dir, config_file)
        self.subscriptions = {}  # group_id_bot_id -> {datasource_ids, enabled, last_update}
        self.datasources = {}    # datasource_id -> datasource_info
        self.bot_instance = bot_instance  # 保存bot实例用于日志
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
            debug_log(f"加载了 {len(self.subscriptions)} 个订阅配置", bot_instance=self.bot_instance)
            
        except Exception as e:
            debug_log(f"加载订阅配置失败: {e}", force=True, bot_instance=self.bot_instance)
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
                
            debug_log(f"订阅配置已保存到 {self.config_file}", bot_instance=self.bot_instance)
            
        except Exception as e:
            debug_log(f"保存订阅配置失败: {e}", force=True, bot_instance=self.bot_instance)
    
    def add_subscription(self, group_id: str, bot_id: str, datasource_ids: List[str]) -> bool:
        """添加订阅 - datasource_ids现在是数据源名称列表"""
        try:
            group_key = self._get_group_key(group_id, bot_id)
            
            # 将UUID转换为数据源名称
            datasource_names = []
            for uuid_id in datasource_ids:
                ds_info = self.datasources.get(uuid_id)
                if ds_info:
                    nickname = ds_info.get('nickname', '未知数据源')
                    datasource_names.append(nickname)
                else:
                    datasource_names.append(uuid_id)  # 如果找不到，保留原ID
            
            self.subscriptions[group_key] = {
                'group_id': group_id,
                'bot_id': bot_id,
                'datasource_ids': datasource_ids,      # 保留UUID用于向后兼容
                'datasource_names': datasource_names,  # 新增：数据源名称列表
                'enabled': True,
                'added_time': time.time(),
                'last_update': time.time()
            }
            
            self._save_subscriptions()
            debug_log(f"成功添加订阅: {group_id} -> {len(datasource_names)} 个数据源: {datasource_names}", bot_instance=self.bot_instance)
            return True
            
        except Exception as e:
            debug_log(f"添加订阅失败: {e}", force=True, bot_instance=self.bot_instance)
            return False
    
    def remove_subscription(self, group_id: str, bot_id: str) -> bool:
        """移除订阅"""
        try:
            group_key = self._get_group_key(group_id, bot_id)
            
            if group_key in self.subscriptions:
                self.subscriptions[group_key]['enabled'] = False
                self.subscriptions[group_key]['last_update'] = time.time()
                self._save_subscriptions()
                debug_log(f"成功禁用订阅: {group_id}", bot_instance=self.bot_instance)
                return True
            
            return False
            
        except Exception as e:
            debug_log(f"移除订阅失败: {e}", force=True, bot_instance=self.bot_instance)
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
        debug_log(f"更新了 {len(datasources)} 个数据源信息", bot_instance=self.bot_instance)
    
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


# 全局订阅管理器实例 - 稍后在main.py中传入bot实例
aggregator_manager = None

def initialize_aggregator_manager(bot_instance):
    """初始化全局订阅管理器"""
    global aggregator_manager
    if aggregator_manager is None:
        aggregator_manager = AggregatorSubscriptionManager(bot_instance=bot_instance)
    return aggregator_manager


async def compress_gif_for_wechat(gif_path: str, cache_dir: str) -> Optional[str]:
    """为WeChat压缩GIF文件"""
    try:
        if not os.path.exists(gif_path):
            debug_log(f"GIF文件不存在: {gif_path}")
            return None
        
        # 检查文件大小和尺寸
        file_size = os.path.getsize(gif_path)
        with Image.open(gif_path) as img:
            width, height = img.size
            
            # 如果文件已经符合要求，直接返回
            if width <= 1000 and file_size <= 10 * 1024 * 1024:  # 10MB
                return gif_path
            
            # 计算新尺寸
            if width > 1000:
                ratio = 1000 / width
                new_width = 1000
                new_height = int(height * ratio)
            else:
                new_width, new_height = width, height
            
            # 生成压缩后的文件名
            name = os.path.basename(gif_path)
            name_without_ext = os.path.splitext(name)[0]
            compressed_path = os.path.join(cache_dir, f"{name_without_ext}_compressed.gif")
            create_dir(compressed_path, is_file=True)
            
            # 压缩GIF
            frames = []
            durations = []
            
            for frame_index in range(img.n_frames):
                img.seek(frame_index)
                frame = img.copy()
                if frame.mode != 'RGBA':
                    frame = frame.convert('RGBA')
                
                # 调整大小
                frame = frame.resize((new_width, new_height), Image.Resampling.LANCZOS)
                frames.append(frame)
                
                # 获取帧持续时间
                duration = img.info.get('duration', 100)
                durations.append(duration)
            
            # 保存压缩后的GIF
            if frames:
                frames[0].save(
                    compressed_path,
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=img.info.get('loop', 0),
                    optimize=True
                )
                
                debug_log(f"GIF压缩完成: {gif_path} -> {compressed_path}")
                return compressed_path
            
    except Exception as e:
        debug_log(f"GIF压缩失败: {e}", force=True)
        return gif_path  # 压缩失败则返回原文件
    
    return None


def check_dimensions_consistent(image_paths: List[str], tolerance_percent: float = 5.0) -> Optional[Tuple[int, int]]:
    """检查图片尺寸是否一致（允许一定误差）"""
    if not image_paths:
        return None
    
    try:
        # 获取第一张图片的尺寸作为基准
        first_img = Image.open(image_paths[0])
        base_width, base_height = first_img.size
        first_img.close()
        
        # 计算允许的误差范围
        width_tolerance = int(base_width * tolerance_percent / 100)
        height_tolerance = int(base_height * tolerance_percent / 100)
        
        debug_log(f"基准尺寸: {base_width}x{base_height}, 允许误差: ±{width_tolerance}x{height_tolerance}")
        
        # 检查其他图片是否在误差范围内
        for i, path in enumerate(image_paths[1:], 1):
            img = Image.open(path)
            width, height = img.size
            img.close()
            
            width_diff = abs(width - base_width)
            height_diff = abs(height - base_height)
            
            if width_diff > width_tolerance or height_diff > height_tolerance:
                debug_log(f"图片{i+1}尺寸不匹配: {width}x{height}, 误差: {width_diff}x{height_diff}")
                return None
        
        debug_log(f"所有图片尺寸通过一致性检查")
        return (base_width, base_height)
        
    except Exception as e:
        debug_log(f"检查图片尺寸一致性失败: {e}")
        return None


def can_crop_to_fit(img_path: str, target_size: Tuple[int, int], tolerance_percent: float = 5.0) -> bool:
    """检查图片是否可以裁剪适配目标尺寸"""
    try:
        img = Image.open(img_path)
        width, height = img.size
        img.close()
        
        target_width, target_height = target_size
        width_tolerance = int(target_width * tolerance_percent / 100)
        
        # 检查宽度是否匹配（允许误差），且高度足够
        width_match = abs(width - target_width) <= width_tolerance
        height_enough = height >= target_height
        
        return width_match and height_enough
        
    except Exception as e:
        debug_log(f"检查裁剪适配失败: {e}")
        return False


def find_consistent_image_group_with_crop(image_paths: List[str], tolerance_percent: float = 5.0) -> Tuple[Optional[Tuple[int, int]], List[str], List[str], List[str]]:
    """找到尺寸一致的图片组，支持第9张长图裁剪，返回(基准尺寸, 一致图片列表, 可裁剪图片列表, 异常图片列表)"""
    if not image_paths:
        return None, [], [], []
    
    try:
        # 获取第一张图片的尺寸作为基准
        first_img = Image.open(image_paths[0])
        base_width, base_height = first_img.size
        first_img.close()
        
        # 计算允许的误差范围
        width_tolerance = int(base_width * tolerance_percent / 100)
        height_tolerance = int(base_height * tolerance_percent / 100)
        
        consistent_images = [image_paths[0]]  # 第一张图片作为基准
        croppable_images = []  # 可裁剪的图片（如第9张长图）
        inconsistent_images = []
        
        # 检查其他图片
        for i, path in enumerate(image_paths[1:], 1):
            img = Image.open(path)
            width, height = img.size
            img.close()
            
            width_diff = abs(width - base_width)
            height_diff = abs(height - base_height)
            
            if width_diff <= width_tolerance and height_diff <= height_tolerance:
                # 尺寸完全一致
                consistent_images.append(path)
            elif (width_diff <= width_tolerance and height >= base_height and 
                  len(consistent_images) >= 8 and len(croppable_images) == 0):
                # 宽度匹配，高度足够，且已有8张一致图片，这张可以作为第9张裁剪
                croppable_images.append(path)
                debug_log(f"图片{i+1}可裁剪适配: {width}x{height} -> {base_width}x{base_height}")
            else:
                inconsistent_images.append(path)
                debug_log(f"图片{i+1}尺寸不匹配: {width}x{height}, 误差: {width_diff}x{height_diff}")
        
        debug_log(f"找到{len(consistent_images)}张一致图片, {len(croppable_images)}张可裁剪图片, {len(inconsistent_images)}张异常图片")
        return (base_width, base_height), consistent_images, croppable_images, inconsistent_images
        
    except Exception as e:
        debug_log(f"分析图片组失败: {e}")
        return None, [], [], image_paths


def crop_image_to_fit(img_path: str, target_size: Tuple[int, int]) -> Optional[Image.Image]:
    """裁剪图片适配目标尺寸（从顶部开始裁剪）"""
    try:
        img = Image.open(img_path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        target_width, target_height = target_size
        current_width, current_height = img.size
        
        # 如果需要调整宽度，先调整
        if current_width != target_width:
            # 保持纵横比调整到目标宽度
            ratio = target_width / current_width
            new_height = int(current_height * ratio)
            img = img.resize((target_width, new_height), Image.Resampling.LANCZOS)
            current_height = new_height
        
        # 从顶部裁剪到目标高度
        if current_height > target_height:
            img = img.crop((0, 0, target_width, target_height))
        
        debug_log(f"图片裁剪完成: 目标尺寸{target_size}")
        return img
        
    except Exception as e:
        debug_log(f"图片裁剪失败: {e}")
        return None


def merge_images(image_paths: List[str], cache_dir: str, bot_config: dict = None) -> Tuple[Optional[str], List[str]]:
    """拼接多张图片 - 支持第9张长图裁剪"""
    try:
        if len(image_paths) < 3:
            return None, image_paths
        
        # 从配置获取容忍度
        tolerance = 5.0
        if bot_config:
            tolerance = bot_config.get('setting', {}).get('mergeTolerance', 5.0)
        
        # 找到尺寸一致的图片组，支持第9张裁剪
        base_size, consistent_images, croppable_images, inconsistent_images = find_consistent_image_group_with_crop(
            image_paths, tolerance_percent=tolerance
        )
        
        if not base_size or len(consistent_images) < 3:
            debug_log("没有足够的一致尺寸图片进行拼接")
            return None, image_paths
        
        # 加载一致尺寸的图片
        images = []
        used_paths = []
        
        # 加载一致图片
        for path in consistent_images:
            if os.path.exists(path):
                img = Image.open(path)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # 如果尺寸不完全一致，调整到目标尺寸
                if img.size != base_size:
                    img = img.resize(base_size, Image.Resampling.LANCZOS)
                
                images.append(img)
                used_paths.append(path)
        
        # 如果有8张一致图片且有可裁剪的第9张，进行裁剪处理
        if len(images) >= 8 and croppable_images:
            croppable_path = croppable_images[0]  # 取第一张可裁剪的
            cropped_img = crop_image_to_fit(croppable_path, base_size)
            if cropped_img:
                images.append(cropped_img)
                used_paths.append(croppable_path)
                debug_log(f"第9张图片裁剪成功，创建完整9宫格")
        
        if len(images) < 3:
            return None, image_paths
        
        # 按优先级尝试拼接
        merged = None
        merge_type = ""
        used_count = 0
        
        # 9宫格：至少8张图片（支持第9张裁剪）
        if len(images) >= 8:
            merged = merge_9_grid_consistent(images, base_size)
            if merged:
                merge_type = "9宫格"
                used_count = min(9, len(images))
        
        # 6宫格：至少5张图片
        if not merged and len(images) >= 5:
            merged = merge_6_grid_consistent(images, base_size)
            if merged:
                merge_type = "6宫格"
                used_count = min(6, len(images))
        
        # 3图横排：恰好3张图片
        if not merged and len(images) == 3:
            merged = merge_3_horizontal_consistent(images, base_size)
            if merged:
                merge_type = "3图横排"
                used_count = 3
        
        # 关闭所有图片对象
        for img in images:
            img.close()
        
        if merged:
            # 保存拼接后的图片
            timestamp = int(time.time())
            merged_path = os.path.join(cache_dir, f"merged_{timestamp}_{merge_type}.jpg")
            create_dir(merged_path, is_file=True)
            merged.save(merged_path, 'JPEG', quality=85)
            debug_log(f"图片拼接完成({merge_type}): {merged_path}")
            
            # 计算剩余图片：
            # 1. 未使用的一致图片
            remaining_consistent = consistent_images[used_count:] if used_count < len(consistent_images) else []
            # 2. 未使用的可裁剪图片（原始完整版本）
            remaining_croppable = croppable_images[1:] if len(croppable_images) > 1 else []
            if len(images) >= 9 and croppable_images:
                # 如果第9张被用于裁剪，则原始完整版本也要包含在剩余图片中
                remaining_croppable = croppable_images  
            # 3. 所有异常图片
            remaining_images = remaining_consistent + remaining_croppable + inconsistent_images
            
            debug_log(f"剩余图片数量: {len(remaining_images)}")
            return merged_path, remaining_images
        else:
            debug_log("未满足拼接条件")
            return None, image_paths
        
    except Exception as e:
        debug_log(f"图片拼接失败: {e}", force=True)
        return None, image_paths


def merge_3_horizontal_consistent(images: List[Image.Image], base_size: Tuple[int, int]) -> Optional[Image.Image]:
    """3张同尺寸图片横向拼接"""
    try:
        if len(images) != 3:
            return None
            
        width, height = base_size
        
        # 创建拼接后的图片
        merged = Image.new('RGB', (width * 3, height), (255, 255, 255))
        
        for i, img in enumerate(images[:3]):
            x_offset = i * width
            merged.paste(img, (x_offset, 0))
        
        return merged
        
    except Exception as e:
        debug_log(f"3图横向拼接失败: {e}")
        return None


def merge_6_grid_consistent(images: List[Image.Image], base_size: Tuple[int, int]) -> Optional[Image.Image]:
    """6张同尺寸图片网格拼接 (2x3)"""
    try:
        if len(images) < 5:
            return None
            
        width, height = base_size
        
        # 创建2x3网格
        merged = Image.new('RGB', (width * 3, height * 2), (255, 255, 255))
        
        for i in range(min(6, len(images))):
            row = i // 3
            col = i % 3
            x = col * width
            y = row * height
            merged.paste(images[i], (x, y))
        
        return merged
        
    except Exception as e:
        debug_log(f"6宫格拼接失败: {e}")
        return None


def merge_9_grid_consistent(images: List[Image.Image], base_size: Tuple[int, int]) -> Optional[Image.Image]:
    """9张同尺寸图片网格拼接 (3x3)"""
    try:
        if len(images) < 8:
            return None
            
        width, height = base_size
        
        # 创建3x3网格
        merged = Image.new('RGB', (width * 3, height * 3), (255, 255, 255))
        
        for i in range(min(9, len(images))):
            row = i // 3
            col = i % 3
            x = col * width
            y = row * height
            merged.paste(images[i], (x, y))
        
        return merged
        
    except Exception as e:
        debug_log(f"9宫格拼接失败: {e}")
        return None
