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

# Set up logger to output to both console and file grass.log
logger.add("grass.log", format="{time} {level} {message}", level="INFO", filter=lambda record: "UserID" in record["message"])

# Counter to track the number of proxies successfully running for each ID
running_proxies_count = {}

async def connect_to_wss(socks5_proxy, account_id, user_proxy_map, retry_delay=10, max_retries=20):  # Modify retry interval to 10 second and set max_retries to 20
    global running_proxies_count
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy))
    logger.info(device_id)
    retries = 0  # Add retry counter
    while retries < max_retries:  # Set maximum retries
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
                running_proxies_count[account_id] += 1  # Increment counter on successful connection
                connected = True
                retries = 0  # Reset retry counter on successful connection
                asyncio.create_task(send_ping(websocket))

                while True:
                    response = await websocket.recv()
                    message = json.loads(response)
                    logger.info(message)
                    await handle_message(message, websocket, device_id, account_id.split(':')[1], custom_headers['User-Agent'])
        except Exception as e:
            retries += 1  # Increment retry counter
            logger.error(f"Error: {e}. Retrying {retries}/{max_retries} times...")
            await asyncio.sleep(retry_delay)
        finally:
            if connected:
                running_proxies_count[account_id] -= 1  # Decrement counter on connection close
            logger.info(f"Connection closed: {socks5_proxy}, UserID: {account_id}, Current successful proxies: {running_proxies_count.get(account_id, 0)}")
    logger.error(f"Max retries reached for {socks5_proxy}, UserID: {account_id}. Giving up.")

def remove_proxy(socks5_proxy, account_id, user_proxy_map):
    try:
        with open('local_proxies.txt', 'r') as file:
            lines = file.readlines()
        updated_lines = [line for line in lines if line.strip() != socks5_proxy]
        with open('local_proxies.txt', 'w') as file:
            file.writelines(updated_lines)
        logger.info(f"Proxy '{socks5_proxy}' removed from file.")
        
        # Update user_proxy_map
        if account_id in user_proxy_map:
            user_proxy_map[account_id].remove(socks5_proxy)
        
        # Ensure counter is also updated
        if account_id in running_proxies_count and running_proxies_count[account_id] > 0:
            running_proxies_count[account_id] -= 1
    except Exception as e:
        logger.error(f"Failed to remove proxy: {e}")

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

async def display_proxy_info(user_proxy_map, interval=30):  # Modify interval to 30 seconds
    global running_proxies_count
    while True:
        for account_id, proxies in user_proxy_map.items():
            log_message = f"UserID: {account_id} Assigned proxies: {len(proxies)} Successfully running proxies: {running_proxies_count.get(account_id, 0)}"
            logger.info(log_message)  # Output to both console and file grass.log
        await asyncio.sleep(interval)

async def clear_log(interval=1800):  # Clear log every 30 minutes
    while True:
        with open('grass.log', 'w') as file:
            file.truncate(0)
        logger.info("Log file cleared")
        await asyncio.sleep(interval)

async def main():
    try:
        with open('user_id.txt', 'r') as file:
            accounts = file.read().splitlines()
    except Exception as e:
        logger.error(f"Failed to read user IDs: {e}")
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
    user_proxy_map = {account: [] for account in accounts}

    # Alternately assign proxies to IDs
    for index, proxy in enumerate(local_proxies):
        account = accounts[index % len(accounts)]
        user_proxy_map[account].append(proxy)

    # Add task to periodically output proxy info
    tasks.append(asyncio.ensure_future(display_proxy_info(user_proxy_map)))

    # Add task to periodically clear log file
    tasks.append(asyncio.ensure_future(clear_log()))

    for account, proxies in user_proxy_map.items():
        for proxy in proxies:
            tasks.append(asyncio.ensure_future(connect_to_wss(proxy, account, user_proxy_map)))

    await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())
