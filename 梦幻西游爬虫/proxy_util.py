import configparser

from bs4 import BeautifulSoup as bs
import requests as _r
import random
from log import logger

PROXY_UPDATE_QUANTITY = 80

PROXY_WEB_API = 'https://free-proxy-list.net'
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')
main_config = config['Main']


class ProxyManager:
    def __init__(self, metux=None):
        self.proxies = []
        self.metux = metux

    def update(self):
        try:
            resp = _r.get(PROXY_WEB_API)
            html = resp.text
        except Exception as err:
            print(err)
            print('IP表更新失败')
            return 0
        soup = bs(html, 'html.parser')
        table = soup.find('table')
        tmp_proxies = []
        c = 0
        for tr in table.tbody:
            if c >= PROXY_UPDATE_QUANTITY: break
            td = tr.findAll('td')
            ip = td[0].string
            port = td[1].string
            tmp_proxies.append(ip + ':' + port)
            c += 1

        if self.metux:
            with self.metux:
                self.proxies = tmp_proxies[:]
        else:
            self.proxies = tmp_proxies[:]
        return len(self.proxies)

    def get(self):
        if main_config['use_proxies'].upper() == 'YES':
            self.update()
            proxy = {'http': 'http://' + random.choice(self.proxies)}
            # logger.info("本次请求使用代理," + proxy.get('http'))
        else:
            proxy = None
        return proxy
