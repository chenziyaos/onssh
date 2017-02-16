#coding=utf-8

import os

## static /templates
settings = dict(
    static_path=os.path.join(os.path.dirname(__file__), "static"),
    template_path=os.path.join(os.path.dirname(__file__), "templates"),
    #系统调试模式，服务器可设置为False
    debug=True,
)

port = 8000
## Mysql
## database="mysql"
mysql = {
    'url' : "localhost",
    'port' : "3306",
    'user' : "root",
    'password' : "",
}

redis = {
    'url' : "",
    'port' : "",
    'password' : "",
}

# 日志开启
log = "on"
log_path = ""
logging = "info"
log_to_stderr = True
log_file_max_size = 2*1024*1024
log_file_num_backups = 7
