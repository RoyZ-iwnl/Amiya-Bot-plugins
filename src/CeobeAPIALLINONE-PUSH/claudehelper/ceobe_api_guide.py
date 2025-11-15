#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小刻食堂API向导式测试脚本 - 功能增强版
基于 weibo_fetcher.py 扩展而来，提供更丰富的交互体验和数据查看功能

使用方法：
python ceobe_api_guide.py

功能特色：
- 🎯 向导式操作界面
- 🔍 多种数据源筛选
- 📊 详细数据统计分析  
- 🖼️ 图片链接提取
- 💾 数据导出功能
- ⚙️ 自定义查询参数
"""

import requests
import uuid
import json
import time
import sys
import os
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple
import threading

# 设置编码避免Windows控制台问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class Colors:
    """控制台颜色定义"""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class CeobeAPIGuide:
    """小刻食堂API向导式测试工具"""
    
    def __init__(self):
        # API 端点配置
        self.SERVER_BASE = 'https://server.ceobecanteen.top/api/v1/'
        self.SERVER_CDN_BASE = 'https://server-cdn.ceobecanteen.top/api/v1/'
        self.CDN_BASE = 'https://cdn.ceobecanteen.top/'
        
        # 生成唯一客户端ID
        self.client_id = str(uuid.uuid4())
        
        # 会话对象，用于复用连接
        self.session = requests.Session()
        self.session.headers.update(self.get_base_headers())
        
        # 缓存数据
        self.datasources_cache = None
        self.last_combo_id = None
        self.last_cookie_info = None
        self.last_weibo_data = None
        
        print(f"{Colors.HEADER}🚀 小刻食堂API向导式测试工具已启动{Colors.ENDC}")
        print(f"{Colors.OKCYAN}客户端ID: {self.client_id}{Colors.ENDC}")
        print("-" * 80)
    
    def get_base_headers(self) -> Dict[str, str]:
        """获取基础请求头"""
        return {
            'Content-Type': 'application/json',
            'User-Agent': 'Ceobe-Canteen-Browser-Extension/4.0.5',
            'x-ceobe-client-id': self.client_id,
            'x-ceobe-client-type': 'browser-extension',
            'x-ceobe-client-platform': 'chrome',
            'x-ceobe-client-version': '4.0.5'
        }
    
    def make_request(self, url: str, method: str = 'GET', data: Optional[Dict] = None, 
                    timeout: int = 10, show_response: bool = True) -> Optional[Dict]:
        """发送HTTP请求"""
        try:
            if method.upper() == 'GET':
                response = self.session.get(url, timeout=timeout)
            elif method.upper() == 'POST':
                response = self.session.post(url, json=data, timeout=timeout)
            else:
                raise ValueError(f"不支持的HTTP方法: {method}")
            
            print(f'{Colors.OKCYAN}🔗 请求: {method} {url}{Colors.ENDC}')
            if response.status_code == 200:
                print(f'{Colors.OKGREEN}✅ 状态码: {response.status_code}{Colors.ENDC}')
            else:
                print(f'{Colors.FAIL}❌ 状态码: {response.status_code}{Colors.ENDC}')
            
            # 处理非200状态码
            if response.status_code != 200:
                if response.status_code == 404:
                    print(f'{Colors.WARNING}⚠️ 资源未找到{Colors.ENDC}')
                elif response.status_code >= 500:
                    print(f'{Colors.FAIL}💥 服务器内部错误{Colors.ENDC}')
                return None
            
            # 解析JSON响应
            try:
                result = response.json()
                if show_response:
                    print(f'{Colors.OKBLUE}📄 响应数据预览:{Colors.ENDC}')
                    # 只显示前500个字符的预览
                    preview = json.dumps(result, indent=2, ensure_ascii=False)[:500]
                    print(preview + "..." if len(preview) >= 500 else preview)
                    print("-" * 60)
                return result
            except json.JSONDecodeError:
                print(f'{Colors.FAIL}❌ 响应不是有效的JSON格式{Colors.ENDC}')
                print(f'响应内容: {response.text[:300]}...')
                return None
                
        except requests.RequestException as e:
            print(f'{Colors.FAIL}🌐 网络请求异常: {e}{Colors.ENDC}')
            return None
        except Exception as e:
            print(f'{Colors.FAIL}💥 未知错误: {e}{Colors.ENDC}')
            return None
    
    def get_all_datasources(self, force_refresh: bool = False) -> Optional[List[Dict]]:
        """获取所有数据源"""
        if self.datasources_cache and not force_refresh:
            return self.datasources_cache
            
        print(f'\n{Colors.HEADER}=== 🔍 获取数据源列表 ==={Colors.ENDC}')
        url = f'{self.SERVER_BASE}canteen/config/datasource/list'
        
        response = self.make_request(url, show_response=False)
        if response and response.get('code') == '00000':
            datasources = response.get('data', [])
            self.datasources_cache = datasources
            
            # 统计各平台数据源
            platform_counts = {}
            for ds in datasources:
                platform = ds.get('platform', '未知')
                platform_counts[platform] = platform_counts.get(platform, 0) + 1
            
            print(f'{Colors.OKGREEN}✅ 成功获取 {len(datasources)} 个数据源{Colors.ENDC}')
            print(f'{Colors.OKBLUE}📊 平台分布:{Colors.ENDC}')
            for platform, count in sorted(platform_counts.items()):
                emoji = self.get_platform_emoji(platform)
                print(f'  {emoji} {platform}: {count} 个')
            
            return datasources
        else:
            print(f'{Colors.FAIL}❌ 获取数据源列表失败{Colors.ENDC}')
            return None
    
    def get_platform_emoji(self, platform: str) -> str:
        """获取平台对应的emoji"""
        emoji_map = {
            'weibo': '🔵',
            'bilibili': '📺',
            'netease-cloud-music': '🎵',
            'arknights-game': '🎮',
            'arknights-website': '🌐'
        }
        return emoji_map.get(platform, '📱')
    
    def filter_datasources_by_platform(self, datasources: List[Dict], platform: str) -> List[Dict]:
        """按平台筛选数据源"""
        return [ds for ds in datasources if ds.get('platform') == platform]
    
    def display_datasources(self, datasources: List[Dict], title: str = "数据源列表"):
        """显示数据源列表"""
        print(f'\n{Colors.HEADER}📋 {title}{Colors.ENDC}')
        if not datasources:
            print(f'{Colors.WARNING}⚠️ 没有找到匹配的数据源{Colors.ENDC}')
            return
        
        for i, ds in enumerate(datasources):
            platform_emoji = self.get_platform_emoji(ds.get('platform'))
            nickname = ds.get('nickname', '未知')
            platform = ds.get('platform', '未知')
            unique_id = ds.get('unique_id', '未知')
            db_key = ds.get('db_unique_key', '未知')
            jump_url = ds.get('jump_url', '')
            
            print(f'{Colors.OKCYAN}  [{i+1:2d}] {platform_emoji} {nickname}{Colors.ENDC}')
            print(f'       📍 平台: {platform}')
            print(f'       🆔 ID: {unique_id}')
            print(f'       🔑 Key: {db_key}')
            if jump_url:
                print(f'       🔗 链接: {jump_url}')
            print()
    
    def get_datasource_combo_id(self, datasource_ids: List[str]) -> Optional[str]:
        """获取数据源组合ID"""
        print(f'\n{Colors.HEADER}=== 🔗 获取数据源组合ID ==={Colors.ENDC}')
        print(f'{Colors.OKBLUE}📝 数据源数量: {len(datasource_ids)}{Colors.ENDC}')
        
        url = f'{self.SERVER_BASE}canteen/user/getDatasourceComb'
        data = {'datasource_push': datasource_ids}
        
        response = self.make_request(url, 'POST', data, show_response=False)
        if response and response.get('code') == '00000':
            combo_id = response['data']['datasource_comb_id']
            self.last_combo_id = combo_id
            print(f'{Colors.OKGREEN}✅ 组合ID: {combo_id}{Colors.ENDC}')
            return combo_id
        else:
            error_msg = response.get('message', '未知错误') if response else '请求失败'
            print(f'{Colors.FAIL}❌ 获取组合ID失败: {error_msg}{Colors.ENDC}')
            return None
    
    def get_cookie_info(self, combo_id: str, max_retries: int = 3) -> Optional[Dict[str, str]]:
        """获取cookie信息，包含重试逻辑"""
        print(f'\n{Colors.HEADER}=== 🍪 获取Cookie信息 ==={Colors.ENDC}')
        print(f'{Colors.OKBLUE}🔗 组合ID: {combo_id}{Colors.ENDC}')
        
        url = f'{self.CDN_BASE}datasource-comb/{combo_id}'
        
        for attempt in range(max_retries):
            if attempt > 0:
                print(f'{Colors.WARNING}🔄 第 {attempt + 1} 次重试...{Colors.ENDC}')
                time.sleep(2 ** attempt)  # 指数退避
            
            response = self.make_request(url, show_response=False)
            if response:
                cookie_id = response.get('cookie_id')
                update_cookie_id = response.get('update_cookie_id')
                
                if cookie_id:
                    print(f'{Colors.OKGREEN}✅ Cookie ID: {cookie_id}{Colors.ENDC}')
                    if update_cookie_id:
                        print(f'{Colors.OKGREEN}✅ Update Cookie ID: {update_cookie_id}{Colors.ENDC}')
                    
                    cookie_info = {
                        'cookie_id': cookie_id,
                        'update_cookie_id': update_cookie_id
                    }
                    self.last_cookie_info = cookie_info
                    return cookie_info
                else:
                    print(f'{Colors.WARNING}⚠️ 尝试 {attempt + 1}: Cookie ID 为空{Colors.ENDC}')
            else:
                print(f'{Colors.WARNING}⚠️ 尝试 {attempt + 1}: 请求失败{Colors.ENDC}')
        
        print(f'{Colors.FAIL}❌ 多次尝试后仍无法获取有效的Cookie ID{Colors.ENDC}')
        return None
    
    def get_weibo_data(self, combo_id: str, cookie_id: str, 
                      update_cookie_id: Optional[str] = None) -> Optional[Dict]:
        """获取微博数据"""
        print(f'\n{Colors.HEADER}=== 📱 获取微博数据 ==={Colors.ENDC}')
        
        # 构建URL
        url = f'{self.SERVER_CDN_BASE}cdn/cookie/mainList/cookieList'
        params = [f'datasource_comb_id={combo_id}', f'cookie_id={cookie_id}']
        
        if update_cookie_id:
            params.append(f'update_cookie_id={update_cookie_id}')
        
        full_url = url + '?' + '&'.join(params)
        
        response = self.make_request(full_url, show_response=False)
        if response and response.get('code') == '00000':
            data = response.get('data', {})
            cookies = data.get('cookies', [])
            self.last_weibo_data = data
            print(f'{Colors.OKGREEN}✅ 成功获取微博数据，共 {len(cookies)} 条{Colors.ENDC}')
            return data
        else:
            error_msg = response.get('message', '未知错误') if response else '请求失败'
            print(f'{Colors.FAIL}❌ 获取微博数据失败: {error_msg}{Colors.ENDC}')
            return None
    
    def analyze_weibo_data(self, data: Dict) -> Dict[str, Any]:
        """分析微博数据并生成统计信息"""
        cookies = data.get('cookies', [])
        analysis = {
            'total_count': len(cookies),
            'datasource_stats': {},
            'type_stats': {},
            'time_range': {'earliest': None, 'latest': None},
            'image_stats': {'total_images': 0, 'posts_with_images': 0},
            'text_stats': {'total_chars': 0, 'avg_chars': 0, 'long_text_count': 0}
        }
        
        if not cookies:
            return analysis
        
        timestamps = []
        total_chars = 0
        long_text_count = 0
        
        for cookie in cookies:
            # 统计数据源
            datasource = cookie.get('datasource', '未知')
            analysis['datasource_stats'][datasource] = analysis['datasource_stats'].get(datasource, 0) + 1
            
            # 统计类型
            item_type = cookie.get('item', {}).get('type', '未知')
            analysis['type_stats'][item_type] = analysis['type_stats'].get(item_type, 0) + 1
            
            # 时间统计
            timestamp_info = cookie.get('timestamp', {})
            platform_time = timestamp_info.get('platform', 0)
            if platform_time:
                timestamps.append(platform_time)
            
            # 图片统计
            images = cookie.get('default_cookie', {}).get('images', [])
            if images:
                analysis['image_stats']['posts_with_images'] += 1
                analysis['image_stats']['total_images'] += len(images)
            
            # 文本统计
            text = cookie.get('default_cookie', {}).get('text', '')
            text_len = len(text)
            total_chars += text_len
            if cookie.get('item', {}).get('is_long_text'):
                long_text_count += 1
        
        # 计算时间范围
        if timestamps:
            analysis['time_range']['earliest'] = min(timestamps)
            analysis['time_range']['latest'] = max(timestamps)
        
        # 计算文本统计
        if cookies:
            analysis['text_stats']['total_chars'] = total_chars
            analysis['text_stats']['avg_chars'] = total_chars / len(cookies)
            analysis['text_stats']['long_text_count'] = long_text_count
        
        return analysis
    
    def display_analysis(self, analysis: Dict[str, Any]):
        """显示数据分析结果"""
        print(f'\n{Colors.HEADER}📊 数据分析报告{Colors.ENDC}')
        print(f'{Colors.OKGREEN}📈 总数据量: {analysis["total_count"]} 条{Colors.ENDC}')
        
        # 数据源分布
        if analysis['datasource_stats']:
            print(f'\n{Colors.OKBLUE}🏢 数据源分布:{Colors.ENDC}')
            for datasource, count in sorted(analysis['datasource_stats'].items(), key=lambda x: x[1], reverse=True):
                percentage = (count / analysis['total_count'] * 100) if analysis['total_count'] > 0 else 0
                print(f'  📍 {datasource}: {count} 条 ({percentage:.1f}%)')
        
        # 内容类型分布
        if analysis['type_stats']:
            print(f'\n{Colors.OKBLUE}📝 内容类型分布:{Colors.ENDC}')
            for content_type, count in sorted(analysis['type_stats'].items(), key=lambda x: x[1], reverse=True):
                percentage = (count / analysis['total_count'] * 100) if analysis['total_count'] > 0 else 0
                print(f'  🏷️ {content_type}: {count} 条 ({percentage:.1f}%)')
        
        # 时间范围
        time_range = analysis['time_range']
        if time_range['earliest'] and time_range['latest']:
            earliest = datetime.fromtimestamp(time_range['earliest'] / 1000).strftime('%Y-%m-%d %H:%M')
            latest = datetime.fromtimestamp(time_range['latest'] / 1000).strftime('%Y-%m-%d %H:%M')
            print(f'\n{Colors.OKBLUE}⏰ 时间范围:{Colors.ENDC}')
            print(f'  🕐 最早: {earliest}')
            print(f'  🕐 最新: {latest}')
        
        # 图片统计
        img_stats = analysis['image_stats']
        print(f'\n{Colors.OKBLUE}🖼️ 图片统计:{Colors.ENDC}')
        print(f'  📷 总图片数: {img_stats["total_images"]} 张')
        print(f'  📝 含图片帖子: {img_stats["posts_with_images"]} 条')
        
        # 文本统计
        text_stats = analysis['text_stats']
        print(f'\n{Colors.OKBLUE}📄 文本统计:{Colors.ENDC}')
        print(f'  🔤 总字符数: {text_stats["total_chars"]:,}')
        print(f'  📊 平均字符数: {text_stats["avg_chars"]:.1f}')
        print(f'  📰 长文本数: {text_stats["long_text_count"]} 条')
    
    def display_weibo_details(self, data: Dict, limit: int = 10):
        """显示详细的微博内容"""
        cookies = data.get('cookies', [])
        
        print(f'\n{Colors.HEADER}📱 微博内容详情 (前 {min(limit, len(cookies))} 条){Colors.ENDC}')
        
        for i, cookie in enumerate(cookies[:limit]):
            item = cookie.get('item', {})
            default_cookie = cookie.get('default_cookie', {})
            timestamp_info = cookie.get('timestamp', {})
            datasource = cookie.get('datasource', '未知')
            
            # 格式化时间
            platform_time = timestamp_info.get('platform', 0)
            if platform_time:
                formatted_time = datetime.fromtimestamp(platform_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
            else:
                formatted_time = '时间未知'
            
            # 显示基本信息
            print(f'\n{Colors.OKCYAN}[{i+1:2d}] {datasource}{Colors.ENDC}')
            print(f'{Colors.OKGREEN}🆔 ID: {item.get("id", "未知")}{Colors.ENDC}')
            print(f'{Colors.OKBLUE}⏰ 时间: {formatted_time}{Colors.ENDC}')
            print(f'{Colors.OKBLUE}🔗 链接: {item.get("url", "未知")}{Colors.ENDC}')
            print(f'{Colors.OKBLUE}🏷️ 类型: {item.get("type", "未知")}{Colors.ENDC}')
            
            # 显示文本内容
            text = default_cookie.get('text', '')
            if text:
                # 限制显示长度
                display_text = text[:200] + '...' if len(text) > 200 else text
                print(f'{Colors.OKBLUE}📝 内容: {display_text}{Colors.ENDC}')
            
            # 显示图片信息
            images = default_cookie.get('images', [])
            if images:
                print(f'{Colors.OKBLUE}🖼️ 图片: {len(images)} 张{Colors.ENDC}')
                for j, img in enumerate(images[:3]):  # 最多显示3张图片链接
                    print(f'    [{j+1}] {img.get("origin_url", "无链接")}')
                if len(images) > 3:
                    print(f'    ... 还有 {len(images) - 3} 张')
            
            # 显示转发信息
            if item.get('is_retweeted'):
                print(f'{Colors.WARNING}🔄 转发微博{Colors.ENDC}')
            
            print('-' * 80)
    
    def export_data_to_json(self, data: Dict, filename: Optional[str] = None) -> str:
        """导出数据到JSON文件"""
        if not filename:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'weibo_data_{timestamp}.json'
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            file_size = os.path.getsize(filename)
            print(f'{Colors.OKGREEN}✅ 数据已导出到: {filename}{Colors.ENDC}')
            print(f'{Colors.OKBLUE}📁 文件大小: {file_size:,} 字节{Colors.ENDC}')
            return filename
        except Exception as e:
            print(f'{Colors.FAIL}❌ 导出失败: {e}{Colors.ENDC}')
            return ""
    
    def show_main_menu(self):
        """显示主菜单"""
        menu_text = f"""
{Colors.HEADER}🎯 小刻食堂API测试向导 - 主菜单{Colors.ENDC}

