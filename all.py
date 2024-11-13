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

logger.remove()
logger.add(sys.stdout, format="{extra[user_id]:<20} | {level} | {message}", level="INFO")

used_proxies = set()
unused_proxies = []
connected_proxies_count = {}
connected_users = set()

def parse_socks5_proxy(proxy_url):
    """Parse SOCKS5 proxy URL and extract the components"""
    # Remove the socks5:// protocol prefix
    proxy_url = proxy_url[len("socks5://"):]

    # Split the URL into user info and host:port
    user_info, host_and_port = proxy_url.split('@') if '@' in proxy_url else (None, proxy_url)
    proxy_host, proxy_port = host_and_port.split(':')
    proxy_port = int(proxy_port)
    
    # Extract username and password if available
    if user_info:
        proxy_username, proxy_password = user_info.split(':')
    else:
        proxy_username = proxy_password = None
    
    return proxy_host, proxy_port, proxy_username, proxy_password

async def get_proxy_session(proxy):
    """Choose the appropriate proxy handling method based on protocol"""
    if isinstance(proxy, str):
        if proxy.startswith("socks5://"):
            # SOCKS5 proxy
            proxy_host, proxy_port, proxy_username, proxy_password = parse_socks5_proxy(proxy)
            socks.set_default_proxy(socks.SOCKS5, proxy_host, proxy_port, True, proxy_username, proxy_password)
            socket.socket = socks.socksocket
            return None  # No need to return a session, just modify socket configuration
        elif proxy.startswith("http://") or proxy.startswith("https://"):
            # HTTP or HTTPS proxy
            connector = aiohttp.TCPConnector(ssl=False)
            return aiohttp.ClientSession(connector=connector)
        else:
            raise ValueError(f"Unsupported proxy type: {proxy}")
    elif isinstance(proxy, Proxy):  # If proxy is an AsyncioProxy object
        return proxy  # Handle it as it is
    else:
        raise ValueError(f"Invalid proxy object type: {type(proxy)}")

async def handle_websocket_connection(websocket, custom_id, user_id, device_id, user_logger):
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

    while True:
        response = await websocket.recv()
        message = json.loads(response)
        user_logger.info(message)

        if message.get("action") == "PONG":
            pong_response = {"id": message["id"], "origin_action": "PONG"}
            user_logger.debug(pong_response)
            await websocket.send(json.dumps(pong_response))

async def connect_to_wss(custom_id, user_id, proxy):
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, proxy))
    
    user_logger = logger.bind(user_id=custom_id)
    
    user_logger.info(f"Using proxy: {proxy} with device ID: {device_id}")
    
    if custom_id not in connected_proxies_count:
        connected_proxies_count[custom_id] = 0
    
    connected_users.add(custom_id)

    while True:
        try:
            await asyncio.sleep(random.randint(1, 10) / 10)
            custom_headers = {
                "User-Agent": random_user_agent,
            }
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            uri = "wss://proxy.wynd.network:4444/"  # Single endpoint now
            server_hostname = "proxy.wynd.network"

            proxy_session = await get_proxy_session(proxy)

            used_proxies.add(proxy)
            unused_proxies.remove(proxy)

            # Check if it's a SOCKS5 proxy and use it accordingly
            if proxy.startswith("socks5://"):
                proxy_obj = Proxy.from_url(proxy)
                async with proxy_connect(uri, proxy=proxy_obj, ssl=ssl_context, server_hostname=server_hostname,
                                         extra_headers=custom_headers) as websocket:
                    await handle_websocket_connection(websocket, custom_id, user_id, device_id, user_logger)

            elif proxy.startswith("http://") or proxy.startswith("https://"):
                # Use websockets proxy connect for HTTP/HTTPS
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
        custom_ids = [line.strip().split(":")[0] for line in lines]
        user_ids = [line.strip().split(":")[1] for line in lines]

    with open('proxy.txt', 'r') as file:
        proxy_list = file.read().splitlines()

    if len(proxy_list) < len(user_ids):
        raise ValueError("Not enough proxies for the number of user IDs.")

    random.shuffle(proxy_list)

    unused_proxies.extend(proxy_list)

    tasks = []
    for custom_id, user_id, proxy in zip(custom_ids, user_ids, proxy_list):
        tasks.append(asyncio.ensure_future(connect_to_wss(custom_id, user_id, proxy)))
    
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())
