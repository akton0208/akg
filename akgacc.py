import asyncio
import random
import ssl
import json
import time
import uuid
import requests
import shutil
import aiohttp
from loguru import logger
from websockets_proxy import Proxy, proxy_connect
from fake_useragent import UserAgent
import websockets

# Set up logger to output to both console and file grass.log
logger.add("grass.log", format="{time} {level} {message}", level="INFO", filter=lambda record: "UserID" in record["message"])

# Counter to track the number of proxies successfully running for each ID
running_proxies_count = {}

async def login_and_get_user_id(username, password):
    """Login and retrieve userId and accessToken."""
    url = 'https://api.getgrass.io/login'
    json_data = {
        'password': password,
        'username': username,
    }

    async with aiohttp.ClientSession() as session:
        response = await session.post(url, json=json_data)
        res_json = await response.json()

        if res_json.get("error"):
            raise Exception(f"Login failed: {res_json['error']['message']}")
        
        user_id = res_json["result"]["data"]["userId"]
        access_token = res_json["result"]["data"]["accessToken"]
        return user_id, access_token


async def connect_to_wss(socks5_proxy, username, password, user_proxy_map, retry_delay=10, max_retries=20):
    global running_proxies_count
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy))
    logger.info(device_id)
    retries = 0  # Add retry counter
    
    # Log in and get userId and accessToken
    try:
        user_id, access_token = await login_and_get_user_id(username, password)  # Add your login function here
    except Exception as e:
        logger.error(f"Failed to login for {username}: {e}")
        return

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

            async with proxy_connect(uri, proxy=proxy, ssl=ssl_context, server_hostname=server_hostname,
                                     extra_headers=custom_headers) as websocket:
                if username not in running_proxies_count:
                    running_proxies_count[username] = 0
                running_proxies_count[username] += 1  # Increment counter on successful connection
                connected = True
                retries = 0  # Reset retry counter on successful connection
                asyncio.create_task(send_ping(websocket))

                while True:
                    response = await websocket.recv()
                    message = json.loads(response)
                    logger.info(message)
                    await handle_message(message, websocket, device_id, username, password, custom_headers['User-Agent'], user_id, access_token)

        except Exception as e:
            retries += 1  # Increment retry counter
            logger.error(f"Error: {e}. Retrying {retries}/{max_retries} times...")
            await asyncio.sleep(retry_delay)
        finally:
            if connected:
                running_proxies_count[username] -= 1  # Decrement counter on connection close
            logger.info(f"Connection closed: {socks5_proxy}, Username: {username}, Current successful proxies: {running_proxies_count.get(username, 0)}")
    
    # If max retries reached, remove the proxy
    logger.error(f"Max retries reached for {socks5_proxy}, Username: {username}. Removing proxy.")
    remove_proxy(socks5_proxy, username, user_proxy_map)

def remove_proxy(socks5_proxy, username, user_proxy_map):
    try:
        with open('local_proxies.txt', 'r') as file:
            lines = file.readlines()
        updated_lines = [line for line in lines if line.strip() != socks5_proxy]
        with open('local_proxies.txt', 'w') as file:
            file.writelines(updated_lines)
        logger.info(f"Proxy '{socks5_proxy}' removed from file.")
        
        # Update user_proxy_map
        if username in user_proxy_map:
            user_proxy_map[username].remove(socks5_proxy)
        
        # Ensure counter is also updated
        if username in running_proxies_count and running_proxies_count[username] > 0:
            running_proxies_count[username] -= 1
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

async def handle_message(message, websocket, device_id, username, password, user_agent, user_id, access_token):
    if message.get("action") == "AUTH":
        auth_response = {
            "id": message["id"],
            "origin_action": "AUTH",
            "result": {
                "browser_id": device_id,
                "username": username,  # Use username for authentication
                "password": password,  # Use password for authentication
                "user_agent": user_agent,
                "timestamp": int(time.time()),
                "device_type": "desktop",
                "version": "4.28.2",
                "user_id": user_id,  # Use userId for authentication
                "access_token": access_token,  # Use accessToken for authentication
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
        for username, proxies in user_proxy_map.items():
            log_message = f"Username: {username} Assigned proxies: {len(proxies)} Successfully running proxies: {running_proxies_count.get(username, 0)}"
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
        with open('user_credentials.txt', 'r') as file:
            accounts = [line.split(":") for line in file.read().splitlines()]  # Expect username:password format
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
    user_proxy_map = {account[0]: [] for account in accounts}  # Use username as key

    # Alternately assign proxies to IDs
    for index, proxy in enumerate(local_proxies):
        account = accounts[index % len(accounts)]
        username, password = account  # Extract username and password
        user_proxy_map[username].append(proxy)

    # Add task to periodically output proxy info
    tasks.append(asyncio.ensure_future(display_proxy_info(user_proxy_map)))

    # Add task to periodically clear log file
    tasks.append(asyncio.ensure_future(clear_log()))

    for account, proxies in user_proxy_map.items():
        username, password = account, next(p for u, p in accounts if u == account)
        for proxy in proxies:
            tasks.append(asyncio.ensure_future(connect_to_wss(proxy, username, password, user_proxy_map)))

    await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())
