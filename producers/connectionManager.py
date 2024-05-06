import asyncio
import time
import websockets
import ssl
import rapidjson as json
from .utilis import ws_fetcher_helper
import re
import gzip
import aiocouch
import io
import sys
import logging
from logging.handlers import RotatingFileHandler
import backoff
from aiokafka import AIOKafkaProducer
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka._model import TopicCollection
from prometheus_client import start_http_server, Counter, Gauge, Histogram

from kafka.errors import (
    KafkaError, KafkaConnectionError, KafkaTimeoutError, NoBrokersAvailable, AuthenticationFailedError,
    BrokerNotAvailableError, LeaderNotAvailableError, NotLeaderForPartitionError,
    UnknownTopicOrPartitionError, MessageSizeTooLargeError, RecordListTooLargeError,
    CorruptRecordException, NotEnoughReplicasError, NotEnoughReplicasAfterAppendError,
    TopicAuthorizationFailedError, ClusterAuthorizationFailedError, GroupAuthorizationFailedError,
    IllegalStateError
)

from websockets.exceptions import (
    ConnectionClosed, ConnectionClosedError, ConnectionClosedOK, ProtocolError, InvalidHandshake
    )

websockets_errors = (ConnectionClosed, ConnectionClosedError, ConnectionClosedOK, ProtocolError, InvalidHandshake)

recoverable_errors = (
    asyncio.TimeoutError,
    ConnectionError,
    NotLeaderForPartitionError,
    LeaderNotAvailableError,
    BrokerNotAvailableError,
    NotEnoughReplicasError,
    NotEnoughReplicasAfterAppendError
)

producer_restart_errors = (
    IllegalStateError,
    KafkaTimeoutError,
    KafkaConnectionError,
)

giveup_errors = (
    UnknownTopicOrPartitionError,
    MessageSizeTooLargeError,
    RecordListTooLargeError,
    GroupAuthorizationFailedError,
    ClusterAuthorizationFailedError,
    TopicAuthorizationFailedError,
    TopicAuthorizationFailedError,
    Exception,
    IllegalStateError,
    KafkaTimeoutError,
    KafkaConnectionError,
)

def should_give_up(exc):
    return isinstance(exc, giveup_errors)

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


# Connection duration
# Latency of every websocket
# Latency of a group of websockets (per exchange)
# Number of disconnects. When do they happe

