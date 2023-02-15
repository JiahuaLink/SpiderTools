"""
梦幻西游https://xyq.cbg.163.com/爬虫
"""
import json
import multiprocessing
import os
import random
import re
import sys
import threading
import time
import urllib
from queue import Queue
from typing import List
import pandas as pd
import traceback
import requests
from threading import Thread
import urllib3
from log import logger
from selenium import webdriver
from selenium.common import TimeoutException
from selenium.webdriver.support import expected_conditions as EC, expected_conditions
from selenium.webdriver.support.ui import Select
import configparser
from selenium.webdriver import ActionChains
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.by import By
from urllib3.exceptions import InsecureRequestWarning

from proxy_util import ProxyManager

urllib3.disable_warnings(InsecureRequestWarning)

lock = threading.Lock()
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')
main_config = config['Main']
login_lock = threading.Lock()
# 进程间共享信号量，默认值0 是需要继续下载的，非0就需要
semaphores = multiprocessing.Value('i', 0)
per_page = 17


class FileManager:
    # 逐行读取
    @staticmethod
    def read_big_file(fpath):
        with open(fpath, "r", encoding="utf8") as f:
            while True:
                line = f.readline()
                if not line:
                    break
                yield line

    @staticmethod
    def get_cookies():
        try:
            with open("./cookies", 'r', encoding='utf-8') as load_f:
                return load_f.read()
        except Exception as e:
            # logger.info(traceback.print_exc(), str(e))
            return None

    @staticmethod
    def save_json(load_dict):
        with open("./app.json", "w") as dump_f:
            json.dump(load_dict, dump_f, ensure_ascii=False)
            return True

    @staticmethod
    def save_cookies(cookies: str):
        with open("./cookies", "w") as f:
            f.write(cookies)
            return True

    @staticmethod
    def export_excel(file_path, file_name, export: List[dict]):
        if not os.path.exists(file_path):
            os.mkdir(file_path)
        pf = pd.DataFrame(list(export))
        file_name = f'./{file_path}/{file_name}.xlsx'
        # 替换空格
        pf.fillna(' ', inplace=True)
        try:
            if export:
                pf.to_excel(file_name, encoding='utf-8', index=False)
                logger.debug(f"导出数据至文件{file_name} ok")
            else:
                logger.info(f"未匹配到相关数据，无需导出")

        except Exception as e:
            logger.info("导出excel失败", e)
            return False
        finally:
            return True

    @staticmethod
    def save_txt(log_message):
        with open('./log_message.log', 'a', encoding='utf-8') as f:
            f.write(log_message + '\n')

    @staticmethod
    def init_log_path():
        if os.path.exists('./log_message.log'):
            os.remove('./log_message.log')
        return


cookies = FileManager.get_cookies()


