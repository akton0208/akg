#for ID login using socks5 or http or https
import websockets
import sys
import asyncio
import random
import ssl
import json
import time
import uuid
from loguru import logger
from websockets_proxy import Proxy, proxy_connect
from fake_useragent import UserAgent

# Remove timestamp from log output and include user ID in the logs
logger.remove()
logger.add(sys.stdout, format="{extra[user_id]:<20} | {level} | {message}", level="INFO")

# This will track the proxies that have been used
used_proxies = set()
# This will track proxies that are available for future use
unused_proxies = []

# Dictionary to track connected proxies count per user
connected_proxies_count = {}

# Dictionary to track which user_ids are currently connected
connected_users = set()

async def connect_to_wss(custom_id, user_id, socks5_proxy):
    user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
    random_user_agent = user_agent.random
    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy))
    
    # Bind custom_id to the logger context using extra
    user_logger = logger.bind(user_id=custom_id)  # Create a new logger with the custom_id in extra context
    
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
                            "user_id": user_id,
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



# Periodically output the current status of connected proxies
async def output_connected_proxies():
    while True:
        await asyncio.sleep(30)
        # Open the log.txt file in write mode to clear the contents
        with open('log.txt', 'w') as log_file:
            # Write the header (optional)
            log_file.write(f"{'User ID':<20} | {'Correct connected proxies':<25}\n")
            log_file.write("="*50 + "\n")  # Divider line
            
            for custom_id, count in connected_proxies_count.items():
                # Bind custom_id to the logger context
                user_logger = logger.bind(user_id=custom_id)
                log_message = f"{custom_id:<20} | {count:<25}\n"  # Format with fixed widths
                user_logger.info(log_message)  # Log to console
                log_file.write(log_message)  # Write to log.txt

async def main():
    # Read user IDs and custom identifiers from 'id.txt' file
    with open('id.txt', 'r') as file:
        lines = file.readlines()
        custom_ids = [line.strip().split(":")[0] for line in lines]  # Custom identifiers (e.g. XXXX)
        user_ids = [line.strip().split(":")[1] for line in lines]  # Actual user IDs (e.g. 2oee0OtMo9XsSXJati8rBORh5fh)

    # Read proxies from 'proxy.txt' file
    with open('proxy.txt', 'r') as file:
        proxy_list = file.read().splitlines()

    # Ensure the number of proxies is at least equal to the number of user IDs
    if len(proxy_list) < len(user_ids):
        raise ValueError("Not enough proxies for the number of user IDs.")

    # Shuffle proxies to ensure random distribution
    random.shuffle(proxy_list)

    # Initialize unused_proxies with all proxies
    unused_proxies.extend(proxy_list)

    # Create tasks to connect each user ID with a unique proxy
    tasks = []
    for custom_id, user_id, socks5_proxy in zip(custom_ids, user_ids, proxy_list):
        tasks.append(asyncio.ensure_future(connect_to_wss(custom_id, user_id, socks5_proxy)))
    
    # Create a task to output the connected proxies status every 30 seconds
    tasks.append(asyncio.ensure_future(output_connected_proxies()))
    
    # Wait for all tasks to finish
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())