class keepalive():
    """
        Handles pings pongs of websockets connections
        Intervals are in seconds
    """
    prefered_intervals = {
        "binance" : 30,   
        "bybit" : 20,       
        "okx" : 15,         
        "deribit" : None, # heartbeats mechanism
        "kucoin" : None,  # defined by kucoin server
        "bitget" : 30,
        "bingx" : 5,
        "mexc" : 15, 
        "gateio" : 15,
        "htx" : 5,
        "coinbase" : None # heartbeats mechanism
    }
    binance_keep_alive = False
    bybit_keep_alive = False
    okx_keep_alive = False
    bitget_keep_alive = False
    deribit_keep_alive = False
    bingx_keep_alive = False
    kucoin_keep_alive = False
    coinbase_keep_alive = False
    mexc_keep_alive = False
    htx_keep_alive = False
    gateio_keep_alive = False
    
    async def binance_keep_alive(self, websocket, logger, connection_data:dict=None, ping_interval:int=30):
        """
            https://binance-docs.github.io/apidocs/spot/en/#websocket-market-streams
            Websocket server will send a ping frame every 3 minutes.
            If the websocket server does not receive a pong frame back from the connection within a 10 minute period, the connection will be disconnected.
            When you receive a ping, you must send a pong with a copy of ping's payload as soon as possible.
            Unsolicited pong frames are allowed, but will not prevent disconnection. It is recommended that the payload for these pong frames are empty.
        """
        id_ws = connection_data.get("id_ws", "unknown")
        req_id = connection_data.get('req_id', None)
        self.binance_keep_alive = True
        
        while self.binance_keep_alive:
            try:
                await websocket.pong(b"") 
                await asyncio.sleep(ping_interval) 
            except websockets_errors as e:  
                self.logger.exception(f"Keep-Alive error for {id_ws}, {e}", exc_info=True)
                break
            except Exception as e:
                self.logger.exception(f"Unexpected error in keep-alive for {id_ws}, {e}", exc_info=True)
                break
            
    def binance_keep_alive_stop(self):
        self.binance_keep_alive = False

    async def bybit_keep_alive(self, websocket, connection_data=None, ping_interval:int=20):
        """
            https://bybit-exchange.github.io/docs/v5/ws/connect
            In general, if there is no "ping-pong" and no stream data sent from server end, the connection will be 
            cut off after 10 minutes. When you have a particular need, you can configure connection alive time by max_active_time.
            Since ticker scans every 30s, so it is not fully exact, i.e., if you configure 45s, and your last update or ping-pong is 
            occurred on 2023-08-15 17:27:23, your disconnection time maybe happened on 2023-08-15 17:28:15
            To avoid network or program issues, we recommend that you send the ping heartbeat packet every 20 seconds to maintain the WebSocket connection.

            How to seng: // req_id is a customised ID, which is optional ws.send(JSON.stringify({"req_id": "100001", "op": "ping"})
        """
        id_ws = connection_data.get("id_ws", "unknown")
        req_id = connection_data.get('req_id', None)
        self.bybit_keep_alive = True
        
        while self.bybit_keep_alive:
            try:
                await websocket.ping(json.dumps({"req_id": req_id, "op": "ping"})) 
                await asyncio.sleep(ping_interval) 
            except websockets_errors as e:  
                self.logger.exception(f"Keep-Alive error for {id_ws}, {e}", exc_info=True)
                break
            except Exception as e:
                self.logger.exception(f"Unexpected error in keep-alive for {id_ws}, {e}", exc_info=True)
                break
            
    def bybit_keep_alive_stop(self):
        self.bybit_keep_alive = False

    async def okx_keep_alive(self, websocket, connection_data=None, ping_interval:int=15):
        """
            https://www.okx.com/docs-v5/en/#overview-websocket-overview

        If there’s a network problem, the system will automatically disable the connection.
        The connection will break automatically if the subscription is not established or data has not been pushed for more than 30 seconds.
        To keep the connection stable:
        1. Set a timer of N seconds whenever a response message is received, where N is less than 30.
        2. If the timer is triggered, which means that no new message is received within N seconds, send the String 'ping'.
        3. Expect a 'pong' as a response. If the response message is not received within N seconds, please raise an error or reconnect. 
        """
        id_ws = connection_data.get("id_ws", "unknown")
        req_id = connection_data.get('req_id', None)
        self.okx_keep_alive = True
        
        while self.okx_keep_alive:
            try:
                await websocket.ping("ping") 
                await asyncio.sleep(ping_interval) 
            except websockets_errors as e:  
                self.logger.exception(f"Keep-Alive error for {id_ws}, {e}", exc_info=True)
                break
            except Exception as e:
                self.logger.exception(f"Unexpected error in keep-alive for {id_ws}, {e}", exc_info=True)
                break

    def okx_keep_alive_stop(self):
        self.okx_keep_alive = False

    async def deribit_keep_alive(self, websocket, conn_id, data=None):
        """
            https://docs.deribit.com/#public-set_heartbeat

            Deribit's heartbeat mechanism is different from a traditional "keep-alive" approach. It uses two types of messages:

                Heartbeats: Sent by the server at the specified interval (30 seconds in this example).
                Test Requests: Sent by the server periodically to verify that the client is responsive.

            Responding to both types of messages is crucial to maintain a healthy connection. Your application doesn't need to send periodic "keep-alive" messages itself.
        """
        pass

    async def bitget_keep_alive(self, websocket, connection_data=None, ping_interval:int=30):
        """
            https://www.bitget.com/api-doc/common/websocket-intro

        To keep the connection stable:

            Set a timer of 30 seconds.
            If the timer is triggered, send a String 'ping'.
            Expect a 'pong' as a response. If the response message is not received within 30 seconds, please raise an error and/or reconnect.
            The Websocket server accepts up to 10 messages per second. The message includes:

            PING frame (Not tcp ping)
            Messages in JSON format, such as subscribe, unsubscribe.

            If the user sends more messages than the limit, the connection will be disconnected. IPs that are repeatedly disconnected may be blocked by the server;
        """
        id_ws = connection_data.get("id_ws", "unknown")
        req_id = connection_data.get('req_id', None)
        self.bitget_keep_alive = True
        
        while self.bitget_keep_alive:
            try:
                await websocket.ping("ping") 
                await asyncio.sleep(ping_interval) 
            except websockets_errors as e:  
                self.logger.exception(f"Keep-Alive error for {id_ws}, {e}", exc_info=True)
                break
            except Exception as e:
                self.logger.exception(f"Unexpected error in keep-alive for {id_ws}, {e}", exc_info=True)
                break

    def bitget_keep_alive_stop(self):
        self.bitget_keep_alive = False

    async def bingx_keep_alive(self, websocket, connection_data=None, ping_interval:int=5):
        """
            https://bingx-api.github.io/docs/#/en-us/swapV2/socket/

            Once the Websocket Client and Websocket Server get connected, the server will send a heartbeat- Ping message every 5 seconds (the frequency might change).
            When the Websocket Client receives this heartbeat message, it should return Pong message.

            # SPOT
            Once the Websocket Client and Websocket Server get connected, the server will send a heartbeat- Ping message every 5 seconds (the frequency might change).
            {"ping":"2177c68e4d0e45679965f482929b59c2","time":"2022-06-07T16:27:36.323+0800"}

            When the Websocket Client receives this heartbeat message, it should return Pong message.
            {"pong":"2177c68e4d0e45679965f482929b59c2","time":"2022-06-07T16:27:36.323+0800"}

            # PERP
            send simply Pong
            
            Yeah, as you get keepalive is useless
        """
        
        id_ws = connection_data.get("id_ws", "unknown")
        req_id = connection_data.get('req_id', None)
        self.bingx_keep_alive = True
        
        while self.bingx_keep_alive:
            try:
                await websocket.send("Pong") 
                await asyncio.sleep(ping_interval) 
            except websockets_errors as e:  
                self.logger.exception(f"Keep-Alive error for {id_ws}, {e}", exc_info=True)
                break
            except Exception as e:
                self.logger.exception(f"Unexpected error in keep-alive for {id_ws}, {e}", exc_info=True)
                break

    def bingx_keep_alive_stop(self):
        self.bingx_keep_alive = False

    async def kucoin_keep_alive(self, websocket, connection_data=None, ping_interval=None):
        """
            https://www.kucoin.com/docs/websocket/basic-info/ping
            {
            "id": "1545910590801",
            "type": "ping"
            }
            To prevent the TCP link being disconnected by the server, the client side needs to send ping messages every pingInterval time to the server to keep alive the link.
            After the ping message is sent to the server, the system would return a pong message to the client side.
            If the server has not received any message from the client for a long time, the connection will be disconnected.
            {
            "id": "1545910590801",
            "type": "pong"
            }
        """
        connection_id = connection_data.get("connect_id")
        ping_interval = connection_data.get("ping_interval")
        id_ws = connection_data.get("id_ws", None)
        self.kucoin_keep_alive = True
        
        while self.kucoin_keep_alive:
            try:
                await websocket.send({
                                        "id": str(connection_id),
                                        "type": "ping"
                                        })
                await asyncio.sleep(ping_interval) 
            except websockets_errors as e:  
                self.logger.exception(f"Keep-Alive error for {id_ws}, {e}", exc_info=True)
                break
            except Exception as e:
                self.logger.exception(f"Unexpected error in keep-alive for {id_ws}, {e}", exc_info=True)
                break
            
    def kucoin_keep_alive_stop(self):
        self.kucoin_keep_alive = False
        
    async def mexc_keep_alive(self, websocket, connection_data=None, ping_interval:int=15):
        """
            https://mexcdevelop.github.io/apidocs/contract_v1_en/#switch-the-stop-limit-price-of-trigger-orders
            Detailed data interaction commands
            Send ping message
            {
            "method": "ping"
            }
            Server return
            {
            "channel": "pong",
            "data": 1587453241453
            }
            List of subscribe/unsubscribe data commands ( ws identification is not required except the list of personal related commands)
            If no ping is received within 1 minute, the connection will be disconnected. It is recommended to send a ping for 10-20 seconds
            The ping message and server return are shown on the right

            https://mexcdevelop.github.io/apidocs/spot_v3_en/#live-subscribing-unsubscribing-to-streams
            PING/PONG
            Request
            {"method":"PING"}
            PING/PONG Response
            {
            "id":0,
            "code":0,
            "msg":"PONG"
            }
        """
        ping_interval = connection_data.get("ping_interval")
        id_ws = connection_data.get("id_ws", None)
        self.mexc_keep_alive = True
        while self.mexc_keep_alive:
            try:
                if connection_data.get("instType") == "spot":
                    await websocket.send("PING") 
                    await asyncio.sleep(ping_interval) 
                else:
                    await websocket.send("ping") 
                    await asyncio.sleep(ping_interval) 
            except websockets_errors as e:  
                self.logger.exception(f"Keep-Alive error for {id_ws}, {e}", exc_info=True)
                break
            except Exception as e:
                self.logger.exception(f"Unexpected error in keep-alive for {id_ws}, {e}", exc_info=True)
                break

    def mexc_keep_alive_stop(self):
        self.mexc_keep_alive = False

    async def gateio_keep_alive(self, websocket, connection_data=None, ping_interval:int=15):
        """
            https://www.gate.io/docs/developers/apiv4/ws/en/#system-ap
            spot.ping

            https://www.gate.io/docs/developers/futures/ws/en/#ping-and-pong
            gate.io futures contract use the protocol layer ping/pong message.The server will initiate a ping message actively. If the client does not reply, the client will be disconnected.
            channel : futures.ping

            https://www.gate.io/docs/developers/apiv4/ws/en/#system-api
            options.ping
        """
        ping_interval = connection_data.get("ping_interval")
        id_ws = connection_data.get("id_ws", None)
        self.gateio_keep_alive = True
        while self.gateio_keep_alive:
            try:
                if connection_data.get("instType") == "spot":
                    await websocket.send({"channel" : "spot.ping"}) 
                if connection_data.get("instType") in ["future", "perpetual"]:
                    await websocket.send({"channel" : "futures.ping"}) 
                if connection_data.get("instType") == "option":
                    await websocket.send({"channel" : "options.ping"}) 
                await asyncio.sleep(ping_interval) 
            except websockets_errors as e:  
                self.logger.exception(f"Keep-Alive error for {id_ws}, {e}", exc_info=True)
                break
            except Exception as e:
                self.logger.exception(f"Unexpected error in keep-alive for {id_ws}, {e}", exc_info=True)
                break
            
    def gateio_keep_alive_stop(self):
        self.gateio_keep_alive = False

    async def htx_keep_alive(self, websocket, connection_data=None, ping_interval:int=5):
        """
            https://www.htx.com/en-us/opend/newApiPages/?id=662
            Heartbeat and Connection
            {"ping": 1492420473027}
            After connected to Huobi's Websocket server, the server will send heartbeat periodically (currently at 5s interval). The heartbeat message will have an integer in it, e.g.
            {"pong": 1492420473027}
            When client receives this heartbeat message, it should respond with a matching "pong" message which has the same integer in it, e.g.
        """
        pass

    async def coinbase_keep_alive(self, websocket, connection_data=None, ping_interval:int=5):
        """
            https://docs.cloud.coinbase.com/advanced-trade-api/docs/ws-channels#heartbeats-channel
            Subscribe to the heartbeats channel to receive heartbeats messages for specific products every second. Heartbeats include a heartbeat_counter which verifies that no messages were missed..
            Note, 1 hearbeat for a specific product
        """
        pass