class XYQSpider:
    def __init__(self):
        self.session = requests.session()
        self.url = 'https://xyq.cbg.163.com/'
        self.cookies = cookies
        self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
        self.headers = {
            "content-type": "application/json",
            "user-agent": self.user_agent
        }
        if self.cookies:
            self.headers["cookie"] = self.cookies
        self.results = []

    def init_driver(self):
        options = webdriver.ChromeOptions()
        options.add_argument(f'user-agent={self.user_agent}')
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        options.add_argument('ignore-certificate-errors')
        # 设置浏览器无头
        # options.add_argument('--headless')
        self.options = options
        # 配置驱动
        self.driver = webdriver.Chrome(options=self.options)
        self.driver.maximize_window()

    def get_base_info(self, region_name, server_name, areaid, server_id, door, price_max,
                      price_min, page=1, per_page=15, search_mode='data'):
        schools = ['--不限--', '大唐官府', '化生寺', '女儿村', '方寸山', '天宫', '普陀山', '龙宫', '五庄观', '狮驼岭', '魔王寨', '阴曹地府', '盘丝洞', '神木林',
                   '凌波城', '无底洞', '女魃墓', '天机城', '花果山', '东海渊']
        check_ok = False
        retry_times = 0
        while retry_times <= 2 and not check_ok:
            school = schools.index(door)
            callback = "Request.JSONP.request_map.request_0"
            timestamp = int(time.time() * 1000)
            act = "recommd_by_role"
            view_loc = "search_cond"
            search_type = "search_role"
            search_str = f'callback={callback}&_={timestamp}&act={act}&server_id={server_id}&areaid={areaid}&server_name={urllib.parse.quote_plus(server_name)}&page={page}&view_loc={view_loc}&count={per_page}&search_type={search_type}'
            if school != 0:
                search_str += f'&school={school}'
            if price_min != 0:
                search_str += f'&price_min={price_min}'
            if price_max != 0:
                search_str += f'&price_max={price_max}'

            # self.headers[":path"] = '/cgi-bin/recommend.py?' +search_str
            encoded_url = f'https://xyq.cbg.163.com/cgi-bin/recommend.py?' + search_str
            # logger.info(encoded_url)
            try:
                r = self.session.get(url=encoded_url, headers=self.headers, proxies=ProxyManager().get())
                # logger.info(r.text)
                if r.status_code == 200:
                    pattern = r"\((.*)\)"
                    result = re.search(pattern, r.text)
                    if result:
                        json_data = result.group(1)
                        data = json.loads(json_data)
                        if data:
                            if data.get('status_code') == 'SESSION_TIMEOUT':
                                logger.info('SESSION_TIMEOUT', data.get('msg'))
                                self.login(region_name, server_name)
                            elif data.get('status_code') == 'CAPTCHA_AUTH':
                                logger.info(f'[Thread-{region_name}-{server_name}-{areaid}-{door}-{page}]',
                                            'CAPTCHA_AUTH',
                                            data.get('msg'), '需要重新登录！')
                                if not self.login_chek():
                                    lock.acquire()
                                    self.login(region_name, server_name)
                                    lock.release()
                            else:
                                total_pages = data['pager'].get('total_pages')
                                check_ok = True


                else:
                    logger.info(r.text)
                    logger.error('get base info 请求非200 重试' + traceback.print_exc() + str(e))
                    retry_times += 1
            except Exception as e:
                logger.error('Exception get base info 重试' + traceback.print_exc() + str(e))
                retry_times += 1

        if search_mode == 'data':
            return data
        elif search_mode == 'total_pages':
            return total_pages

    def search(self, region_name, server_name, areaid, server_id, door, from_shareid, price_max,
               price_min, id_start, id_end, page=1, per_page=15, ):

        global process_counts
        time_sleep = int(main_config['time_sleep']) + random.randint(1, 3)
        time.sleep(time_sleep)
        temp = []
        view_loc = "search_cond"
        # school = schools.index(door)
        # 全局变量信号量如果不需要下载了，值会大于0
        if semaphores.value > 0:
            print('no need to search')
            return []
        data = self.get_base_info(region_name, server_name, areaid, server_id, door, price_max, price_min, page,
                                  per_page,
                                  search_mode='data')
        equips = data['equips']
        reco_request_id = data.get('reco_request_id')
        thread_name = f'[Thread-{region_name}-{server_name}-{door}-{page}页]'
        # logger.info(thread_name + '开始查询')
        try:
            if equips:
                # logger.info(thread_name, '采集到数据量', len(equips))
                for equip in equips:
                    server_name = equip['server_name']
                    # area_name = equip['area_name']
                    seller_roleid = int(equip['seller_roleid'])
                    seller_nickname = equip['seller_nickname']
                    equip_level_desc = equip['equip_level_desc']
                    price = equip['price']
                    expire_time = equip['expire_time']
                    selling_time = equip['selling_time']
                    tag_key = equip['tag_key']
                    eid = equip['eid']
                    equip_refer = equip['kindid']
                    buy_url = f'https://xyq.cbg.163.com/equip?s={server_id}&eid={eid}&view_loc={view_loc}|{urllib.parse.quote_plus(tag_key)}&from_shareid={from_shareid}&reco_request_id={reco_request_id}&equip_refer={equip_refer}'
                    # logger.info(seller_roleid, id_start, id_end, low_price, high_price, price)
                    message = f"\nID：{seller_roleid} 门派：{door} 昵称：{seller_nickname} 等级：{equip_level_desc} 价格：{price} 出售时间：{selling_time} 超时时间：{expire_time}\n" \
                              f"藏宝阁链接：{buy_url}\n"
                    is_all_print = main_config['all_print']
                    if is_all_print.upper() == 'YES':
                        print(message)
                    lock.acquire()
                    process_counts += 1
                    progress_bar(process_counts,
                                 message=f"【{region_name} {server_name} {door}】第{page}页，总进度")
                    FileManager.save_txt(message)
                    lock.release()
                    if self.check_condition(seller_roleid, id_start, id_end):
                        print(message)
                        data = {
                            "大区": region_name,
                            "区服": server_name,
                            "门派": door,
                            "ID": seller_roleid,
                            "昵称": seller_nickname,
                            "类型": equip_refer,
                            "级别": equip_level_desc,
                            "价格": price,
                            "出售时间": selling_time,
                            "过期时间": expire_time,
                            "藏宝阁链接": buy_url
                        }
                        temp.append(data)


            else:
                logger.info(thread_name + "获取不到数据任务，终止", print(data))
                lock.acquire()
                semaphores.value += 1
                lock.release()
                return []
        except Exception as e:
            logger.error(f"{thread_name},{traceback.print_exc()},{str(e)}")
            return []
        return temp

    def get_userinfo_shareid(self):
        url = 'https://xyq.cbg.163.com/cgi-bin/userinfo.py?act=get_share_data'
        r = self.session.get(url, headers=self.headers, proxies=ProxyManager().get())
        if r.status_code > 200:
            logger.info(r.content.decode('unicode_escape'))
        try:
            result = json.loads(r.content.decode('unicode_escape'))

            if result.get('status') == 0:
                raise Exception('get userinfo shareid ERROR', result.get('msg'))
            from_shareid = result.get("from_shareid")
            # logger.info("from_shareid:" + from_shareid)
        except Exception as e:
            raise Exception('ERROR:get userinfo shareid failed. ', e)
        return from_shareid

    def login_chek(self):
        logger.info("Login check 登录检测...")
        url = 'https://xyq.cbg.163.com/cgi-bin/userinfo.py?act=get_realname_status'
        r = self.session.get(url, headers=self.headers, proxies=ProxyManager().get())
        if r.status_code > 200:
            logger.info(r.content.decode('unicode_escape'))
        result = json.loads(r.content.decode('unicode_escape'))

        if result.get("status") == 0:
            logger.error(f"Login check no pass. 登录检测不通过,{result.get('msg')}")
            if result.get('msg') == '系统繁忙，请稍候再试！':
                raise Exception('系统繁忙，请稍候再试！您的ip已被官方限制，请30分钟后再试！')
            return False
        elif result.get("status") == 1:
            logger.info("Login check ok.登录检测 ok")
            return True
        else:
            logger.info("Login check has exception. 登录检测异常，不通过")
            return False

    def get_kind_data(self):
        url = 'https://cbg-xyq.res.netease.com/js/kind_data.js?v=20190514'
        r = self.session.get(url, headers=self.headers, proxies=ProxyManager().get())
        if r.status_code > 200:
            logger.info(r.text)
        pattern = """var kind = (.*);"""

        result = re.search(pattern, r.content.decode('unicode_escape'))
        if result:
            kind_data: dict = json.loads(result.group(1))
        role_data = kind_data[1][1][1]
        for i, role in enumerate(role_data):
            logger.info(i, role[0])

        return role_data

    def get_server_info(self, region: str, server: str):
        try:
            url = 'https://cbg-xyq.res.netease.com/js/server_list_data.js'
            r = self.session.get(url, headers=self.headers, proxies=ProxyManager().get())
            pattern = """var server_data = (.*);"""
            result = re.search(pattern, r.content.decode('unicode_escape'))
            if result:
                server_data: dict = json.loads(result.group(1))
                for areaid in server_data:
                    region_name = server_data.get(areaid)[0][0]
                    server_list = server_data.get(areaid)[1]
                    for server_info in server_list:
                        server_id = server_info[0]
                        server_name = server_info[1]
                        if region == region_name and server == server_name:
                            return server_id, areaid
        except Exception as e:
            raise Exception("get_server_info failed", e)

    def login(self, region_name, server_name):
        """重新登录并且刷新cookies"""
        self.init_driver()
        driver = self.driver
        logger.info('正在启动浏览器')
        driver.get(self.url)
        try:
            # 设置变量
            # 使用XPath模糊匹配
            link = driver.find_element(by=By.XPATH, value="//a[contains(., '{}')]".format(region_name))
            link.click()
            # 使用XPath模糊匹配
            server_link = driver.find_element(by=By.XPATH, value="//a[@data_server_name='{}']".format(server_name))
            server_link.click()
            # Wait for the page to load and the image to appear
            wait = WebDriverWait(driver, 3)
            logger.info("等待扫码")
            wait.until(EC.presence_of_element_located((By.XPATH, "//div[@id='qrcode']/img")))
            # 等待进入按钮出现
            element = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//a[@id='login_btn']"))
            )
            logger.info("扫码ok")
            # 选择登录角色
            logger.info("选择登录角色")
            try:
                dialog = driver.find_element(By.XPATH, '//a[text()="我知道了"]')
                if dialog.is_displayed():
                    logger.info("关闭弹窗")
                    dialog.click()
            except Exception:
                # logger.error("未发现提示弹窗")
                pass
            logger.info("点击进入按钮")
            element.click()
            logger.info("登录角色ok")
            try:
                # logger.info("检测站内信消息")
                # 点击弹出框的确定按钮
                # 智能显式等待
                WebDriverWait(driver, 3, 0.5).until(
                    expected_conditions.alert_is_present())  # 最大等待时间30s,每0.5s检测一次元素，只要检测到即可进行下一步操作
                update_status = driver.switch_to.alert.text
                logger.info(update_status)
                driver.switch_to.alert.accept()
                logger.info("点击我是买家页面")
                element = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//a[contains(., '{}')]".format('我是买家')))
                )
                element.click()

            except Exception:
                # logger.error('没有站内信消息')
                pass
            try:
                logger.info("点击角色搜索")
                element = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//a[contains(., '{}')]".format('角色搜索')))
                )
                element.click()
            except Exception:
                raise Exception("点击角色搜索 失败！你的ip已被官方限制，请30分钟后再试！")
            except TimeoutException:
                raise Exception("点击角色搜索 失败超时")
        except TimeoutException:
            logger.error("超时退出")
            driver.close()
            return False
        except Exception as e:
            logger.error("登录失败，异常退出," + str(e))
            driver.close()
            return False
        cookies_lst = driver.get_cookies()
        cookies = [i.get('name') + '=' + i.get('value') for i in cookies_lst]
        cookies_str = ';'.join(cookies)
        # logger.info(f"获取cookies：{cookies_str}")
        FileManager.save_cookies(cookies_str)
        self.headers["cookie"] = cookies_str
        logger.info("保存cookies成功")
        driver.close()
        return True

    def do_browser_work(self, region_name, server_name, id_start, id_end, doors: list, low_price, high_price, total):
        driver = self.driver
        result = []
        file_name = f'{region_name}_{server_name}'
        total_count = 0
        print(f"\n查询条件：\n"
              f"大区：{region_name}\n"
              f"区服：{server_name}\n"
              f"门派：{','.join(doors)} \n"
              f"最低价格：{low_price}\n"
              f"最高价格：{high_price} \n"
              f"ID搜索范围: {id_start} - {id_end}\n"
              f"计划获取总数: {total}\n")

        logger.info("选择角色搜索ok")
        # logger.info("最小化浏览器")
        # self.driver.minimize_window()
        price_min = driver.find_element(By.XPATH, "//input[@id='txt_price_min']")
        price_min.send_keys(low_price)
        price_max = driver.find_element(By.XPATH, "//input[@id='txt_price_max']")
        price_max.send_keys(high_price)
        logger.info(f"金额范围：{low_price} 至 {high_price}")
        # logger.info("点击查询按钮")
        for door in doors:
            try:
                count = 0
                page = 1
                logger.info(f"选择门派：{door}")
                select_door = driver.find_element(by=By.ID, value="role_school")
                search_button = driver.find_element(By.XPATH, '//input[@value="查询" and @class="btn1"]')
                # ActionChains(driver).move_to_element(select_door).perform()
                select = Select(select_door)
                select.select_by_visible_text(door)
                element = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, '//input[@value="查询" and @class="btn1"]'))
                )
                ActionChains(driver).move_to_element(search_button).perform()
                element.click()
                # logger.info("点击查询按钮 ok")
                # 定位到tr元素
            except Exception:
                logger.error("Exception", traceback.print_exc())
                continue
            try:
                while True and count < total:
                    # logger.info("采集数据")
                    tr_elements = driver.find_elements(By.XPATH, '//tr[@recommd="true"]')
                    for i, tr_element in enumerate(tr_elements):
                        # level = driver.find_element(By.XPATH, "//table[@class='tb02']/tbody/tr[3]/td[2").text
                        # 悬停在tr元素上 # 定位到td元素
                        td_element = tr_element.find_element(By.XPATH, ".//td")
                        try:
                            price = td_element.find_element(By.XPATH,
                                                            f"//tr[@recommd=\"true\"][{i + 1}]//span[contains(@class,'p1000')]").text
                        except Exception:
                            logger.error("获取不到金额")
                            continue
                        ActionChains(driver).move_to_element(td_element).perform()
                        nickname = driver.find_element(By.XPATH, "//table[@class='tb02']/tbody/tr[1]/td").text
                        ID = driver.find_element(By.XPATH, "//table[@class='tb02']/tbody/tr[2]/td").text
                        door_type = driver.find_element(By.XPATH, "//table[@class='tb02']/tbody/tr[3]/td").text
                        role = driver.find_element(By.XPATH, "//table[@class='tb02']/tbody/tr[4]/td").text
                        if ID:
                            ID = int(ID)
                            # 定位到a标签
                            a_element = td_element.find_element(By.XPATH, ".//a")
                            # 获取a标签的超链接
                            a_element_href = a_element.get_attribute("href")
                            count += 1
                            total_count += 1
                            print('\r',
                                  f"已解析数据量:第{page}页 {total_count} {ID} {region_name} {server_name} {door_type} {nickname} {price} {a_element_href}",
                                  end='', flush=True)

                            if self.check_condition(ID, id_start, id_end):
                                logger.info(
                                    f"\n查询到符合条件的账号:{ID}, {region_name}, {server_name}, {door}, {door_type}, {nickname},{price},{a_element_href} ")
                                # 悬停在tr元素上 # 定位到td元素
                                data = {
                                    "大区": region_name,
                                    "区服": server_name,
                                    "门派": door_type,
                                    "ID": ID,
                                    "昵称": nickname,
                                    "类型": role,
                                    # "级别": level,
                                    "价格": price,
                                    "藏宝阁链接": a_element_href,
                                }
                                result.append(data)
                        else:
                            continue

                    try:
                        next_element = driver.find_element(By.XPATH, "//div[@class=\'pages\']//a[contains(., '下一页')]")
                        ActionChains(driver).move_to_element(next_element).perform()
                        next_element.click()
                    except Exception:
                        logger.warning("\n已查询到末页")
                        logger.info(f'\n{door} 门派获取 ok')
                        FileManager.export_excel(region_name, f'{file_name}', result)
                        break
                    page += 1
            except Exception as e:
                logger.info(traceback.print_exc() + str(e))
                # FileManager.export_excel(f'{file_name}', result)
                # driver.close()
            finally:
                logger.info(f'\n{door} 门派获取 ok')
                FileManager.export_excel(region_name, f'{file_name}', result)
        if total_count == 0:
            logger.info("未发现目标账号信息")
        driver.close()

    def check_condition(self, ID, id_start, id_end):
        if ID > id_start and ID < id_end:
            return True
        return False


