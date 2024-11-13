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

logger.remove()
logger.add(sys.stdout, format="{extra[user_id]:<20} | {level} | {message}", level="INFO")

used_proxies = set()
unused_proxies = []
connected_proxies_count = {}
connected_users = set()

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

async def login_and_get_user_id(email, password):
    """Login using email and password to get user_id and access_token"""
    url = 'https://api.getgrass.io/login'
    json_data = {
        'password': password,
        'username': email,
    }

    user_logger = logger.bind(user_id=email)

    async with aiohttp.ClientSession() as session:
        user_logger.info(f"Sending login request for {email}")
        
        response = await session.post(url, json=json_data)
        res_json = await response.json()

        user_logger.info(f"Login response: {res_json}")

        if res_json.get("error"):
            raise Exception(f"Login failed: {res_json['error']['message']}")

        user_id = res_json["result"]["data"]["userId"]
        access_token = res_json["result"]["data"]["accessToken"]

        # Bind user_id to the logger for subsequent actions
        user_logger = logger.bind(user_id=user_id)
        user_logger.info(f"Login successful for {email}: user_id={user_id}, access_token={access_token}")

        return user_id, access_token, user_logger

async def handle_websocket_connection(websocket, custom_id, user_id, device_id, user_logger):
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

async def connect_to_wss(custom_id, socks5_proxy, user_id):
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy))
    
    # Bind custom_id to the logger context using extra
    user_logger = logger.bind(user_id=user_id)  # Now using user_id here for logging
    
    user_logger.info(f"Using proxy: {socks5_proxy} with device ID: {device_id}")
    
    # Initialize the count for this user if not already present
    if custom_id not in connected_proxies_count:
        connected_proxies_count[custom_id] = 0
    
    # Add this custom_id to the connected users to avoid multiple connections
    connected_users.add(custom_id)

    while True:  # Outer loop to handle reconnect attempts
        try:
            # Wait a random time before attempting the connection
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

            # Mark the chosen proxy as used
            used_proxies.add(socks5_proxy)
            unused_proxies.remove(socks5_proxy)

            # Try to establish the WebSocket connection
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
                            break  # Break the loop and reconnect
                        except Exception as e:
                            user_logger.error(f"Unexpected error: {e}")
                            break  # Break the loop and reconnect

                await asyncio.sleep(1)
                asyncio.create_task(send_ping())

                # First connect - when we receive AUTH, we consider the connection successful
                message = await websocket.recv()
                message = json.loads(message)
                if message.get("action") == "AUTH":
                    auth_response = {
                        "id": message["id"],
                        "origin_action": "AUTH",
                        "result": {
                            "browser_id": device_id,
                            "user_id": user_id,  # Using user_id here
                            "user_agent": custom_headers['User-Agent'],
                            "timestamp": int(time.time()),
                            "device_type": "desktop",
                            "version": "4.28.2",
                        }
                    }
                    user_logger.info(auth_response)
                    await websocket.send(json.dumps(auth_response))

                    # At this point, we count this as a successful connection
                    connected_proxies_count[custom_id] += 1
                    user_logger.info(f"Correct connected proxies: {connected_proxies_count[custom_id]}")

                while True:
                    response = await websocket.recv()
                    message = json.loads(response)
                    user_logger.info(message)

                    # If we get PONG, it's a valid response to keep the connection alive
                    if message.get("action") == "PONG":
                        pong_response = {"id": message["id"], "origin_action": "PONG"}
                        user_logger.debug(pong_response)
                        await websocket.send(json.dumps(pong_response))

        except Exception as e:
            user_logger.error(f"Error with proxy {socks5_proxy}: {e}")
            user_logger.error("Switching to another proxy.")
            
            # If connection fails, add this proxy back to the unused proxies list
            unused_proxies.append(socks5_proxy)

            # Decrease the count of connected proxies for the user if the proxy was already counted
            if connected_proxies_count[custom_id] > 0:
                connected_proxies_count[custom_id] -= 1

            # Output the current count after proxy failure
            user_logger.info(f"Correct connected proxies: {connected_proxies_count[custom_id]}")

            # Choose a proxy from unused proxies
            if unused_proxies:
                socks5_proxy = random.choice(unused_proxies)
                used_proxies.add(socks5_proxy)  # Mark the newly selected proxy as used
                user_logger.info(f"New proxy selected: {socks5_proxy}")
            else:
                user_logger.error("No available proxies left.")

            # Remove user from connected_users only if it exists
            if custom_id in connected_users:
                connected_users.remove(custom_id)

async def main():
    with open('id.txt', 'r') as file:
        lines = file.readlines()
        custom_ids = []
        emails = []
        passwords = []

        for line in lines:
            parts = line.strip().split(":")
            custom_ids.append(parts[0])  # Always extract custom ID (XXXX)

            if len(parts) == 3:
                emails.append(parts[1])  # Email
                passwords.append(parts[2])  # Password
            else:
                raise ValueError(f"Invalid line format in id.txt: {line.strip()}")

    with open('proxy.txt', 'r') as file:
        proxy_list = file.read().splitlines()

    if len(proxy_list) < len(custom_ids):
        raise ValueError("Not enough proxies for the number of user IDs.")

    random.shuffle(proxy_list)

    unused_proxies.extend(proxy_list)

    tasks = []
    for custom_id, email, password, proxy in zip(custom_ids, emails, passwords, proxy_list):
        # Get user_id and access_token from login
        user_id, access_token, user_logger = await login_and_get_user_id(email, password)  # Fetch user_id and access_token
        
        # Pass the user_id to connect_to_wss
        tasks.append(asyncio.ensure_future(connect_to_wss(custom_id, proxy, user_id=user_id)))

    await asyncio.gather(*tasks)


if __name__ == '__main__':
    asyncio.run(main())
