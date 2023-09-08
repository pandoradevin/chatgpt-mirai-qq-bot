import json
import os
import time
from typing import Callable

import aiohttp
from graia.ariadne.message.chain import MessageChain
from graia.ariadne.message.element import Plain
from loguru import logger

from config import Config
from framework.middlewares.middleware import Middleware
from framework.request import Request, Response

config = Config.load_config()


class BaiduCloud:
    def __init__(self):
        self.access_token = None
        self.expiration_time = None
        self.token_file = "data/baidu_token_info.json"
        self.load_token_info()

    def save_token_info(self):
        try:
            with open(self.token_file, 'w') as f:
                json.dump({"access_token": self.access_token,
                          "expiration_time": self.expiration_time}, f)
        except IOError as e:
            logger.error(f"[百度云文本审核] 无法保存 access token 到指定位置: {e}")

    def load_token_info(self):
        if os.path.isfile(self.token_file):
            try:
                with open(self.token_file, 'r') as f:
                    token_info = json.load(f)
                    self.access_token = token_info.get("access_token")
                    self.expiration_time = token_info.get("expiration_time")
            except IOError as e:
                logger.error(f"[百度云文本审核] 无法从目标位置加载 access token: {e}")
            except json.JSONDecodeError as e:
                logger.error(f"[百度云文本审核] access token文件格式错误: {e}")

    async def get_access_token(self):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    "https://aip.baidubce.com/oauth/2.0/token",
                    params={
                        "grant_type": "client_credentials",
                        "client_id": config.baiducloud.baidu_api_key,
                        "client_secret": config.baiducloud.baidu_secret_key,
                    }
            ) as response:
                response.raise_for_status()
                result = await response.json()
                self.access_token = result.get("access_token")
                expires_in = result.get("expires_in")

                # 计算 access_token 过期时间
                self.expiration_time = time.time() + expires_in - 60  # 提前 60 秒更新

                self.save_token_info()

                return self.access_token

    async def check_and_update_access_token(self):
        if not self.access_token or time.time() > self.expiration_time:
            await self.get_access_token()

    async def get_conclusion(self, text: str):
        await self.check_and_update_access_token()

        baidu_url = f"https://aip.baidubce.com/rest/2.0/solution/v1/text_censor/v2/user_defined" \
                    f"?access_token={self.access_token}"
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'}

        async with aiohttp.ClientSession() as session:
            async with session.post(baidu_url, headers=headers, data={'text': text}) as response:
                response.raise_for_status()
                response_dict = await response.json()

        return response_dict


class MiddlewareBaiduCloud(Middleware):
    def __init__(self):
        self.baidu_cloud = BaiduCloud()

    async def handle_respond(self, request: Request, response: Response, _next: Callable):
        # 未审核消息路径
        if not config.baiducloud.check:
            return await _next(request, response)
        # 不处理没有文字的信息
        if not response.text:
            return await _next(request, response)

        try:
            if not self.baidu_cloud.access_token:
                logger.debug("[百度云文本审核] 正在获取access_token，请稍等")
                self.baidu_cloud.access_token = await self.baidu_cloud.get_access_token()

            response_dict = await self.baidu_cloud.get_conclusion(response.text or str(response.body))

            # 处理百度云审核结果
            conclusion = response_dict["conclusion"]
            if conclusion in "合规":
                logger.success(f"[百度云文本审核] 判定结果：{conclusion}")
            else:
                msg = response_dict['data'][0]['msg']
                logger.error(f"[百度云文本审核] 判定结果：{conclusion}")
                response.body = MessageChain(
                    [Plain(f"{config.baiducloud.prompt_message}\n原因：{msg}")])
                return await _next(request, response)

        except aiohttp.ClientError as e:
            logger.error(f"HTTP error occurred: {e}")

            await response.send(text="[百度云文本审核] 判定出错\n以下是原消息：")

        except json.JSONDecodeError as e:
            logger.error(f"[百度云文本审核] JSON解码错误: {e}")
        except StopIteration as e:
            logger.error(f"[百度云文本审核] StopIteration exception occurred: {e}")
        return await _next(request, response)
