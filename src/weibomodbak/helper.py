import json
import asyncio
import websockets
import os
import time
from websockets.protocol import State

from typing import Dict, List, Tuple, Optional
from amiyabot.log import LoggerManager
from PIL import Image

logger = LoggerManager('WeiBo')


class WeiboWebSocketManager:
    """WebSocket管理器，用于处理微博实时更新"""

    def __init__(self, config_provider=None):
        self.websocket = None
        self.config_provider = config_provider
        self.ws_url = 'wss://cdn.amiyabot.com/api/v1/weibo/ws'  # 默认URL
        self.token = ''
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5
        # 回调字典：key为消息type，value为对应回调函数列表；None表示接收所有类型
        self.message_callbacks = {}
        self.user_ids = []
        # 连接锁，防止重复连接
        self._connection_lock = asyncio.Lock()

        # 从配置中加载WebSocket设置
        self._load_config()

    @property
    def is_connected(self) -> bool:
        """检查WebSocket是否已连接并可用"""
        return self.websocket is not None and self.websocket.state == State.OPEN

    def _load_config(self):
        """从配置中加载WebSocket设置"""
        if self.config_provider:
            try:
                websocket_config = self.config_provider.get_config('websocket') or {}
                self.ws_url = websocket_config.get('url', self.ws_url)
                self.token = websocket_config.get('token', '')
                self.max_reconnect_attempts = websocket_config.get(
                    'reconnectAttempts', self.max_reconnect_attempts
                )
                self.reconnect_delay = websocket_config.get(
                    'reconnectDelay', self.reconnect_delay
                )
                listen: List[Dict[str, str]] = self.config_provider.get_config(
                    'listen'
                ) or []
                self.user_ids = [item['uid'] for item in listen if 'uid' in item]

                if not self.token:
                    logger.warning("WebSocket token未配置，可能影响连接")
            except Exception as e:
                logger.error(f"加载WebSocket配置失败: {e}")

    async def connect(self, skip: bool = False):
        """连接到WebSocket服务器

        Args:
            skip: 如果为True，当锁被其他进程持有时跳过本次连接，默认为False
        """
        # 在获取锁之前先检查连接状态，避免不必要的等待
        listen: List[Dict[str, str]] = (
                self.config_provider.get_config('listen') or []
            )
        user_ids = [item['uid'] for item in listen if 'uid' in item]

        if self.is_connected:
            if set(user_ids) == set(self.user_ids):
                return  # 已连接且订阅用户未变，无需重新连接
            else:
                # 只在需要修改订阅时获取锁
                async with self._connection_lock:
                    if self.is_connected and set(user_ids) != set(self.user_ids):
                        self.user_ids = user_ids
                        await self.subscribe_users(user_ids)
                return

        # 如果未连接，获取锁进行连接
        if skip:
            # 检查锁是否被持有，如果被持有则跳过
            if self._connection_lock.locked():
                logger.debug("锁被其他进程持有，跳过本次连接")
                return

            # 使用 asyncio.wait_for 的极短超时来实现非阻塞
            try:
                await asyncio.wait_for(self._connection_lock.acquire(), timeout=0.01)
            except asyncio.TimeoutError:
                logger.debug("无法立即获取锁，跳过本次连接")
                return

            try:
                # 再次检查，可能在获取锁的过程中其他协程已经建立了连接
                if self.is_connected:
                    if set(user_ids) == set(self.user_ids):
                        return
                    else:
                        self.user_ids = user_ids
                        await self.subscribe_users(user_ids)
                        return
                await self._do_connect(user_ids)
            finally:
                self._connection_lock.release()
        else:
            # 正常等待获取锁
            async with self._connection_lock:
                # 再次检查，可能在等待锁的过程中其他协程已经建立了连接
                if self.is_connected:
                    if set(user_ids) == set(self.user_ids):
                        return
                    else:
                        self.user_ids = user_ids
                        await self.subscribe_users(user_ids)
                        return
                await self._do_connect(user_ids)

    async def _do_connect_internal(self, user_ids=None):
        """内部连接方法，执行实际连接但不处理重连"""
        if user_ids is None:
            user_ids = self.user_ids

        try:
            token = self.config_provider.get_config('websocket').get('token', '') if self.config_provider else ''
            if token:
                self.token = token
                # 使用token作为查询参数
                url = f"{self.ws_url}?token={self.token}"
                logger.info(f"正在连接WebSocket: {url}")
            else:
                logger.warning("WebSocket token未配置，跳过连接")
                return  # 如果没有token，直接返回不连接

            self.websocket = await websockets.connect(url)
            logger.info(f"WebSocket连接成功: {self.ws_url}")
            self.reconnect_attempts = 0  # 重置重连计数器
            self.user_ids = user_ids

            # 启动消息监听任务
            asyncio.create_task(self._listen_messages())
            logger.info("WebSocket消息监听任务已启动")

            if user_ids:
                logger.info(f"开始订阅用户: {user_ids}")
                await self.subscribe_users(user_ids)
            else:
                logger.info("未指定订阅用户")

        except Exception as e:
            logger.error(f"WebSocket连接失败: {e}")
            # 不再调用重连，由调用者处理
            raise

    async def _do_connect(self, user_ids):
        """实际执行连接的内部方法"""
        try:
            await self._do_connect_internal(user_ids)
        except Exception as e:
            # 在锁外部处理重连，避免死锁
            asyncio.create_task(self._handle_reconnect())

    async def _listen_messages(self):
        """监听WebSocket消息，按type分发到对应回调"""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                msg_type = data.get('type') if isinstance(data, dict) else None

                # 获取特定type和全局(None)的回调
                callbacks_specific = self.message_callbacks.get(msg_type, [])
                callbacks_all = self.message_callbacks.get(None, [])

                # 先执行特定再执行全局
                for callback in callbacks_specific + callbacks_all:
                    await callback(data)

        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket连接关闭，尝试重连...")
            await self._handle_reconnect()
        except Exception as e:
            logger.error(f"WebSocket监听出错: {e}")
            await self._handle_reconnect()

    async def _handle_reconnect(self):
        """处理重连逻辑"""
        while self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            logger.info(f"尝试重连 {self.reconnect_attempts}/{self.max_reconnect_attempts}...")

            # 先等待一段时间再尝试连接
            await asyncio.sleep(self.reconnect_delay)

            try:
                # 使用内部连接方法，避免重复加锁
                await self._reconnect_attempt()
                logger.info(f"重连成功，已重新建立WebSocket连接")
                return  # 成功连接，退出重连循环
            except asyncio.TimeoutError as e:
                logger.error(f"重连超时: {e}")
                # 继续下一次重连
            except ConnectionRefusedError as e:
                logger.error(f"连接被拒绝: {e}")
                # 继续下一次重连
            except Exception as e:
                logger.error(f"重连失败: {e}")
                # 继续下一次重连

        logger.error("重连次数已达上限，停止重连")

    async def _reconnect_attempt(self):
        """尝试重新连接，用于重连过程中"""
        async with self._connection_lock:
            # 再次检查连接状态，可能在重连等待期间其他地方已经建立了连接
            if self.is_connected:
                return

            await self._do_connect_internal()

    async def subscribe_users(self, user_ids):
        """订阅特定用户"""
        if not self.is_connected:
            await self.connect()
        else:
            # 发送订阅消息
            subscribe_msg = {"type": "subscribe", "user_ids": user_ids}
            await self.websocket.send(json.dumps(subscribe_msg))
            logger.info(f"已发送订阅请求，用户: {user_ids}")

    def register_message_handler(self, type=None):
        """装饰器：注册消息回调函数，可指定type类型

        使用示例：

        @manager.register_message_handler()        # 收到所有消息
        async def handle_all(msg): ...

        @manager.register_message_handler('update') # 仅处理type为'update'
        async def handle_update(msg): ...
        """

        def decorator(func):
            callbacks = self.message_callbacks.setdefault(type, [])
            callbacks.append(func)
            return func

        return decorator

    async def close(self):
        """关闭WebSocket连接"""
        if self.websocket:
            await self.websocket.close()


