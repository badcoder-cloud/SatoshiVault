import websockets
import kafka
import asyncio
import aiohttp
import json

import websockets.exceptions

websockets_errors = (
    websockets.exceptions.ConnectionClosed, 
    websockets.exceptions.ConnectionClosedError, 
    websockets.exceptions.ConnectionClosedOK, 
    websockets.exceptions.ProtocolError, 
    websockets.exceptions.InvalidHandshake,
    websockets.exceptions.InvalidState
    )

kafka_recoverable_errors = (
    asyncio.TimeoutError,
    ConnectionError,
    kafka.errors.NotLeaderForPartitionError,
    kafka.errors.LeaderNotAvailableError,
    kafka.errors.BrokerNotAvailableError,
    kafka.errors.NotEnoughReplicasError,
    kafka.errors.NotEnoughReplicasAfterAppendError
)

kafka_restart_errors = (
    kafka.errors.IllegalStateError,
    kafka.errors.KafkaTimeoutError,
    kafka.errors.KafkaConnectionError,
)

kafka_giveup_errors = (
    kafka.errors.UnknownTopicOrPartitionError,
    kafka.errors.MessageSizeTooLargeError,
    kafka.errors.RecordListTooLargeError,
    kafka.errors.GroupAuthorizationFailedError,
    kafka.errors.ClusterAuthorizationFailedError,
    kafka.errors.TopicAuthorizationFailedError,
    kafka.errors.TopicAuthorizationFailedError,
    Exception,
    kafka.errors.IllegalStateError,
    kafka.errors.KafkaTimeoutError,
    kafka.errors.KafkaConnectionError, 
    kafka.errors.AuthenticationFailedError,
    kafka.errors.NoBrokersAvailable,
    kafka.errors.KafkaError
)

kafka_send_errors = (
    kafka.errors.UnknownTopicOrPartitionError,
    kafka.errors.MessageSizeTooLargeError,
    kafka.errors.RecordListTooLargeError,
    kafka.errors.GroupAuthorizationFailedError,
    kafka.errors.ClusterAuthorizationFailedError,
    kafka.errors.TopicAuthorizationFailedError,
    Exception,
)

aiohttp_recoverable_errors = (
    ConnectionError,
    asyncio.TimeoutError,
    Exception,
    aiohttp.ClientResponseError,
    asyncio.CancelledError,
    aiohttp.ServerTimeoutError,
    json.JSONDecodeError,
    ValueError,
    TimeoutError
)