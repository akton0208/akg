import sys
import asyncio
import random
import ssl
import json
import uuid
import aiohttp
from loguru import logger
from websockets_proxy import Proxy, proxy_connect
import socks
import socket
from fake_useragent import UserAgent

logger.remove()
logger.add(sys.stdout, format="{extra[user_id]:<20} | {level} | {message}", level="INFO")

used_proxies = set()
unused_proxies = []
connected_proxies_count = {}
connected_users = set()

def parse_socks5_proxy(proxy_url):
    """Parse SOCKS5 proxy URL and extract the components"""
    proxy_url = proxy_url[len("socks5://"):]
    user_info, host_and_port = proxy_url.split('@') if '@' in proxy_url else (None, proxy_url)
    proxy_host, proxy_port = host_and_port.split(':')
    proxy_port = int(proxy_port)
    
    if user_info:
        proxy_username, proxy_password = user_info.split(':')
    else:
        proxy_username = proxy_password = None
    
    return proxy_host, proxy_port, proxy_username, proxy_password

async def get_proxy_session(proxy):
    """Choose the appropriate proxy handling method based on protocol"""
    if isinstance(proxy, str):
        if proxy.startswith("socks5://"):
            proxy_host, proxy_port, proxy_username, proxy_password = parse_socks5_proxy(proxy)
            socks.set_default_proxy(socks.SOCKS5, proxy_host, proxy_port, True, proxy_username, proxy_password)
            socket.socket = socks.socksocket
            return None
        elif proxy.startswith("http://") or proxy.startswith("https://"):
            connector = aiohttp.TCPConnector(ssl=False)
            return aiohttp.ClientSession(connector=connector)
        else:
            raise ValueError(f"Unsupported proxy type: {proxy}")
    elif isinstance(proxy, Proxy):
        return proxy
    else:
        raise ValueError(f"Invalid proxy object type: {type(proxy)}")

async def login_and_get_user_id(email, password):
    """Login using email and password to get user_id"""
    url = 'https://api.getgrass.io/login'
    json_data = {
        'password': password,
        'username': email,
    }

    async with aiohttp.ClientSession() as session:
        response = await session.post(url, json=json_data)
        res_json = await response.json()

        if res_json.get("error"):
            raise Exception(f"Login failed: {res_json['error']['message']}")
        
        user_id = res_json["result"]["data"]["userId"]
        access_token = res_json["result"]["data"]["accessToken"]
        return user_id, access_token

async def handle_websocket_connection(websocket, custom_id, user_id, device_id, user_logger):
    async def send_ping():
        while True:
            try:
                send_message = json.dumps({"id": str(uuid.uuid4()), "version": "1.0.0", "action": "PING", "data": {}})
                user_logger.info(send_message)
                await websocket.send(send_message)
                await asyncio.sleep(random.randint(119, 120))
            except websockets.exceptions.ConnectionClosedError as e:
                user_logger.error(f"WebSocket connection closed: {e}")
                break
            except Exception as e:
                user_logger.error(f"Unexpected error: {e}")
                break

    await asyncio.sleep(1)
    asyncio.create_task(send_ping())

    while True:
        response = await websocket.recv()
        message = json.loads(response)
        user_logger.info(message)

        if message.get("action") == "PONG":
            pong_response = {"id": message["id"], "origin_action": "PONG"}
            user_logger.debug(pong_response)
            await websocket.send(json.dumps(pong_response))

async def connect_to_wss(custom_id, proxy, email=None, password=None, user_id=None):
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, proxy))
    
    user_logger = logger.bind(user_id=custom_id)
    
    user_logger.info(f"Using proxy: {proxy} with device ID: {device_id}")
    
    if custom_id not in connected_proxies_count:
        connected_proxies_count[custom_id] = 0
    
    connected_users.add(custom_id)

    if email and password:
        try:
            user_id, access_token = await login_and_get_user_id(email, password)
        except Exception as e:
            user_logger.error(f"Login failed for {email}: {e}")
            return
    
    while True:
        try:
            await asyncio.sleep(random.randint(1, 10) / 10)
            custom_headers = {
                "User-Agent": random_user_agent,
            }
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            uri = "wss://proxy.wynd.network:4444/"
            server_hostname = "proxy.wynd.network"

            proxy_session = await get_proxy_session(proxy)

            used_proxies.add(proxy)
            unused_proxies.remove(proxy)

            if proxy.startswith("socks5://"):
                proxy_obj = Proxy.from_url(proxy)
                async with proxy_connect(uri, proxy=proxy_obj, ssl=ssl_context, server_hostname=server_hostname,
                                         extra_headers=custom_headers) as websocket:
                    await handle_websocket_connection(websocket, custom_id, user_id, device_id, user_logger)

            elif proxy.startswith("http://") or proxy.startswith("https://"):
                proxy_obj = Proxy.from_url(proxy)
                async with proxy_connect(uri, proxy=proxy_obj, ssl=ssl_context, server_hostname=server_hostname,
                                         extra_headers=custom_headers) as websocket:
                    await handle_websocket_connection(websocket, custom_id, user_id, device_id, user_logger)

        except Exception as e:
            user_logger.error(f"Error with proxy {proxy}: {e}")
            user_logger.error("Switching to another proxy.")
            unused_proxies.append(proxy)

            if connected_proxies_count[custom_id] > 0:
                connected_proxies_count[custom_id] -= 1

            user_logger.info(f"Correct connected proxies: {connected_proxies_count[custom_id]}")

            if unused_proxies:
                proxy = random.choice(unused_proxies)
                used_proxies.add(proxy)
                user_logger.info(f"New proxy selected: {proxy}")
            else:
                user_logger.error("No available proxies left.")
            if custom_id in connected_users:
                connected_users.remove(custom_id)

async def main():
    with open('id.txt', 'r') as file:
        lines = file.readlines()
        custom_ids = []
        user_ids = []
        emails = []
        passwords = []

        for line in lines:
            parts = line.strip().split(":")
            custom_ids.append(parts[0])  # Always extract custom ID (XXXX)
            
            if len(parts) == 2:
                user_ids.append(parts[1])  # Only user_id (method 1)
                emails.append(None)  # No email in this case
                passwords.append(None)  # No password in this case
            elif len(parts) == 3:
                emails.append(parts[1])  # Email
                passwords.append(parts[2])  # Password
                user_ids.append(None)  # No user_id (method 2)
            else:
                raise ValueError(f"Invalid line format in id.txt: {line.strip()}")

    with open('proxy.txt', 'r') as file:
        proxy_list = file.read().splitlines()

    if len(proxy_list) < len(custom_ids):
        raise ValueError("Not enough proxies for the number of user IDs.")

    random.shuffle(proxy_list)

    unused_proxies.extend(proxy_list)

    tasks = []
    for custom_id, email, password, user_id, proxy in zip(custom_ids, emails, passwords, user_ids, proxy_list):
        tasks.append(asyncio.ensure_future(connect_to_wss(custom_id, proxy, email=email, password=password, user_id=user_id)))
    
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())