# ========== 图片拼合功能 ==========

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
            else:
                inconsistent_images.append(path)

        return (base_width, base_height), consistent_images, croppable_images, inconsistent_images

    except Exception as e:
        logger.error(f"分析图片组失败: {e}")
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

        return img

    except Exception as e:
        logger.error(f"图片裁剪失败: {e}")
        return None


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
        logger.error(f"3图横向拼接失败: {e}")
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
        logger.error(f"6宫格拼接失败: {e}")
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
        logger.error(f"9宫格拼接失败: {e}")
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
            os.makedirs(os.path.dirname(merged_path), exist_ok=True)
            merged.save(merged_path, 'JPEG', quality=85)

            # 计算剩余图片
            remaining_consistent = consistent_images[used_count:] if used_count < len(consistent_images) else []
            remaining_croppable = croppable_images[1:] if len(croppable_images) > 1 else []
            if len(images) >= 9 and croppable_images:
                remaining_croppable = croppable_images
            remaining_images = remaining_consistent + remaining_croppable + inconsistent_images

            return merged_path, remaining_images
        else:
            return None, image_paths

    except Exception as e:
        logger.error(f"图片拼接失败: {e}")
        return None, image_paths


def is_long_strip_image(img_path: str, aspect_ratio_threshold: float = 1.5) -> bool:
    """判断是否为长条图（高度明显大于宽度）"""
    try:
        img = Image.open(img_path)
        width, height = img.size
        img.close()

        # 长条图定义：高度/宽度 > 阈值
        aspect_ratio = height / width
        return aspect_ratio > aspect_ratio_threshold

    except Exception as e:
        logger.error(f"检查长条图失败: {e}")
        return False


