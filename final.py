import websockets
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

class ProxyManager:
    def __init__(self, proxies):
        self.used_proxies = set()  # To store allocated proxies
        self.unused_proxies = set(proxies)  # To store available proxies

    def get_proxy(self):
        """Randomly select a proxy from unused proxies"""
        if not self.unused_proxies:
            raise Exception("No available proxies.")
        proxy = random.choice(list(self.unused_proxies))
        self.unused_proxies.remove(proxy)
        self.used_proxies.add(proxy)
        return proxy

    def return_proxy(self, proxy):
        """Return the proxy to the unused proxy pool"""
        if proxy in self.used_proxies:
            self.used_proxies.remove(proxy)
            self.unused_proxies.add(proxy)

    def mark_proxy_as_used(self, proxy):
        """If the proxy connects successfully, add it to the used pool"""
        self.used_proxies.add(proxy)

# Remove default logger and add custom configuration
logger.remove()
logger.add(sys.stdout, format="{extra[email]:<20} | {level} | {message}", level="INFO")


user_id_cache = {}
active_connections = {}

async def parse_socks5_proxy(proxy_url):
    """Parse SOCKS5 proxy URL and extract the components."""
    proxy_url = proxy_url[len("socks5://"):]  # Remove the "socks5://" prefix
    user_info, host_and_port = proxy_url.split('@') if '@' in proxy_url else (None, proxy_url)
    proxy_host, proxy_port = host_and_port.split(':')
    proxy_port = int(proxy_port)

    if user_info:
        proxy_username, proxy_password = user_info.split(':')
    else:
        proxy_username = proxy_password = None

    return proxy_host, proxy_port, proxy_username, proxy_password

async def get_proxy_session(proxy):
    """Choose the appropriate proxy handling method based on protocol."""
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
    """Login using email and password to get user_id and access_token."""
    if email in user_id_cache:
        return user_id_cache[email], None, logger.bind(email=email)

    url = 'https://api.getgrass.io/login'
    json_data = {'password': password, 'username': email}
    user_logger = logger.bind(email=email)

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
        
        user_id_cache[email] = user_id
        return user_id, access_token, user_logger

async def send_ping(websocket, user_logger):
    """Send PING messages to WebSocket."""
    while True:
        try:
            send_message = json.dumps({"id": str(uuid.uuid4()), "version": "1.0.0", "action": "PING", "data": {}})
            user_logger.info(f"Sending PING: {send_message}")
            await websocket.send(send_message)
            await asyncio.sleep(random.randint(2, 3))  # Shortened interval to 2-3 seconds
        except websockets.exceptions.ConnectionClosedError as e:
            user_logger.error(f"WebSocket connection closed: {e}")
            break
        except Exception as e:
            user_logger.error(f"Unexpected error: {e}")
            break

async def listen_for_messages(websocket, user_logger):
    """Listen for incoming messages and handle them."""
    try:
        while True:
            message = await websocket.recv()
            message = json.loads(message)
            user_logger.info(f"Received message: {message}")
            
            if message.get("action") == "PONG":
                pong_response = {"id": message["id"], "origin_action": "PONG"}
                user_logger.debug(f"Sending PONG: {pong_response}")
                await websocket.send(json.dumps(pong_response))
    except websockets.exceptions.ConnectionClosed as e:
        user_logger.error(f"WebSocket closed: {e}")
    except Exception as e:
        user_logger.error(f"Error while receiving message: {e}")
    finally:
        if websocket.open:
            await websocket.close()
            user_logger.info("WebSocket connection closed properly.")

async def connect_to_wss(email, socks5_proxy, user_id, proxy_manager):
    """Connect to WebSocket using the provided proxy and email."""
    user_logger = logger.bind(email=email)
    user_logger.info(f"Opening WebSocket connection for {email}")
    
    while True:
        try:
            proxy = Proxy.from_url(socks5_proxy)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            urilist = ["wss://proxy.wynd.network:4444/", "wss://proxy.wynd.network:4650/"]
            uri = random.choice(urilist)
            server_hostname = "proxy.wynd.network"
            
            async with proxy_connect(uri, proxy=proxy, ssl=ssl_context, server_hostname=server_hostname) as websocket:
                message = await websocket.recv()
                message = json.loads(message)
                user_logger.info(f"Received initial message: {message}")

                if message.get("action") == "AUTH":
                    auth_response = {
                        "id": message["id"],
                        "origin_action": "AUTH",
                        "result": {
                            "browser_id": str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy)),
                            "user_id": user_id,
                            "user_agent": "Mozilla/5.0",
                            "timestamp": int(time.time()),
                            "device_type": "desktop",
                            "version": "4.28.2",
                        }
                    }
                    await websocket.send(json.dumps(auth_response))
                    user_logger.info(f"Authentication successful for {email}")
                    
                    # Ensure the connection is recorded
                    if email not in active_connections:
                        active_connections[email] = []  # Initialize list if email not in active_connections
                    active_connections[email].append(websocket)  # Add new websocket to the list
                    
                    user_logger.info(f"Connection added to active_connections for {email}")

                    ping_task = asyncio.create_task(send_ping(websocket, user_logger))
                    listen_task = asyncio.create_task(listen_for_messages(websocket, user_logger))
                    
                    await asyncio.gather(ping_task, listen_task)
                else:
                    user_logger.error("No AUTH message received.")
        except Exception as e:
            user_logger.error(f"Error with proxy {socks5_proxy}: {e}")
            proxy_manager.return_proxy(socks5_proxy)
            user_logger.error("Proxy returned to the pool.")
            
            socks5_proxy = proxy_manager.get_proxy()
            user_logger.info(f"Trying with new proxy: {socks5_proxy}")

async def display_connection_count():
    """Every 30 seconds, display the number of active WebSocket connections per email."""
    while True:
        await asyncio.sleep(30)
        
        for email, websockets in active_connections.items():
            user_logger = logger.bind(email=email)
            user_logger.info(f"{email} has {len(websockets)} active connections.")

async def main():
    with open('id.txt', 'r') as file:
        emails, passwords = zip(*[line.strip().split(":") for line in file.readlines()])

    with open('proxy.txt', 'r') as file:
        proxy_list = file.read().splitlines()

    if len(proxy_list) < len(emails):
        raise ValueError("Not enough proxies for the number of user IDs.")
    
    proxy_manager = ProxyManager(proxy_list)
    
    tasks = []
    for email, password in zip(emails, passwords):
        user_id, access_token, user_logger = await login_and_get_user_id(email, password)
        proxy = proxy_manager.get_proxy()  # Get a proxy from the manager
        tasks.append(connect_to_wss(email, proxy, user_id, proxy_manager))

    # Run tasks to connect to WebSocket
    tasks.append(display_connection_count())  # Add the display_connection_count task
    await asyncio.gather(*tasks)

# Run the asyncio loop
if __name__ == '__main__':
    asyncio.run(main())
