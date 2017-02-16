#!/bin/env python
# coding = utf-8

import settings
from settings import *
import tornado.httpserver
import tornado.ioloop
from ioloop import IOLoop

def main():
    #application = settings.application
    #print application
    print setting
    http_server = tornado.httpserver.HTTPServer(application)
    http_server.listen(options.port)
    IOLoop.instance().start()
    tornado.ioloop.IOLoop.instance().start()

if __name__ == "__main__":
    main()


