#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚合推送插件核心逻辑测试（不依赖amiyabot）
"""

import asyncio
import sys
import os
import json
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime

# 设置输出编码
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

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


class AggregatorSubscriptionManager:
    """聚合推送订阅管理器 - 基于JSON文件"""
    
    def __init__(self, config_file: str = 'test_subscriptions.json'):
        self.config_file = config_file
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
            print(f"加载了 {len(self.subscriptions)} 个订阅配置")
            
        except Exception as e:
            print(f"加载订阅配置失败: {e}")
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
                
        except Exception as e:
            print(f"保存订阅配置失败: {e}")
    
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
            print(f"成功添加订阅: {group_id} -> {len(datasource_ids)} 个数据源")
            return True
            
        except Exception as e:
            print(f"添加订阅失败: {e}")
            return False
    
    def remove_subscription(self, group_id: str, bot_id: str) -> bool:
        """移除订阅"""
        try:
            group_key = self._get_group_key(group_id, bot_id)
            
            if group_key in self.subscriptions:
                self.subscriptions[group_key]['enabled'] = False
                self.subscriptions[group_key]['last_update'] = time.time()
                self._save_subscriptions()
                print(f"成功禁用订阅: {group_id}")
                return True
            
            return False
            
        except Exception as e:
            print(f"移除订阅失败: {e}")
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
        print(f"更新了 {len(datasources)} 个数据源信息")
    
    def generate_datasource_menu(self, supported_platforms: List[str] = None) -> tuple[str, Dict[int, str]]:
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


async def test_basic_logic():
    """测试基本逻辑"""
    print("开始测试聚合推送核心逻辑...")
    
    try:
        # 测试订阅管理器
        manager = AggregatorSubscriptionManager()
        
        # 模拟数据源数据
        mock_datasources = [
            {
                'unique_id': 'test-weibo-1',
                'nickname': '测试微博1',
                'platform': 'weibo',
                'db_unique_key': '12345',
                'jump_url': 'https://weibo.com/12345'
            },
            {
                'unique_id': 'test-bili-1', 
                'nickname': '测试B站1',
                'platform': 'bilibili',
                'db_unique_key': '67890',
                'jump_url': 'https://space.bilibili.com/67890'
            },
            {
                'unique_id': 'test-music-1',
                'nickname': '测试网易云音乐1',
                'platform': 'netease-cloud-music',
                'db_unique_key': 'music123',
                'jump_url': 'https://music.163.com'
            }
        ]
        
        # 更新数据源
        manager.update_datasources(mock_datasources)
        print(f"成功添加 {len(mock_datasources)} 个模拟数据源")
        
        # 生成菜单
        menu_text, index_map = manager.generate_datasource_menu()
        print(f"成功生成菜单，包含 {len(index_map)} 个选项")
        print("菜单内容：")
        print(menu_text)
        
        # 测试订阅功能
        test_datasource_ids = ['test-weibo-1', 'test-bili-1']
        success = manager.add_subscription('test_group', 'test_bot', test_datasource_ids)
        print(f"订阅添加结果: {'成功' if success else '失败'}")
        
        # 测试获取订阅
        enabled_subs = manager.get_enabled_subscriptions()
        print(f"获取到 {len(enabled_subs)} 个启用的订阅")
        
        # 测试统一内容格式
        test_content = UnifiedContent(
            content_id='test123',
            platform='weibo',
            source_id='test-weibo-1',
            source_name='测试微博1',
            text='这是一条测试内容，用于验证聚合推送功能是否正常工作。这条内容会被截断显示前面的部分。',
            publish_time=datetime.now(),
            source_url='https://weibo.com/test123',
            media_urls=['https://example.com/image1.jpg', 'https://example.com/image2.jpg']
        )
        
        print(f"测试内容: {test_content.source_name}")
        print(f"显示文本: {test_content.get_display_text(50)}")
        
        # 清理测试文件
        if os.path.exists('test_subscriptions.json'):
            os.remove('test_subscriptions.json')
            print("清理测试文件")
        
        print("\n核心逻辑测试完成！")
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()


async def main():
    """主测试函数"""
    print("=" * 50)
    print("CeobeAPI聚合推送插件核心逻辑测试")
    print("=" * 50)
    
    await test_basic_logic()
    
    print("\n" + "=" * 50)
    print("测试完成！")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())