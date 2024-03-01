import json 
import gzip
import time
import websockets 
import asyncio
from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaStorageError
import ssl
import codecs
import io
from utilis2 import AllStreamsByInstrumentS, books_snapshot
import aiohttp

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

# Bingx Websocket for Depth is useless, do API calls instead as it contains more as much as 1000 peaces

class btcproducer():

    def __init__ (self, host, data):
        self.host = host
        self.api = [x for x in data if x["type"] == "api" and x["obj"] != "depth" and x["exchange"] != x["bingx"]] + [x for x in data if x["type"] == "api" and x["obj"] == "depth" and x["exchange"] == x["bingx"]] 
        self.ws = [x for x in data if x["type"] == "websocket"]

    async def keep_alive(self, websocket, exchange, id=None, ping_interval=30):
        while True:
            try:
                if exchange == "binance":
                    await websocket.pong()
                    await asyncio.sleep(ping_interval)
                if exchange in ["mexc"]:
                    await websocket.send(json.dumps({"method": "PING"}))
                    await asyncio.sleep(ping_interval)
                if exchange in ["bitget"]:
                    await websocket.send("ping")
                    await asyncio.sleep(ping_interval)
                if exchange == "okx":
                    await asyncio.sleep(ping_interval - 10)
                    await websocket.send('ping')
                if exchange == "bybit":
                    await asyncio.sleep(ping_interval - 10)
                    await websocket.send(json.dumps({"op": "ping"}))  
                if exchange in ["coinbase", "htx"]:
                    await asyncio.sleep(ping_interval - 10)
                if exchange == "bingx":
                    await asyncio.sleep(ping_interval - 27)
                    await websocket.send("Pong")  
                if exchange in ["deribit"]:
                    await asyncio.sleep(ping_interval)
                    await websocket.send(json.dumps({"jsonrpc":"2.0", "id": id, "method": "/api/v2/public/test"}))
                if exchange == "kucoin":
                    await asyncio.sleep(ping_interval - 10)
                    await websocket.send(json.dumps({"type": "ping", "id":id}))   # generate random id
                if exchange == "gateio":
                    await asyncio.sleep(ping_interval - 25)
                    await websocket.send("ping")
            except websockets.exceptions.ConnectionClosed:
                print(f"Connection closed. Stopping keep-alive of {exchange}.")
                break
    
    async def receive_messages(self, websocket):
        try:
            async for message in websocket:
                print(message)
        except asyncio.exceptions.TimeoutError:
            print("WebSocket operation timed out")

    async def websocket_connection(self, connection_data):

        count = 1
        id = connection_data["id"]
        exchange = connection_data["exchange"]
        instrument = connection_data["instrument"]
        insType = connection_data["insType"]
        obj = connection_data["obj"]
        endpoint = connection_data["url"]
        msg = connection_data["msg"]
        
        if connection_data["id"] in ["deribit_hearbeat"]:
            id = connection_data.get("msg").get("id")


        async for websocket in websockets.connect(endpoint, ping_interval=30, timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
                    
            await websocket.send(json.dumps(msg))

            if connection_data["id"] in ["deribit_hearbeat"]:
                keep_alive_task = asyncio.create_task(self.keep_alive(websocket, exchange, id, 30))
            else:
                keep_alive_task = asyncio.create_task(self.keep_alive(websocket, exchange, 30))
            try:
                if obj != "heartbeat":
                    async for message in websocket:
                        try:
                            
                            message = await websocket.recv()
                            
                            # Decompressing and dealing with pings
                            if exchange in ["htx"]:
                                message =  json.loads(gzip.decompress(message).decode('utf-8'))
                            if exchange == "bingx":
                                compressed_data = gzip.GzipFile(fileobj=io.BytesIO(message), mode='rb')
                                decompressed_data = compressed_data.read()
                                utf8_data = decompressed_data.decode('utf-8')
                                try:
                                    message = json.loads(utf8_data)
                                except:
                                    message = utf8_data
                            else:
                                message = json.loads(message)
                            if exchange == "deribit":
                                try:
                                    if message.get("error", None).get("message") == 'Method not found':
                                        await websocket.send(json.dumps({"jsonrpc":"2.0", "id":  message.get("id", None), "method": "/api/v2/public/test"}))
                                except:
                                    pass
                            if exchange == "htx":
                                if message.get("ping"):
                                    await websocket.send(json.dumps({"pong" : message.get("ping")}))
                            if exchange == "bingx":
                                if isinstance(message, dict):
                                    if message.get("ping") and insType == "spot":
                                        await websocket.send(json.dumps({"pong" : message.get("ping"), "time" : message.get("time")}))
                                if insType == "perpetual" and utf8_data == "Ping":
                                    await websocket.send("Pong")

                            # Writing down into a json file
                                    
                            if exchange == "binance" and obj == "trades" and insType == "spot" and instrument == "btcusdt":
                                response = await websocket.recv()
                                response = json.loads(response)
                                self.btc_price = float(response['p'])

                            # Some websockets doesn't return the whole book data after the first pull. You need to fetch it via api
                            if count == 1 and exchange in ["binance", "bybit", "coinbase"] and obj in ["depth"]:
                                data = books_snapshot(id snaplength=1000)
                                data = data["response"]
                                count += 1   
                            else:
                                data = await websocket.recv()
                                try:
                                    data = json.loads(data)
                                except:
                                    data = {}
                            try:
                                with open(f"data/{exchange}_{instrument}_{insType}_{obj}.json", 'r') as json_file:
                                    d = json.load(json_file)
                            except (FileNotFoundError, json.JSONDecodeError):
                                d = []

                            new_data = { 
                                    "exchange" : exchange,
                                    "instrument" : instrument,
                                    "insType" : insType,
                                    "obj" : obj,
                                    "btc_price" : self.btc_price,
                                    "timestamp" : time.time(),  
                                    "data" : data 
                                   }
                            d.append(new_data)

                            with open(f"data/{exchange}_{instrument}_{insType}_{obj}.json", 'w') as file:
                                json.dump(d, file, indent=2)
                            
                        except KafkaStorageError as e:
                            print(f"KafkaStorageError: {e}")
                            await asyncio.sleep(5)
                            continue
            except asyncio.exceptions.TimeoutError:
                print("WebSocket operation timed out")
                await asyncio.sleep(5)
                continue
            except websockets.exceptions.ConnectionClosed:
                print(f"connection  closed of {exchange}, {instrument}, {insType}, {obj}. Reconnecting!")
                if exchange == "bingx":
                    await asyncio.sleep(1)
                else:
                    await asyncio.sleep(5)
                continue

    async def websockets_fetcher(self, info):
        """
            Json rpc api need to be called via websockets
        """

        exchange = info["exchange"]
        instrument = info["instrument"]
        insType = info["insType"]
        obj = info["obj"]
        
        while True:
            async with websockets.connect(info["url"],  ssl=ssl_context) as websocket:
                await websocket.send(json.dumps(info["msg"]))
                
                data = await websocket.recv()
                
                try:
                    with open(f'data/{info["exchange"]}_{info["instrument"]}_{info["insType"]}_{info["obj"]}.json', 'r') as json_file:
                        d = json.load(json_file)
                except (FileNotFoundError, json.JSONDecodeError):
                    d = []

                new_data = { 
                        "exchange" : exchange,
                        "instrument" : instrument,
                        "insType" : insType,
                        "obj" : obj,
                        "btc_price" : self.btc_price,
                        "timestamp" : time.time(),  
                        "data" : json.loads(data) 
                        }

                d.append(new_data)

                with open(f'data/{info["exchange"]}_{info["instrument"]}_{info["insType"]}_{info["obj"]}.json', 'w') as file:
                    json.dump(d, file, indent=2)         
                
                await asyncio.sleep(info["updateSpeed"])


    async def aiohttp_fetcher(self, info):

        exchange = info["exchange"]
        instrument = info["instrument"]
        insType = info["insType"]
        obj = info["obj"]

        while True:
            async with aiohttp.ClientSession() as session:
                async with session.get(info["url"]) as response:

                    data =  await response.text()
                    
                    try:
                        with open(f'data/{info["exchange"]}_{info["instrument"]}_{info["insType"]}_{info["obj"]}.json', 'r') as json_file:
                            d = json.load(json_file)
                    except (FileNotFoundError, json.JSONDecodeError):
                        d = []

                    new_data = { 
                            "exchange" : exchange,
                            "instrument" : instrument,
                            "insType" : insType,
                            "obj" : obj,
                            "btc_price" : self.btc_price,
                            "timestamp" : time.time(),  
                            "data" : json.loads(data) 
                            }
                    
                    d.append(new_data)

                    with open(f'data/{info["exchange"]}_{info["instrument"]}_{info["insType"]}_{info["obj"]}.json', 'w') as file:
                        json.dump(d, file, indent=2)

                    await asyncio.sleep(info["updateSpeed"])


    async def main(self):
        """
            Make sure to call btcusdt trades in the first place
        """
        #producer = AIOKafkaProducer(bootstrap_servers=self.host)
        #await producer.start()
        producer = ''
        topic = ''

        tasks = []
        tasks +=  [ 
                self.websocket_connection(
                        connection_data=self.ws[x],
                        producer=producer, 
                        topic=topic) 
                        for x in range(0, len(self.ws)-1) 
                  ]

        for info in self.api:
            if info["exchange"] != "deribit":
                tasks.append(asyncio.ensure_future(self.aiohttp_fetcher(info)))
            if info["exchange"] == "deribit":
                tasks.append(asyncio.ensure_future(self.websockets_fetcher(info)))

        await asyncio.gather(*tasks) 

streams = [
    ["gateio", "perpetual", "btcusdt"],
    ["htx", "perpetual", "btcusdt"],
    ["bingx", "perpetual", "btcusdt"],
    ["bitget", "perpetual", "btcusdt"],
    ["bitget", "spot", "btcusdt"],
    ["mexc", "spot", "btcusdt"],
    ["gateio", "spot", "btcusdt"],
    ["bitget", "spot", "btcusdt"],
    ["htx", "spot", "btcusdt"],
    ["mexc", "perpetual", "btcusdt"],
    ["kucoin", "perpetual", "btcusdt"],
    ["kucoin", "spot", "btcusdt"],
    ["htx", "spot", "btcusdt"],
    ["bingx", "spot", "btcusdt"],
    ["bybit", "spot", "btcusdc"],
    ["deribit", "perpetual", "btcusd"],
]

data = AllStreamsByInstrumentS(streams)

if __name__ == '__main__':
    client = btcproducer('localhost:9092', data)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(client.main())

