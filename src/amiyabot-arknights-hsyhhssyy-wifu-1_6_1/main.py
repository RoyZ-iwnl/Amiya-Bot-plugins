import re
import time
import datetime
import json
import copy
import random
import os

from pathlib import Path
from urllib.parse import quote
from amiyabot.builtin.messageChain import ChainBuilder
from amiyabot import PluginInstance
from core.util import read_yaml
from core import log, Message, Chain
from core.database.user import User, UserInfo
from core.database.bot import OperatorConfig
from core.resource.arknightsGameData import ArknightsGameData, ArknightsGameDataResource, Operator
from .database import AmiyaBotWifuStatusDataBase

curr_dir = os.path.dirname(__file__)

class WaitAllRequestsDone(ChainBuilder):
    @classmethod
    async def on_page_rendered(cls,page):
        await page.wait_for_load_state('networkidle')
        log.info('Page loaded and networkidle.')

class WifuPluginInstance(PluginInstance):
    def install(self):
        AmiyaBotWifuStatusDataBase.create_table(safe=True)

bot = WifuPluginInstance(
    name='每日随机助理',
    version='1.7.0',
    plugin_id='amiyabot-arknights-hsyhhssyy-wifu',
    plugin_type='',
    description='每日生成一个随机助理，支持个人专属助理池',
    document=f'{curr_dir}/README.md'
)

# 功能开关和用户权限配置
SPECIAL_USER_CONFIG = {
    'enable_special_features': True,  # 总开关
    'keyword_restricted_users': {
        # 示例：'12345': ['阿米娅', '凯尔希'],
        # 示例：'user_abc': ['推进之王', '银灰']
    },
    'multi_draw_users': [
        # 可以多次抽选的用户ID列表
        # 示例：'12345', 'user_abc'
    ]
}

def compare_date_difference(day1: str,day2: str):
    time_array1 = time.strptime(''.join(day1.split(' ')[0]), "%Y-%m-%d") 
    timestamp_day1 = int(time.mktime(time_array1))
    time_array2 = time.strptime(''.join(day2.split(' ')[0]), "%Y-%m-%d")
    timestamp_day2 = int(time.mktime(time_array2))
    result = (timestamp_day1 - timestamp_day2) // 60 // 60 // 24
    return result

def compare_second_difference(day1: str,day2: str):
    time_array1 = time.strptime(''.join(day1.split(' ')[0]), "%Y-%m-%d %H:%M:%S") 
    timestamp_day1 = int(time.mktime(time_array1))
    time_array2 = time.strptime(''.join(day2.split(' ')[0]), "%Y-%m-%d %H:%M:%S")
    timestamp_day2 = int(time.mktime(time_array2))
    result = (timestamp_day1 - timestamp_day2)
    return result

def get_user_assistant_mode(user_id: str):
    """获取用户助理模式"""
    wifu_meta = UserInfo.get_meta_value(user_id, 'amiyabot-arknights-wifu')
    return wifu_meta.get('assistant_mode', 'random')

def get_user_exclusive_assistants(user_id: str):
    """获取用户专属助理池"""
    wifu_meta = UserInfo.get_meta_value(user_id, 'amiyabot-arknights-wifu')
    return wifu_meta.get('exclusive_assistants', [])

def set_assistant_mode(user_id: str, mode: str):
    """设置用户助理模式"""
    wifu_meta = UserInfo.get_meta_value(user_id, 'amiyabot-arknights-wifu')
    wifu_meta['assistant_mode'] = mode
    UserInfo.set_meta_value(user_id, 'amiyabot-arknights-wifu', wifu_meta)

def add_exclusive_assistant(user_id: str, assistant_name: str):
    """添加专属助理"""
    wifu_meta = UserInfo.get_meta_value(user_id, 'amiyabot-arknights-wifu')
    exclusive_assistants = wifu_meta.get('exclusive_assistants', [])
    if assistant_name not in exclusive_assistants:
        exclusive_assistants.append(assistant_name)
        wifu_meta['exclusive_assistants'] = exclusive_assistants
        UserInfo.set_meta_value(user_id, 'amiyabot-arknights-wifu', wifu_meta)
        return True
    return False

def remove_exclusive_assistant(user_id: str, assistant_name: str):
    """删除专属助理"""
    wifu_meta = UserInfo.get_meta_value(user_id, 'amiyabot-arknights-wifu')
    exclusive_assistants = wifu_meta.get('exclusive_assistants', [])
    if assistant_name in exclusive_assistants:
        exclusive_assistants.remove(assistant_name)
        wifu_meta['exclusive_assistants'] = exclusive_assistants
        UserInfo.set_meta_value(user_id, 'amiyabot-arknights-wifu', wifu_meta)
        return True
    return False

