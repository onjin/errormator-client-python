# -*- coding: utf-8 -*-
"""
 Copyright (c) 2010, Webreactor - Marcin Lulek <info@webreactor.eu>
 All rights reserved.

 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the <organization> nor the
      names of its contributors may be used to endorse or promote products
      derived from this software without specific prior written permission.

 THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 ARE DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
 DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
 ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import datetime
import logging
import sys
import threading
import time
import socket
#import requests
import urllib
import urllib2
import uuid

from errormator_client.ext_json import json
from errormator_client.utils import asbool, aslist
from webob import Request

# are we running python 3.x ?
PY3 = sys.version_info[0] == 3

DATE_FRMT = '%Y-%m-%dT%H:%M:%S.%f'
LEVELS = {'debug': logging.DEBUG,
          'info': logging.INFO,
          'warning': logging.WARNING,
          'error': logging.ERROR,
          'critical': logging.CRITICAL}

log = logging.getLogger(__name__)


class Client(object):
    __version__ = '0.4'
    __protocol_version__ = '0.3'

    def __init__(self, config):
        """
        at minimum client expects following keys to be present::
        
            errormator = true
            errormator.server_url = https://api.errormator.com
            errormator.api_key = YOUR_API_KEY
            errormator.report_404 = true
        
        additional config keys you can set in config object::
        
            errormator.server_name - identifier for Instance/Server Name your application is running on (default: auto determined fqdn of server)
            errormator.timeout - connection timeout when communicating with API
            errormator.reraise_exceptions - reraise exceptions when wsgi catches exception
            errormator.slow_requests - record slow requests in application (needs to be enabled for slow datastore recording)
            errormator.logging - enable hooking to application loggers
            errormator.logging.level - minimum log level for log capture
            errormator.logging_on_error - send logs only from erroneous/slow requests (default false) 
            errormator.datastores - enable query execution tracking for various datastore layers
            errormator.slow_request_time - (float/int) time in seconds after request is considered being slow (default 3)
            errormator.report_404 - enables 404 error logging (default False)
            errormator.report_errors - enables 500 error logging (default True)
            errormator.buffer_flush_interval - how often send data to mothership Errormator (default 5)
            errormator.force_send - send all data after request is finished - handy for crons or other voliatile applications
            errormator.environ_keys_whitelist - list of addotonal keywords that should be grabbed from environ object - can be string with comma separated list of words in lowercase
            (by default client will always send following info 'REMOTE_USER', 'REMOTE_ADDR', 'SERVER_NAME', 'CONTENT_TYPE' + all keys that start with HTTP* this list be extended with additional keywords set in config)
            errormator.request_keys_blacklist - list of keywords that should be blanked from request object - can be string with comma separated list of words in lowercase
            (by default client will always blank keys that contain following words 'password', 'passwd', 'pwd', 'auth_tkt', 'secret', 'csrf', this list be extended with additional keywords set in config)
            errormator.log_namespace_blacklist - list of namespaces that should be ignores when gathering log entries, can be string with comma separated list of namespaces
            (by default the client ignores own entries: errormator_client.client)
            
        """
        self.config = {}
        # general options
        self.config['enabled'] = asbool(config.get('errormator', True))
        self.config['server_name'] = config.get('errormator.server_name') or socket.getfqdn()
        self.config['client'] = config.get('errormator.client', 'python')
        self.config['api_key'] = config.get('errormator.api_key')
        self.config['server_url'] = config.get('errormator.server_url')
        self.config['timeout'] = int(config.get('errormator.timeout', 10))
        self.config['reraise_exceptions'] = asbool(
                config.get('errormator.reraise_exceptions', True))
        self.config['slow_requests'] = asbool(config.get('errormator.slow_requests', True))
        self.config['slow_request_time'] = float(config.get('errormator.slow_request.time', 3))
        self.config['slow_query_time'] = float(config.get('errormator.slow_query.time', 1))
        if self.config['slow_request_time'] < 0.1:
            self.config['slow_request_time'] = 0.1
        if self.config['slow_query_time'] < 0.1:
            self.config['slow_query_time'] = 0.1
        self.config['slow_request_time'] = datetime.timedelta(seconds=self.config['slow_request_time'])
        self.config['slow_query_time'] = datetime.timedelta(seconds=self.config['slow_query_time'])
        self.config['logging'] = asbool(config.get('errormator.logging', True))
        self.config['logging_on_error'] = asbool(config.get('errormator.logging_on_error', False))
        self.config['datastores'] = asbool(config.get('errormator.datastores', True))
        self.config['report_404'] = asbool(config.get('errormator.report_404', False))
        self.config['report_errors'] = asbool(config.get('errormator.report_errors', True))
        self.config['buffer_flush_interval'] = int(config.get('errormator.buffer_flush_interval', 5))
        self.config['force_send'] = asbool(config.get('errormator.force_send', False))
        self.config['request_keys_blacklist'] = ['password', 'passwd', 'pwd', 'auth_tkt', 'secret', 'csrf']
        user_blacklist = aslist(config.get('errormator.request_keys_blacklist',
                                           config.get('errormator.bad_request_keys')), ',')
        self.config['request_keys_blacklist'].extend(user_blacklist)
        if config.get('errormator.bad_request_keys'):
            log.warning('errormator.bad_request_keys is deprecated use request_keys_blacklist')

        self.config['environ_keys_whitelist'] = [
                'REMOTE_USER', 'REMOTE_ADDR', 'SERVER_NAME', 'CONTENT_TYPE']
        environ_whitelist = aslist(config.get('errormator.environ_keys_whitelist'), ',')
        self.config['environ_keys_whitelist'].extend(environ_whitelist)
        self.config['log_namespace_blacklist'] = aslist(config.get('errormator.log_namespace_blacklist', 'errormator_client.client'), ',')

        self.filter_callable = config.get('errormator.filter_callable')
        if self.filter_callable:
            try:
                parts = self.filter_callable.split(':')
                _tmp = __import__(parts[0], globals(), locals(), [parts[1], ], -1)
                self.filter_callable = getattr(_tmp, parts[1])
            except ImportError as e:
                self.filter_callable = self.data_filter
                log.error('Could not import filter callable, using default, %s' % e)
        else:
            self.filter_callable = self.data_filter

        if self.config['buffer_flush_interval'] < 1:
            self.config['buffer_flush_interval'] = 1
        # register logging
        import errormator_client.logger
        if self.config['logging']:
            self.log_handler = errormator_client.logger.register_logging()
            level = LEVELS.get(config.get('errormator.logging.level',
                                      'NOTSET').lower(), logging.NOTSET)
            self.log_handler.setLevel(level)

        # register slow call metrics
        if self.config['slow_requests']:
            self.config['timing'] = config.get('errormator.timing', {})
            for k, v in config.items():
                if k.startswith('errormator.timing'):
                    try:
                        self.config['timing'][k[18:]] = float(v)
                    except (TypeError, ValueError), e:
                        self.config['timing'][k[18:]] = False
            import errormator_client.timing
            errormator_client.timing.register_timing(self.config)
            self.datastore_handler = errormator_client.logger.register_datastores()

        self.endpoints = {
                          "reports": '/api/reports',
                          "slow_reports":'/api/slow_reports',
                          "logs":'/api/logs',
                          }

        self.report_queue = []
        self.report_queue_lock = threading.RLock()
        self.slow_report_queue = []
        self.slow_report_queue_lock = threading.RLock()
        self.log_queue = []
        self.log_queue_lock = threading.RLock()
        self.uuid = uuid.uuid4()
        self.last_submit = datetime.datetime.now()

    def submit_report_data(self):
        def send():
            with self.report_queue_lock:
                to_send_items = self.report_queue[:250]
                self.report_queue = self.report_queue[250:]
            if to_send_items:
                try:
                    self.remote_call(to_send_items, self.endpoints['reports'])
                except KeyboardInterrupt as e:
                    raise KeyboardInterrupt()
                except Exception as e:
                    log.warning('REPORTS: connection issue: %s' % e)
        send()
        # FIXME: reintroduce threads

    def submit_other_data(self):
        def send():
            with self.slow_report_queue_lock:
                slow_to_send_items = self.slow_report_queue[:250]
                self.slow_report_queue = self.slow_report_queue[250:]
            with self.log_queue_lock:
                logs_to_send_items = self.log_queue[:2000]
                self.log_queue = self.log_queue[2000:]

            if slow_to_send_items:
                try:
                    self.remote_call(slow_to_send_items,
                                 self.endpoints['slow_reports'])
                except KeyboardInterrupt as e:
                    raise KeyboardInterrupt()
                except Exception as e:
                    log.warning('SLOW REPORTS: connection issue: %s' % e)
            if logs_to_send_items:
                try:
                    self.remote_call(logs_to_send_items,
                                 self.endpoints['logs'])
                except KeyboardInterrupt as e:
                    raise KeyboardInterrupt()
                except Exception as e:
                    log.warning('LOGS: connection issue: %s' % e)
        send()
        # FIXME: reintroduce threads

    def check_if_deliver(self, force_send=False):
        delta = datetime.datetime.now() - self.last_submit
        if delta.seconds > self.config['buffer_flush_interval'] or force_send:
            submit_report_data_t = threading.Thread(target=self.submit_report_data)
            submit_report_data_t.start()
            submit_other_data_t = threading.Thread(target=self.submit_other_data)
            submit_other_data_t.start()
            self.last_submit = datetime.datetime.now()

    def remote_call(self, data, endpoint):
        GET_vars = urllib.urlencode({'protocol_version': self.__protocol_version__})
        server_url = '%s%s?%s' % (self.config['server_url'], endpoint, GET_vars,)
        headers = {'content-type': 'application/json',
                   'x-errormator-api-key':self.config['api_key']}
        log.info('sending out %s entries to %s' % (len(data), endpoint,))
        try:
            req = urllib2.Request(server_url,
                                  json.dumps(data),
                                  headers=headers)
        except IOError as e:
            message = 'ERRORMATOR: problem: %s' % e
            log.error(message)
            return False
        try:
            conn = urllib2.urlopen(req, timeout=self.config['timeout'])
            conn.close()
            return True
        except TypeError as e:
            conn = urllib2.urlopen(req)
            conn.close()
            return True
        if conn.getcode() != 200:
            message = 'ERRORMATOR: response code: %s' % conn.getcode()
            log.error(message)

    def data_filter(self, structure, section=None):
        if section in ['error_report', 'slow_report']:
            keys_to_check = (structure['report_details'][0]['request'].get('COOKIES'),
                              structure['report_details'][0]['request'].get('POST')
                              )

        for source in filter(None, keys_to_check):
            for k in source.iterkeys():
                for bad_key in self.config['request_keys_blacklist']:
                    if (bad_key in k.lower()):
                        source[k] = u'***'
        return structure

    def py_report(self, environ, traceback=None, message=None, http_status=200,
                  start_time=None):
        report_data, errormator_info = self.create_report_structure(environ,
                        traceback, server=self.config['server_name'],
                        http_status=http_status, include_params=True)
        report_data = self.filter_callable(report_data, 'error_report')
        url = report_data['report_details'][0]['url']
        if start_time:
            report_data['report_details'][0]['start_time'] = start_time
        with self.report_queue_lock:
            self.report_queue.append(report_data)
        log.warning(u'%s code: %s @%s' % (http_status,
                            report_data.get('error_type'), url.decode('utf8', 'ignore'),))
        return True

    def py_log(self, environ, records=None, r_uuid=None, traceback=None):
        log_entries = []
        if not records:
            records = self.log_handler.get_records()
            self.log_handler.clear_records()

        if self.config['logging_on_error'] and traceback is None:
            return False

        for record in records:
            if record.name in self.config['log_namespace_blacklist']:
                continue
            if not getattr(record, 'created'):
                time_string = datetime.datetime.utcnow().isoformat()
            else:
                time_string = time.strftime(DATE_FRMT,
                                time.gmtime(record.created)) % record.msecs
            try:
                message = record.getMessage()
                log_entries.append(
                        {'log_level':record.levelname,
                         "namespace":record.name,
                        'message':'%s' % (message.encode('utf8') if isinstance(message, unicode) else message,),
                        'server': self.config['server_name'],
                        'date':time_string,
                        'request_id':r_uuid
                        })
            except (TypeError, UnicodeDecodeError, UnicodeEncodeError), e:
                #handle some weird case where record.getMessage() fails
                log.warning(e)
        with self.log_queue_lock:
            self.log_queue.extend(log_entries)
        log.debug('add %s log entries to queue' % len(records))
        return True

    def py_slow_report(self, environ, start_time, end_time, records=()):
        report_data, errormator_info = self.create_report_structure(environ,
                    server=self.config['server_name'], include_params=True)
        report_data = self.filter_callable(report_data, 'slow_report')
        url = report_data['report_details'][0]['url']
        if not records:
            records = self.datastore_handler.get_records()
            self.datastore_handler.clear_records()
        report_data['report_details'][0]['start_time'] = start_time
        report_data['report_details'][0]['end_time'] = end_time
        report_data['report_details'][0]['slow_calls'] = []
        for record in records:
            r = getattr(record, 'errormator_data', record)
            report_data['report_details'][0]['slow_calls'].append(r)
        with self.slow_report_queue_lock:
            self.slow_report_queue.append(report_data)
        log.info('slow request/queries detected: %s' % url)
        return True

    def process_environ(self, environ, traceback=None, include_params=False):
        # form friendly to json encode
        parsed_environ = {}
        errormator_info = {}
        req = Request(environ)
        for key, value in req.environ.items():
            if key.startswith('errormator.') and key not in ('errormator.client',
                                                        'errormator.force_send',
                                                        'errormator.log',
                                                        'errormator.report'):
                errormator_info[key[11:]] = unicode(value)
            else:
                if traceback and (key.startswith('HTTP') or key in self.config['environ_keys_whitelist']):
                    try:
                        if isinstance(value, str):
                            parsed_environ[key] = value.decode('utf8')
                        else:
                            parsed_environ[key] = unicode(value)
                    except Exception as e:
                        pass
        # provide better details for 500's
        if include_params:
            try:
                parsed_environ['COOKIES'] = dict(req.cookies)
            except Exception as e:
                parsed_environ['COOKIES'] = {}
            try:
                parsed_environ['GET'] = dict([(k, req.GET.getall(k)) for k in req.GET])
            except Exception as e:
                parsed_environ['GET'] = {}
            try:
                parsed_environ['POST'] = dict([(k, req.POST.getall(k))
                                           for k in req.POST])
            except Exception as e:
                parsed_environ['POST'] = {}
                
        # figure out real ip
        if environ.get("HTTP_X_FORWARDED_FOR"):
            remote_addr = environ.get("HTTP_X_FORWARDED_FOR").split(',')[0].strip()
        else:
            remote_addr = (environ.get("HTTP_X_REAL_IP")
                           or environ.get('REMOTE_ADDR'))
        parsed_environ['REMOTE_ADDR'] = remote_addr
        errormator_info['URL'] = req.url
        return parsed_environ, errormator_info


    def create_report_structure(self, environ, traceback=None, message=None,
                http_status=200, server='unknown server', include_params=False):
        (parsed_environ, errormator_info) = self.process_environ(environ, traceback,
                                                            include_params)
        report_data = {'client': 'Python', 'report_details': []}
        if traceback:
            exception_text = traceback.exception
            traceback_text = traceback.plaintext
            report_data['error_type'] = exception_text
            report_data['traceback'] = traceback_text
        report_data['http_status'] = 500 if traceback else http_status
        if http_status == 404:
            report_data['error_type'] = '404 Not Found'
        report_data['priority'] = 5
        report_data['server'] = (server or
                    environ.get('SERVER_NAME', 'unknown server'))
        detail_entry = {}
        detail_entry['request'] = parsed_environ
        # fill in all other required info
        detail_entry['ip'] = parsed_environ.get('REMOTE_ADDR', u'')
        detail_entry['user_agent'] = parsed_environ.get('HTTP_USER_AGENT', u'')
        detail_entry['username'] = parsed_environ.get('REMOTE_USER',
                                                parsed_environ.get('username', ''))
        detail_entry['url'] = errormator_info.pop('URL', 'unknown')
        if 'request_id' in errormator_info:
            detail_entry['request_id'] = errormator_info.pop('request_id', None)
        detail_entry['message'] = message or errormator_info.get('message', u'')
        #conserve bandwidth pop keys that we dont need in request details
        exclude_keys = ('HTTP_USER_AGENT', 'REMOTE_ADDR', 'HTTP_COOKIE',
                        'errormator.client')
        for k in exclude_keys:
            detail_entry['request'].pop(k, None)
        report_data['report_details'].append(detail_entry)
        report_data.update(errormator_info)
        return report_data, errormator_info


def make_errormator_middleware(app, global_config, **kw):
    config = global_config.copy()
    config.update(kw)
    #this shuts down all errormator functionalities
    if not asbool(config.get('errormator', True)):
        return app

    client = Client(config=config)
    from errormator_client.wsgi import ErrormatorWSGIWrapper
    app = ErrormatorWSGIWrapper(app, client)
    return app
