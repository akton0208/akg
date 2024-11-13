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
import time

# Configure the logger to show the user number first, followed by email, in the log messages
logger.remove()
logger.add(sys.stdout, format="{extra[user_number]:<3} | {extra[email]:<20} | {level} | {message}", level="INFO")

used_proxies = set()
unused_proxies = []
connected_proxies_count = {}
connected_users = set()
user_id_cache = {}  # Cache for storing user_id for each email

def parse_socks5_proxy(proxy_url):
    """Parse SOCKS5 proxy URL and extract the components"""
    proxy_url = proxy_url[len("socks5://"):]  # Remove socks5:// prefix

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

async def login_and_get_user_id(email, password, user_number):
    """Login using email and password to get user_id and access_token"""
    if email in user_id_cache:
        return user_id_cache[email], None, logger.bind(email=email, user_number=user_number)  # Add user_number to logger context

    url = 'https://api.getgrass.io/login'
    json_data = {
        'password': password,
        'username': email,
    }

    user_logger = logger.bind(email=email, user_number=user_number)  # Add user_number to logger context

    async with aiohttp.ClientSession() as session:
        user_logger.info(f"Sending login request for {email}")
        
        response = await session.post(url, json=json_data)
        res_json = await response.json()

        user_logger.info(f"Login response: {res_json}")

        if res_json.get("error"):
            raise Exception(f"Login failed: {res_json['error']['message']}")

        user_id = res_json["result"]["data"]["userId"]
        access_token = res_json["result"]["data"]["accessToken"]

        user_logger.info(f"Login successful for {email}: user_id={user_id}, access_token={access_token}")

        # Cache the user_id for future use
        user_id_cache[email] = user_id

        return user_id, access_token, user_logger

async def handle_websocket_connection(websocket, email, user_id, user_number, user_logger):
    async def send_ping():
        while True:
            try:
                send_message = json.dumps({"id": str(uuid.uuid4()), "version": "1.0.0", "action": "PING", "data": {}})
                user_logger.info(send_message)
                await websocket.send(send_message)
                await asyncio.sleep(5)
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

async def connect_to_wss(email, socks5_proxy, user_id, user_number):
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    
    user_logger = logger.bind(email=email, user_number=user_number)
    
    user_logger.info(f"Using proxy: {socks5_proxy}")
    
    if email not in connected_proxies_count:
        connected_proxies_count[email] = 0  # Ensure the count starts from 0 if not initialized
    
    connected_users.add(email)

    while True:
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

            used_proxies.add(socks5_proxy)
            unused_proxies.remove(socks5_proxy)

            async with proxy_connect(uri, proxy=proxy, ssl=ssl_context, server_hostname=server_hostname,
                                     extra_headers=custom_headers) as websocket:

                async def send_ping():
                    while True:
                        try:
                            send_message = json.dumps(
                                {"id": str(uuid.uuid4()), "version": "1.0.0", "action": "PING", "data": {}})
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

                message = await websocket.recv()
                message = json.loads(message)
                if message.get("action") == "AUTH":
                    auth_response = {
                        "id": message["id"],
                        "origin_action": "AUTH",
                        "result": {
                            "browser_id": user_number,
                            "user_id": user_id,
                            "user_agent": custom_headers['User-Agent'],
                            "timestamp": int(time.time()),
                            "device_type": "desktop",
                            "version": "4.28.2",
                        }
                    }
                    user_logger.info(auth_response)
                    await websocket.send(json.dumps(auth_response))

                    connected_proxies_count[email] += 1
                    user_logger.info(f"Correct connected proxies: {connected_proxies_count[email]}")

                while True:
                    response = await websocket.recv()
                    message = json.loads(response)
                    user_logger.info(message)

                    if message.get("action") == "PONG":
                        pong_response = {"id": message["id"], "origin_action": "PONG"}
                        user_logger.debug(pong_response)
                        await websocket.send(json.dumps(pong_response))

        except Exception as e:
            user_logger.error(f"Error with proxy {socks5_proxy}: {e}")
            user_logger.error("Switching to another proxy.")
            
            unused_proxies.append(socks5_proxy)

            # Ensure that the proxy count is correctly reduced when an error occurs
            if connected_proxies_count[email] > 0:
                connected_proxies_count[email] -= 1

            user_logger.info("AUTH Success")

            if unused_proxies:
                socks5_proxy = random.choice(unused_proxies)
                used_proxies.add(socks5_proxy)
                user_logger.info(f"New proxy selected: {socks5_proxy}")
            else:
                user_logger.error("No available proxies left.")

            if email in connected_users:
                connected_users.remove(email)

async def main():
    with open('id.txt', 'r') as file:
        emails = []
        passwords = []

        for line in file.readlines():
            parts = line.strip().split(":")
            if len(parts) == 2:
                emails.append(parts[0])
                passwords.append(parts[1])
            else:
                raise ValueError(f"Invalid line format in id.txt: {line.strip()}")

    with open('proxy.txt', 'r') as file:
        proxy_list = file.read().splitlines()

    if len(proxy_list) < len(emails):
        raise ValueError("Not enough proxies for the number of user IDs.")

    random.shuffle(proxy_list)

    unused_proxies.extend(proxy_list)

    tasks = []
    for user_number, (email, password, proxy) in enumerate(zip(emails, passwords, proxy_list), 1):
        user_id, access_token, user_logger = await login_and_get_user_id(email, password, user_number)
        tasks.append(asyncio.ensure_future(connect_to_wss(email, proxy, user_id=user_id, user_number=user_number)))

    await asyncio.gather(*tasks)


if __name__ == '__main__':
    asyncio.run(main())