def clear_exclusive_assistants(user_id: str):
    """清空专属助理池"""
    wifu_meta = UserInfo.get_meta_value(user_id, 'amiyabot-arknights-wifu')
    wifu_meta['exclusive_assistants'] = []
    UserInfo.set_meta_value(user_id, 'amiyabot-arknights-wifu', wifu_meta)

def find_operator_by_name(operators_dict: dict, operator_name: str):
    """根据干员名称查找干员"""
    for op_key, operator in operators_dict.items():
        if operator.name == operator_name:
            return op_key, operator
    return None, None

def filter_operators_by_user(operators_dict: dict, user_id: str):
    """根据用户ID过滤可选择的干员"""
    if not SPECIAL_USER_CONFIG['enable_special_features']:
        return operators_dict
    
    # 转换user_id为字符串以支持字母数字混合ID
    user_id_str = str(user_id)
    
    # 检查用户助理模式
    assistant_mode = get_user_assistant_mode(user_id_str)
    
    if assistant_mode == 'exclusive':
        # 专属模式：只从专属助理池中选择
        exclusive_assistants = get_user_exclusive_assistants(user_id_str)
        if exclusive_assistants:
            filtered_operators = {}
            for assistant_name in exclusive_assistants:
                op_key, operator = find_operator_by_name(operators_dict, assistant_name)
                if operator:
                    filtered_operators[op_key] = operator
            # 如果专属助理池中没有有效干员，返回原始列表避免程序崩溃
            if filtered_operators:
                return filtered_operators
    
    # 检查用户是否有关键字限制（原有功能保留）
    if user_id_str in SPECIAL_USER_CONFIG['keyword_restricted_users']:
        keywords = SPECIAL_USER_CONFIG['keyword_restricted_users'][user_id_str]
        filtered_operators = {}
        
        for op_key, operator in operators_dict.items():
            # 检查干员名称是否包含指定关键字
            for keyword in keywords:
                if keyword in operator.name:
                    filtered_operators[op_key] = operator
                    break
        
        # 如果过滤后没有干员，返回原始列表避免程序崩溃
        return filtered_operators if filtered_operators else operators_dict
    
    return operators_dict

def can_user_multi_draw(user_id: str):
    """检查用户是否可以多次抽选"""
    if not SPECIAL_USER_CONFIG['enable_special_features']:
        return False
    
    user_id_str = str(user_id)
    return user_id_str in SPECIAL_USER_CONFIG['multi_draw_users']

async def wifu_action(data: Message):
    # log.info('触发了选老婆功能.')
    wifu_meta = UserInfo.get_meta_value(data.user_id,'amiyabot-arknights-wifu')

    now = datetime.date.today()
    user_id_str = str(data.user_id)  # 支持字母数字混合ID

    # 查看User是不是已经有Wifu了
    if wifu_meta.__contains__('wifu_date') and wifu_meta.__contains__('wifu_name'):        
        # 计算日期
        last_wifu_time = wifu_meta['wifu_date']
        time_delta = compare_date_difference(now.strftime("%Y-%m-%d"),last_wifu_time)

        # 检查是否可以多次抽选或者日期已过
        if time_delta < 1 and not can_user_multi_draw(user_id_str):            
            log.info(f'选老婆TimeDelta{time_delta}')
            return await show_existing_wifu(data,data.user_id)           

    wifu_meta['wifu_date'] = now.strftime("%Y-%m-%d")

    # 随机一位 Wifu给他
    operators = {}
    if not operators:
        operators = copy.deepcopy(ArknightsGameData().operators)

    # 根据用户ID过滤可选择的干员
    filtered_operators = filter_operators_by_user(operators, user_id_str)
    
    if not filtered_operators:
        # 如果没有可选择的干员，返回错误信息
        ask = Chain(data, at=True).text('抱歉博士，当前没有符合您条件的助理可以选择呢~')
        return ask
    
    # 先过滤掉被OperatorConfig排除的干员
    available_operators = {}
    for op_key, operator in filtered_operators.items():
        if not OperatorConfig.get_or_none(operator_name=operator.name, operator_type=8):
            available_operators[op_key] = operator

    # 如果没有可用的干员，使用所有过滤后的干员避免程序崩溃
    if not available_operators:
        available_operators = filtered_operators

    # 随机选择一个干员
    operator = available_operators[random.choice(list(available_operators.keys()))]

    wifu_meta['wifu_name'] = operator.name

    UserInfo.set_meta_value(data.user_id,'amiyabot-arknights-wifu',wifu_meta)

    # 如果是多次抽选用户，删除今天的历史记录
    if can_user_multi_draw(user_id_str):
        AmiyaBotWifuStatusDataBase.delete().where(
            (AmiyaBotWifuStatusDataBase.channel_id == data.channel_id) &
            (AmiyaBotWifuStatusDataBase.user_id == data.user_id) &
            (AmiyaBotWifuStatusDataBase.create_at == datetime.date.today())
        ).execute()

    AmiyaBotWifuStatusDataBase.create(channel_id=data.channel_id, user_id=data.user_id, wifu_name=operator.name,
                                      create_at=datetime.date.today())

    count = count_in_channel(data.channel_id,operator.name,data.user_id)

    # 构建消息
    assistant_mode = get_user_assistant_mode(user_id_str)
    
    # 根据助理模式设置不同的提示文字
    if assistant_mode == 'exclusive':
        message_str = f'博士，您今日的专属助理是干员{operator.name}呢'
    else:
        message_str = f'博士，您今日选到的助理是干员{operator.name}呢'

    if can_user_multi_draw(user_id_str):
        message_str += "（您拥有多次抽选权限）"

    if count>1:
        message_str += f"，他已经是第{count}次成为您的助理了！\n"
    else:
        message_str += "！\n"

    ask = Chain(data, at=True, chain_builder = WaitAllRequestsDone()).text(message_str)
 
    return await create_ret_data(data, ask,operator)

