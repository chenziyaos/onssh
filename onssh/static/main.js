(function () {
    'use strict';

    var form = document.getElementById('login');
    var box = document.getElementById('term');
    var REMEMBER = ['host', 'port', 'username'];   // never the password

    REMEMBER.forEach(function (name) {
        var value = window.localStorage.getItem('onssh.' + name);
        if (value) {
            form.elements[name].value = value;
            form.elements.remember.checked = true;
        }
    });
    window.localStorage.removeItem('password');    // left behind by onssh 0.1
    var current = null;

    function connect(fields) {
        if (current) {
            current.dispose();
        }
        var term = new Terminal({cursorBlink: true, fontSize: 14,
                                 fontFamily: 'Menlo, Monaco, "DejaVu Sans Mono", "Liberation Mono", monospace'});
        current = term;
        var fit = new FitAddon.FitAddon();
        term.loadAddon(fit);
        box.hidden = false;
        term.open(box);
        fit.fit();
        term.write('Connecting to ' + fields.host + ':' + fields.port + '...\r\n');

        var scheme = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
        var path = window.location.pathname.replace(/[^/]*$/, '') + 'ws';
        var ws = new WebSocket(scheme + window.location.host + path);
        var send = function (obj) {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify(obj));
            }
        };
        var onResize = function () {
            fit.fit();
        };

        ws.onopen = function () {
            send({type: 'connect', host: fields.host, port: fields.port, username: fields.username,
                  password: fields.password, cols: term.cols, rows: term.rows});
        };
        ws.onmessage = function (evt) {
            var msg = JSON.parse(evt.data);
            if (msg.type === 'data') {
                term.write(msg.data);
            } else if (msg.type === 'status') {
                term.write('\x1b[2m' + msg.message + '\x1b[0m\r\n');
            } else if (msg.type === 'error') {
                term.write('\x1b[31m' + msg.message + '\x1b[0m\r\n');
            }
        };
        ws.onclose = function () {
            term.write('\r\n\x1b[2mConnection closed.\x1b[0m\r\n');
            window.removeEventListener('resize', onResize);
            form.hidden = false;
        };

        term.onData(function (data) {
            send({type: 'data', data: data});
        });
        term.onResize(function (size) {
            send({type: 'resize', cols: size.cols, rows: size.rows});
        });
        term.onTitleChange(function (title) {
            document.title = title;
        });
        window.addEventListener('resize', onResize);
        term.focus();
    }

    form.addEventListener('submit', function (evt) {
        evt.preventDefault();
        var fields = {};
        ['host', 'port', 'username', 'password'].forEach(function (name) {
            fields[name] = form.elements[name].value.trim();
        });
        fields.password = form.elements.password.value;
        REMEMBER.forEach(function (name) {
            if (form.elements.remember.checked) {
                window.localStorage.setItem('onssh.' + name, fields[name]);
            } else {
                window.localStorage.removeItem('onssh.' + name);
            }
        });
        form.elements.password.value = '';
        form.hidden = true;
        connect(fields);
    });
})();