class MultiTask:
    @staticmethod
    def run_process_task(region_name, server_name, server_id, areaid, door, per_page, total_reqs, total, from_shareid,
                         high_price, low_price, id_start, id_end, results):
        print(f'\n开始获取【{region_name}】【{server_name}】【{door}】查总询页数 {total_reqs},每页数据量 {per_page}')
        params = []
        process_list = []
        global process_counts
        # 任务是否结束的信号量
        semaphores_break = False
        process_counts = 0
        try:
            for page in range(1, total_reqs + 1):
                req = {
                    "region_name": region_name,
                    "server_name": server_name,
                    "server_id": server_id,
                    "areaid": areaid,
                    "door": door,
                    "per_page": per_page,
                    "page": page,
                    "from_shareid": from_shareid,
                    "price_max": high_price,
                    "price_min": low_price,
                    "id_start": id_start,
                    "id_end": id_end,
                }
                params.append(req)
            # print("params", params)

            if total <= process_counts:
                print(f'\n多进程获取完成!【{region_name}】【{server_name}】【{door}】,获取数量为：{process_counts}')
                return
            MultiTask.run_thread_task(params, total, process_list, results, semaphores_break)
            pass

        except RuntimeError as e:
            print("获取不到数据，程序结束", e)
            exit()
        except Exception as e:
            print("Exception", e)

    @staticmethod
    def run_thread_task(params: list, total, process_list, results, semaphores_break):
        """
        执行线程
        :return:
        """
        queue = Queue()
        max_threads = int(main_config['max_threads'])
        for x in range(max_threads):
            worker = SpiderThread(queue, total, process_list, results, semaphores_break)
            worker.daemon = True
            worker.start()
        for param in params:
            queue.put(param)
        # 执行方法在SpiderThread里面的run方法
        queue.join()


