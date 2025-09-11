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

# 调试开关 - 从配置文件读取
DEBUG_CEOBE_API = True  # 默认值，实际值从bot配置读取

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