def merge_remaining_long_strips(image_paths: List[str], cache_dir: str,
                               aspect_ratio_threshold: float = 1.5,
                               max_width: int = 2000) -> Tuple[Optional[str], List[str]]:
    """对剩余的长条图进行左右拼接，保持比例，顶部对齐"""
    try:
        if not image_paths:
            return None, []

        # 分离长条图和普通图片
        long_strip_paths = []
        normal_image_paths = []

        for path in image_paths:
            if is_long_strip_image(path, aspect_ratio_threshold):
                long_strip_paths.append(path)
            else:
                normal_image_paths.append(path)

        # 如果长条图少于2张，不进行拼接
        if len(long_strip_paths) < 2:
            return None, image_paths

        # 加载长条图并分析尺寸
        long_strip_images = []
        max_height = 0

        for path in long_strip_paths:
            if os.path.exists(path):
                img = Image.open(path)
                if img.mode != 'RGB':
                    img = img.convert('RGB')

                width, height = img.size
                max_height = max(max_height, height)

                long_strip_images.append((img, width, height))

        if not long_strip_images:
            return None, image_paths

        # 限制最大高度以避免过大的拼接图
        if max_height > 4000:
            scale_ratio = 4000 / max_height
            max_height = 4000
        else:
            scale_ratio = 1.0

        # 计算所有图片按比例缩放后的宽度总和
        scaled_images = []
        actual_max_height = 0
        total_width = 0

        for img, width, height in long_strip_images:
            # 按最大高度等比例缩放
            scale = (max_height * scale_ratio) / height
            new_width = int(width * scale)
            new_height = int(height * scale)

            if new_width > 0 and new_height > 0:
                scaled_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                scaled_images.append(scaled_img)
                total_width += new_width
                actual_max_height = max(actual_max_height, new_height)

        # 关闭原始图片对象
        for img, _, _ in long_strip_images:
            img.close()

        if not scaled_images:
            return None, image_paths

        max_height = actual_max_height

        # 检查拼接后的总宽度是否过大
        if total_width > max_width:
            # 重新计算缩放比例
            width_scale = max_width / total_width

            # 重新加载和缩放图片
            for img in scaled_images:
                img.close()
            scaled_images = []
            total_width = 0
            actual_max_height = 0

            for path in long_strip_paths:
                if os.path.exists(path):
                    img = Image.open(path)
                    if img.mode != 'RGB':
                        img = img.convert('RGB')

                    width, height = img.size
                    # 应用两个缩放比例
                    scale = (max_height * scale_ratio * width_scale) / height
                    new_width = int(width * scale)
                    new_height = int(height * scale)

                    if new_width > 0 and new_height > 0:
                        scaled_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                        scaled_images.append(scaled_img)
                        total_width += new_width
                        actual_max_height = max(actual_max_height, new_height)

                    img.close()

            max_height = actual_max_height

        # 创建拼接画布
        merged = Image.new('RGB', (total_width, max_height), (255, 255, 255))

        # 从左到右粘贴图片，都从顶部开始
        x_offset = 0
        for scaled_img in scaled_images:
            merged.paste(scaled_img, (x_offset, 0))
            x_offset += scaled_img.width
            scaled_img.close()

        # 保存拼接结果
        timestamp = int(time.time())
        merged_path = os.path.join(cache_dir, f"merged_long_strips_{timestamp}.jpg")
        os.makedirs(os.path.dirname(merged_path), exist_ok=True)
        merged.save(merged_path, 'JPEG', quality=85)
        merged.close()

        # 返回拼接图片路径和剩余的普通图片
        return merged_path, normal_image_paths

    except Exception as e:
        logger.error(f"长条图拼接失败: {e}")
        return None, image_paths