class producer(keepalive):
    """
        2 modes: production, testing
    """
    def __init__(self, 
                 connection_data,
                 kafka_host='localhost:9092',
                 num_partitions=5,
                 replication_factor=1,
                 producer_reconnection_attempts=5, 
                 prometeus_start_server=8000
                 ):
        """
            databases : CouchDB, mockCouchDB
            ws_timestamp_keys: possible key of timestamps. Needed evaluate latency
            if using tinydb, you must create a folder tinybase
        """
        self.kafka_host = kafka_host
        self.producer = None
        self.producer_running = False
        self.producer_reconnection_attempts = producer_reconnection_attempts
        self.admin = AdminClient({'bootstrap.servers': self.kafka_host})
        self.num_partitions = num_partitions
        self.replication_factor = replication_factor
        self.connection_data = connection_data
        self.ws_failed_connections = {}
        self.start_prometeus_server = start_http_server
        # Deribit requires websockets to make api calls. websockets carrotines cant be called within websockets carotines (maybe can idk). This is the helper to mitigate the problem
        try:
            deribit_depths = [x for x in connection_data if x["exchange"]=="deribit" and x["objective"]=="depth"]
            self.deribit_depths = {x.get("id_api_2") : asyncio.run(ws_fetcher_helper(x.get("1stBooksSnapMethod"))) for x in deribit_depths}
            del deribit_depths
        except:
            pass
        self.kafka_topics = [NewTopic(cond.get("topic_name"), num_partitions=self.num_partitions, replication_factor=self.replication_factor) for cond in self.connection_data]
        self.kafka_topics_names = [cond.get("topic_name") for cond in self.connection_data]
        self.logger = self.setup_logger(base_path, log_file_bytes, log_file_backup_count)
        
        # Connection metrics
        self.ws_connections_active = Gauge('websocket_connections_active', 'Number of active WebSocket connections')
        self.api_connections_active = Gauge('API Asyncio pipes active', 'Number of active API Asyncio pipes active')
        self.connection_errors = Counter('app_connection_errors_total', 'Total number of connection errors')
        
        self.connection_duration = Histogram('app_connection_duration_seconds', 'Time connected before disconnect', buckets=(1, 5, 10, 30, 60, 300, 600))
        self.message_throughput = Counter('app_messages_total', 'Total messages sent and received')
        self.data_volume = Counter('app_data_volume_bytes', 'Total data transmitted in bytes')
        self.reconnection_attempts = Counter('app_reconnection_attempts_total', 'Total reconnection attempts')
        self.ping_pong_duration = Histogram('app_ping_pong_duration_seconds', 'Round-trip time for ping-pong messages', buckets=(0.01, 0.05, 0.1, 0.5, 1, 5))

        
    def start_metrics_server(self):
        self.start_prometeus_server()
        
    def setup_logger(self, log_file, maxBytes=10*1024*1024, backupCount=5):
        """
            Setups rotating logger with spesific logfile size and backup count
        """
        log_file = log_file / "logs/producerlogger.log"
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        file_handler = RotatingFileHandler(
                        log_file, 
                        maxBytes=maxBytes, 
                        backupCount=backupCount      
                    )
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        return logger
                        
                        
    async def binance_ws(self, connection_data, producer=None, topic=None):
        """
            producer and topic are reserved for kafka integration
            Maintain a WebSocket connection with automatic reconnection using exponential backoff.
        """ 
        @backoff.on_exception(backoff.expo,
                            websockets_errors,
                            max_tries=8)
        async def connect_and_listen():
            try:
                async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
                    await websocket.send(json.dumps(connection_data.get("msg_method")()))
                    keep_alive_task = asyncio.create_task(self.binance_keep_alive(websocket, connection_data))
                    while websocket.open:
                        try:
                            message = await websocket.recv()
                            if message == b'\x89\x00':
                                print(f"Received ping from {connection_data.get('id_ws')}. Sending pong...")
                                await websocket.pong(message)
                            await self.send_message_to_topic(topic, message)
                        except websockets_errors as e:  
                            self.logger.exception(f"WebSocket error for {connection_data.get('id_ws')}, {e}", exc_info=True)
                            break
            except asyncio.TimeoutError as e:
                self.logger.exception(f"WebSocket connection timed out for {connection_data.get('id_ws')}, {e}", exc_info=True)
            except Exception as e:
                self.logger.exception(f"Failed to establish WebSocket connection for {connection_data.get('id_ws')}, {e}", exc_info=True)
            finally:
                if self.binance_keep_alive:
                    self.binance_keep_alive_stop()
                self.logger.info(f"WebSocket connection for {connection_data.get('id_ws')} has ended.")


    async def bybit_ws(self, connection_data, producer=None, topic=None):        
        async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
            await websocket.send(json.dumps(connection_data.get("msg_method")()))
            keep_alive_task = asyncio.create_task(self.bybit_keep_alive(websocket, connection_data))
            while websocket.open:
                try:
                    message = await websocket.recv()
                    if message == b'\x89\x00':
                        print(f"Received ping from {connection_data.get('id_ws')}. Sending pong...")
                    await self.send_message_to_topic(topic, message)
                except websockets.ConnectionClosed:
                    print(f"Connection closed of {connection_data.get('id_ws')}")
                    break

    async def okx_ws(self, connection_data, producer=None, topic=None):
        async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
            await websocket.send(json.dumps(connection_data.get("msg_method")()))
            keep_alive_task = asyncio.create_task(self.okx_keep_alive(websocket, connection_data))
            while websocket.open:
                try:
                    message = await websocket.recv()
                    await self.send_message_to_topic(topic, message)
                except websockets.ConnectionClosed:
                    print(f"Connection closed of {connection_data.get('id_ws')}")
                    break

    async def deribit_ws(self, connection_data, producer=None, topic=None):
        if connection_data.get("objective") == "heartbeats":
            async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
                await websocket.send(json.dumps(connection_data.get("msg_method")()))
                while websocket.open:
                    try:
                        message = await websocket.recv()
                        message = json.loads(message)
                        if message.get("method") == "heartbeat":
                            print(f"Received heartbeat from Deribit server.")
                        elif message.get("result") == "ok":
                            test_response = {
                                "jsonrpc": "2.0",
                                "id": message["id"],
                                "result": {
                                    "method": "public/test"   # /api/v2/public/test
                                }
                            }
                            await websocket.send(json.dumps(test_response))
                    except websockets.ConnectionClosed:
                        print(f"Connection closed of {connection_data.get('id_ws')}")
                        break
        else:
            async for websocket in websockets.connect(connection_data.get("url"), ping_interval=30, timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
                await websocket.send(json.dumps(connection_data.get("msg_method")()))
                while websocket.open:
                    try:
                        message = await websocket.recv()
                        await self.send_message_to_topic(topic, message)
                    except websockets.ConnectionClosed:
                        print(f"Connection closed of {connection_data.get('id_ws')}")
                        break

    async def bitget_ws(self, connection_data, producer=None, topic=None):    
        async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
            await websocket.send(json.dumps(connection_data.get("msg_method")()))
            keep_alive_task = asyncio.create_task(self.bitget_keep_alive(websocket, connection_data))
            while websocket.open:
                try:
                    message = await websocket.recv()
                    if message == b'\x89\x00':
                        print(f"Received ping from {connection_data.get('id_ws')}. Sending pong...")
                    await self.send_message_to_topic(topic, message)
                except websockets.ConnectionClosed:
                    print(f"Connection closed of {connection_data.get('id_ws')}")
                    break

    async def kucoin_ws(self, connection_data, producer=None, topic=None):
        connection_message = connection_data.get("msg_method")()
        endpoint, ping_data = connection_data.get("url_method")()
        connect_id = re.search(r'connectId=([^&\]]+)', endpoint).group(1)
        ping_interval = ping_data.get("pingInterval")
        connection_data["ping_interval"] = ping_interval
        connection_data["connect_id"] = connect_id
        async for websocket in websockets.connect(endpoint, timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
            await websocket.send(json.dumps(connection_message))
            keep_alive_task = asyncio.create_task(self.kucoin_keep_alive(websocket, connection_data))
            while websocket.open:
                try:
                    message = await websocket.recv()
                    if "ping" in message:
                        await websocket.send({
                                        "id": str(connect_id),
                                        "type": "pong"
                                        })
                    await self.send_message_to_topic(topic, message)
                except websockets.ConnectionClosed:
                    print(f"Connection closed of {connection_data.get('id_ws')}")
                    break
    
    async def bingx_ws(self, connection_data, producer=None, topic=None):
        async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
            await websocket.send(json.dumps(connection_data.get("msg_method")()))
            keep_alive_task = asyncio.create_task(self.bingx_keep_alive(websocket, connection_data))
            while websocket.open:
                try:
                    message = await websocket.recv()
                    message = gzip.GzipFile(fileobj=io.BytesIO(message), mode='rb').read().decode('utf-8')
                    if "ping" in message:
                        message = json.loads(message)
                        await websocket.send(json.dumps({"pong" : message.get("ping"), "time" : message.get("time")}))
                    await self.send_message_to_topic(topic, message)
                except websockets.ConnectionClosed:
                    print(f"Connection closed of {connection_data.get('id_ws')}")
                    break
    
    async def mexc_ws(self, connection_data, producer=None, topic=None):        
        async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
            await websocket.send(json.dumps(connection_data.get("msg_method")()))
            keep_alive_task = asyncio.create_task(self.mexc_keep_alive(websocket, connection_data))
            while websocket.open:
                try:
                    message = await websocket.recv()
                    if "pong" in message:
                        await websocket.send("ping")
                    if "PONG" in message:
                        await websocket.send("PONG")
                    await self.send_message_to_topic(topic, message)
                except websockets.ConnectionClosed:
                    print(f"Connection closed of {connection_data.get('id_ws')}")
                    break

    async def gateio_ws(self, connection_data, producer=None, topic=None):        
        async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
            await websocket.send(json.dumps(connection_data.get("msg_method")()))
            keep_alive_task = asyncio.create_task(self.gateio_keep_alive(websocket, connection_data))
            while websocket.open:
                try:
                    message = await websocket.recv()
                    if "ping" in message:
                        await websocket.send({"channel" : json.loads(message).get("channel").repalce("ping", "pong")})
                    if "pong" in message:
                        await websocket.send({"channel" : json.loads(message).get("channel").repalce("pong", "ping")})
                    await self.send_message_to_topic(topic, message)
                except websockets.ConnectionClosed:
                    print(f"Connection closed of {connection_data.get('id_ws')}")
                    break

    async def htx_ws(self, connection_data, producer=None, topic=None):
        async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
            await websocket.send(json.dumps(connection_data.get("msg_method")()))
            while websocket.open:
                try:
                    message = await websocket.recv()
                    message =  gzip.decompress(message).decode('utf-8')
                    if "ping" in message:
                        await websocket.send(json.dumps({"pong": json.loads(message).get("ping")}))
                    await self.send_message_to_topic(topic, message)
                except websockets.ConnectionClosed:
                    print(f"Connection closed of {connection_data.get('id_ws')}")
                    break

    async def coinbase_ws(self, connection_data, producer=None, topic=None):
        if message.get("channel") != "heartbeats":
            conM, a, b = connection_data.get("msg_method")()
            async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
                await websocket.send(json.dumps(conM))
                while websocket.open:
                    try:
                        message = await websocket.recv()
                        await self.send_message_to_topic(topic, message)
                    except websockets.ConnectionClosed:
                        print(f"Connection closed of {connection_data.get('id_ws')}")
                        break
        if message.get("channel") == "heartbeats":
            conM, a, b = connection_data.get("msg_method")()
            async for websocket in websockets.connect(connection_data.get("url"), timeout=86400, ssl=ssl_context, max_size=1024 * 1024 * 10):
                await websocket.send(json.dumps(conM))
                while websocket.open:
                    pass

    @backoff.on_exception(backoff.expo,
                          recoverable_errors,
                          max_tries=5,
                          max_time=300,
                          givup=should_give_up)
    def ensure_topic_exists(self, topic_names):
        """
            Ensures all of the topics exist with correct configurations
            https://github.com/confluentinc/confluent-kafka-python    
        """
        try:
            fs = self.admin.create_topics(self.kafka_topics)
            for topic, f in fs.items():
                f.result()  
                print("Topic {} created".format(topic))
        # retriable errors
        except recoverable_errors as e:
            self.logger.error("%s raised for topic %s: %s", type(e).__name__, topic_name, e, exc_info=True)
            self.logger.exception(e)
            raise  
        # restart errors
        except KafkaConnectionError:
            self.logger.error(f"KafkaConnectionError raised, {e}", exc_info=True)
            self.logger.exception("KafkaConnectionError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        except KafkaTimeoutError:
            self.logger.error(f"KafkaTimeoutError raised, {e}", exc_info=True)
            self.logger.exception("KafkaTimeoutError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        except IllegalStateError as e:  
            self.logger.error(f"IllegalStateError raised, {e}", exc_info=True)
            self.logger.exception("IllegalStateError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        # others
        except NoBrokersAvailable:
            self.logger.error(f"NoBrokersAvailable raised, {e}", exc_info=True)
            self.logger.exception(e)
            await producer.stop()
        except AuthenticationFailedError:
            self.logger.error(f"AuthenticationFailedError raised, {e}", exc_info=True)
            self.logger.exception(e)
            await producer.stop()
        except KafkaError as e:  
            self.logger.error(f"KafkaError raised, {e}", exc_info=True)
            self.logger.exception(e)
            await producer.stop()
        except Exception as e: 
            self.logger.error(f"Unexpected error raised, {e}", exc_info=True)
            self.logger.exception(e)
            await producer.stop()

    @backoff.on_exception(backoff.expo,
                          recoverable_errors,
                          max_tries=5,
                          max_time=300,
                          givup=should_give_up)
    async def start_producer():
        try:
            await producer.start()
            print("Producer started successfully.")
        # retriable errors
        except recoverable_errors as e:
            self.logger.error("%s raised for topic %s: %s", type(e).__name__, topic_name, e, exc_info=True)
            self.logger.exception(e)
            raise  
        # restart errors
        except KafkaConnectionError:
            self.logger.error(f"KafkaConnectionError raised, {e}", exc_info=True)
            self.logger.exception("KafkaConnectionError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        except KafkaTimeoutError:
            self.logger.error(f"KafkaTimeoutError raised, {e}", exc_info=True)
            self.logger.exception("KafkaTimeoutError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        except IllegalStateError as e:  
            self.logger.error(f"IllegalStateError raised, {e}", exc_info=True)
            self.logger.exception("IllegalStateError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        # others
        except NoBrokersAvailable:
            self.logger.error(f"NoBrokersAvailable raised, {e}", exc_info=True)
            self.logger.exception(e)
        except AuthenticationFailedError:
            self.logger.error(f"AuthenticationFailedError raised, {e}", exc_info=True)
            self.logger.exception(e)
        except KafkaError as e:  
            self.logger.error(f"KafkaError raised, {e}", exc_info=True)
            self.logger.exception(e)
        except Exception as e: 
            self.logger.error(f"Unexpected error raised, {e}", exc_info=True)
            self.logger.exception(e)
        finally:
            await producer.stop()

    @backoff.on_exception(backoff.expo,
                          recoverable_errors,
                          max_tries=5,
                          max_time=300,
                          givup=should_give_up)
    async def send_message_to_topic(self, topic_name, message):
        try:
            await self.producer.send_and_wait(topic_name, message.encode("utf-8"))
        # retriable errors
        except recoverable_errors as e:
            self.logger.error("%s raised for topic %s: %s", type(e).__name__, topic_name, e, exc_info=True)
            self.logger.exception(e)
            raise  
        # restart errors
        except KafkaConnectionError:
            self.logger.error(f"KafkaConnectionError raised, {e}", exc_info=True)
            self.logger.exception("KafkaConnectionError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        except KafkaTimeoutError:
            self.logger.error(f"KafkaTimeoutError raised, {e}", exc_info=True)
            self.logger.exception("KafkaTimeoutError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        except IllegalStateError as e:  
            self.logger.error(f"IllegalStateError raised, {e}", exc_info=True)
            self.logger.exception("IllegalStateError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        # others
        except UnknownTopicOrPartitionError as e:
            self.logger.error("UnknownTopicOrPartitionError raised for topic %s: %e", topic_name, e, exc_info=True)
            self.logger.exception(e)
        except MessageSizeTooLargeError as e:
            self.logger.error("MessageSizeTooLargeError raised for topic %s: %e", topic_name, e, exc_info=True)
            self.logger.exception(e)
        except RecordListTooLargeError as e:
            self.logger.error("RecordListTooLargeError raised for topic %s: %e", topic_name, e, exc_info=True)
            self.logger.exception(e)
        except GroupAuthorizationFailedError as e:
            topic_name = connection_dict.get("topic_name")
            self.logger.error("GroupAuthorizationFailedError raised for topic %s: %e", topic_name, e, exc_info=True)
            self.logger.exception(e)
        except ClusterAuthorizationFailedError as e:
            topic_name = connection_dict.get("topic_name")
            self.logger.error("ClusterAuthorizationFailedError raised for topic %s: %e", topic_name, e, exc_info=True)
            self.logger.exception(e)
        except TopicAuthorizationFailedError as e:
            topic_name = connection_dict.get("topic_name")
            self.logger.error("TopicAuthorizationFailedError raised for topic %s: %e", topic_name, e, exc_info=True)
            self.logger.exception(e)
        except Exception as e:
            self.logger.error("Unexpected error raised for topic %s: %e", topic_name, e, exc_info=True)
            self.logger.exception(e)

    @backoff.on_exception(backoff.expo,
                        (ConnectionError, asyncio.TimeoutError, Exception, aiohttp.ClientResponseError, asyncio.CancelledError,
                         aiohttp.ServerTimeoutError, json.JSONDecodeError, ValueError, TimeoutError),  
                        max_tries=10,  
                        max_time=300) 
    async def aiohttp_socket(self, connection_data, topic, initial_delay):
        await asyncio.sleep(initial_delay)
        try:
            while True:
                message = await connection_data.get("aiohttpMethod")()
                await self.send_message_to_topic(topic, message)
                message = message.encode("utf-8")
                print(sys.getsizeof(message))
                await asyncio.sleep(connection_data.get("pullTimeout"))
        except aiohttp.ClientResponseError as e:
            self.logger.error(f"ClientResponseError of {connection_data.get('id_api')}, {e}", exc_info=True)
            self.logger.exception(f"ClientResponseError of {connection_data.get('id_api')}, {e}", exc_info=True)
        except json.JSONDecodeError as e:
            self.logger.error(f"JSONDecodeError of {connection_data.get('id_api')}, {e}", exc_info=True)
            self.logger.exception(f"JSONDecodeError of {connection_data.get('id_api')}, {e}", exc_info=True)
        except ValueError as e:
            self.logger.error(f"ValueError of {connection_data.get('id_api')}, {e}", exc_info=True)
            self.logger.exception(f"ValueError of {connection_data.get('id_api')}, {e}", exc_info=True)
        except asyncio.CancelledError as e:
            self.logger.error(f"asyncio.CancelledError of {connection_data.get('id_api')}, {e}", exc_info=True)
            self.logger.exception(f"asyncio.CancelledError of {connection_data.get('id_api')}, {e}", exc_info=True)
        except ConnectionError as e:
            self.logger.error(f"Connection error of {connection_data.get('id_api')}, {e}", exc_info=True)
            self.logger.exception(f"Connection error of of {connection_data.get('id_api')}, {e}", exc_info=True)
        except TimeoutError:
            self.logger.error(f"Timeout error occurred of {connection_data.get('id_api')}, {e}", exc_info=True)
            self.logger.exception(f"Timeout error occurred of {connection_data.get('id_api')}, {e}", exc_info=True)
        except asyncio.TimeoutError:
            self.logger.error(f"Asynchronous timeout error occurred of {connection_data.get('id_api')}, {e}", exc_info=True)
            self.logger.exception(f"Asynchronous timeout error occurredof {connection_data.get('id_api')}, {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"Unexpected error occurred of {connection_data.get('id_api')}, {e}", exc_info=True)
            self.logger.exception(f"Unexpected error occurred of {connection_data.get('id_api')}, {e}", exc_info=True)
            await asyncio.sleep(5)  # Add a delay before retrying or handling the error further

    @backoff.on_exception(backoff.expo,
                        BrokerNotAvailableError,
                        max_time=300)
    async def reconnect_producer(self):
        for i in range(self.producer_reconnection_attempts):  
            await asyncio.sleep(10) 
            try:
                await self.run_producer(is_reconnect=True)
                self.logger.info("Reconnected to the broker successfully")
                self.producer_running = True
                return True
            except Exception as e:
                self.logger.error("Reconnection failed: %s", str(e))
                self.logger.exception(e)
        self.logger.critical("Unable to reconnect to the broker after several attempts.")
        await self.producer.stop() 
        self.producer_running = False
        return False

    @backoff.on_exception(backoff.expo,
                          recoverable_errors,
                          max_tries=5,
                          max_time=300,
                          givup=should_give_up)
    async def run_producer(self, is_reconnect=False):
        try:
            # self.delete_all_topics()
            # print(self.delete_all_topics())
            if is_reconnect is False:
                self.ensure_topic_exists(self.kafka_topics_names)    
                self.producer = AIOKafkaProducer(bootstrap_servers=self.kafka_host)
            await self.start_producer()
            topics = self.admin.list_topics(timeout=10)        
            tasks = []
            for delay, connection_dict in enumerate(self.connection_data):
                if "id_ws" in connection_dict:
                    exchange = connection_dict.get("exchange")
                    ws_method = getattr(self, f"{exchange}_ws", None)
                    tasks.append(asyncio.ensure_future(ws_method(connection_dict)))
                if "id_ws" not in connection_dict and "id_api" in connection_dict:
                    if connection_dict.get("symbol_update_task") is True:
                        connection_dict["api_call_manager"].pullTimeout = connection_dict.get("pullTimeout")
                        connection_dict["api_call_manager"].send_message_to_topic = self.send_message_to_topic
                        connection_dict["api_call_manager"].topic_name = connection_dict.get("topic_name")
                        tasks.append(asyncio.ensure_future(connection_dict.get("api_call_manager").update_symbols(0)))
                        tasks.append(asyncio.ensure_future(connection_dict.get("api_call_manager").fetch_data()))
                    elif connection_dict.get("is_still_nested") is True:
                        connection_dict["api_call_manager"].pullTimeout = connection_dict.get("pullTimeout")
                        connection_dict["api_call_manager"].send_message_to_topic = self.send_message_to_topic
                        connection_dict["api_call_manager"].topic_name = connection_dict.get("topic_name")
                        tasks.append(asyncio.ensure_future(connection_dict.get("api_call_manager").fetch_data()))
                    else:
                        tasks.append(asyncio.ensure_future(self.aiohttp_socket(connection_dict, connection_dict.get("topic_name"), initial_delay=delay)))
                await asyncio.gather(*tasks)
        except recoverable_errors as e:
            self.logger.error("%s raised for topic %s: %s", type(e).__name__, topic_name, e, exc_info=True)
            self.logger.exception(e)
            raise  
        # restart errors
        except KafkaConnectionError:
            self.logger.error(f"KafkaConnectionError raised, {e}", exc_info=True)
            self.logger.exception("KafkaConnectionError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        except KafkaTimeoutError:
            self.logger.error(f"KafkaTimeoutError raised, {e}", exc_info=True)
            self.logger.exception("KafkaTimeoutError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        except IllegalStateError as e:  
            self.logger.error(f"IllegalStateError raised, {e}", exc_info=True)
            self.logger.exception("IllegalStateError raised, reconnecting producer...", exc_info=True)
            await self.reconnect_producer()
        # others
        except NoBrokersAvailable:
            self.logger.error(f"NoBrokersAvailable raised, {e}", exc_info=True)
            self.logger.exception(e)
            await producer.stop()
        except AuthenticationFailedError:
            self.logger.error(f"AuthenticationFailedError raised, {e}", exc_info=True)
            self.logger.exception(e)
            await producer.stop()
        except KafkaError as e:  
            self.logger.error(f"KafkaError raised, {e}", exc_info=True)
            self.logger.exception(e)
            await producer.stop()
        except Exception as e: 
            self.logger.error(f"Unexpected error raised, {e}", exc_info=True)
            self.logger.exception(e)
            await producer.stop()
            
    def describe_topics(self):
        """
            https://github.com/confluentinc/confluent-kafka-python/blob/master/src/confluent_kafka/admin/_topic.py
        """
        topics = self.admin.describe_topics(TopicCollection(self.kafka_topics_names))
        return list(topics.keys())

    def delete_all_topics(self):
        try:
            topics = self.describe_topics()
            self.admin.delete_topics(topics)
            print(f"{topics} were deleted")
        except Exception as e:
            print(f"An error occurred: {e}")
            
            
            
1. Granularity of connection duration per exchange
2. Measure latency for every book websocket and consider sending alerts when latency os too high. Integrate telegram into the notification system. Or email notification system. Many modern monitoring tools like Prometheus come with built-in alerting functionalities. Prometheus’s Alertmanager, for example, can route alerts to various endpoints including email, Slack, PagerDuty, and more.
3. Overall system latency to correlate with CPU and memory usage to identify resource bottlenecks
4. Reconnection attempts
import psutil

# CPU Utilization
cpu_usage = psutil.cpu_percent(interval=1)

# Memory Utilization
memory = psutil.virtual_memory()
memory_usage = memory.percent  # percentage of memory use

print(f"CPU Usage: {cpu_usage}%")
print(f"Memory Usage: {memory_usage}%")


5. Infrastructure Health: Monitor infrastructure metrics such as server load, network I/O, and disk I/O. 

# Disk I/O
disk_io = psutil.disk_io_counters()
print(disk_io)

# Network I/O
net_io = psutil.net_io_counters()
print(net_io)

6. Historical Data Analysis: Store historical metrics data for trend analysis and capacity planning. 
Summarize the data before storing it

Time-Series Databases: Databases like InfluxDB or TimescaleDB (an extension to PostgreSQL) are designed for time-series data and can be ideal for storing metrics. These databases efficiently handle high write and read loads and are good at compressing data.
Relational Databases: If your load is not extremely high, traditional relational databases like MySQL or PostgreSQL can be used effectively with proper indexing.
Integration with Prometheus: If you are using Prometheus for monitoring, it already stores time-series data and provides capabilities for querying historical data. Prometheus stores its data in a custom time-series database format optimized for high performance and efficiency.

Disk Space: The amount of disk space required will grow over time, especially with high-frequency data collection. You need to estimate the data growth and plan the infrastructure accordingly.
Operational Maintenance: Over such long periods, you will likely need to perform maintenance on the storage system, including hardware replacements and upgrades, migration to newer systems, and possibly changes in the data format.
Data Summarization: Over long periods, it may become impractical to keep all data at the highest granularity. You might need to summarize or downsample older data.
Integration with External Long-term Storage Solutions: Because Prometheus is not ideally suited for multi-year data retention due to its in-memory indexing, many use it in conjunction with other solutions designed for long-term storage. Common strategies include:
Using Prometheus with Remote Write: Prometheus can be configured to "remote write" its data to external long-term storage systems such as Thanos, Cortex, or M3, which are designed to handle longer retention periods efficiently.
Thanos: Integrating Prometheus with Thanos can provide a seamless way to manage long-term storage across multiple Prometheus instances. Thanos adds a storage layer that supports querying large amounts of historical metric data and can integrate with cloud storage solutions like Amazon S3, Google Cloud Storage, or Microsoft Azure Blob Storage, making it more scalable and resilient.
Cortex: Similar to Thanos, Cortex provides horizontally scalable, highly available, multi-tenant, long-term storage for Prometheus.

# Faust has proper metrics to monitor kafka servers