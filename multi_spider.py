import ctypes
import os.path
import sys
import threading
import traceback
from typing import List

import requests
import json
import time
import multiprocessing
import pandas as pd
from Crypto.Util.Padding import pad
from Crypto.Cipher import AES
import base64
from json import JSONDecodeError

from queue import Queue
from threading import Thread

from tqdm import tqdm

lock = threading.Lock()
# 每次执行任务最多进程数 （同时执行多少个主类别任务的采集任务）非必要不改，改大了会被流控
max_processes = multiprocessing.cpu_count()
# IO密集型任务线程数是系统核心数的2倍数量
max_threads = max_processes * 2
# 每个类别获取app总数量 主要是修改这个配置 100,200,300
class_app_max_num = 500
# 每次请求获取应用信息的个数（可改 35 50 75 100 200）'''''''''''。
request_limits = 200 if class_app_max_num > 200 else class_app_max_num
# 每次请求调用所需要的休眠时间 单位秒
time_sleep = 0
# 文件保存每save_file_nums个就保存一次 0就不保存
save_file_nums = 0
# 保存的文本类型  可选 excel 或 json 或者 all 两种都保存
save_file_mode = 'all'
# task_list填入需要采集的任务列表 可选 '聊天社交', '输入法', '浏览器', '下载工具', '视频直播', '音乐软件', '图片图形', '安全防护', '解压刻录', '系统工具', '驱动工具', '办公教育', '编程软件', '股票网银', '剪辑工具', '助手工具', '桌面美化'
task_list = ['聊天社交', '聊天社交', '输入法']
# 爬取首页
need_home_page = False


class FileManager:
    """文本读写工具类"""

    @staticmethod
    def read_big_file(file_name):
        with open(file_name, "r", encoding="utf8") as f:
            while True:
                line = f.readline()
                if not line:
                    break
                yield line

    @staticmethod
    def load_json(file_name):
        try:
            with open(file_name, 'r', encoding='UTF-8') as load_f:
                load_dict = json.load(load_f)
                return load_dict
        except FileNotFoundError as e:
            raise FileNotFoundError(f'FileNotFoundError,请确认同路径下有{file_name}文件', e)
        except JSONDecodeError as e:
            raise JSONDecodeError('JSONDecodeError', e)

    @staticmethod
    def save_json(load_dict):
        file_name = './app.json'
        if load_dict:
            with open(file_name, "w") as dump_f:
                json.dump(load_dict, dump_f, ensure_ascii=False)
                # print(f"导出数据至文件{file_name}完成")
                return True

    @staticmethod
    def export_excel(export: List[dict]):
        pf = pd.DataFrame(export)
        file_name = './apps.xlsx'
        # 替换空格
        pf.fillna(' ', inplace=True)
        try:
            if export:
                pf.to_excel(file_name, encoding='utf-8', index=False)
                # print(f"导出数据至文件{file_name}完成")
        except Exception as e:
            print("导出excel失败", e)
            return False
        finally:
            return True

    @staticmethod
    def save_file(data_list):
        data_list = list(data_list)
        if save_file_mode.lower() == 'json':
            FileManager.save_json(data_list)
        elif save_file_mode.lower() == 'excel':
            FileManager.export_excel(data_list)
        elif save_file_mode.lower() == 'all':
            FileManager.export_excel(data_list)
            FileManager.save_json(data_list)

    @staticmethod
    def save_txt(app_message):
        with open('./app.txt', 'a', encoding='utf-8') as f:
            f.write(app_message + '\n')

    @staticmethod
    def save_failed_info(failed_str):
        with open('./failed.txt', 'a', encoding='utf-8') as f:
            f.write(failed_str)

    @staticmethod
    def remove_txt():
        failed_txt = './failed.txt'
        app_txt = './app.txt'
        if os.path.exists(failed_txt):
            os.remove(failed_txt)
        if os.path.exists(app_txt):
            os.remove(app_txt)


