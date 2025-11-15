#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚合推送插件测试脚本
"""

import asyncio
import sys
import os

# 设置输出编码
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加路径以便导入插件模块
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

async def test_basic_functions():
    """测试基本功能"""
    print("开始测试聚合推送插件...")
    
    try:
        # 测试导入
        from helper import get_all_datasources, aggregator_manager, debug_log
        print("成功导入helper模块")
        
        # 测试数据源获取
        print("\n测试数据源获取...")
        datasources = await get_all_datasources()
        if datasources:
            print(f"成功获取 {len(datasources)} 个数据源")
            
            # 按平台统计
            platforms = {}
            for ds in datasources:
                platform = ds.get('platform', 'unknown')
                platforms[platform] = platforms.get(platform, 0) + 1
            
            print("平台分布：")
            for platform, count in platforms.items():
                print(f"   {platform}: {count} 个")
                
            # 更新订阅管理器
            aggregator_manager.update_datasources(datasources)
            print("订阅管理器数据源更新成功")
            
        else:
            print("数据源获取失败")
        
        # 测试菜单生成
        print("\n测试菜单生成...")
        menu_text, index_map = aggregator_manager.generate_datasource_menu()
        if index_map:
            print(f"成功生成菜单，包含 {len(index_map)} 个选项")
            print("菜单预览（前5行）：")
            lines = menu_text.split('\n')[:5]
            for line in lines:
                print(f"   {line}")
        else:
            print("菜单生成失败")
            
        print("\n基本功能测试完成！")
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()


async def test_subscription_manager():
    """测试订阅管理"""
    print("\n测试订阅管理功能...")
    
    try:
        from helper import aggregator_manager
        
        # 测试添加订阅
        test_datasource_ids = ['test-1', 'test-2']
        success = aggregator_manager.add_subscription('test_group', 'test_bot', test_datasource_ids)
        if success:
            print("测试订阅添加成功")
        else:
            print("测试订阅添加失败")
        
        # 测试获取订阅
        subscriptions = aggregator_manager.get_enabled_subscriptions()
        print(f"获取到 {len(subscriptions)} 个启用的订阅")
        
        # 测试移除订阅
        success = aggregator_manager.remove_subscription('test_group', 'test_bot')
        if success:
            print("测试订阅移除成功")
        else:
            print("测试订阅移除失败")
            
    except Exception as e:
        print(f"订阅管理测试失败: {e}")


async def main():
    """主测试函数"""
    print("=" * 50)
    print("CeobeAPI聚合推送插件功能测试")
    print("=" * 50)
    
    await test_basic_functions()
    await test_subscription_manager()
    
    print("\n" + "=" * 50)
    print("测试完成！")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())