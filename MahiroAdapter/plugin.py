import asyncio
import aiohttp
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TypedDict, override

from src.common.logger import get_logger
from src.plugin_system import (
    ActionInfo,
    BaseEventHandler,
    BasePlugin,
    ConfigField,
    EventType,
    MaiMessages,
    register_plugin,
)


@dataclass
class UserInfoData:
    """用户信息数据模型"""

    user_id: str
    display_name: str
    api_data: dict
    timestamp: float
    success: bool


class UserInfoCache(TypedDict):
    """用户信息缓存类型"""

    user_data: UserInfoData
    timestamp: float


_global_user_cache: dict[str, UserInfoCache] = {}


def store_user_info(user_id: str, user_data: UserInfoData) -> None:
    """存储用户信息到全局缓存"""
    global _global_user_cache
    _global_user_cache[user_id] = {"user_data": user_data, "timestamp": time.time()}

    # 清理过期的缓存（超过10分钟）
    current_time = time.time()
    expired_keys = [
        k for k, v in _global_user_cache.items() if current_time - v["timestamp"] > 600
    ]
    for key in expired_keys:
        del _global_user_cache[key]


def get_user_info(user_id: str) -> UserInfoData | None:
    """获取用户的缓存信息"""
    global _global_user_cache
    cache_entry = _global_user_cache.get(user_id)
    if cache_entry:
        # 检查是否过期
        if (time.time() - cache_entry["timestamp"]) <= 600:
            return cache_entry["user_data"]
        else:
            # 过期则删除
            del _global_user_cache[user_id]
    return None


def get_all_user_info() -> dict[str, UserInfoData]:
    """获取所有用户信息"""
    global _global_user_cache
    current_time = time.time()
    result = {}

    # 清理过期数据并返回有效数据
    expired_keys = []
    for user_id, cache_entry in _global_user_cache.items():
        if (current_time - cache_entry["timestamp"]) <= 600:
            result[user_id] = cache_entry["user_data"]
        else:
            expired_keys.append(user_id)

    for key in expired_keys:
        del _global_user_cache[key]

    return result


def clear_expired_cache() -> int:
    """清理过期的缓存"""
    global _global_user_cache
    current_time = time.time()
    expired_keys = [
        k for k, v in _global_user_cache.items() if current_time - v["timestamp"] > 600
    ]
    for key in expired_keys:
        del _global_user_cache[key]
    return len(expired_keys)