class SpiderThread(Thread):
    """多线程类"""

    def __init__(self, queue: Queue, app_data_list):
        self.app_data_list = app_data_list
        Thread.__init__(self)
        self.queue = queue

    def run(self):
        while True:
            app_task, app_info = self.queue.get()
            try:
                app_data = self.parse(app_task, app_info)
                # print(f"当前进程结束，数量为{len(app_data_list)}")
                if app_data:
                    if app_data not in self.app_data_list:
                        lock.acquire()
                        # acquire和release方法之间放置对共享数据进行操作的代码，这里放了个将数据插入数组的操作
                        self.app_data_list.append(app_data)
                        current_counts = len(self.app_data_list)
                        # print(f"已获取数据量：{current_counts}",end='\n',flush=False)
                        # print('\r',
                        #       "已采集应用数据量: {:d} {} {}".format(current_counts, app_data.get('主分类'), app_data.get('软件名称')),
                        #       end='', flush=True)
                        progress_bar(current_counts, message='多线程任务采集进度：')
                        if save_file_nums != 0 and len(self.app_data_list) % save_file_nums == 0 and len(
                                self.app_data_list) > 0:
                            # print(f"已获取数据量：{len(self.app_data_list)},需要更新表格")
                            # 每保存了save_file_nums个就更新下表格数据
                            FileManager.save_file(self.app_data_list)
                        lock.release()
            finally:
                self.queue.task_done()

    def parse(self, app_task, app_info):
        # 开启多线程运行
        spider = ShopSpider()
        class_id = app_task.get('soft_id')
        class_name = app_task.get('class_name')
        tag_id = app_task.get('tag_id')
        read_url = f'https://lestore.lenovo.com/cate/soft?cid={class_id}&tid={tag_id}'
        tag_name = app_task.get('tag_name')
        app_name = app_info["softName"]
        task_str = f"[{self.getName()}-{self.ident}] [CLASS-{class_id}-{class_name}] [TAG-{tag_id}-{tag_name}] [{app_name}]"

        details_info = spider.get_soft_desc(app_info)
        download_url = spider.get_download_url(app_info)
        if details_info:
            softName = details_info.get('softName')
            softID = details_info.get('softID')
            downloadCount = details_info.get('downloadCount')
            version = details_info.get('version')
            installFileSize = details_info.get('installFileSize')
            createTime = details_info.get('createTime')
            detailInfo = details_info.get('detailInfo')
            name = details_info.get('name')
            tips = details_info.get('tips')
            warmTips = details_info.get('warmTips')
            whatNew = details_info.get('whatNew')
        app_data = {
            # "网址链接": read_url,
            "主分类": class_name,
            "软件名称": softName,
            # "子类别": tag_name,
            # "子类别Id": tag_id,
            "软件ID": softID,
            "下载次数": downloadCount,
            "版本号": version,
            "文件大小": installFileSize,
            "创建时间": createTime,
            "下载链接": download_url,
            "简介": detailInfo,
            "最近更新内容": whatNew,
            "name": name,
            "tips": tips,
            "warmTips": warmTips,
        }
        app_message = f"{task_str}\n网址链接：{read_url}\n软件名：{softName}\n软件Id:{softID}\n下载次数：{downloadCount}\n版本号：{version}\n文件大小：{installFileSize}\n创建时间：{createTime}\n简介：{detailInfo}\n下载链接：{download_url}\n"
        # print(app_message)
        FileManager.save_txt(app_message)
        return app_data


