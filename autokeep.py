import asyncio
import random
import ssl
import json
import time
import uuid
import requests
import shutil
from loguru import logger
from websockets_proxy import Proxy, proxy_connect
from fake_useragent import UserAgent
import websockets  # 添加這行

# 設置 logger 同時輸出到控制台和文件 grass.log
logger.add("grass.log", format="{time} {level} {message}", level="INFO", filter=lambda record: "用户ID" in record["message"])

# 計數器來跟踪每個ID成功運行的代理數量
running_proxies_count = {}

async def connect_to_wss(socks5_proxy, account_id, user_proxy_map, retry_delay=1):  # 修改重試間隔時間為1秒
    global running_proxies_count
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy))
    logger.info(device_id)
    retries = 0  # 添加重試次數計數器
    while True:  # 無限重試循環
        connected = False
        try:
            await asyncio.sleep(random.randint(1, 10) / 10)
            custom_headers = {
                "User-Agent": random_user_agent,
            }
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            urilist = ["wss://proxy.wynd.network:4444/", "wss://proxy.wynd.network:4650/"]
            uri = random.choice(urilist)
            server_hostname = "proxy.wynd.network"
            proxy = Proxy.from_url(socks5_proxy)
            async with proxy_connect(uri, proxy=proxy, ssl=ssl_context, server_hostname=server_hostname,
                                     extra_headers=custom_headers) as websocket:
                if account_id not in running_proxies_count:
                    running_proxies_count[account_id] = 0
                running_proxies_count[account_id] += 1  # 成功連接後增加計數器
                connected = True
                retries = 0  # 成功連接後重置重試次數計數器
                asyncio.create_task(send_ping(websocket))

                while True:
                    response = await websocket.recv()
                    message = json.loads(response)
                    logger.info(message)
                    await handle_message(message, websocket, device_id, account_id.split(':')[1], custom_headers['User-Agent'])
        except Exception as e:
            retries += 1  # 增加重試次數計數器
            logger.error(f"错误: {e}. 重试 {retries} 次...")
            await asyncio.sleep(retry_delay)
        finally:
            if connected:
                running_proxies_count[account_id] -= 1  # 連接關閉後減少計數器
            logger.info(f"连接关闭: {socks5_proxy}, 用户ID: {account_id}, 当前成功运行的代理数量: {running_proxies_count.get(account_id, 0)}")

def remove_proxy(socks5_proxy, account_id, user_proxy_map):
    try:
        with open('local_proxies.txt', 'r') as file:
            lines = file.readlines()
        updated_lines = [line for line in lines if line.strip() != socks5_proxy]
        with open('local_proxies.txt', 'w') as file:
            file.writelines(updated_lines)
        logger.info(f"代理 '{socks5_proxy}' 已从文件中移除。")
        
        # 更新 user_proxy_map
        if account_id in user_proxy_map:
            user_proxy_map[account_id].remove(socks5_proxy)
        
        # 確保計數器也被更新
        if account_id in running_proxies_count and running_proxies_count[account_id] > 0:
            running_proxies_count[account_id] -= 1
    except Exception as e:
        logger.error(f"移除代理失败: {e}")

async def send_ping(websocket):
    while True:
        try:
            send_message = json.dumps({"id": str(uuid.uuid4()), "version": "1.0.0", "action": "PING", "data": {}})
            logger.debug(send_message)
            await websocket.send(send_message)
            await asyncio.sleep(5)
        except websockets.exceptions.ConnectionClosedError as e:
            logger.error(f"连接关闭错误: {e}")
            break

async def handle_message(message, websocket, device_id, user_id, user_agent):
    if message.get("action") == "AUTH":
        auth_response = {
            "id": message["id"],
            "origin_action": "AUTH",
            "result": {
                "browser_id": device_id,
                "user_id": user_id,
                "user_agent": user_agent,
                "timestamp": int(time.time()),
                "device_type": "desktop",
                "version": "4.28.2",
            }
        }
        logger.debug(auth_response)
        await websocket.send(json.dumps(auth_response))
    elif message.get("action") == "PONG":
        pong_response = {"id": message["id"], "origin_action": "PONG"}
        logger.debug(pong_response)
        await websocket.send(json.dumps(pong_response))

async def display_proxy_info(user_proxy_map, interval=30):  # 修改間隔時間為30秒
    global running_proxies_count
    while True:
        for account_id, proxies in user_proxy_map.items():
            log_message = f"用户ID: {account_id} 分配到的代理数量: {len(proxies)} 成功运行的代理数量: {running_proxies_count.get(account_id, 0)}"
            logger.info(log_message)  # 同時輸出到控制台和文件 grass.log
        await asyncio.sleep(interval)

async def main():
    try:
        with open('user_id.txt', 'r') as file:
            accounts = file.read().splitlines()
    except Exception as e:
        logger.error(f"读取用户ID失败: {e}")
        return

    try:
        with open('local_proxies.txt', 'r') as file:
            local_proxies = file.read().splitlines()
    except Exception as e:
        logger.error(f"读取代理列表失败: {e}")
        return

    if len(accounts) * len(local_proxies) < 1:
        logger.error("用户ID数量多于代理数量，无法分配。")
        return

    tasks = []
    user_proxy_map = {account: [] for account in accounts}

    # 交替分配代理給ID
    for index, proxy in enumerate(local_proxies):
        account = accounts[index % len(accounts)]
        user_proxy_map[account].append(proxy)

    # 添加定時輸出代理信息的任務
    tasks.append(asyncio.ensure_future(display_proxy_info(user_proxy_map)))

    for account, proxies in user_proxy_map.items():
        for proxy in proxies:
            tasks.append(asyncio.ensure_future(connect_to_wss(proxy, account, user_proxy_map)))

    await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())
