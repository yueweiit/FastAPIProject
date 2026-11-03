"""日志读取的HTTP缓存策略；仅作用于已明确列出的日志接口。"""
from fastapi.routing import APIRoute
from starlette.datastructures import MutableHeaders

LOG_READ_PATHS = frozenset({
    '/operation-logs', '/operation-logs/options', '/operation-logs/{source}/{row_id}',
    '/batches/{batch_id}/logs',
})
LOG_CACHE_CONTROL = 'private, no-store'


class PrivateLogRoute(APIRoute):
    async def handle(self, scope, receive, send):
        if scope['type'] != 'http' or self.path not in LOG_READ_PATHS:
            return await super().handle(scope, receive, send)

        async def private_send(message):
            if message['type'] == 'http.response.start':
                # 在发送阶段设置，保留原响应序列化和异常处理（401/403/422等）。
                MutableHeaders(scope=message)['Cache-Control'] = LOG_CACHE_CONTROL
            await send(message)

        return await super().handle(scope, receive, private_send)
