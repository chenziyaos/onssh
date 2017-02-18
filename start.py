__author__ = 'yao'

import os.path

import tornado.ioloop
import tornado.web
import tornado.httpserver
import tornado.options

from tornado.options import define
from tornado.options import options

import settings
import urls
from ioloop import IOLoop



define('port', default=settings.port, type=int, help='server listening port')
    
class Application(tornado.web.Application):

    def __init__(self):
        tornado.web.Application.__init__(self, urls.url, **(settings.settings))


def main():
    http_server = tornado.httpserver.HTTPServer(Application())
    http_server.listen(options.port)
    IOLoop.instance().start()
    tornado.ioloop.IOLoop.instance().start()


if __name__ == "__main__":
    main()