def is_square_like_image(img_path: str, tolerance_percent: float = 5.0) -> bool:
    """判断是否为接近1:1比例的图片"""
    try:
        img = Image.open(img_path)
        width, height = img.size
        img.close()

        # 计算长宽比
        aspect_ratio = max(width, height) / min(width, height)

        # 计算容忍范围：1 + (tolerance_percent / 100)
        tolerance_ratio = 1 + (tolerance_percent / 100)

        # 如果长宽比在容忍范围内，认为是接近1:1的图片
        return aspect_ratio <= tolerance_ratio

    except Exception as e:
        logger.error(f"检查1:1图片失败: {e}")
        return False


def merge_square_grid(image_paths: List[str], cache_dir: str, grid_size: Tuple[int, int]) -> Tuple[Optional[str], int]:
    """拼接接近1:1的图片为网格"""
    try:
        rows, cols = grid_size
        total_slots = rows * cols
        images_to_use = image_paths[:total_slots]

        if len(images_to_use) < 2:
            return None, 0

        # 加载图片并找到合适的统一尺寸
        loaded_images = []
        max_size = 0

        for path in images_to_use:
            if os.path.exists(path):
                img = Image.open(path)
                if img.mode != 'RGB':
                    img = img.convert('RGB')

                width, height = img.size
                # 使用较小的边作为正方形边长
                size = min(width, height)
                max_size = max(max_size, size)
                loaded_images.append((img, size))

        if not loaded_images:
            return None, 0

        # 限制最大尺寸避免过大
        if max_size > 800:
            max_size = 800

        # 将所有图片调整为统一的正方形尺寸
        processed_images = []
        for img, original_size in loaded_images:
            # 从中心裁剪为正方形
            width, height = img.size
            size = min(width, height)

            # 计算裁剪区域（中心裁剪）
            left = (width - size) // 2
            top = (height - size) // 2
            right = left + size
            bottom = top + size

            # 裁剪为正方形
            square_img = img.crop((left, top, right, bottom))

            # 调整到目标尺寸
            if size != max_size:
                square_img = square_img.resize((max_size, max_size), Image.Resampling.LANCZOS)

            processed_images.append(square_img)
            img.close()

        # 创建网格拼接画布
        canvas_width = cols * max_size
        canvas_height = rows * max_size
        merged = Image.new('RGB', (canvas_width, canvas_height), (255, 255, 255))

        # 拼接图片
        for i, img in enumerate(processed_images):
            row = i // cols
            col = i % cols
            x = col * max_size
            y = row * max_size
            merged.paste(img, (x, y))
            img.close()

        # 保存拼接结果
        timestamp = int(time.time())
        merged_path = os.path.join(cache_dir, f"merged_square_{rows}x{cols}_{timestamp}.jpg")
        os.makedirs(os.path.dirname(merged_path), exist_ok=True)
        merged.save(merged_path, 'JPEG', quality=85)
        merged.close()

        return merged_path, len(processed_images)

    except Exception as e:
        logger.error(f"1:1图片网格拼接失败: {e}")
        return None, 0


def merge_square_like_images(image_paths: List[str], cache_dir: str, tolerance_percent: float = 5.0) -> Tuple[Optional[str], List[str]]:
    """对剩余的接近1:1比例图片进行拼接"""
    try:
        if not image_paths:
            return None, []

        # 筛选出接近1:1比例的图片
        square_like_paths = []
        other_paths = []

        for path in image_paths:
            if is_square_like_image(path, tolerance_percent):
                square_like_paths.append(path)
            else:
                other_paths.append(path)

        # 如果接近1:1的图片少于2张，不进行拼接
        if len(square_like_paths) < 2:
            return None, image_paths

        # 根据图片数量选择合适的拼接方式
        merged_path = None
        used_count = 0

        if len(square_like_paths) >= 4:
            # 4张或更多：2x2网格
            merged_path, used_count = merge_square_grid(square_like_paths[:4], cache_dir, grid_size=(2, 2))

        if not merged_path and len(square_like_paths) >= 2:
            # 2张：横向拼接
            merged_path, used_count = merge_square_grid(square_like_paths[:2], cache_dir, grid_size=(1, 2))

        if merged_path:
            # 计算剩余图片
            remaining_square_paths = square_like_paths[used_count:]
            remaining_images = remaining_square_paths + other_paths
            return merged_path, remaining_images
        else:
            return None, image_paths

    except Exception as e:
        logger.error(f"1:1图片拼接失败: {e}")
        return None, image_paths