async def create_ret_data(data, ask,operator):
    
    skin = random.choice(operator.skins())
    skin_path = (await ArknightsGameDataResource.get_skin_file(skin, encode_url=True)) if skin else ''

    if not skin_path:
        return ask.text('目前还没有该干员的立绘，真是抱歉博士~[face:9]')
    else:
        relative_path = Path(f"../../../{skin_path}")
        log.info(f'skin: {relative_path}')
        
        ask.html(path=f'{curr_dir}/template/wifu.html',
                 data={"id": "testAlt", "image": f"{relative_path}"}, width=1024)

    voices = operator.voices()
    if not voices:
        log.info(f'No voice file for operator {operator.operator_name}.')
        return ask
    else:
        voice = voices[0]
        voice_path = await ArknightsGameDataResource.get_voice_file(operator, voice['voice_title'],'_cn')

        if not voice_path:
            return ask
        else:
            return ask.text(voice['voice_text'].replace('{@nickname}',data.nickname)).voice(voice_path)

    return ask

# 计算user_id在指定channel_id和wifu_name下的记录count数
def count_in_channel(channel_id, wifu_name, user_id):
    return AmiyaBotWifuStatusDataBase.select().where(
        (AmiyaBotWifuStatusDataBase.channel_id == channel_id) &
        (AmiyaBotWifuStatusDataBase.wifu_name == wifu_name) &
        (AmiyaBotWifuStatusDataBase.user_id == user_id)
    ).count()

# 计算user_id在全部channel_id和指定wifu_name下的记录count数
def count_in_all_channels(wifu_name, user_id):
    return AmiyaBotWifuStatusDataBase.select().where(
        (AmiyaBotWifuStatusDataBase.wifu_name == wifu_name) &
        (AmiyaBotWifuStatusDataBase.user_id == user_id)
    ).count()

async def show_existing_wifu(data: Message, user_id: int):

    wifu_meta = UserInfo.get_meta_value(user_id,'amiyabot-arknights-wifu')

    operator_name = wifu_meta['wifu_name']

    operators = {}
    if not operators:
        operators = copy.deepcopy(ArknightsGameData().operators)

    operator = operators[operator_name]

    count = count_in_channel(data.channel_id,operator.name,data.user_id)

    # 构建消息
    user_id_str = str(user_id)
    assistant_mode = get_user_assistant_mode(user_id_str)
    
    # 根据助理模式设置不同的提示文字
    if assistant_mode == 'exclusive':
        message_str = f'博士，您今天已经选过助理啦，您的专属助理是干员{operator.name}哦'
    else:
        message_str = f'博士，您今天已经选过助理啦，您的助理是干员{operator.name}哦'

    if can_user_multi_draw(user_id_str):
        message_str += "（您可以重新抽选）"

    if count>1:
        message_str += f"，他已经是第{count}次成为您的助理了呢~"
    else:
        message_str += "~"

    ask = Chain(data, at=True, chain_builder = WaitAllRequestsDone()).text(message_str)

    return await create_ret_data(data,ask,operator)

# 助理模式管理功能
@bot.on_message(keywords=['兔兔切换助理模式随机', '兔兔切换助理模式 随机'], level=3)
async def switch_to_random_mode(data: Message):
    user_id_str = str(data.user_id)
    set_assistant_mode(user_id_str, 'random')
    return Chain(data, at=True).text('已切换到随机助理模式，将从所有助理中随机选择~')

