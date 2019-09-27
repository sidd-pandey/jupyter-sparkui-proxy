from tornado import web
import inspect
import socket
import os
from urllib.parse import urlunparse, urlparse

from tornado import gen, web, httpclient, httputil, process, websocket, ioloop, version_info

from notebook.utils import url_path_join
from notebook.base.handlers import IPythonHandler, utcnow

try:
    # Tornado 6.0 deprecated its `maybe_future` function so `notebook` made their own.
    # See: https://github.com/jupyter/notebook/pull/4453
    from notebook.utils import maybe_future
except ImportError:
    # We can't find it in `notebook` then we should be able to find it in Tornado
    from tornado.gen import maybe_future


class PingableWSClientConnection(websocket.WebSocketClientConnection):
    """A WebSocketClientConnection with an on_ping callback."""
    def __init__(self, **kwargs):
        if 'on_ping_callback' in kwargs:
            self._on_ping_callback = kwargs['on_ping_callback']
            del(kwargs['on_ping_callback'])
        super().__init__(**kwargs)

    def on_ping(self, data):
        if self._on_ping_callback:
            self._on_ping_callback(data)


def pingable_ws_connect(request=None, on_message_callback=None,
                        on_ping_callback=None):
    """
    A variation on websocket_connect that returns a PingableWSClientConnection
    with on_ping_callback.
    """
    # Copy and convert the headers dict/object (see comments in
    # AsyncHTTPClient.fetch)
    request.headers = httputil.HTTPHeaders(request.headers)
    request = httpclient._RequestProxy(
        request, httpclient.HTTPRequest._DEFAULTS)

    # for tornado 4.5.x compatibility
    if version_info[0] == 4:
        conn = PingableWSClientConnection(io_loop=ioloop.IOLoop.current(),
            compression_options={},
            request=request,
            on_message_callback=on_message_callback,
            on_ping_callback=on_ping_callback)
    else:
        conn = PingableWSClientConnection(request=request,
            compression_options={},
            on_message_callback=on_message_callback,
            on_ping_callback=on_ping_callback,
            max_message_size=getattr(websocket, '_default_max_message_size', 10 * 1024 * 1024))

    return conn.connect_future