{Colors.OKGREEN}1.{Colors.ENDC} 🔍 查看所有数据源
{Colors.OKGREEN}2.{Colors.ENDC} 📱 查看微博数据源
{Colors.OKGREEN}3.{Colors.ENDC} 📺 查看B站数据源  
{Colors.OKGREEN}4.{Colors.ENDC} 🎵 查看网易云音乐数据源
{Colors.OKGREEN}5.{Colors.ENDC} 🎮 查看游戏相关数据源
{Colors.OKGREEN}6.{Colors.ENDC} 🚀 快速获取微博数据
{Colors.OKGREEN}7.{Colors.ENDC} ⚙️ 自定义数据源组合查询
{Colors.OKGREEN}8.{Colors.ENDC} 📊 查看上次数据分析
{Colors.OKGREEN}9.{Colors.ENDC} 💾 导出上次获取的数据
{Colors.OKGREEN}0.{Colors.ENDC} 🚪 退出程序

{Colors.OKCYAN}请输入选项编号:{Colors.ENDC} """
        
        return input(menu_text).strip()
    
    def handle_view_datasources(self, platform_filter: Optional[str] = None):
        """处理查看数据源"""
        datasources = self.get_all_datasources()
        if not datasources:
            return
        
        if platform_filter:
            filtered = self.filter_datasources_by_platform(datasources, platform_filter)
            title = f"{platform_filter.upper()} 数据源列表"
        else:
            filtered = datasources
            title = "所有数据源列表"
        
        self.display_datasources(filtered, title)
        
        input(f"\n{Colors.OKCYAN}按回车键继续...{Colors.ENDC}")
    
    def handle_quick_weibo_fetch(self):
        """处理快速获取微博数据"""
        print(f'{Colors.HEADER}🚀 快速获取微博数据{Colors.ENDC}')
        
        # 获取数据源
        datasources = self.get_all_datasources()
        if not datasources:
            return
        
        # 筛选微博数据源
        weibo_sources = self.filter_datasources_by_platform(datasources, 'weibo')
        if not weibo_sources:
            print(f'{Colors.FAIL}❌ 未找到微博数据源{Colors.ENDC}')
            return
        
        print(f'{Colors.OKGREEN}找到 {len(weibo_sources)} 个微博数据源{Colors.ENDC}')
        
        # 获取组合ID
        datasource_ids = [ds.get('unique_id') for ds in weibo_sources if ds.get('unique_id')]
        combo_id = self.get_datasource_combo_id(datasource_ids)
        if not combo_id:
            return
        
        # 获取Cookie信息
        cookie_info = self.get_cookie_info(combo_id)
        if not cookie_info:
            print(f'{Colors.WARNING}💡 这通常是正常情况，表示目前没有新的微博更新{Colors.ENDC}')
            return
        
        # 获取微博数据
        weibo_data = self.get_weibo_data(
            combo_id,
            cookie_info['cookie_id'],
            cookie_info.get('update_cookie_id')
        )
        
        if weibo_data:
            # 显示分析结果
            analysis = self.analyze_weibo_data(weibo_data)
            self.display_analysis(analysis)
            
            # 询问是否查看详细内容
            show_details = input(f'\n{Colors.OKCYAN}是否查看详细内容? (y/n): {Colors.ENDC}').strip().lower()
            if show_details in ['y', 'yes', '是']:
                try:
                    limit = int(input(f'{Colors.OKCYAN}显示多少条? (默认10): {Colors.ENDC}') or "10")
                except ValueError:
                    limit = 10
                self.display_weibo_details(weibo_data, limit)
        
        input(f"\n{Colors.OKCYAN}按回车键继续...{Colors.ENDC}")
    
    def handle_custom_query(self):
        """处理自定义数据源组合查询"""
        print(f'{Colors.HEADER}⚙️ 自定义数据源组合查询{Colors.ENDC}')
        
        datasources = self.get_all_datasources()
        if not datasources:
            return
        
        # 显示所有数据源供选择
        self.display_datasources(datasources)
        
        print(f'\n{Colors.OKCYAN}请输入要查询的数据源编号，用逗号分隔 (如: 1,3,5):{Colors.ENDC}')
        selection = input().strip()
        
        if not selection:
            return
        
        try:
            indices = [int(x.strip()) - 1 for x in selection.split(',')]
            selected_sources = [datasources[i] for i in indices if 0 <= i < len(datasources)]
        except (ValueError, IndexError):
            print(f'{Colors.FAIL}❌ 输入格式有误{Colors.ENDC}')
            return
        
        if not selected_sources:
            print(f'{Colors.FAIL}❌ 未选择有效的数据源{Colors.ENDC}')
            return
        
        print(f'{Colors.OKGREEN}✅ 已选择 {len(selected_sources)} 个数据源{Colors.ENDC}')
        
        # 获取组合ID并查询数据
        datasource_ids = [ds.get('unique_id') for ds in selected_sources if ds.get('unique_id')]
        combo_id = self.get_datasource_combo_id(datasource_ids)
        
        if combo_id:
            cookie_info = self.get_cookie_info(combo_id)
            if cookie_info:
                weibo_data = self.get_weibo_data(
                    combo_id,
                    cookie_info['cookie_id'],
                    cookie_info.get('update_cookie_id')
                )
                
                if weibo_data:
                    analysis = self.analyze_weibo_data(weibo_data)
                    self.display_analysis(analysis)
        
        input(f"\n{Colors.OKCYAN}按回车键继续...{Colors.ENDC}")
    
    def handle_view_last_analysis(self):
        """查看上次的数据分析"""
        if not self.last_weibo_data:
            print(f'{Colors.WARNING}⚠️ 没有可用的数据，请先执行查询操作{Colors.ENDC}')
        else:
            analysis = self.analyze_weibo_data(self.last_weibo_data)
            self.display_analysis(analysis)
            
            show_details = input(f'\n{Colors.OKCYAN}是否查看详细内容? (y/n): {Colors.ENDC}').strip().lower()
            if show_details in ['y', 'yes', '是']:
                try:
                    limit = int(input(f'{Colors.OKCYAN}显示多少条? (默认10): {Colors.ENDC}') or "10")
                except ValueError:
                    limit = 10
                self.display_weibo_details(self.last_weibo_data, limit)
        
        input(f"\n{Colors.OKCYAN}按回车键继续...{Colors.ENDC}")
    
    def handle_export_data(self):
        """处理数据导出"""
        if not self.last_weibo_data:
            print(f'{Colors.WARNING}⚠️ 没有可导出的数据，请先执行查询操作{Colors.ENDC}')
        else:
            filename = input(f'{Colors.OKCYAN}请输入文件名 (按回车使用默认名称): {Colors.ENDC}').strip()
            self.export_data_to_json(self.last_weibo_data, filename if filename else None)
        
        input(f"\n{Colors.OKCYAN}按回车键继续...{Colors.ENDC}")
    
    def handle_show_final_data(self):
        """显示最终数据的完整详情"""
        if not self.last_weibo_data:
            print(f'{Colors.WARNING}⚠️ 没有可显示的数据，请先执行查询操作{Colors.ENDC}')
            return
            
        print(f'\n{Colors.HEADER}🔍 最终数据完整分析{Colors.ENDC}')
        
        # 显示原始API响应信息
        print(f'\n{Colors.OKBLUE}📡 API响应信息:{Colors.ENDC}')
        if 'request_id' in self.last_weibo_data:
            print(f'  🆔 请求ID: {self.last_weibo_data["request_id"]}')
        if 'timestamp' in self.last_weibo_data:
            print(f'  ⏰ 响应时间: {datetime.fromtimestamp(self.last_weibo_data["timestamp"] / 1000).strftime("%Y-%m-%d %H:%M:%S")}')
        if 'cache_info' in self.last_weibo_data:
            cache = self.last_weibo_data['cache_info']
            print(f'  💾 缓存信息: 命中率 {cache.get("hit_rate", "N/A")}%')
        
        # 显示详细统计
        analysis = self.analyze_weibo_data(self.last_weibo_data)
        self.display_analysis(analysis)
        
        # 显示完整的数据结构信息
        cookies = self.last_weibo_data.get('cookies', [])
        if cookies:
            print(f'\n{Colors.OKBLUE}📊 数据结构分析:{Colors.ENDC}')
            sample_cookie = cookies[0]
            
            # 分析数据字段
            def analyze_dict_structure(d, prefix="", max_depth=3, current_depth=0):
                if current_depth >= max_depth:
                    return
                    
                for key, value in d.items():
                    if isinstance(value, dict):
                        print(f'  {prefix}📁 {key}: dict ({len(value)} 字段)')
                        if current_depth < max_depth - 1:
                            analyze_dict_structure(value, prefix + "  ", max_depth, current_depth + 1)
                    elif isinstance(value, list):
                        print(f'  {prefix}📋 {key}: list ({len(value)} 项)')
                        if value and isinstance(value[0], dict):
                            print(f'  {prefix}  └─ 项目结构: dict ({len(value[0])} 字段)')
                    else:
                        value_type = type(value).__name__
                        if isinstance(value, str) and len(value) > 50:
                            print(f'  {prefix}📝 {key}: {value_type} (长度: {len(value)})')
                        else:
                            print(f'  {prefix}📄 {key}: {value_type}')
            
            print(f'  🔍 单条微博数据结构:')
            analyze_dict_structure(sample_cookie)
        
        # 询问是否查看原始JSON
        show_raw = input(f'\n{Colors.OKCYAN}是否查看原始JSON数据? (y/n): {Colors.ENDC}').strip().lower()
        if show_raw in ['y', 'yes', '是']:
            print(f'\n{Colors.HEADER}📄 原始JSON数据:{Colors.ENDC}')
            print(json.dumps(self.last_weibo_data, indent=2, ensure_ascii=False))
            
        # 询问是否查看详细内容  
        show_details = input(f'\n{Colors.OKCYAN}是否查看格式化的详细内容? (y/n): {Colors.ENDC}').strip().lower()
        if show_details in ['y', 'yes', '是']:
            try:
                limit = int(input(f'{Colors.OKCYAN}显示多少条? (默认所有): {Colors.ENDC}') or str(len(cookies)))
            except ValueError:
                limit = len(cookies)
            
            self.display_weibo_details(self.last_weibo_data, limit, show_full_text=True)
        
        input(f"\n{Colors.OKCYAN}按回车键继续...{Colors.ENDC}")
    
    def run(self):
        """运行主程序"""
        while True:
            try:
                choice = self.show_main_menu()
                
                if choice == '0':
                    print(f'{Colors.OKGREEN}👋 感谢使用小刻食堂API测试工具！{Colors.ENDC}')
                    break
                elif choice == '1':
                    self.handle_view_datasources()
                elif choice == '2':
                    self.handle_view_datasources('weibo')
                elif choice == '3':
                    self.handle_view_datasources('bilibili')
                elif choice == '4':
                    self.handle_view_datasources('netease-cloud-music')
                elif choice == '5':
                    platforms = ['arknights-game', 'arknights-website']
                    datasources = self.get_all_datasources()
                    if datasources:
                        filtered = [ds for ds in datasources if ds.get('platform') in platforms]
                        self.display_datasources(filtered, "游戏相关数据源列表")
                        input(f"\n{Colors.OKCYAN}按回车键继续...{Colors.ENDC}")
                elif choice == '6':
                    self.handle_quick_weibo_fetch()
                elif choice == '7':
                    self.handle_custom_query()
                elif choice == '8':
                    self.handle_view_last_analysis()
                elif choice == '9':
                    self.handle_export_data()
                else:
                    print(f'{Colors.WARNING}⚠️ 无效选项，请重新输入{Colors.ENDC}')
                    time.sleep(1)
            
            except KeyboardInterrupt:
                print(f'\n{Colors.OKCYAN}👋 用户取消操作，退出程序{Colors.ENDC}')
                break
            except Exception as e:
                print(f'{Colors.FAIL}💥 程序出现错误: {e}{Colors.ENDC}')
                input(f"{Colors.OKCYAN}按回车键继续...{Colors.ENDC}")


def main():
    """主函数"""
    guide = CeobeAPIGuide()
    guide.run()


if __name__ == '__main__':
    main()