class SpiderThread(Thread):
    """多线程类"""

    def __init__(self, queue: Queue, total, process_list, results, semaphores_break):
        Thread.__init__(self)
        self.results: list = results
        self.total: int = total
        self.semaphores_break: bool = semaphores_break
        self.process_list: list = process_list
        self.queue = queue
        self.spider = XYQSpider()

    def run(self):
        while True:
            process_counts = len(self.process_list)
            if self.semaphores_break:
                return
            try:
                if process_counts <= self.total:
                    # print("还需要获取")
                    param = self.queue.get()
                    # 执行这句话队列元素会减去1，通知到主线程 否则join()阻塞无法结束
                    self.queue.task_done()
                    region_name = param.get('region_name')
                    server_name = param.get('server_name')
                    door = param.get('door')
                    page = param.get('page')
                    # print(region_name, server_name, door, page)

                    temp_data = self.parse(self.spider, param)
                    if temp_data:
                        if temp_data not in self.results:
                            self.results.extend(temp_data)
                            self.process_list.extend(temp_data)
                            # current_counts = len(self.results)
                            if process_counts > 0 and page % 2 == 0:
                                # acquire和release方法之间放置对共享数据进行操作的代码，操作表格
                                lock.acquire()
                                FileManager.export_excel(region_name, server_name, self.results)
                                lock.release()
                            process_counts = len(self.process_list)
                            if process_counts >= self.total:
                                lock.acquire()
                                FileManager.export_excel(region_name, f'{server_name}_{door}', self.results)
                                print(f"\n{door}数据导出成功，当前获取数量为{process_counts}")
                                lock.release()
                                self.semaphores_break = True


            except Exception as e:
                print(e)

    def parse(self, spider: XYQSpider, param: dict):
        # 开启多线程运行
        region_name = param.get('region_name')
        server_name = param.get('server_name')
        areaid = param.get('areaid')
        server_id = param.get('server_id')
        door = param.get('door')
        from_shareid = param.get('from_shareid')
        price_max = param.get('price_max')
        price_min = param.get('price_min')
        page = param.get('page')
        per_page = param.get('per_page')
        id_start = param.get('id_start')
        id_end = param.get('id_end')
        # 核心查询代码
        return spider.search(region_name, server_name, areaid, server_id, door, from_shareid, price_max,
                             price_min, id_start, id_end, page, per_page)