class ShopSpider:
    def __init__(self):
        self.clsss_app_list_url = "https://lestore.lenovo.com/api/webstorecontents/class/class_apps_list"
        self.download_url = "https://lestore.lenovo.com/api/webstorecontents/download/getDownloadUrl"
        self.details_url = "https://lestore.lenovo.com/api/webstorecontents/app/details"
        self.headers = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36",
            "content-type": "application/json",
        }
        self.session = requests.session()

    def aes_cipher(self, plain):
        aes_key = "65023EC4BA7420BB"
        iv = "65023EC4BA7420BB"
        aes = AES.new(aes_key.encode(), AES.MODE_CBC, iv.encode())
        padding_text = pad(plain.encode(), AES.block_size, style='pkcs7')
        encrypted_text = aes.encrypt(padding_text)
        return base64.b64encode(encrypted_text).decode()

    def get_app_list(self, soft_id, skip, tagId):
        """
        :param soft_id: 软件spp的id
        :param skip: 从第几个开始获取
        :param tagId: 子分类，首页是-1，之后根据tagid实际情况分类
        :return:
        """

        plain = '{"code":"soft","id":"%s","limit":%s,"skip":%s,"tagId":%s}' % (soft_id, request_limits, skip, tagId)
        enc_data = self.aes_cipher(plain)
        data = {"data": enc_data}
        try_time = 3
        is_ok = False
        while not is_ok and try_time > 0:
            try:
                response = self.session.post(url=self.clsss_app_list_url, headers=self.headers, data=json.dumps(data))
                if response.status_code > 200:
                    print(response.text)
                app_list = json.loads(response.text)["data"]["apps"]
                # print(f"[Process-{soft_id}-{tagId} 总数：{len(app_list)}] 数据范围：{skip}至{skip + request_limits - 1} ")

                is_ok = True
            except Exception as e:

                # print(f'Error，获取应用软件列表失败', soft_id, request_limits, skip, tagId, traceback.print_exc())
                app_list = []
                try_time -= 1
                time.sleep(1)
                if try_time == 0:
                    error_info = f'{soft_id} {tagId} get_app_list重试失败\n'
                    FileManager.save_failed_info(error_info)
        time.sleep(time_sleep)
        return app_list

    def get_download_url(self, app: dict):
        soft_id = app["softID"]
        app_name = app["softName"]
        plain = '{"bizType":"1","product":"3","softId":"%s","type":"0"}' % (soft_id)
        enc_data = self.aes_cipher(plain)
        data = {"data": enc_data}
        try_time = 3
        is_ok = False
        while not is_ok and try_time > 0:
            try:
                response = requests.post(self.download_url, headers=self.headers, data=json.dumps(data))
                if response.status_code > 200:
                    print(response.status_code, response.text)
                app_download_info = json.loads(response.text)
                app_download_url = app_download_info["data"]["downloadUrls"][0]["downLoadUrl"]
                is_ok = True
            except Exception as e:
                app_download_url = ''
                try_time -= 1
                # print(f'{app_name} 获取下载链接失败,尝试再重试{try_time}次')
                if try_time == 0:
                    error_info = f'{app_name} {soft_id} get_download_url重试失败\n'
                    FileManager.save_failed_info(error_info)
                time.sleep(1)
        time.sleep(time_sleep)
        return app_download_url

    def get_soft_desc(self, app: dict):
        app_name = app["softName"]
        app_soft_id = app["softID"]
        plain = '{"softId":"%s"}' % app_soft_id
        enc_data = self.aes_cipher(plain)
        data = {"data": enc_data}
        data = json.dumps(data)
        try_time = 3
        is_ok = False
        while not is_ok and try_time > 0:
            try:
                response = requests.post(self.details_url, headers=self.headers, data=data)
                if response.status_code > 200:
                    print(response.status_code, response.text)
                response_data = json.loads(response.text)
                details_info = response_data.get('data')
                is_ok = True
            except Exception as e:
                # print(f'{app_name} 获取软件详情失败,尝试再重试{try_time}次')
                details_info = {}
                try_time -= 1
                time.sleep(1)
                if try_time == 0:
                    error_info = f'{app_name} {app_soft_id} get_soft_desc重试失败 '
                    FileManager.save_failed_info(error_info)
        time.sleep(time_sleep)
        return details_info