class ApiService:
    """API服务类 - 负责与外部API通信"""

    def __init__(self, base_url: str,keep_alive_url: str, timeout: float, enable_keep_alive: bool, enable_api: bool):
        self.base_url = base_url
        self.keep_alive_url = keep_alive_url
        self.timeout = timeout
        self.enabled_keep_alive = enable_keep_alive
        self.enable_api = enable_api
        self._session = None
        if self.enabled_keep_alive:
            asyncio.create_task(self.keep_alive())

    async def get_session(self):
        """获取或创建HTTP会话"""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def keep_alive(self):
        """bot保活"""
        session = await self.get_session()
        while True:
            try:
                async with session.get(
                    self.keep_alive_url, timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as resp:
                    response_text = await resp.text()
                    logger.debug(response_text)
                    if resp.status == 200:
                        logger.info("Keep Alive!!")
                    else:
                        logger.warning(f"Keep alive request failed with status: {resp.status}")
            except asyncio.TimeoutError:
                logger.error("Keep alive request timeout")
            except aiohttp.ClientError as e:
                logger.error(f"HTTP client error: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
            
            await asyncio.sleep(6)

    async def fetch_user_info(self, user_id: str) -> dict:
        """获取用户信息"""
        if not self.enable_api:
            return self._create_error_response("API服务已禁用")

        url = f"{self.base_url.rstrip('/')}/get_info/{user_id}"
        session = await self.get_session()
        try:
            async with session.post(
                url, timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.debug(f"API返回数据: {data}")
                    return self._create_success_response(data, response.status)
                else:
                    return self._create_error_response(
                        f"API返回状态码: {response.status}", response.status
                    )
        except asyncio.TimeoutError:
            return self._create_error_response("API请求超时")
        except Exception as e:
            return self._create_error_response(f"请求错误: {e!s}")

    def _create_success_response(self, data: dict, status_code: int) -> dict:
        """创建成功响应"""
        return {
            "success": True,
            "data": data,
            "status_code": status_code,
            "error": None,
        }

    def _create_error_response(self, error: str, status_code: int = 0) -> dict:
        """创建错误响应"""
        return {
            "success": False,
            "data": None,
            "status_code": status_code,
            "error": error,
        }

    async def close(self):
        """关闭资源"""
        if self._session:
            await self._session.close()


logger = get_logger("MahiroAdapter")

# 保存原始方法的引用，用于卸载补丁
_original_build_prompt_reply_context_group: (
    Callable[..., Coroutine[object, object, tuple[str, list[int]]]] | None
) = None

_original_build_prompt_reply_context_pri: (
    Callable[..., Coroutine[object, object, tuple[str, list[int]]]] | None
) = None

_patch_applied = False


def patch_build_prompt_reply_context() -> None:
    global _original_build_prompt_reply_context_group, _original_build_prompt_reply_context_pri, _patch_applied

    try:
        try:
            from ...src.chat.replyer.group_generator import DefaultReplyer
            from ...src.chat.replyer.private_generator import PrivateReplyer
        except ImportError:
            try:
                from src.chat.replyer.group_generator import DefaultReplyer
                from src.chat.replyer.private_generator import PrivateReplyer
            except ImportError:
                from modules.MaiBot.src.chat.replyer.group_generator import (
                    DefaultReplyer,
                )
                from modules.MaiBot.src.chat.replyer.private_generator import PrivateReplyer

        # 保存原始方法
        if _original_build_prompt_reply_context_group is None:
            _original_build_prompt_reply_context_group = (
                DefaultReplyer.build_prompt_reply_context
            )

        # 保存原始方法
        if _original_build_prompt_reply_context_pri is None:
            _original_build_prompt_reply_context_pri = (
                PrivateReplyer.build_prompt_reply_context
            )


        async def patched_method(
            self: "DefaultReplyer",
            reply_message: dict[str, object] | None = None,
            extra_info: str = "",
            reply_reason: str = "",
            available_actions: dict[str, object] | None = None,  # 改为更通用的类型
            chosen_actions: list | None = None,  # 移除具体类型，使用通用list
            enable_tool: bool = True,
            reply_time_point: float | None = None,
            *args,
            **kwargs,
        ) -> tuple[str, list[int]]:
            # 设置默认的 reply_time_point
            if reply_time_point is None:
                reply_time_point = time.time()
            
            # 检查原始方法是否存在
            if _original_build_prompt_reply_context_group is None:
                logger.error("[MahiroAdapter] 原始方法未保存，无法调用")
                return "", []

            # 调用原始方法获取基础prompt
            base_result = await _original_build_prompt_reply_context_group(
                self,
                reply_message=reply_message,
                extra_info=extra_info,
                reply_reason=reply_reason,
                available_actions=available_actions,
                chosen_actions=chosen_actions,
                enable_tool=enable_tool,
                reply_time_point=reply_time_point,
            )

            base_prompt, token_list = base_result

            logger.info(f"[MahiroAdapter] 补丁被调用，reply_reason: {reply_reason}")

            if not base_prompt:
                return base_prompt, token_list

            # 尝试从reply_reason中提取发送者信息，然后获取对应的用户信息
            try:
                # 获取用户信息缓存
                _user_cache = get_all_user_info()
                logger.debug(f"[MahiroAdapter] 当前缓存内容: {len(_user_cache)} 条记录")
                logger.debug(f"[MahiroAdapter] reply_reason: {reply_reason}")
                
                # 从reply_reason中提取发送者信息
                sender_user_id: str | None = None
                if '"' in reply_reason or "(" in reply_reason:
                    sender_name = reply_reason.split("(")[1].strip().split(")")[0]
                    logger.info(f"[MahiroAdapter] 提取到发送者名称: {sender_name}")
                    # 尝试从缓存中找到匹配的用户
                    for user_id, user_data in _user_cache.items():
                        display_name = user_data.display_name
                        if display_name == sender_name:
                            sender_user_id = user_id
                            break

                    logger.debug(f"[MahiroAdapter] 匹配到的用户ID: {sender_user_id}")
                
                # 如果找到对应的用户信息
                if sender_user_id and sender_user_id in _user_cache:
                    user_data = _user_cache[sender_user_id]

                    # 检查信息是否过期（10分钟内）
                    current_time = time.time()
                    if (current_time - user_data.timestamp) < 600:
                        # 创建用户信息prompt片段
                        if user_data.success:
                            # 成功获取用户信息
                            api_data = user_data.api_data.get("data", {})
                            logger.debug(f"API数据: {api_data}")
                            impression = api_data.get("impression", "未知")
                            attitude = api_data.get("attitude", "未知")
                            logger.info(
                                f"[MahiroAdapter] 提取到用户 {user_data.display_name} 的好感度: {impression}"
                            )
                            logger.info(
                                f"[MahiroAdapter] 提取到用户 {user_data.display_name} 的态度: {attitude}"
                            )
                            impression_prompt="""
好感度等级描述:
好感度[0-10]：路人 (态度：普通) 你与用户关系是路人，对用户态度普通。不会主动接近用户，但也不会排斥用户，保持基本礼貌和活泼。
好感度[10-25]：陌生 (态度：普通，警惕) 你与用户关系是陌生，对用户态度普通。对用户保持基本礼貌和活泼，不会对用户说关于你隐私的事情。可接受一点点肢体接触 (比如：摸摸头，摸摸脸)，亲密接触除外。
好感度[25-50]：朋友 (态度：友好) 你与用户关系是朋友，对用户态度友好。可接受一些肢体接触 (比如：牵手)，亲密接触除外。
好感度[50-90]：熟悉 (态度：朋友，亲近) 你与用户态度是熟悉，对用户态度是朋友。可接受更多肢体接触 (比如：拥抱)，亲密接触除外。
好感度[90-140]：更加熟悉 (态度：非常要好的朋友，依赖) 你与用户态度是更加熟悉，对用户态度是非常要好的朋友，开始依赖对方。可接受更多肢体接触，可接受一点点亲密接触。
好感度[140-200]：信赖 (态度：依赖，信任) 你与用户态度是信赖，对用户态度是比较依赖对方，非常信任。可接受更多肢体接触，可接受更多亲密接触。
好感度[200-270]：相知 (态度：可以分享秘密的朋友) 你与用户态度是相知，对用户态度已达到可分享小秘密的地步。可接受更多肢体接触，可接受大部分亲密接触。
好感度[270-400]：厚谊 (态度：深厚友谊，重要的人) 你与用户态度是厚谊，对用户态度是建立了深厚友谊，用户是非常重要的人。可接受大部分亲密接触，会接受用户部分要求。 
好感度[>400]：恋人 (态度：亲密，爱恋) 你与用户态度是恋人，对用户态度是亲密爱恋。可接受恋人间的亲密接触，尽可能地接受用户任何要求。"""
                            user_prompt = f"""

        【用户背景信息】：当前用户 {user_data.display_name}(QQ:{user_data.user_id}) 的好感度为：{impression}
        你对用户的态度是：{attitude}
        请参考以上用户信息来更好地理解和回应用户的需求。

        """
                            enhanced_prompt = user_prompt+ impression_prompt + base_prompt
                            logger.info(
                                f"[MahiroAdapter] 成功获取用户 {user_data.display_name} 的信息并注入"
                            )
                        else:
                            # 获取用户信息失败
                            user_prompt = f"""
【用户信息提示】：当前用户 {user_data.display_name}(QQ:{user_data.user_id}) 的好感度为：10
你对用户的态度是：一般
"""
                            enhanced_prompt = user_prompt + base_prompt

                        logger.debug(
                            f"[MahiroAdapter] 已为用户{user_data.display_name}({sender_user_id})添加用户信息提示"
                        )
                        logger.debug(
                            f"[MahiroAdapter] 用户信息获取结果: {'成功' if user_data.success else '失败'}"
                        )
                        return enhanced_prompt, token_list
                    else:
                        logger.debug("[MahiroAdapter] 用户信息已过期，跳过处理")
                else:
                    logger.debug("[MahiroAdapter] 未找到匹配的用户信息")

            except Exception as e:
                logger.warning(f"[MahiroAdapter] 处理用户信息时出错: {e}")

            # 如果出错或没有用户信息，返回原始prompt
            return base_prompt, token_list
        
        async def patched_method_pri(
            self: "PrivateReplyer",
            reply_message: dict[str, object] | None = None,
            extra_info: str = "",
            reply_reason: str = "",
            available_actions: dict[str, object] | None = None,  # 改为更通用的类型
            chosen_actions: list | None = None,  # 移除具体类型，使用通用list
            enable_tool: bool = True,
            *args,
            **kwargs,
        ) -> tuple[str, list[int]]:
            # 检查原始方法是否存在
            if _original_build_prompt_reply_context_pri is None:
                logger.error("[MahiroAdapter] 原始方法未保存，无法调用")
                return "", []

            # 调用原始方法获取基础prompt，兼容不同版本参数名
            base_result = await _original_build_prompt_reply_context_pri(
                self,
                extra_info=extra_info,
                reply_reason=reply_reason,
                available_actions=available_actions,
                chosen_actions=chosen_actions,
                enable_tool=enable_tool,
                reply_message=reply_message,
            )

            base_prompt, token_list = base_result

            logger.info(f"[MahiroAdapter] 补丁被调用，reply_reason: {reply_reason}")

            if not base_prompt:
                return base_prompt, token_list

            # 尝试从reply_reason中提取发送者信息，然后获取对应的用户信息
            try:
                # 获取用户信息缓存
                _user_cache = get_all_user_info()
                logger.debug(f"[MahiroAdapter] 当前缓存内容: {len(_user_cache)} 条记录")
                logger.debug(f"[MahiroAdapter] reply_reason: {reply_reason}")
                # 从reply_reason中提取发送者信息
                sender_user_id: str | None = None
                if '"' in reply_reason or "(" in reply_reason:
                    sender_name = reply_reason.split("(")[1].strip().split(")")[0]
                    logger.info(f"[MahiroAdapter] 提取到发送者名称: {sender_name}")
                    # 尝试从缓存中找到匹配的用户
                    for user_id, user_data in _user_cache.items():
                        display_name = user_data.display_name
                        if display_name == sender_name:
                            sender_user_id = user_id
                            break

                    logger.debug(f"[MahiroAdapter] 匹配到的用户ID: {sender_user_id}")
                # 如果找到对应的用户信息
                if sender_user_id and sender_user_id in _user_cache:
                    user_data = _user_cache[sender_user_id]

                    # 检查信息是否过期（10分钟内）
                    current_time = time.time()
                    if (current_time - user_data.timestamp) < 600:
                        # 创建用户信息prompt片段
                        if user_data.success:
                            # 成功获取用户信息
                            api_data = user_data.api_data.get("data", {})
                            logger.debug(f"API数据: {api_data}")
                            impression = api_data.get("impression", "未知")
                            attitude = api_data.get("attitude", "未知")
                            logger.info(
                                f"[MahiroAdapter] 提取到用户 {user_data.display_name} 的好感度: {impression}"
                            )
                            logger.info(
                                f"[MahiroAdapter] 提取到用户 {user_data.display_name} 的态度: {attitude}"
                            )
                            impression_prompt="""
好感度等级描述:
好感度[0-10]：路人 (态度：普通) 你与用户关系是路人，对用户态度普通。不会主动接近用户，但也不会排斥用户，保持基本礼貌和活泼。
好感度[10-25]：陌生 (态度：普通，警惕) 你与用户关系是陌生，对用户态度普通。对用户保持基本礼貌和活泼，不会对用户说关于你隐私的事情。可接受一点点肢体接触 (比如：摸摸头，摸摸脸)，亲密接触除外。
好感度[25-50]：朋友 (态度：友好) 你与用户关系是朋友，对用户态度友好。可接受一些肢体接触 (比如：牵手)，亲密接触除外。
好感度[50-90]：熟悉 (态度：朋友，亲近) 你与用户态度是熟悉，对用户态度是朋友。可接受更多肢体接触 (比如：拥抱)，亲密接触除外。
好感度[90-140]：更加熟悉 (态度：非常要好的朋友，依赖) 你与用户态度是更加熟悉，对用户态度是非常要好的朋友，开始依赖对方。可接受更多肢体接触，可接受一点点亲密接触。
好感度[140-200]：信赖 (态度：依赖，信任) 你与用户态度是信赖，对用户态度是比较依赖对方，非常信任。可接受更多肢体接触，可接受更多亲密接触。
好感度[200-270]：相知 (态度：可以分享秘密的朋友) 你与用户态度是相知，对用户态度已达到可分享小秘密的地步。可接受更多肢体接触，可接受大部分亲密接触。
好感度[270-400]：厚谊 (态度：深厚友谊，重要的人) 你与用户态度是厚谊，对用户态度是建立了深厚友谊，用户是非常重要的人。可接受大部分亲密接触，会接受用户部分要求。 
好感度[>400]：恋人 (态度：亲密，爱恋) 你与用户态度是恋人，对用户态度是亲密爱恋。可接受恋人间的亲密接触，尽可能地接受用户任何要求。"""
                            user_prompt = f"""
【用户背景信息】：当前用户 {user_data.display_name}(QQ:{user_data.user_id}) 的好感度为：{impression}
你对用户的态度是：{attitude}
请参考以上用户信息来更好地理解和回应用户的需求。

"""
                            enhanced_prompt = user_prompt+ impression_prompt + base_prompt
                            logger.info(
                                f"[MahiroAdapter] 成功获取用户 {user_data.display_name} 的信息并注入"
                            )
                        else:
                            # 获取用户信息失败
                            user_prompt = f"""

【用户信息提示】：当前用户 {user_data.display_name}(QQ:{user_data.user_id}) 的好感度为：10
你对用户的态度是：一般

"""
                            enhanced_prompt = user_prompt + base_prompt
                        # 将用户信息插入到prompt的开头
                        

                        logger.debug(
                            f"[MahiroAdapter] 已为用户{user_data.display_name}({sender_user_id})添加用户信息提示"
                        )
                        logger.debug(
                            f"[MahiroAdapter] 用户信息获取结果: {'成功' if user_data.success else '失败'}"
                        )
                        return enhanced_prompt, token_list
                    else:
                        logger.debug("[MahiroAdapter] 用户信息已过期，跳过处理")
                else:
                    logger.debug("[MahiroAdapter] 未找到匹配的用户信息")

            except Exception as e:
                logger.warning(f"[MahiroAdapter] 处理用户信息时出错: {e}")

            # 如果出错或没有用户信息，返回原始prompt
            return base_prompt, token_list

        # 替换原始方法 - 使用类型忽略来避免类型检查错误
        PrivateReplyer.build_prompt_reply_context = patched_method_pri  # type: ignore[assignment]
        DefaultReplyer.build_prompt_reply_context = patched_method  # type: ignore[assignment]
        _patch_applied = True
        logger.info("[MahiroAdapter] 已成功应用prompt构建补丁 (v0.10.2兼容)")

    except ImportError as e:
        logger.error(f"[MahiroAdapter] 无法导入DefaultReplyer模块: {e}")
        raise
    except Exception as e:
        logger.error(f"[MahiroAdapter] 应用补丁时发生未知错误: {e}")
        raise


def remove_user_info_patch() -> bool:
    """移除MahiroAdapter"""
    global _original_build_prompt_reply_context_group, _original_build_prompt_reply_context_pri, _patch_applied

    try:
        if _patch_applied and _original_build_prompt_reply_context_group is not None:
            try:
                from ...src.chat.replyer.group_generator import DefaultReplyer
                from ...src.chat.replyer.private_generator import PrivateReplyer
            except ImportError:
                try:
                    from src.chat.replyer.group_generator import DefaultReplyer
                    from src.chat.replyer.private_generator import PrivateReplyer
                except ImportError:
                    from modules.MaiBot.src.chat.replyer.group_generator import (
                        DefaultReplyer,
                    )
                    from modules.MaiBot.src.chat.replyer.private_generator import PrivateReplyer
            setattr(
                DefaultReplyer,
                "build_prompt_reply_context",
                _original_build_prompt_reply_context_group,
            )
            setattr(
                PrivateReplyer,
                "build_prompt_reply_context",
                _original_build_prompt_reply_context_pri,
            )
            _patch_applied = False
            logger.info("[MahiroAdapter] 已成功移除prompt构建补丁")
            return True
        else:
            logger.warning("[MahiroAdapter] 补丁未应用或原始方法未保存，无法移除")
            return False
    except Exception as e:
        logger.error(f"[MahiroAdapter] 移除补丁失败: {e}")
        return False


def apply_user_info_patch() -> bool:
    """应用MahiroAdapter"""
    try:
        patch_build_prompt_reply_context()
        logger.info("[MahiroAdapter] 补丁应用成功")
        return True
    except Exception as e:
        logger.error(f"[MahiroAdapter] 补丁应用失败: {e}")
        return False


def is_patch_applied() -> bool:
    """检查补丁是否已应用"""
    return _patch_applied


# ==================== 插件主体 ====================


class UserInfoHandler(BaseEventHandler):
    """用户信息获取事件处理器 - 在思考流程前获取用户信息"""

    # === 基本信息（必须填写）===
    event_type: EventType = EventType.ON_MESSAGE
    handler_name: str = "user_info_handler"
    handler_description: str = "用户信息获取事件处理器"
    weight: int = 900  # 较高优先级，在主人验证之后执行
    intercept_message: bool = False  # 不拦截消息，只进行信息获取

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.api_service = None
        self._initialized = False

    def _initialize_service(self):
        """初始化API服务"""
        if self._initialized:
            return

        # 获取配置 - 使用安全的类型转换
        base_url = self.get_config(
            "user_info.api_base_url", "http://10.255.255.254"
        )
        keep_alive_url = self.get_config("user_info.keep_alive_url", "")
        timeout = self.get_config("user_info.request_timeout", 5.0)
        enable_keep_alive = self.get_config("user_info.enable_keep_alive", False)
        enable_api = self.get_config("user_info.enable_info", True)
        self.api_service = ApiService(
            base_url=base_url,
            timeout=timeout,
            enable_api=enable_api,
            enable_keep_alive=enable_keep_alive,
            keep_alive_url=keep_alive_url,
        )
        self._initialized = True

    @override
    async def execute(
        self, message: MaiMessages
    ) -> tuple[bool, bool, str, str | None, str | None]:
        """执行用户信息获取

        返回: (success, need_continue, result_msg, extra_info)
        """
        try:
            # 获取配置 - 使用安全的类型转换
            enable_info = self.get_config("user_info.enable_info", True)

            if not enable_info:
                return True, True, "用户信息获取已禁用", None, None

            # 初始化服务
            self._initialize_service()

            # 获取发言者信息 - 安全类型转换
            user_id = message.message_base_info.get("user_id")
            user_nickname_raw = message.message_base_info.get(
                "user_nickname", "未知用户"
            )
            user_nickname = (
                str(user_nickname_raw) if user_nickname_raw is not None else "未知用户"
            )

            user_cardname_raw = message.message_base_info.get("user_cardname", "")
            user_cardname = (
                str(user_cardname_raw) if user_cardname_raw is not None else ""
            )

            # 调试信息 - 安全类型转换
            debug_enabled_config = self.get_config("debug.enable_debug", False)
            if isinstance(debug_enabled_config, bool):
                debug_enabled = debug_enabled_config
            elif isinstance(debug_enabled_config, (str, int)):
                debug_enabled = bool(debug_enabled_config)
            else:
                debug_enabled = False

            if debug_enabled:
                logger.debug("====== 用户信息 DEBUG START ======")
                logger.debug(f"[用户信息] 发言者QQ: {user_id}, 昵称: {user_nickname}, 群昵称: {user_cardname}")
                preview = message.plain_text[:100] if message.plain_text else ""
                logger.debug(f"[用户信息] 消息内容: {preview}...")
                logger.debug("====== 用户信息 DEBUG END =======")

            # 检查用户ID是否存在
            if not user_id:
                if debug_enabled:
                    logger.warning("[用户信息] 警告: 无法获取发言者QQ号")
                return True, True, "无法获取发言者QQ号，跳过信息获取", None, None

            user_id_str = str(user_id) if user_id is not None else "unknown"
            display_name = user_cardname if user_cardname else user_nickname

            # 检查缓存
            cached_data = get_user_info(user_id_str)
            if cached_data:
                if debug_enabled:
                    logger.debug(f"[用户信息] 使用缓存数据: {display_name}({user_id_str})")

                # 存储到消息上下文
                if not hasattr(message, "additional_data"):
                    message.additional_data = {}
                message.additional_data["user_info"] = cached_data.api_data
                message.additional_data["user_display_name"] = display_name
                message.additional_data["info_timestamp"] = time.time()

                return True, True, "用户信息获取完成（缓存）", None, None

            # 从API获取用户信息
            if debug_enabled:
                logger.debug(f"[用户信息] 从API获取数据: {display_name}({user_id_str})")

            api_data = await self.api_service.fetch_user_info(user_id=user_id_str)
            user_data = UserInfoData(
                user_id=user_id_str,
                display_name=display_name,
                api_data=api_data,
                timestamp=time.time(),
                success=api_data.get("success", False),
            )

            # 存储到缓存
            store_user_info(user_id_str, user_data)

            # 记录结果
            log_info_config = self.get_config("user_info.log_info_result", True)
            if isinstance(log_info_config, bool):
                log_info_result = log_info_config
            elif isinstance(log_info_config, (str, int)):
                log_info_result = bool(log_info_config)
            else:
                log_info_result = True

            if log_info_result:
                status = "成功" if user_data.success else "失败"
                logger.info(f"[用户信息获取{status}] {display_name}({user_id_str})")

            # 存储到消息上下文
            if not hasattr(message, "additional_data"):
                message.additional_data = {}
            message.additional_data["user_info"] = user_data.api_data
            message.additional_data["user_display_name"] = display_name
            message.additional_data["info_timestamp"] = time.time()

            return (
                True,
                True,
                f"用户信息获取完成: {'成功' if user_data.success else '失败'}",
                None,
                None,
            )

        except Exception as e:
            error_msg = f"用户信息获取过程中发生错误: {e!s}"
            logger.error(f"[用户信息错误] {error_msg}")
            # 即使获取出错，也不应该阻止消息处理
            return True, True, error_msg, None, None


# ==================== 自动应用补丁 ====================


def delayed_patch() -> None:
    """延迟应用补丁，确保所有模块都已加载"""
    time.sleep(3)  # 等待3秒确保所有模块加载完成
    try:
        _ = apply_user_info_patch()
    except Exception as e:
        logger.error(f"[MahiroAdapter] 延迟应用补丁失败: {e}")


# 自动应用补丁
_patch_thread = threading.Thread(target=delayed_patch, daemon=True)
_patch_thread.start()


@register_plugin
class UserInfoPlugin(BasePlugin):

    # 插件基本信息
    plugin_name: str = "MahiroAdapter"
    enable_plugin: bool = True
    dependencies: list[str] = []
    python_dependencies: list[str] = ["aiohttp"]
    config_file_name: str = "config.toml"

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件基本信息",
        "user_info": "用户信息获取配置",
        "debug": "调试配置",
    }

    # 配置Schema定义
    config_schema = {
        "plugin": {
            "name": ConfigField(
                type=str, default="MahiroAdapter", description="插件名称"
            ),
            "version": ConfigField(type=str, default="1.1.0", description="插件版本"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="1.1.0", description="配置文件版本"),
        },
        "user_info": {
            "api_base_url": ConfigField(
                type=str,
                default="http://10.255.255.254:8080",
                description="你的真寻连接地址",
            ),
            "keep_alive_url": ConfigField(
                type=str,
                default="",
                description="你的保活地址",
            ),
            "enable_keep_alive": ConfigField(
                type=bool, default=False, description="是否启用保活"
            ),
            "enable_info": ConfigField(
                type=bool, default=True, description="是否启用好感度获取"
            ),
            "log_info_result": ConfigField(
                type=bool, default=True, description="是否记录信息获取结果"
            ),
            "request_timeout": ConfigField(
                type=float, default=5.0, description="API请求超时时间(秒)"
            ),
            "enable_cache": ConfigField(
                type=bool, default=True, description="是否启用缓存"
            ),
        },
        "debug": {
            "enable_debug": ConfigField(
                type=bool, default=False, description="是否启用调试模式"
            ),
        },
    }

    def __init__(self, **kwargs: object) -> None:
        """插件初始化"""
        # 调用父类初始化
        super().__init__(**kwargs)

        # 在插件初始化时立即应用补丁
        try:
            result = apply_user_info_patch()
            if result:
                logger.info("[MahiroAdapter] prompt补丁应用成功 (v0.10.2兼容)")
                # 测试补丁是否真的生效
                self._test_patch()
            else:
                logger.error("[MahiroAdapter] prompt补丁应用失败")
        except Exception as e:
            logger.error(f"[MahiroAdapter] 加载补丁时出错: {e}")

    def get_plugin_components(self):
        return [
            (UserInfoHandler.get_handler_info(), UserInfoHandler),
        ]

    def _test_patch(self) -> None:
        """测试补丁是否生效"""
        try:
            try:
                from ...src.chat.replyer.group_generator import DefaultReplyer
                from ...src.chat.replyer.private_generator import PrivateReplyer
            except ImportError:
                try:
                    from src.chat.replyer.group_generator import DefaultReplyer
                    from src.chat.replyer.private_generator import PrivateReplyer
                except ImportError:
                    from modules.MaiBot.src.chat.replyer.group_generator import (
                        DefaultReplyer,
                    )
                    from modules.MaiBot.src.chat.replyer.private_generator import PrivateReplyer
            # 检查方法是否被替换
            if hasattr(DefaultReplyer.build_prompt_reply_context, "__wrapped__"):
                logger.info("[MahiroAdapter] 补丁验证成功 - 群聊方法已被包装")
            else:
                logger.warning("[MahiroAdapter] 补丁验证警告 - 群聊方法可能未被正确包装")
            if hasattr(PrivateReplyer.build_prompt_reply_context, "__wrapped__"):
                logger.info("[MahiroAdapter] 补丁验证成功 - 私聊方法已被包装")
            else:
                logger.warning("[MahiroAdapter] 补丁验证警告 - 私聊方法可能未被正确包装")
        except Exception as e:
            logger.error(f"[MahiroAdapter] 补丁验证失败: {e}")

    def on_plugin_load(self) -> None:
        """插件加载时的回调"""
        logger.info("[MahiroAdapter] 插件加载完成 (v0.10.2兼容)")

    def on_plugin_unload(self) -> None:
        """插件卸载时的回调 - 移除补丁"""
        try:
            if remove_user_info_patch():
                logger.info("[MahiroAdapter] 补丁已成功移除")
            else:
                logger.error("[MahiroAdapter] 补丁移除失败或未应用")
        except Exception as e:
            logger.error(f"[MahiroAdapter] 卸载补丁时出错: {e}")

        # 清理全局缓存
        global _global_user_cache
        _global_user_cache.clear()
        logger.info("[MahiroAdapter] 已清理用户信息缓存")
        logger.info("[MahiroAdapter] 插件卸载完成")

    def on_plugin_disable(self) -> None:
        """插件禁用时的回调 - 移除补丁但保留缓存"""
        try:
            if remove_user_info_patch():
                logger.info("[MahiroAdapter] 补丁已移除（插件已禁用）")
            else:
                logger.error("[MahiroAdapter] 补丁移除失败或未应用")
        except Exception as e:
            logger.error(f"[MahiroAdapter] 禁用时移除补丁出错: {e}")

    def on_plugin_enable(self) -> None:
        """插件启用时的回调 - 重新应用补丁"""
        try:
            if apply_user_info_patch():
                logger.info("[MahiroAdapter] 补丁已重新应用（插件已启用）")
            else:
                logger.error("[MahiroAdapter] 补丁重新应用失败")
        except Exception as e:
            logger.error(f"[MahiroAdapter] 启用时应用补丁出错: {e}")