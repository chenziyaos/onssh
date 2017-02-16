import logging

import tornado.web
import tornado.websocket

from daemon import LinkSSH
from data import ClientData
from check import check_ip, check_port


class IndexHandler(tornado.web.RequestHandler):

    def get(self):
        self.render("index.html")


class SSHHandler(tornado.websocket.WebSocketHandler):

    clients = dict()

    def get_client(self):
        return self.clients.get(self._id(), None)

    def put_client(self):
        link = LinkSSH(self)
        self.clients[self._id()] = link

    def remove_client(self):
        link = self.get_client()
        if link:
            link.destroy()
            del self.clients[self._id()]

    @staticmethod
    def _check_init_param(data):
        return check_ip(data["hostname"]) and check_port(data["port"])

    @staticmethod
    def _is_init_data(data):
        return data.get_type() == 'init'

    def _id(self):
        return id(self)

    def open(self):
        self.put_client()

    def on_message(self, message):
        link = self.get_client()
        client_data = ClientData(message)
        if self._is_init_data(client_data):
            if self._check_init_param(client_data.data):
                link.open(client_data.data)
                logging.info('connection established from: %s' % self._id())
            else:
                self.remove_client()
                logging.warning('init param invalid: %s' % client_data.data)
        else:
            if link:
                link.trans_forward(client_data.data)

    def on_close(self):
        self.remove_client()
        logging.info('client close the connection: %s' % self._id())

