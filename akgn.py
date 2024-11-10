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
import websockets

from better_proxy import Proxy  # 假设有一个 Proxy 类用来处理代理
from proxy_score import ProxyScoreManager  # 假设副脚本名称为 proxy_score.py

# Set up logger to output to both console and file grass.log
logger.add("grass.log", format="{time} {level} {message}", level="INFO", filter=lambda record: "UserID" in record["message"])

# Counter to track the number of proxies successfully running for each ID
running_proxies_count = {}

async def connect_to_wss(socks5_proxy, username, password, user_proxy_map, proxy_score_manager, retry_delay=10, max_retries=20):
    global running_proxies_count
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy))
    logger.info(device_id)
    retries = 0  
    while retries < max_retries:
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
            
            # 代理评分检查
            min_score = 80  # 假设设置了一个最低分数要求
            if not await proxy_score_manager.handle_proxy_score(socks5_proxy, min_score, username):
                logger.error(f"代理 {socks5_proxy} 不符合要求，跳过。")
                return  # 如果评分不合格则跳过连接

            # 如果代理评分合格，继续执行连接
            async with proxy_connect(uri, proxy=proxy, ssl=ssl_context, server_hostname=server_hostname,
                                     extra_headers=custom_headers) as websocket:
                if username not in running_proxies_count:
                    running_proxies_count[username] = 0
                running_proxies_count[username] += 1  
                connected = True
                retries = 0  
                asyncio.create_task(send_ping(websocket))

                while True:
                    response = await websocket.recv()
                    message = json.loads(response)
                    logger.info(message)
                    await handle_message(message, websocket, device_id, username, password, custom_headers['User-Agent'])
        except Exception as e:
            retries += 1  
            logger.error(f"Error: {e}. Retrying {retries}/{max_retries} times...")
            await asyncio.sleep(retry_delay)
        finally:
            if connected:
                running_proxies_count[username] -= 1  
            logger.info(f"Connection closed: {socks5_proxy}, Username: {username}, Current successful proxies: {running_proxies_count.get(username, 0)}")
    
    logger.error(f"Max retries reached for {socks5_proxy}, Username: {username}. Removing proxy.")
    remove_proxy(socks5_proxy, username, user_proxy_map)

async def send_ping(websocket):
    while True:
        try:
            send_message = json.dumps({"id": str(uuid.uuid4()), "version": "1.0.0", "action": "PING", "data": {}})
            logger.debug(send_message)
            await websocket.send(send_message)
            await asyncio.sleep(5)
        except websockets.exceptions.ConnectionClosedError as e:
            logger.error(f"Connection closed error: {e}")
            break

async def handle_message(message, websocket, device_id, username, password, user_agent):
    if message.get("action") == "AUTH":
        auth_response = {
            "id": message["id"],
            "origin_action": "AUTH",
            "result": {
                "browser_id": device_id,
                "username": username,  
                "password": password,  
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

async def display_proxy_info(user_proxy_map, interval=30): 
    global running_proxies_count
    while True:
        for username, proxies in user_proxy_map.items():
            log_message = f"Username: {username} Assigned proxies: {len(proxies)} Successfully running proxies: {running_proxies_count.get(username, 0)}"
            logger.info(log_message)
        await asyncio.sleep(interval)

async def clear_log(interval=1800):  
    while True:
        with open('grass.log', 'w') as file:
            file.truncate(0)
        logger.info("Log file cleared")
        await asyncio.sleep(interval)

async def main():
    # 读取账户信息和代理列表
    try:
        with open('user_credentials.txt', 'r') as file:
            accounts = [line.split(":") for line in file.read().splitlines()]  
    except Exception as e:
        logger.error(f"Failed to read user credentials: {e}")
        return

    try:
        with open('local_proxies.txt', 'r') as file:
            local_proxies = file.read().splitlines()
    except Exception as e:
        logger.error(f"Failed to read proxy list: {e}")
        return

    if len(accounts) * len(local_proxies) < 1:
        logger.error("Number of user IDs exceeds number of proxies, cannot assign.")
        return

    tasks = []
    user_proxy_map = {account[0]: [] for account in accounts}  

    for index, proxy in enumerate(local_proxies):
        account = accounts[index % len(accounts)]
        username, password = account  
        user_proxy_map[username].append(proxy)

    # 实例化 ProxyScoreManager
    db = None  # 假设这里传入您的数据库连接对象
    proxy_score_manager = ProxyScoreManager(db)

    # 启动显示代理信息和清理日志的任务
    tasks.append(asyncio.ensure_future(display_proxy_info(user_proxy_map)))
    tasks.append(asyncio.ensure_future(clear_log()))

    # 启动连接任务并传递 proxy_score_manager
    for account, proxies in user_proxy_map.items():
        username, password = account, next(p for u, p in accounts if u == account)
        for proxy in proxies:
            tasks.append(asyncio.ensure_future(connect_to_wss(proxy, username, password, user_proxy_map, proxy_score_manager)))

    await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())