class MultiTask:
    @staticmethod
    def run_process_task(app_task, app_data_list):
        try:
            spider = ShopSpider()
            soft_id = app_task.get('soft_id')
            tag_id = app_task.get('tag_id')

            for start in range(0, class_app_max_num, request_limits):
                app_list = spider.get_app_list(soft_id, skip=start, tagId=tag_id)
                MultiTask.run_thread_task(app_task, app_list, app_data_list)
                if len(app_list) < request_limits:
                    break

        except RuntimeError:
            print("获取不到数据，程序结束")
            exit()

    @staticmethod
    def run_thread_task(app_task, app_list: list, app_data_list):
        """
        执行线程
        :param app_task:当前分类任务的详细信息
        :param app_list: 当前分类下的应用信息列表
        :param max_threads: 同时处理的最大线程数
        :param time_leep: 调用请求的休眠秒数
        :return:
        """
        queue = Queue()
        for x in range(max_threads):
            worker = SpiderThread(queue, app_data_list)
            worker.daemon = True
            worker.start()

        for app_info in app_list:
            queue.put([app_task, app_info])
        # 执行方法在SpiderThread里面的run方法
        queue.join()


def generate_app_task_list():
    class_list = FileManager.load_json('class_list.json')
    if class_list:
        class_data = class_list.get('data')
        app_task_list = []
        for item in class_data:
            class_name = item.get('className')
            if class_name in task_list:
                soft_id = item.get('classId')
                tags_list = item.get('tags')
                if need_home_page:
                    task_info = {
                        "soft_id": soft_id,
                        "class_name": class_name,
                        "tag_id": -1,
                        "tag_name": '首页',
                    }
                    app_task_list.append(task_info)
                for tag_item in tags_list:
                    tag_id = tag_item.get('tagId')
                    tag_name = tag_item.get('tagName')
                    task_info = {
                        "soft_id": soft_id,
                        "class_name": class_name,
                        "tag_id": tag_id,
                        "tag_name": tag_name,
                    }
                    app_task_list.append(task_info)
    return app_task_list


def check_valid():
    # 校验每秒请求的总次数是否超过阈值
    threshold_nums = 120
    if max_processes * max_threads > threshold_nums:
        print(f"max_processes:{max_processes} 与 max_threads:{max_threads} 的乘积已大于阈值{threshold_nums},存在系统流控隐患，请重新配置合理范围")
        exit()
    if class_app_max_num < request_limits:
        print(f"class_app_max_num：{class_app_max_num} 不能小于 request_limits:{request_limits},请重新配置合理范围")
        exit()


def run(app_task_list):
    # 设置一个允许max_processes个进程并发的进程池
    pool = multiprocessing.Pool(processes=max_processes)
    app_task_list_pbar = tqdm(app_task_list)
    for i, app_task in enumerate(app_task_list_pbar):
        time.sleep(0.1)
        app_task_list_pbar.set_description(f"加载进程中:{app_task.get('class_name')}")
    for i, app_task in enumerate(app_task_list_pbar):
        '''
         （1）循环遍历，将app_task_list个子进程添加到进程池（相对父进程会阻塞）\n'
         （2）每次执行2个子进程，等一个子进程执行完后，立马启动新的子进程。（相对父进程不阻塞）\n'
        '''

        time.sleep(0.1)
        pool.apply_async(MultiTask.run_process_task,
                         args=(app_task, app_data_list))  # 维持执行的进程总数为max_processes，当一个进程执行完后启动一个新进程.
    pool.close()
    progress_bar(len(app_task_list), f'多进程任务加载加载完毕,执行进程总数{len(app_task_list)}，启动线程数{max_threads}\n')
    pool.join()


def progress_bar(i, message):
    time.sleep(0.05)
    print("\r", "{}：{}".format(message, i), "▋" * (int(i / 100) + 1), end="")
    sys.stdout.flush()
    time.sleep(0.05)


if __name__ == '__main__':
    """
    使用说明：
    1、第一次执行需要命令行执行 pip install -r requirement.txt  安装所需模块
    2、执行本程序即可
    """
    # 进程共享变量 需要用Manager
    app_data_list = multiprocessing.Manager().list()

    try:
        start = time.time()
        FileManager.remove_txt()
        print("开始解析网页")
        app_task_list = generate_app_task_list()
        total_task_counts = multiprocessing.Manager().Value(ctypes.c_int, len(app_task_list) * class_app_max_num)
        run(app_task_list)
        print('\n已完成，程序耗时:{}s'.format(round(time.time() - start, 2)))
        FileManager.save_file(app_data_list)
        # 导出数据

    except Exception as e:
        print(traceback.print_exc())