# from https://stackoverflow.com/questions/38663666/how-can-i-serve-a-http-page-and-a-websocket-on-the-same-url-in-tornado
class WebSocketHandlerMixin(websocket.WebSocketHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # print("args from WebSocketHandlerMixin contructor")
        # for arg in args:
        #     print(arg)
        # print("------------")
        bases = inspect.getmro(type(self))
        assert WebSocketHandlerMixin in bases
        meindex = bases.index(WebSocketHandlerMixin)
        try:
            nextparent = bases[meindex + 1]
        except IndexError:
            raise Exception("WebSocketHandlerMixin should be followed "
                            "by another parent to make sense")

        # undisallow methods --- t.ws.WebSocketHandler disallows methods,
        # we need to re-enable these methods
        def wrapper(method):
            def undisallow(*args2, **kwargs2):
                getattr(nextparent, method)(self, *args2, **kwargs2)
            return undisallow

        for method in ["write", "redirect", "set_header", "set_cookie",
                       "set_status", "flush", "finish"]:
            setattr(self, method, wrapper(method))
        nextparent.__init__(self, *args, **kwargs)
        # print("args afte next parent: ")
        # for arg in args:
        #     print(arg)
        # print("------------")

    async def get(self, *args, **kwargs):
        
        if len(args) == 1 and args[0] == "applications" :
            args = []
            args.append(4040)
            args.append('/api/v1/applications')

        if len(args) == 1 and args[0].endswith("allexecutors"):
            proxy_url = args[0]
            args = []
            args.append(4040)
            args.append("/api/v1/" + proxy_url)

        if len(args) == 1 and args[0].endswith("page-template.html"):
            proxy_url = "executorspage-template.html"
            args = []
            args.append(4040)
            args.append("/static/" + proxy_url)

        if len(args) == 1 and args[0].endswith("s/"):
            proxy_url = "jobs/"
            args = []
            args.append(4040)
            args.append("/" + proxy_url)
        
        # print("print args")
        # for arg in args:
        #     print(arg)
        # print("print kwargs")
        # for kwarg in kwargs:
        #     print(kwarg)
        if self.request.headers.get("Upgrade", "").lower() != 'websocket':
            return await self.http_get(*args, **kwargs)
        else:
            await maybe_future(super().get(*args, **kwargs))

class ProxyHandler(WebSocketHandlerMixin, IPythonHandler):
    """
    A tornado request handler that proxies HTTP and websockets from
    a given host/port combination. This class is not meant to be
    used directly as a means of overriding CORS. This presents significant
    security risks, and could allow arbitrary remote code access. Instead, it is
    meant to be subclassed and used for proxying URLs from trusted sources.

    Subclasses should implement open, http_get, post, put, delete, head, patch,
    and options.
    """
    def __init__(self, *args, **kwargs):
        
        # print("args one is: ", args[1])
        # print(type(args[1]))
        # print("uri is: ", args[1].uri)
        # http_obj = args[1]

        # if (http_obj.uri == "/api/v1/applications"):
        #     print("chaging the uri: ")
        #     http_obj.uri = "/uiproxy/4040/api/v1/applications"
        #     print(args[1])
        
        self.proxy_base = ''
        self.absolute_url = kwargs.pop('absolute_url', False)
        super().__init__(*args, **kwargs)

    # Support all the methods that torando does by default except for GET which
    # is passed to WebSocketHandlerMixin and then to WebSocketHandler.

    async def open(self, port, proxied_path):
        raise NotImplementedError('Subclasses of ProxyHandler should implement open')

    async def http_get(self, host, port, proxy_path=''):
        '''Our non-websocket GET.'''
        raise NotImplementedError('Subclasses of ProxyHandler should implement http_get')

    def post(self, host, port, proxy_path=''):
        raise NotImplementedError('Subclasses of ProxyHandler should implement this post')

    def put(self, port, proxy_path=''):
        raise NotImplementedError('Subclasses of ProxyHandler should implement this put')

    def delete(self, host, port, proxy_path=''):
        raise NotImplementedError('Subclasses of ProxyHandler should implement delete')

    def head(self, host, port, proxy_path=''):
        raise NotImplementedError('Subclasses of ProxyHandler should implement head')

    def patch(self, host, port, proxy_path=''):
        raise NotImplementedError('Subclasses of ProxyHandler should implement patch')

    def options(self, host, port, proxy_path=''):
        raise NotImplementedError('Subclasses of ProxyHandler should implement options')

    def on_message(self, message):
        """
        Called when we receive a message from our client.

        We proxy it to the backend.
        """
        self._record_activity()
        if hasattr(self, 'ws'):
            self.ws.write_message(message, binary=isinstance(message, bytes))

    def on_ping(self, data):
        """
        Called when the client pings our websocket connection.

        We proxy it to the backend.
        """
        self.log.debug('jupyter_server_proxy: on_ping: {}'.format(data))
        self._record_activity()
        if hasattr(self, 'ws'):
            self.ws.protocol.write_ping(data)

    def on_pong(self, data):
        """
        Called when we receive a ping back.
        """
        self.log.debug('jupyter_server_proxy: on_pong: {}'.format(data))

    def on_close(self):
        """
        Called when the client closes our websocket connection.

        We close our connection to the backend too.
        """
        if hasattr(self, 'ws'):
            self.ws.close()

    def _record_activity(self):
        """Record proxied activity as API activity

        avoids proxied traffic being ignored by the notebook's
        internal idle-shutdown mechanism
        """
        self.settings['api_last_activity'] = utcnow()

    def _get_context_path(self, port):
        """
        Some applications need to know where they are being proxied from.
        This is either:
        - {base_url}/proxy/{port}
        - {base_url}/proxy/absolute/{port}
        - {base_url}/{proxy_base}
        """
        if self.proxy_base:
            return url_path_join(self.base_url, self.proxy_base)
        if self.absolute_url:
            return url_path_join(self.base_url, 'uiproxy', 'absolute', str(port))
        else:
            return url_path_join(self.base_url, 'uiproxy', str(port))

    def get_client_uri(self, protocol, host, port, proxied_path):
        context_path = self._get_context_path(port)
        if self.absolute_url:
            client_path = url_path_join(context_path, proxied_path)
        else:
            client_path = proxied_path

        client_uri = '{protocol}://{host}:{port}{path}'.format(
            protocol=protocol,
            host=host,
            port=port,
            path=client_path
        )
        if self.request.query:
            client_uri += '?' + self.request.query

        return client_uri

    def _build_proxy_request(self, host, port, proxied_path, body):

        headers = self.proxy_request_headers()

        client_uri = self.get_client_uri('http', host, port, proxied_path)
        # Some applications check X-Forwarded-Context and X-ProxyContextPath
        # headers to see if and where they are being proxied from.
        if not self.absolute_url:
            context_path = self._get_context_path(port)
            headers['X-Forwarded-Context'] = context_path
            headers['X-ProxyContextPath'] = context_path

        req = httpclient.HTTPRequest(
            client_uri, method=self.request.method, body=body,
            headers=headers, **self.proxy_request_options())
        return req

    @web.authenticated
    async def proxy(self, host, port, proxied_path):
        '''
        This serverextension handles:
            {base_url}/proxy/{port([0-9]+)}/{proxied_path}
            {base_url}/proxy/absolute/{port([0-9]+)}/{proxied_path}
            {base_url}/{proxy_base}/{proxied_path}
        '''

        if 'Proxy-Connection' in self.request.headers:
            del self.request.headers['Proxy-Connection']

        self._record_activity()

        if self.request.headers.get("Upgrade", "").lower() == 'websocket':
            # We wanna websocket!
            # jupyterhub/jupyter-server-proxy@36b3214
            self.log.info("we wanna websocket, but we don't define WebSocketProxyHandler")
            self.set_status(500)

        body = self.request.body
        if not body:
            if self.request.method == 'POST':
                body = b''
            else:
                body = None

        client = httpclient.AsyncHTTPClient()

        req = self._build_proxy_request(host, port, proxied_path, body)
        response = await client.fetch(req, raise_error=False)
        # record activity at start and end of requests
        self._record_activity()

        # For all non http errors...
        if response.error and type(response.error) is not httpclient.HTTPError:
            self.set_status(500)
            self.write(str(response.error))
        else:
            self.set_status(response.code, response.reason)

            # clear tornado default header
            self._headers = httputil.HTTPHeaders()

            for header, v in response.headers.get_all():
                if header not in ('Content-Length', 'Transfer-Encoding',
                                  'Content-Encoding', 'Connection'):
                    # some header appear multiple times, eg 'Set-Cookie'
                    self.add_header(header, v)

            if response.body:
                self.write(response.body)

    async def proxy_open(self, host, port, proxied_path=''):
        """
        Called when a client opens a websocket connection.

        We establish a websocket connection to the proxied backend &
        set up a callback to relay messages through.
        """
        if not proxied_path.startswith('/'):
            proxied_path = '/' + proxied_path

        client_uri = self.get_client_uri('ws', host, port, proxied_path)
        headers = self.request.headers
        current_loop = ioloop.IOLoop.current()
        ws_connected = current_loop.asyncio_loop.create_future()

        def message_cb(message):
            """
            Callback when the backend sends messages to us

            We just pass it back to the frontend
            """
            # Websockets support both string (utf-8) and binary data, so let's
            # make sure we signal that appropriately when proxying
            self._record_activity()
            if message is None:
                self.close()
            else:
                self.write_message(message, binary=isinstance(message, bytes))

        def ping_cb(data):
            """
            Callback when the backend sends pings to us.

            We just pass it back to the frontend.
            """
            self._record_activity()
            self.ping(data)

        async def start_websocket_connection():
            self.log.info('Trying to establish websocket connection to {}'.format(client_uri))
            self._record_activity()
            request = httpclient.HTTPRequest(url=client_uri, headers=headers)
            self.ws = await pingable_ws_connect(request=request,
                on_message_callback=message_cb, on_ping_callback=ping_cb)
            ws_connected.set_result(True)
            self._record_activity()
            self.log.info('Websocket connection established to {}'.format(client_uri))

        current_loop.add_callback(start_websocket_connection)
        # Wait for the WebSocket to be connected before resolving.
        # Otherwise, messages sent by the client before the
        # WebSocket successful connection would be dropped.
        await ws_connected


    def proxy_request_headers(self):
        '''A dictionary of headers to be used when constructing
        a tornado.httpclient.HTTPRequest instance for the proxy request.'''
        return self.request.headers.copy()

    def proxy_request_options(self):
        '''A dictionary of options to be used when constructing
        a tornado.httpclient.HTTPRequest instance for the proxy request.'''
        return dict(follow_redirects=False)

    def check_xsrf_cookie(self):
        '''
        http://www.tornadoweb.org/en/stable/guide/security.html

        Defer to proxied apps.
        '''
        pass

    def select_subprotocol(self, subprotocols):
        '''Select a single Sec-WebSocket-Protocol during handshake.'''
        if isinstance(subprotocols, list) and subprotocols:
            self.log.info('Client sent subprotocols: {}'.format(subprotocols))
            return subprotocols[0]
        return super().select_subprotocol(subprotocols)


class LocalProxyHandler(ProxyHandler):
    """
    A tornado request handler that proxies HTTP and websockets
    from a port on the local system. Same as the above ProxyHandler,
    but specific to 'localhost'.
    """
    async def http_get(self, port, proxied_path):
        return await self.proxy(port, proxied_path)

    async def open(self, port, proxied_path):
        return await self.proxy_open('localhost', port, proxied_path)

    def post(self, port, proxied_path):
        return self.proxy(port, proxied_path)

    def put(self, port, proxied_path):
        return self.proxy(port, proxied_path)

    def delete(self, port, proxied_path):
        return self.proxy(port, proxied_path)

    def head(self, port, proxied_path):
        return self.proxy(port, proxied_path)

    def patch(self, port, proxied_path):
        return self.proxy(port, proxied_path)

    def options(self, port, proxied_path):
        return self.proxy(port, proxied_path)

    def proxy(self, port, proxied_path):
        return super().proxy('localhost', port, proxied_path)

def setup_handlers(web_app):
    web_app.add_handlers('.*', [
        (url_path_join(web_app.settings['base_url'], r'/uiproxy/(\d+)(.*)'),
         LocalProxyHandler, {'absolute_url': False}),
         (url_path_join(web_app.settings['base_url'], r'/api/v1/(.*)'),
         LocalProxyHandler, {'absolute_url': False}),
         (url_path_join(web_app.settings['base_url'], r'/api/v1/applications/(.*)/(.*)'),
         LocalProxyHandler, {'absolute_url': False}),
         (url_path_join(web_app.settings['base_url'], r'/static/executors(.*)'),
         LocalProxyHandler, {'absolute_url': False}),
         (url_path_join(web_app.settings['base_url'], r'/job(.*)'),
         LocalProxyHandler, {'absolute_url': False}),
        (url_path_join(web_app.settings['base_url'], r'/uiproxy/absolute/(\d+)(.*)'),
         LocalProxyHandler, {'absolute_url': True})
    ])




def load_jupyter_server_extension(nbapp):
    setup_handlers(nbapp.web_app)