class TaskFinsh(Exception):
    def __init__(self):
        pass


def progress_bar(i, message):
    time.sleep(0.05)
    print("\r", "{}：{}".format(message, i), "▋" * (int(i / 10) + 1), end="")
    sys.stdout.flush()
    time.sleep(0.05)


if __name__ == '__main__':
    """
    """
    try:
        multiprocessing.freeze_support()
        logger.info("Starting 梦幻西游藏宝阁...")
        FileManager.init_log_path()
        ss = XYQSpider()
        logger.info("Init 初始化配置...")
        region_name = main_config.get('region')
        server_name = main_config.get('server_name')
        doors = main_config.get('doors')
        total = main_config.get('total')
        low_price = main_config.get('low_price')
        high_price = main_config.get('high_price')
        id_start = main_config.get('id_start')
        id_end = main_config.get('id_end')
        mode = int(main_config.get('api_mode'))
        max_threads = main_config.get('max_threads')
        max_processes = int(main_config.get('max_processes'))
        time_sleep = main_config.get('time_sleep')

        if max_threads == '':
            max_threads = 1
        else:
            max_threads = int(max_threads)
        if doors == '':
            doors = ['--不限--']
        else:
            doors = doors.split(',')
        if low_price == '':
            low_price = 0
        else:
            low_price = int(low_price)
        if time_sleep == '' or time_sleep == '0':
            time_sleep = 0.5
        else:
            time_sleep = int(time_sleep)
        if high_price == '':
            high_price = 100000
        else:
            high_price = int(high_price)
        if id_start == '':
            id_start = 0
        else:
            id_start = int(id_start)
        if id_end == '':
            id_end = 999999999
        else:
            id_end = int(id_end)
        if total == '':
            total = 10
        else:
            total = int(total)
        if mode == 0:
            is_login: bool = ss.login(region_name, server_name)
            if is_login:
                ss.do_browser_work(region_name, server_name, id_start, id_end, doors, low_price, high_price, total)
        elif mode == 1:
            """多线程模式"""
            cookies = FileManager.get_cookies()
            if cookies:
                ss.headers['cookie'] = cookies
            is_login = ss.login_chek()
            if not cookies or not is_login:
                logger.info("QRCode Need 需要扫码登录...")
                is_login: bool = ss.login(region_name, server_name)
            if is_login:
                from_shareid = ss.get_userinfo_shareid()
                if from_shareid:
                    server_id, areaid = ss.get_server_info(region_name, server_name)
                    # 进程共享变量 需要用Manager
                    results = multiprocessing.Manager().list()
                    pool = multiprocessing.Pool(processes=max_processes)
                    for door in doors:
                        total_pages = total
                        total_reqs = int(total_pages / per_page) + 1
                        pool.apply_async(MultiTask.run_process_task,
                                         args=(
                                             region_name, server_name, server_id, areaid, door, per_page, total_reqs,
                                             total,
                                             from_shareid,
                                             high_price, low_price, id_start, id_end, results))
                    pool.close()
                    logger.info(f'多进程任务加载加载完毕,执行进程总数{max_processes}，启动线程数{max_threads}')
                    pool.join()
                    print(f"\n执行完毕，获取到符合条件总数：{len(results)}")
                    if results:
                        FileManager.export_excel(region_name, f'{server_name}', results)
                        logger.info('数据导出成功')
                else:
                    logger.info("from_shareid 非法，无法发起请求")
    except Exception as e:
        logger.error(f"Run Failed! 程序运行错误,自动退出")
        logger.error(f"{traceback.print_exc()}")