@bot.on_message(keywords=['兔兔切换助理模式专属', '兔兔切换助理模式 专属'], level=3)
async def switch_to_exclusive_mode(data: Message):
    user_id_str = str(data.user_id)
    exclusive_assistants = get_user_exclusive_assistants(user_id_str)
    
    if not exclusive_assistants:
        return Chain(data, at=True).text('您还没有设置专属助理池，请先使用"新增专属助理"命令添加助理~')
    
    set_assistant_mode(user_id_str, 'exclusive')
    assistant_list = '、'.join(exclusive_assistants)
    return Chain(data, at=True).text(f'已切换到专属助理模式，将只从您的专属助理池中随机选择：{assistant_list}')

@bot.on_message(keywords=['兔兔新增专属助理'], level=3)
async def add_exclusive_assistant_handler(data: Message):
    user_id_str = str(data.user_id)
    
    # 提取助理名称
    message_text = data.text.strip()
    assistant_name = message_text.replace('兔兔新增专属助理', '').strip()
    
    if not assistant_name:
        return Chain(data, at=True).text('请输入要添加的助理名称，格式：兔兔新增专属助理 助理名称')
    
    # 验证助理是否存在
    operators = copy.deepcopy(ArknightsGameData().operators)
    op_key, operator = find_operator_by_name(operators, assistant_name)
    
    if not operator:
        return Chain(data, at=True).text(f'未找到名为"{assistant_name}"的助理，请检查名称是否正确~')
    
    # 添加到专属助理池
    if add_exclusive_assistant(user_id_str, assistant_name):
        exclusive_assistants = get_user_exclusive_assistants(user_id_str)
        assistant_list = '、'.join(exclusive_assistants)
        return Chain(data, at=True).text(f'已成功添加"{assistant_name}"到您的专属助理池！\n当前专属助理：{assistant_list}')
    else:
        return Chain(data, at=True).text(f'"{assistant_name}"已在您的专属助理池中，无需重复添加~')

@bot.on_message(keywords=['兔兔删除专属助理'], level=3)
async def remove_exclusive_assistant_handler(data: Message):
    user_id_str = str(data.user_id)
    
    # 提取助理名称
    message_text = data.text.strip()
    assistant_name = message_text.replace('兔兔删除专属助理', '').strip()
    
    if not assistant_name:
        return Chain(data, at=True).text('请输入要删除的助理名称，格式：删除专属助理 助理名称')
    
    # 从专属助理池删除
    if remove_exclusive_assistant(user_id_str, assistant_name):
        exclusive_assistants = get_user_exclusive_assistants(user_id_str)
        if exclusive_assistants:
            assistant_list = '、'.join(exclusive_assistants)
            return Chain(data, at=True).text(f'已成功从专属助理池中删除"{assistant_name}"！\n当前专属助理：{assistant_list}')
        else:
            # 如果专属助理池为空，自动切换到随机模式
            set_assistant_mode(user_id_str, 'random')
            return Chain(data, at=True).text(f'已成功删除"{assistant_name}"！专属助理池已空，已自动切换到随机助理模式~')
    else:
        return Chain(data, at=True).text(f'"{assistant_name}"不在您的专属助理池中~')

@bot.on_message(keywords=['兔兔清空专属助理'], level=3)
async def clear_exclusive_assistants_handler(data: Message):
    user_id_str = str(data.user_id)
    
    clear_exclusive_assistants(user_id_str)
    set_assistant_mode(user_id_str, 'random')
    
    return Chain(data, at=True).text('已清空您的专属助理池并切换到随机助理模式~')

@bot.on_message(keywords=['兔兔查看助理设置', '兔兔助理设置'], level=3)
async def view_assistant_settings(data: Message):
    user_id_str = str(data.user_id)
    
    assistant_mode = get_user_assistant_mode(user_id_str)
    exclusive_assistants = get_user_exclusive_assistants(user_id_str)
    #can_multi = can_user_multi_draw(user_id_str)
    
    mode_text = "随机模式" if assistant_mode == 'random' else "专属模式"
    #multi_text = "是" if can_multi else "否"
    
    message_str = f'博士，您当前的助理设置：\n模式：{mode_text}\n'
    
    if assistant_mode == 'exclusive' and exclusive_assistants:
        assistant_list = '、'.join(exclusive_assistants)
        message_str += f'\n专属助理池：{assistant_list}'
    elif assistant_mode == 'exclusive':
        message_str += '\n专属助理池：空（请添加助理）'
    
    return Chain(data, at=True).text(message_str)

@bot.on_message(keywords=['选老婆', '抽老婆', '选助理', '抽助理'],level=2)
async def _(data: Message):
    return await wifu_action(data)
